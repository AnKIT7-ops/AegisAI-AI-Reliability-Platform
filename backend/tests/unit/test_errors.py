from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.core.errors import install_error_handlers
from app.services.rag.store import IndexMismatch


def test_index_mismatch_response_hides_the_server_path() -> None:
    app = FastAPI()
    install_error_handlers(app)

    @app.get("/boom")
    def boom() -> None:
        raise IndexMismatch("delete C:/secret/data/index and re-ingest")

    r = TestClient(app).get("/boom")
    assert r.status_code == 500
    assert r.json()["error"]["code"] == "index_mismatch"
    assert "secret" not in r.text


def test_unexpected_errors_return_json_without_internals() -> None:
    app = FastAPI()
    install_error_handlers(app)

    @app.get("/boom")
    def boom() -> None:
        raise ValueError("secret detail at C:/secret")

    r = TestClient(app, raise_server_exceptions=False).get("/boom")
    assert r.status_code == 500
    assert r.json() == {
        "error": {"code": "internal_error", "message": "internal server error"}
    }


def test_index_mismatch_tells_the_client_what_to_do() -> None:
    app = FastAPI()
    install_error_handlers(app)

    @app.get("/boom")
    def boom() -> None:
        raise IndexMismatch("x")

    r = TestClient(app).get("/boom")
    assert "delete the document index" in r.json()["error"]["message"]
