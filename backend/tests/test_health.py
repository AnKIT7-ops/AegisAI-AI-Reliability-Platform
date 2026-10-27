import psycopg
import pytest
from fastapi.testclient import TestClient

from app import main

client = TestClient(main.app)


def test_live() -> None:
    r = client.get("/health/live")
    assert r.status_code == 200
    assert r.json() == {"status": "ok"}


def test_ready_without_database_url(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DATABASE_URL", raising=False)
    r = client.get("/health/ready")
    assert r.status_code == 503
    assert r.json()["checks"]["database"] == "unavailable"


def test_ready_db_error_does_not_leak_dsn(monkeypatch: pytest.MonkeyPatch) -> None:
    dsn = "postgresql://user:hunter2@db:5432/x"
    monkeypatch.setenv("DATABASE_URL", dsn)

    def fail(*args: object, **kwargs: object) -> None:
        raise psycopg.OperationalError(f"could not connect to {dsn}")

    monkeypatch.setattr(psycopg, "connect", fail)
    r = client.get("/health/ready")
    assert r.status_code == 503
    assert "hunter2" not in r.text


def test_ready_db_ok(monkeypatch: pytest.MonkeyPatch) -> None:
    class FakeConn:
        def __enter__(self) -> "FakeConn":
            return self

        def __exit__(self, *exc: object) -> None:
            pass

        def execute(self, sql: str) -> None:
            assert sql == "SELECT 1"

    monkeypatch.setenv("DATABASE_URL", "postgresql://u:p@db/x")
    monkeypatch.setattr(psycopg, "connect", lambda *a, **k: FakeConn())
    r = client.get("/health/ready")
    assert r.status_code == 200
    assert r.json() == {"status": "ready", "checks": {"database": "ok"}}


def test_ready_passes_connect_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: dict[str, object] = {}

    def fail(url: str, **kwargs: object) -> None:
        seen.update(kwargs)
        raise psycopg.OperationalError("down")

    monkeypatch.setenv("DATABASE_URL", "postgresql://u:p@db/x")
    monkeypatch.setattr(psycopg, "connect", fail)
    assert client.get("/health/ready").status_code == 503
    assert seen["connect_timeout"] == main.DB_CONNECT_TIMEOUT_S
    assert seen["options"] == f"-c statement_timeout={main.DB_CONNECT_TIMEOUT_S * 1000}"
