import logging
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import sessionmaker

from app import main
from app.core.errors import install_error_handlers
from app.db.models import Base, EvaluationRun


def test_operational_error_is_503_without_leaking_the_dsn() -> None:
    app = FastAPI()
    install_error_handlers(app)

    @app.get("/x")
    def x() -> None:
        raise OperationalError("SELECT 1", {}, Exception("postgresql://u:pw@host/db"))

    r = TestClient(app, raise_server_exceptions=False).get("/x")
    assert r.status_code == 503
    assert r.json() == {
        "error": {"code": "database_unavailable", "message": "database is unavailable"}
    }
    assert "pw" not in r.text


def _sessions(tmp_path: Path) -> sessionmaker:  # type: ignore[type-arg]
    engine = create_engine(f"sqlite:///{tmp_path / 'l.db'}")
    Base.metadata.create_all(engine)
    return sessionmaker(engine, expire_on_commit=False)


def _run(label: str, status: str) -> EvaluationRun:
    return EvaluationRun(
        label=label, status=status, dataset_version="v1", dataset_checksum="c",
        prompt_version="p", chat_model="m", embed_model="e", top_k=1,
        min_score=0.0, config={},
    )  # fmt: skip


def test_lifespan_fails_interrupted_runs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    sm = _sessions(tmp_path)
    with sm() as s:
        s.add_all([_run("a", "running"), _run("b", "done")])
        s.commit()
    monkeypatch.setenv("DATABASE_URL", "sqlite://")
    monkeypatch.setattr(main, "get_sessionmaker", lambda: sm)
    with TestClient(main.app):
        pass
    with sm() as s:
        status = {r.label: r.status for r in s.query(EvaluationRun)}
    assert status == {"a": "failed", "b": "done"}


def test_lifespan_survives_a_broken_database(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    def boom() -> None:
        raise RuntimeError("down")

    monkeypatch.setenv("DATABASE_URL", "sqlite://")
    monkeypatch.setattr(main, "get_sessionmaker", boom)
    with (
        caplog.at_level(logging.WARNING, logger="aegis"),
        TestClient(main.app, base_url="http://localhost") as c,
    ):
        assert c.get("/health/live").status_code == 200
    assert "could not check for interrupted" in caplog.text


def test_lifespan_skips_without_database_url(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def boom() -> None:
        raise AssertionError("must not be called")

    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setattr(main, "get_sessionmaker", boom)
    with TestClient(main.app):
        pass


@pytest.mark.parametrize(
    ("fields", "warned"),
    [({}, False), ({"fault_delay_ms": 500}, True), ({"fault_error_rate": 0.2}, True)],
)
def test_startup_warns_only_when_fault_injection_is_on(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    fields: dict[str, float],
    warned: bool,
) -> None:
    from app.core.config import Settings

    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setattr(main, "get_settings", lambda: Settings(**fields))  # type: ignore[arg-type]
    with caplog.at_level(logging.WARNING, logger="aegis"):
        with TestClient(main.app):
            pass
    assert any("fault injection is ON" in r.message for r in caplog.records) is warned
