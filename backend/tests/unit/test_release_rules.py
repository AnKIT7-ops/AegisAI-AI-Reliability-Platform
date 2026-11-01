"""Rules the database enforces itself, and the canary comparison."""

import uuid
from collections.abc import Iterator
from typing import Any

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.db.models import Base, EvaluationRun, Release
from app.services.evaluation.jobs import run_to_dict
from app.services.releases import compare


@pytest.fixture
def session() -> Iterator[Session]:
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine) as s:
        yield s


def run_row(session: Session, **kw: Any) -> EvaluationRun:
    fields: dict[str, Any] = dict(
        label="r",
        dataset_version="v1",
        dataset_checksum="abc",
        prompt_version="p",
        chat_model="c",
        embed_model="e",
        top_k=4,
        min_score=0.7,
        config={},
    )
    run = EvaluationRun(**(fields | kw))
    session.add(run)
    session.flush()
    return run


def release(session: Session, **kw: Any) -> Release:
    fields: dict[str, Any] = dict(
        version=uuid.uuid4().hex,
        source="live",
        status="candidate",
        evaluation_run_id=run_row(session).id,
        dataset_version="v1",
        dataset_checksum="abc",
        prompt_version="p",
        chat_model="c",
        embed_model="e",
        top_k=4,
        min_score=0.7,
        config={},
        metrics={},
    )
    row = Release(**(fields | kw))
    session.add(row)
    session.flush()
    return row


SUCCESS = ["canary_passed", "active", "superseded", "rolled_back"]


@pytest.mark.parametrize("status", SUCCESS)
@pytest.mark.parametrize("gate", [None, False])
def test_constraint_refuses_success_without_a_gate(
    session: Session, status: str, gate: bool | None
) -> None:
    with pytest.raises(IntegrityError, match="promotion_rule"):
        release(session, status=status, gate_passed=gate)


@pytest.mark.parametrize("gate", [None, True])
def test_constraint_refuses_rejected_without_a_failed_gate(
    session: Session, gate: bool | None
) -> None:
    with pytest.raises(IntegrityError, match="promotion_rule"):
        release(session, status="rejected", gate_passed=gate)


@pytest.mark.parametrize("gate", [True, False])
def test_constraint_refuses_candidate_with_a_gate_result(
    session: Session, gate: bool
) -> None:
    with pytest.raises(IntegrityError, match="promotion_rule"):
        release(session, status="candidate", gate_passed=gate)


@pytest.mark.parametrize(
    ("status", "gate"),
    [("candidate", None), ("rejected", False)] + [(s, True) for s in SUCCESS],
)
def test_constraint_allows_consistent_rows(
    session: Session, status: str, gate: bool | None
) -> None:
    release(session, status=status, gate_passed=gate)


def test_direct_sql_cannot_mark_a_rejected_release_active(session: Session) -> None:
    row = release(session, status="rejected", gate_passed=False)
    session.commit()
    with pytest.raises(IntegrityError, match="promotion_rule"):
        session.execute(
            text("UPDATE releases SET status = 'active' WHERE id = :id"),
            {"id": row.id.hex},
        )


def test_one_active_release_per_source(session: Session) -> None:
    release(session, status="active", gate_passed=True)
    release(session, status="active", gate_passed=True, source="demo")
    with pytest.raises(IntegrityError):
        release(session, status="active", gate_passed=True)


def test_version_is_unique_within_a_source(session: Session) -> None:
    release(session, version="1.0")
    release(session, version="1.0", source="demo")
    with pytest.raises(IntegrityError):
        release(session, version="1.0")


def test_unknown_status_is_refused(session: Session) -> None:
    with pytest.raises(IntegrityError, match="status"):
        release(session, status="shipped")


def test_unknown_source_is_refused(session: Session) -> None:
    with pytest.raises(IntegrityError, match="source"):
        release(session, source="staging")


def test_unknown_run_source_is_refused(session: Session) -> None:
    with pytest.raises(IntegrityError, match="source"):
        run_row(session, source="staging")


def test_runs_are_live_by_default(session: Session) -> None:
    run = run_row(session)
    assert run.source == "live"
    assert run_to_dict(run)["source"] == "live"


def unsaved(metrics: dict[str, Any], checksum: str = "abc") -> Release:
    return Release(
        id=uuid.uuid4(),
        evaluation_run_id=uuid.uuid4(),
        dataset_checksum=checksum,
        metrics=metrics,
    )


def row(comparison: dict[str, Any], name: str) -> dict[str, Any]:
    found = [m for m in comparison["metrics"] if m["name"] == name]
    assert len(found) == 1, name
    result: dict[str, Any] = found[0]
    return result


def test_no_baseline() -> None:
    assert compare(unsaved({"mrr": 1.0}), None) == {
        "status": "no_baseline",
        "baseline_release_id": None,
        "baseline_run_id": None,
        "metrics": [],
    }


def test_changed_dataset_lists_no_deltas() -> None:
    base = unsaved({"mrr": 0.5}, checksum="other")
    result = compare(unsaved({"mrr": 1.0}), base)
    assert result["status"] == "dataset_changed"
    assert result["metrics"] == []
    assert result["baseline_release_id"] == str(base.id)
    assert result["baseline_run_id"] == str(base.evaluation_run_id)


def test_higher_is_better_by_default() -> None:
    result = compare(unsaved({"mrr": 0.9}), unsaved({"mrr": 0.8}))
    assert result["status"] == "comparable"
    assert row(result, "mrr") == {
        "name": "mrr",
        "candidate": 0.9,
        "baseline": 0.8,
        "delta": 0.1,
        "direction": "improved",
    }
    worse = compare(unsaved({"mrr": 0.7}), unsaved({"mrr": 0.8}))
    assert row(worse, "mrr")["direction"] == "regressed"


def test_lower_is_better_for_latency_and_failures() -> None:
    faster = compare(
        unsaved({"latency_ms_p95": 900, "failure_rate": 0.2}),
        unsaved({"latency_ms_p95": 1000, "failure_rate": 0.1}),
    )
    assert row(faster, "latency_ms_p95")["direction"] == "improved"
    assert row(faster, "failure_rate")["direction"] == "regressed"


def test_unchanged_metric_is_same() -> None:
    result = compare(unsaved({"mrr": 0.8}), unsaved({"mrr": 0.8}))
    assert row(result, "mrr")["direction"] == "same"
    assert row(result, "mrr")["delta"] == 0


def test_counts_have_no_direction_and_are_left_out() -> None:
    metrics = {
        "examples": 3,
        "errors": 0,
        "prompt_tokens_total": 9,
        "completion_tokens_total": 9,
    }
    assert compare(unsaved(metrics), unsaved(metrics))["metrics"] == []


def test_unmeasured_metrics_are_left_out() -> None:
    result = compare(unsaved({"mrr": None, "a": 1}), unsaved({"mrr": 0.5}))
    assert result["metrics"] == []
