import pytest
from fastapi.testclient import TestClient

from app.api.deps import get_chat_model
from app.main import app
from app.services.llm.base import Generation, ModelUnavailable
from app.services.rag.answer import NO_ANSWER

DOC = (
    b"# Probes\n\nA readiness probe decides whether a pod receives traffic.\n\n"
    b"A liveness probe restarts a container that stops responding.\n"
)


@pytest.fixture
def ingested(client: TestClient) -> TestClient:
    r = client.post(
        "/api/v1/documents/ingest", files={"file": ("probes.md", DOC, "text/markdown")}
    )
    assert r.status_code == 201
    return client


def test_answer_with_sources(ingested: TestClient) -> None:
    r = ingested.post(
        "/api/v1/chat", json={"question": "what does a readiness probe do"}
    )
    assert r.status_code == 200
    body = r.json()
    assert body["model"] == "fake-chat"
    assert body["answer"].startswith("[1] ")
    assert isinstance(body["latency_ms"], int)
    src = body["sources"][0]
    assert src["filename"] == "probes.md"
    assert src["n"] == 1
    assert 0 < src["score"] <= 1
    assert "readiness probe" in src["snippet"]


def test_out_of_scope_question_is_refused(ingested: TestClient) -> None:
    r = ingested.post("/api/v1/chat", json={"question": "best sourdough recipe"})
    assert r.status_code == 200
    assert r.json() == {
        "answer": NO_ANSWER,
        "sources": [],
        "model": None,
        "latency_ms": r.json()["latency_ms"],
        "usage": None,
    }


MANY = "\n\n".join(
    f"Probe note {i}: a probe checks container health and probe results gate traffic."
    + " probe health" * 8
    for i in range(6)
).encode()


@pytest.fixture
def many_chunks(client: TestClient) -> TestClient:
    r = client.post(
        "/api/v1/documents/ingest", files={"file": ("many.md", MANY, "text/markdown")}
    )
    assert r.json()["document"]["chunks"] >= 4
    return client


def test_top_k_limits_sources(many_chunks: TestClient) -> None:
    r = many_chunks.post("/api/v1/chat", json={"question": "probe", "top_k": 2})
    assert len(r.json()["sources"]) == 2


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"question": ""},
        {"question": "   "},
        {"question": "x" * 2001},
        {"question": "ok", "top_k": 0},
        {"question": "ok", "top_k": 11},
        {"question": "ok", "extra": 1},
        {"question": 5},
    ],
)
def test_invalid_requests(client: TestClient, payload: dict[str, object]) -> None:
    r = client.post("/api/v1/chat", json=payload)
    assert r.status_code == 422
    assert r.json()["error"]["code"] == "invalid_request"
    assert "x" * 50 not in r.text  # rejected input is not echoed


def test_model_down_is_503(ingested: TestClient) -> None:
    class Down:
        model = "down"

        def generate(self, system: str, prompt: str) -> Generation:
            raise ModelUnavailable("Ollama timed out")

    app.dependency_overrides[get_chat_model] = Down
    r = ingested.post("/api/v1/chat", json={"question": "readiness probe traffic"})
    assert r.status_code == 503
    assert r.json() == {
        "error": {"code": "model_unavailable", "message": "Ollama timed out"}
    }


def test_default_top_k_comes_from_settings(many_chunks: TestClient) -> None:
    from app.core.config import get_settings

    s = app.dependency_overrides[get_settings]()
    s.top_k = 3
    r = many_chunks.post("/api/v1/chat", json={"question": "probe", "top_k": None})
    assert len(r.json()["sources"]) == 3


def test_embedder_down_is_503(ingested: TestClient) -> None:
    from app.api.deps import get_embedder

    class Down:
        model = "down"

        def embed(self, texts: list[str]) -> list[list[float]]:
            raise ModelUnavailable("Ollama is not reachable")

    app.dependency_overrides[get_embedder] = Down
    r = ingested.post("/api/v1/chat", json={"question": "readiness probe"})
    assert r.status_code == 503
    assert r.json()["error"]["code"] == "model_unavailable"
