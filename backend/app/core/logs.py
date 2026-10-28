"""JSON logs with a per-request correlation ID and secret redaction."""

import json
import logging
import re
import sys
import uuid
from contextvars import ContextVar
from datetime import UTC, datetime
from typing import Any

# Set per request by the middleware; background tasks inherit it.
request_id: ContextVar[str | None] = ContextVar("request_id", default=None)

_VALID_ID = re.compile(r"[A-Za-z0-9._-]{1,64}")
_SECRETS = [
    # Authorization: Bearer/Basic ... (first, so "token: Bearer x" masks x)
    (re.compile(r"(?i)\b((?:bearer|basic)\s+)[^\s'\",}]+"), r"\1***"),
    # [user]:password@ in any URL (database DSNs, redis://:pw@host, proxies)
    (re.compile(r"(\w+://[^:/@\s]*:)[^@\s]+@"), r"\1***@"),
    # Keys that name a secret, with any prefix or suffix (POSTGRES_PASSWORD,
    # access_token, aws_secret_access_key, private_key, pwd), quoted or not.
    # A quoted value is masked to its closing quote, spaces and commas included.
    (
        re.compile(
            r"(?i)([\w-]*(?:password|passwd|pwd|secret|token|api[_-]?key|"
            r"private[_-]?key|access[_-]?key)[\w-]*['\"]?\s*[=:]\s*)"
            r"(\"[^\"]*\"|'[^']*'|[^\s'\",}]+)"
        ),
        r"\1***",
    ),
]
# Fields callers may attach with extra={...}; anything else is dropped so a
# careless extra=... cannot put request bodies or documents in the log.
EXTRA_FIELDS = (
    "event",
    "method",
    "path",
    "status",
    "duration_ms",
    "document_id",
    "chunks",
    "size_bytes",
    "new_document",
    "question_chars",
    "top_k",
    "sources",
    "refused",
    "model",
    "latency_ms",
    "prompt_tokens",
    "completion_tokens",
    "error_code",
    "run_id",
    "label",
    "dataset",
    "examples",
    "errors",
)


def redact(text: str) -> str:
    for pattern, replacement in _SECRETS:
        text = pattern.sub(replacement, text)
    return text


def new_request_id(incoming: str | None) -> str:
    """Keep a caller's ID if it is safe to log; otherwise make one."""
    if incoming and _VALID_ID.fullmatch(incoming):
        return incoming
    return uuid.uuid4().hex


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        entry: dict[str, Any] = {
            "ts": datetime.fromtimestamp(record.created, UTC).isoformat(
                timespec="milliseconds"
            ),
            "level": record.levelname.lower(),
            "logger": record.name,
            "message": redact(record.getMessage()),
        }
        if rid := request_id.get():
            entry["request_id"] = rid
        for name in EXTRA_FIELDS:
            if name in record.__dict__:
                value = record.__dict__[name]
                # Some fields are user-supplied (an evaluation label, say).
                entry[name] = redact(value) if isinstance(value, str) else value
        if record.exc_info:
            entry["exception"] = redact(self.formatException(record.exc_info))
        return json.dumps(entry, default=str)


def configure_logging(level: str) -> None:
    """Send app and uvicorn logs to stdout as JSON lines."""
    root = logging.getLogger()
    # Added once and alongside existing handlers (test log capture, for one).
    if not any(isinstance(h.formatter, JsonFormatter) for h in root.handlers):
        handler = logging.StreamHandler(sys.stdout)
        handler.setFormatter(JsonFormatter())
        root.addHandler(handler)
    root.setLevel(level.upper())
    for name in ("uvicorn", "uvicorn.error"):
        logger = logging.getLogger(name)
        logger.handlers = []
        logger.propagate = True
    # The app writes its own access line with the request ID and duration.
    logging.getLogger("uvicorn.access").disabled = True
    # httpx logs every Ollama call at INFO; model calls get their own metrics.
    logging.getLogger("httpx").setLevel(logging.WARNING)
