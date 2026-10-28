"""AegisAI API entry point."""

import logging
import os
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager

import psycopg
from fastapi import FastAPI, Request, Response
from fastapi.responses import JSONResponse

from app.api import chat, documents, evaluations, models
from app.api.deps import get_sessionmaker
from app.core.config import get_settings
from app.core.errors import error_response, install_error_handlers
from app.services.evaluation.jobs import mark_interrupted

DB_CONNECT_TIMEOUT_S = int(os.environ.get("DB_CONNECT_TIMEOUT_S", "2"))
SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}

log = logging.getLogger("aegis")


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    # A run that was active when the last process stopped can never finish.
    if os.environ.get("DATABASE_URL"):
        try:
            with get_sessionmaker()() as session:
                if n := mark_interrupted(session):
                    log.warning("marked %d interrupted evaluation run(s) failed", n)
        except Exception:
            log.warning("could not check for interrupted evaluation runs")
    yield


app = FastAPI(title="AegisAI", lifespan=lifespan)
install_error_handlers(app)


@app.middleware("http")
async def request_guards(
    request: Request, call_next: Callable[[Request], Awaitable[Response]]
) -> Response:
    settings = get_settings()
    # DNS rebinding: the browser reaches 127.0.0.1 but sends the attacker's Host.
    host = request.headers.get("host", "").lower()
    if host.split(":")[0] not in settings.allowed_hosts:
        return error_response(400, "invalid_host", "Host not allowed")
    # Browsers always send Origin on cross-site POSTs; CLI clients send none.
    # Same-origin (the /docs page) is fine: Host was just checked.
    origin = request.headers.get("origin")
    if (
        request.method not in SAFE_METHODS
        and origin is not None
        and origin not in settings.allowed_origins
        and origin != f"{request.url.scheme}://{host}"
    ):
        return error_response(403, "forbidden_origin", "Origin not allowed")
    return await call_next(request)


app.include_router(documents.router)
app.include_router(models.router)
app.include_router(chat.router)
app.include_router(evaluations.router)


@app.get("/health/live")
def live() -> dict[str, str]:
    """The process is up and serving requests."""
    return {"status": "ok"}


def database_ok() -> bool:
    url = os.environ.get("DATABASE_URL")
    if not url:
        return False
    try:
        with psycopg.connect(
            url,
            connect_timeout=DB_CONNECT_TIMEOUT_S,
            options=f"-c statement_timeout={DB_CONNECT_TIMEOUT_S * 1000}",
        ) as conn:
            conn.execute("SELECT 1")
        return True
    except psycopg.Error:
        return False


# Sync def: FastAPI runs it in a threadpool, so the blocking check can't stall the loop.
@app.get("/health/ready")
def ready() -> JSONResponse:
    """Dependencies are reachable. No error details: they could leak the DSN."""
    db = database_ok()
    return JSONResponse(
        status_code=200 if db else 503,
        content={
            "status": "ready" if db else "not ready",
            "checks": {"database": "ok" if db else "unavailable"},
        },
    )
