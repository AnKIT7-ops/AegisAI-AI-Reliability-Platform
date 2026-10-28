import json
import logging
import sys

import pytest

from app.core.logs import (
    EXTRA_FIELDS,
    JsonFormatter,
    configure_logging,
    new_request_id,
    redact,
    request_id,
)


def record(msg: str = "hello", **extra: object) -> logging.LogRecord:
    r = logging.LogRecord("aegis.test", logging.INFO, "f.py", 1, msg, None, None)
    r.__dict__.update(extra)
    return r


def test_formatter_writes_one_json_object() -> None:
    entry = json.loads(JsonFormatter().format(record("hi there")))
    assert entry["message"] == "hi there"
    assert entry["level"] == "info"
    assert entry["logger"] == "aegis.test"
    assert entry["ts"].endswith("+00:00")
    assert "request_id" not in entry


def test_formatter_adds_the_request_id() -> None:
    token = request_id.set("abc-123")
    try:
        entry = json.loads(JsonFormatter().format(record()))
    finally:
        request_id.reset(token)
    assert entry["request_id"] == "abc-123"


def test_only_allowed_extra_fields_are_written() -> None:
    entry = json.loads(
        JsonFormatter().format(record(event="chat", top_k=4, question="secret q"))
    )
    assert (entry["event"], entry["top_k"]) == ("chat", 4)
    assert "question" not in entry


def test_string_extra_fields_are_redacted() -> None:
    entry = json.loads(
        JsonFormatter().format(record(label="run token=abc123", top_k=4))
    )
    assert entry["label"] == "run token=***"
    assert entry["top_k"] == 4


def test_extra_fields_do_not_collide_with_log_record_attributes() -> None:
    # logging raises KeyError when extra= reuses a LogRecord attribute name.
    builtin = set(record().__dict__) | {"message", "asctime"}
    assert not builtin & set(EXTRA_FIELDS)


@pytest.mark.parametrize(
    ("text", "hidden"),
    [
        ("postgresql://aegis:hunter2@postgres:5432/db", "hunter2"),
        ("connect failed password=hunter2 host=db", "hunter2"),
        ("API_KEY: sk-live-123", "sk-live-123"),
        ("token=abc.def", "abc.def"),
        ("POSTGRES_PASSWORD=hunter2", "hunter2"),
        ("access_token=abc123", "abc123"),
        ("AEGIS_API_KEY=sk-1", "sk-1"),
        ("client_secret=xyz", "xyz"),
        ("{'password': 'hunter2'}", "hunter2"),
        ('{"token": "abc123"}', "abc123"),
        ("Authorization: Bearer abc123", "abc123"),
        ("token: Bearer abc123", "abc123"),
        ("SECRET_KEY=abc123", "abc123"),
        ("aws_secret_access_key=AKIA9", "AKIA9"),
        ("private_key=xyz", "xyz"),
        ("access_key=zzz", "zzz"),
        ("pwd=hunter2", "hunter2"),
        ("passwords=hunter2", "hunter2"),
        ("Authorization: Basic dXNlcjpwYXNz", "dXNlcjpwYXNz"),
        ("redis://:pw123@host:6379", "pw123"),
        ('password="two words"', "words"),
        ('{"password": "a,b"}', ",b"),
    ],
)
def test_secrets_are_redacted(text: str, hidden: str) -> None:
    out = redact(text)
    assert hidden not in out and "***" in out


@pytest.mark.parametrize(
    "text",
    [
        "GET /api/v1/documents 200 in 12 ms",
        "evaluation finished",
    ],
)
def test_plain_text_is_unchanged(text: str) -> None:
    assert redact(text) == text


def test_exceptions_are_redacted() -> None:
    try:
        raise RuntimeError("could not connect to postgresql://u:hunter2@db/x")
    except RuntimeError:
        r = record()
        r.exc_info = sys.exc_info()
    entry = json.loads(JsonFormatter().format(r))
    assert "hunter2" not in entry["exception"]
    assert "RuntimeError" in entry["exception"]


@pytest.mark.parametrize("good", ["abc", "A-b_c.1", "x" * 64])
def test_safe_request_ids_are_kept(good: str) -> None:
    assert new_request_id(good) == good


@pytest.mark.parametrize(
    "bad", [None, "", "x" * 65, "a b", "a\nb", "abc\n", "<script>"]
)
def test_unsafe_request_ids_are_replaced(bad: str | None) -> None:
    rid = new_request_id(bad)
    assert rid != bad and len(rid) == 32 and int(rid, 16) >= 0


def test_configure_logging_is_idempotent_and_keeps_other_handlers() -> None:
    root = logging.getLogger()
    before = list(root.handlers)
    level = root.level
    try:
        configure_logging("warning")
        configure_logging("info")
        json_handlers = [
            h for h in root.handlers if isinstance(h.formatter, JsonFormatter)
        ]
        assert len(json_handlers) == 1
        assert all(h in root.handlers for h in before)
        assert root.level == logging.INFO
        assert logging.getLogger("uvicorn.access").disabled
        assert logging.getLogger("uvicorn.error").propagate
    finally:
        root.handlers = before
        root.setLevel(level)
        logging.getLogger("uvicorn.access").disabled = False
