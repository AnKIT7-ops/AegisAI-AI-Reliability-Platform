"""Evaluation runs as background jobs, persisted in the database."""

import json
import logging
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import Settings
from app.db.models import EvaluationResult, EvaluationRun
from app.services.evaluation.dataset import Dataset
from app.services.evaluation.report import (
    check_gate,
    load_thresholds,
    markdown_report,
    results_csv,
)
from app.services.evaluation.runner import EvalConfig, run_evaluation
from app.services.llm.base import ChatModel, Embedder
from app.services.rag.answer import PROMPT_VERSION
from app.services.rag.store import VectorStore

log = logging.getLogger("aegis")
ACTIVE = ("pending", "running")
# Settings that change what a run measures; saved with every run.
SNAPSHOT_KEYS = (
    "llm_provider",
    "chat_model",
    "embed_model",
    "embed_document_prefix",
    "embed_query_prefix",
    "chunk_size",
    "chunk_overlap",
    "generate_timeout_s",
)


def active_run(session: Session) -> EvaluationRun | None:
    return session.scalars(
        select(EvaluationRun).where(EvaluationRun.status.in_(ACTIVE)).limit(1)
    ).first()


def create_run(
    session: Session,
    dataset: Dataset,
    config: EvalConfig,
    chat: ChatModel,
    embedder: Embedder,
    settings: Settings,
) -> EvaluationRun:
    snapshot = {k: getattr(settings, k) for k in SNAPSHOT_KEYS}
    run = EvaluationRun(
        label=config.label,
        dataset_version=dataset.version,
        dataset_checksum=dataset.checksum,
        prompt_version=PROMPT_VERSION,
        chat_model=chat.model,
        embed_model=embedder.model,
        top_k=config.top_k,
        min_score=config.min_score,
        config=snapshot | {"examples": len(dataset.examples)},
    )
    session.add(run)
    session.commit()
    return run


def mark_interrupted(session: Session) -> int:
    """Runs left active by a previous process can never finish; fail them."""
    result = session.execute(
        update(EvaluationRun)
        .where(EvaluationRun.status.in_(ACTIVE))
        .values(
            status="failed",
            error="interrupted: the server restarted during the run",
            finished_at=datetime.now(UTC),
        )
    )
    session.commit()
    return result.rowcount  # type: ignore[attr-defined, no-any-return]


def run_to_dict(run: EvaluationRun) -> dict[str, Any]:
    return {
        "id": str(run.id),
        "label": run.label,
        "status": run.status,
        "dataset_version": run.dataset_version,
        "dataset_checksum": run.dataset_checksum,
        "prompt_version": run.prompt_version,
        "chat_model": run.chat_model,
        "embed_model": run.embed_model,
        "top_k": run.top_k,
        "min_score": run.min_score,
        "config": run.config,
        "metrics": run.metrics,
        "artifact_path": run.artifact_path,
        "error": run.error,
        "created_at": run.created_at.isoformat() if run.created_at else None,
        "started_at": run.started_at.isoformat() if run.started_at else None,
        "finished_at": run.finished_at.isoformat() if run.finished_at else None,
    }


def result_to_dict(r: EvaluationResult) -> dict[str, Any]:
    return {
        "example_id": r.example_id,
        "category": r.category,
        "question": r.question,
        "answer": r.answer,
        "retrieved": r.retrieved,
        "sources": r.sources,
        "cited": r.cited,
        "scores": r.scores,
        "latency_ms": r.latency_ms,
        "prompt_tokens": r.prompt_tokens,
        "completion_tokens": r.completion_tokens,
        "error": r.error,
    }


def _write_artifacts(
    run: EvaluationRun, results: list[dict[str, Any]], settings: Settings
) -> Path:
    out = settings.eval_runs_dir / str(run.id)
    out.mkdir(parents=True, exist_ok=True)
    detail = run_to_dict(run)
    thresholds = load_thresholds(settings.eval_dir, run.dataset_version)
    gate = (
        check_gate(run.metrics or {}, thresholds, run.dataset_checksum)
        if thresholds
        else None
    )
    (out / "report.json").write_text(
        json.dumps(detail | {"results": results}, indent=1), encoding="utf-8"
    )
    (out / "results.csv").write_text(results_csv(results), encoding="utf-8")
    (out / "report.md").write_text(
        markdown_report(detail, results, gate), encoding="utf-8"
    )
    return out


def execute_run(
    run_id: uuid.UUID,
    dataset: Dataset,
    config: EvalConfig,
    store: VectorStore,
    embedder: Embedder,
    chat: ChatModel,
    settings: Settings,
    sessions: sessionmaker[Session],
) -> None:
    """Background job: never raises, always leaves the run done or failed."""
    with sessions() as session:
        run = session.get(EvaluationRun, run_id)
        if run is None:
            return
        try:
            run.status, run.started_at = "running", datetime.now(UTC)
            session.commit()
            report = run_evaluation(dataset, config, store, embedder, chat, settings)
            run.results = [EvaluationResult(**r.model_dump()) for r in report.results]
            run.metrics = report.metrics
            run.status, run.finished_at = "done", datetime.now(UTC)
            session.commit()
            results = [result_to_dict(r) for r in run.results]
            try:
                run.artifact_path = str(_write_artifacts(run, results, settings))
                session.commit()
            except (OSError, ValueError):  # ValueError: malformed thresholds file
                # The run itself succeeded; only the file export did not.
                session.rollback()
                log.exception("evaluation %s: writing artifacts failed", run_id)
        except Exception:
            log.exception("evaluation %s failed", run_id)
            try:
                session.rollback()
                run.status, run.finished_at = "failed", datetime.now(UTC)
                run.error = "the run failed; see the server log"
                session.commit()
            except Exception:
                # Database gone too: the next start marks the run interrupted.
                log.exception("evaluation %s: could not record the failure", run_id)
