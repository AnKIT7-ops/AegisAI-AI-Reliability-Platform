"""Evaluation runs: start one, list them, read results and reports."""

import logging
import uuid
from typing import Annotated, Any, Literal

from fastapi import APIRouter, BackgroundTasks, Depends, Query
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.api.deps import get_chat_model, get_embedder, get_sessionmaker, get_store
from app.core.config import Settings, get_settings
from app.core.errors import ApiError
from app.db.models import EvaluationRun
from app.services.evaluation.dataset import load_dataset
from app.services.evaluation.jobs import (
    active_run,
    create_run,
    execute_run,
    result_to_dict,
    run_to_dict,
)
from app.services.evaluation.report import (
    GateResult,
    check_gate,
    load_thresholds,
    markdown_report,
    results_csv,
)
from app.services.evaluation.runner import EvalConfig
from app.services.llm.base import ChatModel, Embedder
from app.services.rag.store import VectorStore

router = APIRouter(prefix="/api/v1/evaluations", tags=["evaluations"])
log = logging.getLogger("aegis")
Sessions = Annotated[sessionmaker[Session], Depends(get_sessionmaker)]
SettingsDep = Annotated[Settings, Depends(get_settings)]


class RunRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    label: str = Field(default="baseline", min_length=1, max_length=64)
    # A file name inside the evaluation directory, never a path.
    dataset: str = Field(default="v1", pattern=r"^v[0-9]{1,4}$")
    top_k: int | None = Field(default=None, ge=1, le=10)
    min_score: float | None = Field(default=None, ge=-1, le=1)


class RunCreated(BaseModel):
    id: str
    status: str


def _get_run(session: Session, run_id: uuid.UUID) -> EvaluationRun:
    run = session.get(EvaluationRun, run_id)
    if run is None:
        raise ApiError(404, "not_found", "evaluation run not found")
    return run


def _gate(run: EvaluationRun, settings: Settings) -> GateResult | None:
    if run.status != "done":
        return None
    try:
        thresholds = load_thresholds(settings.eval_dir, run.dataset_version)
    except ValueError:
        log.exception("thresholds for dataset %s are invalid", run.dataset_version)
        return GateResult(passed=False, failures=["the thresholds file is invalid"])
    if thresholds is None:
        return None
    return check_gate(run.metrics or {}, thresholds, run.dataset_checksum)


@router.post("/run", status_code=202)
def start_run(
    req: RunRequest,
    background: BackgroundTasks,
    sessions: Sessions,
    settings: SettingsDep,
    store: Annotated[VectorStore, Depends(get_store)],
    embedder: Annotated[Embedder, Depends(get_embedder)],
    chat: Annotated[ChatModel, Depends(get_chat_model)],
) -> RunCreated:
    """Start a run in the background; poll GET /api/v1/evaluations/{id}."""
    path = settings.eval_dir / f"{req.dataset}.jsonl"
    if not path.is_file():
        raise ApiError(404, "not_found", f"dataset {req.dataset} not found")
    dataset = load_dataset(path)
    config = EvalConfig(
        label=req.label,
        top_k=req.top_k or settings.top_k,
        min_score=settings.min_score if req.min_score is None else req.min_score,
    )
    with sessions() as session:
        # ponytail: check-then-insert, so two simultaneous requests can both
        # start; one process and one user make that rare. A DB lock fixes it.
        if (running := active_run(session)) is not None:
            raise ApiError(
                409, "evaluation_running", f"run {running.id} is still {running.status}"
            )
        run = create_run(session, dataset, config, chat, embedder, settings)
    background.add_task(
        execute_run, run.id, dataset, config, store, embedder, chat, settings, sessions
    )
    return RunCreated(id=str(run.id), status=run.status)


@router.get("")
def list_runs(
    sessions: Sessions, limit: Annotated[int, Query(ge=1, le=200)] = 50
) -> dict[str, list[dict[str, Any]]]:
    """Newest first, without per-example results."""
    with sessions() as session:
        runs = session.scalars(
            select(EvaluationRun).order_by(EvaluationRun.created_at.desc()).limit(limit)
        ).all()
        return {"runs": [run_to_dict(r) for r in runs]}


@router.get("/{run_id}")
def get_run(
    run_id: uuid.UUID, sessions: Sessions, settings: SettingsDep
) -> dict[str, Any]:
    """The run, its metrics, the quality gate and every example's result."""
    with sessions() as session:
        run = _get_run(session, run_id)
        gate = _gate(run, settings)
        return run_to_dict(run) | {
            "gate": gate.model_dump() if gate else None,
            "results": [result_to_dict(r) for r in run.results],
        }


@router.get("/{run_id}/report")
def get_report(
    run_id: uuid.UUID,
    sessions: Sessions,
    settings: SettingsDep,
    format: Literal["md", "csv"] = "md",
    baseline: uuid.UUID | None = None,
) -> PlainTextResponse:
    """Markdown report (optionally compared with a baseline run) or results CSV."""
    with sessions() as session:
        run = _get_run(session, run_id)
        results = [result_to_dict(r) for r in run.results]
        if format == "csv":
            return PlainTextResponse(
                results_csv(results),
                media_type="text/csv",
                headers={"Content-Disposition": f'attachment; filename="{run.id}.csv"'},
            )
        base = run_to_dict(_get_run(session, baseline)) if baseline else None
        text = markdown_report(run_to_dict(run), results, _gate(run, settings), base)
        return PlainTextResponse(text, media_type="text/markdown")
