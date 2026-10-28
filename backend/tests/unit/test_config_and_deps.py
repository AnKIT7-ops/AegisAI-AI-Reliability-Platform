import threading
from pathlib import Path

import pytest
from pydantic import ValidationError

from app.api import deps
from app.core.config import Settings
from app.services.llm.base import FakeEmbedder


def test_overlap_must_be_smaller_than_chunk_size() -> None:
    with pytest.raises(ValidationError, match="chunk_overlap"):
        Settings(chunk_size=100, chunk_overlap=100)


def test_store_is_built_once_under_concurrent_first_calls(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(deps, "_store", None)
    monkeypatch.setattr(
        deps, "get_settings", lambda: Settings(llm_provider="fake", index_dir=tmp_path)
    )
    monkeypatch.setattr(deps, "get_embedder", FakeEmbedder)
    seen = []
    barrier = threading.Barrier(8)

    def call() -> None:
        barrier.wait()
        seen.append(deps.get_store())

    threads = [threading.Thread(target=call) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len({id(s) for s in seen}) == 1
