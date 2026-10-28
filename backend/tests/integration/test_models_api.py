import httpx
from fastapi.testclient import TestClient

from app.api.deps import get_ollama
from app.core.config import Settings, get_settings
from app.main import app
from app.services.llm.ollama import OllamaClient


def use_ollama(settings: Settings, handler: object) -> None:
    ollama_settings = settings.model_copy(update={"llm_provider": "ollama"})
    app.dependency_overrides[get_settings] = lambda: ollama_settings
    transport = httpx.MockTransport(handler)  # type: ignore[arg-type]
    app.dependency_overrides[get_ollama] = lambda: OllamaClient(
        "http://ollama", 1, 1, transport
    )


def test_fake_provider_reports_ready(client: TestClient) -> None:
    body = client.get("/api/v1/models").json()
    assert body["provider"] == "fake"
    assert body["reachable"] is True
    assert body["chat_model"]["installed"] is True


def test_ollama_with_models_installed(client: TestClient, settings: Settings) -> None:
    names = [{"name": "qwen3.5:4b"}, {"name": "nomic-embed-text:latest"}]
    use_ollama(settings, lambda _: httpx.Response(200, json={"models": names}))
    body = client.get("/api/v1/models").json()
    assert body == {
        "provider": "ollama",
        "reachable": True,
        "chat_model": {"name": "qwen3.5:4b", "installed": True},
        "embed_model": {"name": "nomic-embed-text", "installed": True},
    }


def test_ollama_missing_embed_model(client: TestClient, settings: Settings) -> None:
    names = [{"name": "qwen3.5:4b"}]
    use_ollama(settings, lambda _: httpx.Response(200, json={"models": names}))
    body = client.get("/api/v1/models").json()
    assert body["embed_model"]["installed"] is False


def test_ollama_down_is_reported_not_raised(
    client: TestClient, settings: Settings
) -> None:
    def down(_: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused")

    use_ollama(settings, down)
    r = client.get("/api/v1/models")
    assert r.status_code == 200
    assert r.json()["reachable"] is False
    assert r.json()["chat_model"]["installed"] is False
