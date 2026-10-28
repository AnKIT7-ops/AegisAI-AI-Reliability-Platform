import threading
from pathlib import Path

import pytest
from pydantic import ValidationError

from app.api import deps
from app.core.config import Settings, get_settings
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


@pytest.mark.parametrize(
    "fields",
    [{"fault_delay_ms": -1}, {"fault_delay_ms": 60_001}, {"fault_error_rate": 1.5}],
)
def test_fault_settings_are_bounded(fields: dict[str, float]) -> None:
    with pytest.raises(ValueError):
        Settings(**fields)  # type: ignore[arg-type]


def test_fault_settings_reach_the_ollama_client(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.api import deps

    monkeypatch.setenv("AEGIS_FAULT_DELAY_MS", "25")
    monkeypatch.setenv("AEGIS_FAULT_ERROR_RATE", "0.5")
    get_settings.cache_clear()
    deps.get_ollama.cache_clear()
    try:
        client = deps.get_ollama()
        assert (client._fault_delay_s, client._fault_error_rate) == (0.025, 0.5)
    finally:
        get_settings.cache_clear()
        deps.get_ollama.cache_clear()
