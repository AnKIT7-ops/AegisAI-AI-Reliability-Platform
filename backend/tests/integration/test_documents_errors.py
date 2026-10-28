import httpx
from fastapi.testclient import TestClient

from app.api.deps import get_embedder
from app.core.config import Settings
from app.main import app
from app.services.llm.base import FakeEmbedder, ModelUnavailable
from app.services.rag.store import VectorStore


def post(client: TestClient, name: str, data: bytes) -> httpx.Response:
    r: httpx.Response = client.post(
        "/api/v1/documents/ingest", files={"file": (name, data, "text/plain")}
    )
    return r


class DownEmbedder:
    model = "fake-embed"

    def embed(self, texts: list[str]) -> list[list[float]]:
        raise ModelUnavailable("ollama is down")


def test_model_unavailable_is_503_and_nothing_stored(
    client: TestClient, store: VectorStore
) -> None:
    app.dependency_overrides[get_embedder] = lambda: DownEmbedder()
    r = post(client, "a.md", b"some text")
    assert r.status_code == 503
    assert r.json()["error"] == {
        "code": "model_unavailable",
        "message": "ollama is down",
    }
    assert store.documents() == []


def test_dimension_mismatch_is_500_index_mismatch(
    client: TestClient, settings: Settings
) -> None:
    assert post(client, "a.md", b"first doc").status_code == 201
    app.dependency_overrides[get_embedder] = lambda: FakeEmbedder(dim=8)
    r = post(client, "b.md", b"second doc")
    assert r.status_code == 500
    assert r.json()["error"]["code"] == "index_mismatch"
    assert str(settings.index_dir.parent) not in r.text  # no server paths


def test_upload_size_boundary(client: TestClient, settings: Settings) -> None:
    limit = settings.max_upload_bytes
    assert post(client, "ok.txt", b"a" * limit).status_code == 201
    assert post(client, "big.txt", b"a" * (limit + 1)).status_code == 413


def test_bom_is_stripped_and_long_name_rejected(client: TestClient) -> None:
    assert post(client, "bom.txt", b"\xef\xbb\xbfhello world").status_code == 201
    r = post(client, "x" * 253 + ".md", b"hello")
    assert (r.status_code, r.json()["error"]["code"]) == (415, "unsupported_type")


def test_document_prefix_is_applied_to_embedded_chunks(
    client: TestClient, settings: Settings
) -> None:
    seen: list[str] = []

    class Spy(FakeEmbedder):
        def embed(self, texts: list[str]) -> list[list[float]]:
            seen.extend(texts)
            return super().embed(texts)

    app.dependency_overrides[get_embedder] = lambda: Spy()
    post(client, "p.md", b"pods and probes")
    assert seen == [settings.embed_document_prefix + "pods and probes"]
