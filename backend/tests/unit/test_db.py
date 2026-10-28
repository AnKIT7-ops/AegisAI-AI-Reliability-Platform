import uuid
from pathlib import Path

import pytest
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from sqlalchemy import create_engine, inspect, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from alembic import command
from app.db.models import Base, EvaluationResult, EvaluationRun
from app.db.session import database_url

ALEMBIC_INI = Path(__file__).parents[2] / "alembic.ini"


def run(**kw: object) -> EvaluationRun:
    fields: dict[str, object] = dict(
        label="baseline",
        dataset_version="v1",
        dataset_checksum="0123456789abcdef",
        prompt_version="1",
        chat_model="c",
        embed_model="e",
        top_k=4,
        min_score=0.7,
        config={"top_k": 4},
    )
    return EvaluationRun(**(fields | kw))


def result(example_id: str = "q1", **kw: object) -> EvaluationResult:
    fields: dict[str, object] = dict(
        example_id=example_id,
        category="answerable",
        question="q",
        retrieved=["a.md"],
        sources=["a.md"],
        cited=[1],
        scores={"recall": 1.0},
    )
    return EvaluationResult(**(fields | kw))


@pytest.fixture
def db_url(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> str:
    url = f"sqlite:///{tmp_path / 'eval.db'}"
    monkeypatch.setenv("DATABASE_URL", url)
    return url


@pytest.fixture
def migrated(db_url: str) -> str:
    command.upgrade(Config(ALEMBIC_INI), "head")
    return db_url


def test_postgres_url_uses_psycopg3(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATABASE_URL", "postgresql://u:p@db:5432/x")
    url = database_url()
    assert (
        url.render_as_string(hide_password=False)
        == "postgresql+psycopg://u:p@db:5432/x"
    )
    assert "p@" not in str(url)  # the password stays masked if logged


def test_missing_database_url_is_a_clear_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("DATABASE_URL", raising=False)
    with pytest.raises(RuntimeError, match="DATABASE_URL"):
        database_url()


def test_migrations_match_the_models(migrated: str) -> None:
    engine = create_engine(migrated)
    with engine.connect() as conn:
        assert compare_metadata(MigrationContext.configure(conn), Base.metadata) == []
    engine.dispose()


def test_downgrade_removes_the_tables(migrated: str) -> None:
    command.downgrade(Config(ALEMBIC_INI), "base")
    engine = create_engine(migrated)
    assert set(inspect(engine).get_table_names()) == {"alembic_version"}
    engine.dispose()


def test_run_with_results_round_trips(migrated: str) -> None:
    engine = create_engine(migrated)
    with Session(engine) as s:
        r = run()
        r.results = [result("q1"), result("q2", category="injection")]
        s.add(r)
        s.commit()
        run_id = r.id

    with Session(engine) as s:
        loaded = s.get(EvaluationRun, run_id)
        assert loaded is not None
        assert isinstance(loaded.id, uuid.UUID)
        assert loaded.status == "pending"
        assert loaded.created_at is not None
        assert [x.example_id for x in loaded.results] == ["q1", "q2"]
        assert loaded.results[0].scores == {"recall": 1.0}

        s.delete(loaded)
        s.commit()
        assert s.scalars(select(EvaluationResult)).all() == []
    engine.dispose()


@pytest.mark.parametrize(
    "bad",
    [
        lambda: run(status="finished"),
        lambda: run(results=[result(category="other")]),
        lambda: run(results=[result("q1"), result("q1")]),
    ],
    ids=["status", "category", "duplicate-example"],
)
def test_constraints_reject_bad_rows(migrated: str, bad: object) -> None:
    engine = create_engine(migrated)
    with Session(engine) as s:
        s.add(bad())  # type: ignore[operator]
        with pytest.raises(IntegrityError):
            s.commit()
    engine.dispose()


def test_postgres_migrations_set_timeouts(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATABASE_URL", "postgresql://u:p@db:5432/x")
    seen: dict[str, object] = {}

    def fake_create_engine(url: object, **kw: object) -> None:
        seen.update(kw)
        raise RuntimeError("stop")

    monkeypatch.setattr("sqlalchemy.create_engine", fake_create_engine)
    with pytest.raises(RuntimeError, match="stop"):
        command.upgrade(Config(ALEMBIC_INI), "head")
    args = seen["connect_args"]
    assert isinstance(args, dict)
    assert args["connect_timeout"] == 5
    assert "lock_timeout" in args["options"]
    assert "statement_timeout" in args["options"]


def test_artifact_path_round_trips(migrated: str) -> None:
    engine = create_engine(migrated)
    with Session(engine) as s:
        r = run(artifact_path="runs/x.json")
        s.add(r)
        s.commit()
        assert s.get(EvaluationRun, r.id).artifact_path == "runs/x.json"  # type: ignore[union-attr]
        s.add(run(label="none"))
        s.commit()
        assert (
            s.scalars(select(EvaluationRun).where(EvaluationRun.label == "none"))
            .one()
            .artifact_path
            is None
        )
    engine.dispose()
