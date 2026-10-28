from collections.abc import Iterator

import httpx
import pytest
from fastapi.testclient import TestClient

from app.api import deps
from app.api.deps import get_ollama
from app.api.models import _installed
from app.core.config import Settings, get_settings
from app.main import app
from app.services.llm.base import FakeChat, FakeEmbedder, ModelUnavailable
from app.services.llm.ollama import OllamaChat, OllamaClient, OllamaEmbedder


def use_ollama(settings: Settings, handler: object) -> None:
    ollama_settings = settings.model_copy(update={"llm_provider": "ollama"})
    app.dependency_overrides[get_settings] = lambda: ollama_settings
    app.dependency_overrides[get_ollama] = lambda: OllamaClient(
        "http://ollama",
        1,
        1,
        httpx.MockTransport(handler),  # type: ignore[arg-type]
    )


def ollama_client(handler: object) -> OllamaClient:
    transport = httpx.MockTransport(handler)  # type: ignore[arg-type]
    return OllamaClient("http://ollama", 1, 1, transport)


def test_404_without_model_payload_is_plain_http_error() -> None:
    c = ollama_client(lambda _: httpx.Response(404))
    with pytest.raises(ModelUnavailable, match="HTTP 404"):
        c.installed_models()


def test_embed_404_names_the_missing_model() -> None:
    c = ollama_client(lambda _: httpx.Response(404, json={"error": "x"}))
    with pytest.raises(ModelUnavailable, match="'nomic' is not installed"):
        OllamaEmbedder(c, "nomic").embed(["a"])


def test_installed_models_rejects_non_list() -> None:
    c = ollama_client(lambda _: httpx.Response(200, json={"models": "x"}))
    with pytest.raises(ModelUnavailable, match="unexpected response"):
        c.installed_models()


@pytest.mark.parametrize(
    ("name", "names", "expected"),
    [
        ("m", ["m:latest"], True),
        ("m:4b", ["m:latest"], False),  # explicit tag must match exactly
        ("m:4b", ["m:4b"], True),
        ("m", ["other:latest"], False),
    ],
)
def test_installed_tag_matching(name: str, names: list[str], expected: bool) -> None:
    assert _installed(name, names) is expected


def test_models_endpoint_reports_ollama_http_error(
    client: TestClient, settings: Settings
) -> None:
    use_ollama(settings, lambda _: httpx.Response(500))
    r = client.get("/api/v1/models")
    assert r.status_code == 200
    assert r.json()["reachable"] is False


@pytest.fixture
def fresh_deps(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    for f in (deps.get_ollama, deps.get_embedder, deps.get_chat_model):
        f.cache_clear()
    yield
    for f in (deps.get_ollama, deps.get_embedder, deps.get_chat_model):
        f.cache_clear()


def test_deps_fake_provider(fresh_deps: None, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(deps, "get_settings", lambda: Settings(llm_provider="fake"))
    assert isinstance(deps.get_embedder(), FakeEmbedder)
    assert isinstance(deps.get_chat_model(), FakeChat)


def test_deps_ollama_provider_shares_one_client(
    fresh_deps: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    s = Settings(llm_provider="ollama", embed_model="e", chat_model="c")
    monkeypatch.setattr(deps, "get_settings", lambda: s)
    emb, chat = deps.get_embedder(), deps.get_chat_model()
    assert isinstance(emb, OllamaEmbedder) and emb.model == "e"
    assert isinstance(chat, OllamaChat) and chat.model == "c"
    assert emb.client is chat.client is deps.get_ollama()
