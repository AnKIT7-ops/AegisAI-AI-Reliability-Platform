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
