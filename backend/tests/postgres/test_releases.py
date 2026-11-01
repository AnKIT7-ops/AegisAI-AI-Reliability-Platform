"""What only PostgreSQL enforces: triggers, foreign keys and the real service flow.

Needs DATABASE_URL at a migrated database, so CI runs this after `alembic upgrade
head`. Each test rolls back, so nothing stays behind.
"""

import json
import os
import uuid
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.core.errors import ApiError
from app.db.models import EvaluationRun, Release, ReleaseEvent
from app.db.session import connect_args, database_url
from app.services.releases import create_release, promote, rollback, run_canary

pytestmark = pytest.mark.skipif(
    not os.environ.get("DATABASE_URL"), reason="needs a migrated PostgreSQL database"
)
GOOD = {"recall_at_k": 1.0}
BAD = {"recall_at_k": 0.5}


@pytest.fixture
def session() -> Iterator[Session]:
    url = database_url()
    engine = create_engine(url, connect_args=connect_args(url))
    with engine.connect() as connection:
        outer = connection.begin()
        # Service commits become savepoints, so the rollback below undoes them.
        with Session(
            connection,
            join_transaction_mode="create_savepoint",
            expire_on_commit=False,
        ) as s:
            yield s
        outer.rollback()
    engine.dispose()


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    (tmp_path / "v1.thresholds.json").write_text(
        json.dumps(
            {
                "dataset_version": "v1",
                "dataset_checksum": "abc",
                "basis": "test",
                "limits": {"recall_at_k": 0.9},
            }
        ),
        encoding="utf-8",
    )
    return Settings(llm_provider="fake", eval_dir=tmp_path)


def add_run(session: Session, metrics: dict[str, Any] | None = None) -> EvaluationRun:
    run = EvaluationRun(
        status="done",
        label="r",
        dataset_version="v1",
        dataset_checksum="abc",
        prompt_version="p",
        chat_model="c",
        embed_model="e",
        top_k=4,
        min_score=0.7,
        config={},
        metrics=GOOD if metrics is None else metrics,
    )
    session.add(run)
    session.flush()
    return run


def add_release(session: Session, metrics: dict[str, Any] | None = None) -> Release:
    version = uuid.uuid4().hex
    return create_release(session, version, add_run(session, metrics).id, "test")


def rejected(session: Session, settings: Settings) -> Release:
    release = add_release(session, BAD)
    return run_canary(session, release.id, None, settings, "test")


def active(session: Session, settings: Settings) -> Release:
    release = add_release(session)
    run_canary(session, release.id, None, settings, "test")
    return promote(session, release.id, None, settings, "test")


def execute(session: Session, sql: str, release: Release) -> None:
    session.execute(text(sql), {"id": release.id})


@pytest.mark.parametrize(
    ("column", "value"),
    [
        ("id", "gen_random_uuid()"),
        ("version", "'changed'"),
        ("source", "'demo'"),
        ("dataset_version", "'v9'"),
        ("dataset_checksum", "'changed'"),
        ("prompt_version", "'changed'"),
        ("chat_model", "'changed'"),
        ("embed_model", "'changed'"),
        ("top_k", "9"),
        ("min_score", "0.1"),
        ("config", """'{"x": 1}'::json"""),
        ("metrics", """'{"recall_at_k": 0.1}'::json"""),
        ("created_at", "now() - interval '1 day'"),
    ],
)
def test_snapshot_columns_cannot_change(
    session: Session, column: str, value: str
) -> None:
    release = add_release(session)
    with pytest.raises(DBAPIError, match="release snapshots cannot change"):
        execute(
            session, f"UPDATE releases SET {column} = {value} WHERE id = :id", release
        )


def test_evaluation_run_cannot_change(session: Session) -> None:
    release = add_release(session)
    other = add_run(session)
    with pytest.raises(DBAPIError, match="release snapshots cannot change"):
        session.execute(
            text("UPDATE releases SET evaluation_run_id = :run WHERE id = :id"),
            {"run": other.id, "id": release.id},
        )


def test_same_value_json_update_is_allowed(session: Session) -> None:
    # json has no equality in PostgreSQL; the trigger compares it as jsonb.
    release = add_release(session)
    execute(session, "UPDATE releases SET config = config WHERE id = :id", release)


def test_a_rejected_release_cannot_be_rewritten_as_passed(
    session: Session, settings: Settings
) -> None:
    release = rejected(session, settings)
    with pytest.raises(DBAPIError, match="a canary result cannot change"):
        execute(
            session,
            "UPDATE releases SET gate_passed = true, status = 'active' WHERE id = :id",
            release,
        )


@pytest.mark.parametrize(
    "assignment",
    [
        "gate_failures = '[]'::json",
        """comparison = '{"status": "changed"}'::json""",
        "baseline_release_id = id",
    ],
)
def test_canary_result_is_written_once(
    session: Session, settings: Settings, assignment: str
) -> None:
    release = rejected(session, settings)
    with pytest.raises(DBAPIError, match="a canary result cannot change"):
        execute(session, f"UPDATE releases SET {assignment} WHERE id = :id", release)


def test_success_needs_a_gate_result(session: Session) -> None:
    release = add_release(session)
    with pytest.raises(IntegrityError, match="promotion_rule"):
        execute(
            session, "UPDATE releases SET status = 'active' WHERE id = :id", release
        )


def test_one_active_release_per_source(session: Session, settings: Settings) -> None:
    active(session, settings)
    other = add_release(session)
    run_canary(session, other.id, None, settings, "test")
    with pytest.raises(IntegrityError, match="uq_releases_one_active"):
        execute(session, "UPDATE releases SET status = 'active' WHERE id = :id", other)


def test_events_cannot_be_updated(session: Session) -> None:
    release = add_release(session)
    with pytest.raises(DBAPIError, match="append-only"):
        execute(
            session,
            "UPDATE release_events SET actor = 'x' WHERE release_id = :id",
            release,
        )


def test_events_cannot_be_deleted(session: Session) -> None:
    release = add_release(session)
    with pytest.raises(DBAPIError, match="append-only"):
        execute(session, "DELETE FROM release_events WHERE release_id = :id", release)


def test_a_release_with_history_cannot_be_deleted(session: Session) -> None:
    release = add_release(session)
    with pytest.raises(IntegrityError, match="release_events"):
        execute(session, "DELETE FROM releases WHERE id = :id", release)


def test_a_released_run_cannot_be_deleted(session: Session) -> None:
    release = add_release(session)
    with pytest.raises(IntegrityError, match="releases"):
        session.execute(
            text("DELETE FROM evaluation_runs WHERE id = :id"),
            {"id": release.evaluation_run_id},
        )


def test_full_lifecycle(session: Session, settings: Settings) -> None:
    first = active(session, settings)
    second = add_release(session)
    run_canary(session, second.id, first.id, settings, "test")
    assert second.comparison is not None
    assert second.comparison["status"] == "comparable"
    promote(session, second.id, "better", settings, "test")
    assert (first.status, second.status) == ("superseded", "active")
    rollback(session, second.id, first.id, "regression", "test")
    assert (first.status, second.status) == ("active", "rolled_back")
    session.expire_all()
    stored = session.get(Release, second.id)
    assert stored is not None
    assert [e.event for e in stored.events] == [
        "created",
        "canary_passed",
        "promoted",
        "rolled_back",
    ]


def test_refused_promotion_keeps_its_audit_event(
    session: Session, settings: Settings
) -> None:
    release = rejected(session, settings)
    with pytest.raises(ApiError) as refused:
        promote(session, release.id, None, settings, "test")
    assert refused.value.code == "invalid_transition"
    session.expire_all()
    events = session.query(ReleaseEvent).filter_by(release_id=release.id).all()
    assert events[-1].event == "promotion_refused"
    assert session.get(Release, release.id).status == "rejected"  # type: ignore[union-attr]
