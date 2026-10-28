"""Fixtures shared by the integration tests."""

import io
import json
import logging
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.api.deps import get_sessionmaker
from app.core.config import Settings
from app.core.logs import JsonFormatter
from app.db.models import Base
from app.main import app
from tests.integration.evaluation_data import DOC, EXAMPLES


@pytest.fixture
def sessions(tmp_path: Path) -> sessionmaker[Session]:
    engine = create_engine(
        f"sqlite:///{tmp_path / 'eval.db'}",
        connect_args={"check_same_thread": False},  # background tasks use a thread
    )
    Base.metadata.create_all(engine)
    return sessionmaker(engine, expire_on_commit=False)


@pytest.fixture
def api(
    client: TestClient,
    settings: Settings,
    sessions: sessionmaker[Session],
    tmp_path: Path,
) -> Iterator[TestClient]:
    settings.eval_dir = tmp_path / "evaluation"
    settings.eval_runs_dir = tmp_path / "runs"
    settings.eval_dir.mkdir()
    (settings.eval_dir / "v1.jsonl").write_text(
        "\n".join(json.dumps(e) for e in EXAMPLES), encoding="utf-8"
    )
    app.dependency_overrides[get_sessionmaker] = lambda: sessions
    r = client.post(
        "/api/v1/documents/ingest", files={"file": ("probes.md", DOC, "text/markdown")}
    )
    assert r.status_code == 201
    yield client


@pytest.fixture
def logs() -> Iterator[io.StringIO]:
    """JSON lines formatted at emit time, like the real handler, at INFO."""
    buf = io.StringIO()
    handler = logging.StreamHandler(buf)
    handler.setFormatter(JsonFormatter())
    root = logging.getLogger()
    level = root.level
    root.addHandler(handler)
    root.setLevel(logging.INFO)
    yield buf
    root.removeHandler(handler)
    root.setLevel(level)
