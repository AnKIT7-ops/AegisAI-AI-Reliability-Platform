"""Log events not covered in test_logging_api: evaluation runs, duplicates, 500s."""

import io
import json

from fastapi.testclient import TestClient

from app.api.deps import get_chat_model
from app.main import app
from tests.integration.test_logging_api import SECRET_DOC, lines


def test_evaluation_run_logs_start_and_finish_with_the_request_id(
    api: TestClient,
    logs: io.StringIO,
) -> None:
    r = api.post("/api/v1/evaluations/run", json={"label": "base"})
    rid = r.headers["x-request-id"]
    evals = [x for x in lines(logs) if x.get("event") == "evaluation"]
    messages = [x["message"] for x in evals]
    assert messages == ["evaluation started", "evaluation finished"]
    assert all(x["request_id"] == rid for x in evals)
    assert evals[0]["label"] == "base" and evals[0]["examples"] == 3
    assert evals[1]["errors"] == 0
    assert evals[0]["run_id"] == evals[1]["run_id"] == r.json()["id"]


def test_duplicate_ingest_logs_new_document_false(
    client: TestClient,
    logs: io.StringIO,
) -> None:
    for _ in range(2):
        client.post(
            "/api/v1/documents/ingest",
            files={"file": ("p.md", SECRET_DOC, "text/markdown")},
        )
    ingests = [x for x in lines(logs) if x.get("event") == "ingest"]
    assert [x["new_document"] for x in ingests] == [True, False]
    assert ingests[0]["document_id"] == ingests[1]["document_id"]


def test_unexpected_errors_log_500_and_keep_the_secret_out(
    client: TestClient,
    logs: io.StringIO,
) -> None:
    class Broken:
        model = "broken"

        def generate(self, system: str, prompt: str) -> None:
            raise RuntimeError("boom postgresql://u:hunter2@db/x")

    client.post(
        "/api/v1/documents/ingest",
        files={"file": ("p.md", SECRET_DOC, "text/markdown")},
    )
    app.dependency_overrides[get_chat_model] = Broken
    quiet = TestClient(app, base_url="http://localhost", raise_server_exceptions=False)
    r = quiet.post("/api/v1/chat", json={"question": "readiness probe traffic"})
    assert r.status_code == 500
    assert r.json()["error"]["code"] == "internal_error"
    rid = r.headers["x-request-id"]  # the client can quote it in a bug report
    access = [x for x in lines(logs) if x.get("event") == "request"][-1]
    assert access["status"] == 500
    error = [x for x in lines(logs) if x.get("event") == "error"][-1]
    assert error["request_id"] == rid
    assert "RuntimeError" in error["exception"]
    assert "hunter2" not in json.dumps(lines(logs))
