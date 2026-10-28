from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.api.deps import get_chat_model, get_embedder, get_store
from app.core.config import Settings, get_settings
from app.main import app
from app.services.llm.base import FakeChat, FakeEmbedder
from app.services.rag.store import VectorStore


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings(
        llm_provider="fake",
        index_dir=tmp_path / "index",
        max_upload_bytes=10_000,
        chunk_size=200,
        chunk_overlap=20,
        min_score=0.2,
    )


@pytest.fixture
def store(settings: Settings) -> VectorStore:
    return VectorStore(settings.index_dir, FakeEmbedder.model)


@pytest.fixture
def client(settings: Settings, store: VectorStore) -> Iterator[TestClient]:
    embedder = FakeEmbedder()
    app.dependency_overrides[get_settings] = lambda: settings
    app.dependency_overrides[get_store] = lambda: store
    app.dependency_overrides[get_embedder] = lambda: embedder
    app.dependency_overrides[get_chat_model] = FakeChat
    yield TestClient(app, base_url="http://localhost")
    app.dependency_overrides.clear()
