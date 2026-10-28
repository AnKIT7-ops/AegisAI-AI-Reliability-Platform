"""What the API writes to its JSON log, captured at INFO as it would run."""

import io
import json
import logging
from typing import Any

from fastapi.testclient import TestClient

from app.api.deps import get_chat_model
from app.main import app
from app.services.llm.base import Generation, ModelUnavailable

SECRET_QUESTION = "what does the readiness probe zebra-unicorn-42 do"
SECRET_DOC = b"# Probes\n\nA readiness probe gates traffic. Codeword: aardvark-77.\n"


def lines(buf: io.StringIO) -> list[dict[str, Any]]:
    return [json.loads(x) for x in buf.getvalue().splitlines()]


def last_access(buf: io.StringIO) -> dict[str, Any]:
    # TestClient's httpx client logs its own line after ours, so select by event.
    return [x for x in lines(buf) if x.get("event") == "request"][-1]


def test_each_request_gets_an_id_and_an_access_line(
    client: TestClient, logs: io.StringIO
) -> None:
    r = client.get("/api/v1/documents?secret=query-value")
    rid = r.headers["x-request-id"]
    assert len(rid) == 32
    access = [x for x in lines(logs) if x.get("event") == "request"]
    assert access[-1]["request_id"] == rid
    assert access[-1]["method"] == "GET"
    assert access[-1]["path"] == "/api/v1/documents"
    assert access[-1]["status"] == 200
    assert access[-1]["duration_ms"] >= 0
    assert "query-value" not in json.dumps(lines(logs))


def test_a_callers_request_id_is_kept(client: TestClient, logs: io.StringIO) -> None:
    r = client.get("/api/v1/documents", headers={"x-request-id": "trace-abc.1"})
    assert r.headers["x-request-id"] == "trace-abc.1"
    assert last_access(logs)["request_id"] == "trace-abc.1"


def test_rejected_requests_are_logged_too(
    client: TestClient, logs: io.StringIO
) -> None:
    r = client.get("/api/v1/documents", headers={"host": "evil.example"})
    assert r.status_code == 400
    assert r.headers["x-request-id"]
    assert last_access(logs)["status"] == 400


def test_chat_logs_counts_never_text(client: TestClient, logs: io.StringIO) -> None:
    client.post(
        "/api/v1/documents/ingest",
        files={"file": ("p.md", SECRET_DOC, "text/markdown")},
    )
    r = client.post("/api/v1/chat", json={"question": SECRET_QUESTION})
    assert r.status_code == 200
    chat = [x for x in lines(logs) if x.get("event") == "chat"][-1]
    assert chat["question_chars"] == len(SECRET_QUESTION)
    assert chat["model"] == "fake-chat"
    assert chat["sources"] >= 1 and chat["refused"] is False
    assert chat["prompt_tokens"] and chat["completion_tokens"]
    assert chat["request_id"] == r.headers["x-request-id"]
    text = json.dumps(lines(logs))
    assert "zebra-unicorn-42" not in text
    assert "aardvark-77" not in text  # neither the document nor the answer


def test_ingest_logs_ids_not_names_or_content(
    client: TestClient, logs: io.StringIO
) -> None:
    client.post(
        "/api/v1/documents/ingest",
        files={"file": ("private-name.md", SECRET_DOC, "text/markdown")},
    )
    ingest = [x for x in lines(logs) if x.get("event") == "ingest"][-1]
    assert ingest["new_document"] is True
    assert ingest["chunks"] >= 1 and ingest["size_bytes"] == len(SECRET_DOC)
    text = json.dumps(lines(logs))
    assert "private-name" not in text and "aardvark-77" not in text


def test_model_errors_are_logged_with_a_code(
    client: TestClient, logs: io.StringIO
) -> None:
    class Down:
        model = "down"

        def generate(self, system: str, prompt: str) -> Generation:
            raise ModelUnavailable("Ollama timed out")

    client.post(
        "/api/v1/documents/ingest",
        files={"file": ("p.md", SECRET_DOC, "text/markdown")},
    )
    app.dependency_overrides[get_chat_model] = Down
    r = client.post("/api/v1/chat", json={"question": "readiness probe traffic"})
    assert r.status_code == 503
    error = [x for x in lines(logs) if x.get("event") == "model_error"][-1]
    assert error["error_code"] == "model_unavailable"
    assert error["level"] == "warning"
    assert last_access(logs)["status"] == 503


def test_health_checks_log_at_debug(client: TestClient, logs: io.StringIO) -> None:
    client.get("/health/live")
    assert not [x for x in lines(logs) if x.get("path") == "/health/live"]
    logging.getLogger("aegis.access").setLevel(logging.DEBUG)
    try:
        client.get("/health/live")
    finally:
        logging.getLogger("aegis.access").setLevel(logging.NOTSET)
    assert last_access(logs)["path"] == "/health/live"
