"""AegisAI API entry point."""

import logging
import os
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager

import psycopg
from fastapi import FastAPI, Request, Response
from fastapi.responses import JSONResponse
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest

from app.api import chat, documents, evaluations, incidents, models
from app.api.deps import get_sessionmaker
from app.core.config import get_settings
from app.core.errors import error_response, install_error_handlers
from app.core.logs import configure_logging, new_request_id, request_id
from app.core.metrics import HTTP_DURATION, HTTP_REQUESTS
from app.services.evaluation.jobs import mark_interrupted

DB_CONNECT_TIMEOUT_S = int(os.environ.get("DB_CONNECT_TIMEOUT_S", "2"))
SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}

# At import, so uvicorn's own startup lines are JSON too.
configure_logging(get_settings().log_level)
log = logging.getLogger("aegis")
access_log = logging.getLogger("aegis.access")


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    if settings.fault_delay_ms or settings.fault_error_rate:
        log.warning(
            "fault injection is ON: %d ms delay, %.0f%% errors on Ollama calls",
            settings.fault_delay_ms,
            settings.fault_error_rate * 100,
        )
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


# Registered last, so it runs first: even rejected requests get an ID and a log line.
@app.middleware("http")
async def request_context(
    request: Request, call_next: Callable[[Request], Awaitable[Response]]
) -> Response:
    rid = new_request_id(request.headers.get("x-request-id"))
    token = request_id.set(rid)
    start = time.perf_counter()
    status = 500
    try:
        try:
            response = await call_next(request)
        except Exception:
            # Handled here, not by Starlette's outer error middleware, so the
            # traceback is logged with the request ID and the 500 carries it.
            log.exception("unhandled error", extra={"event": "error"})
            response = error_response(500, "internal_error", "internal server error")
        status = response.status_code
        response.headers["X-Request-ID"] = rid
        return response
    finally:
        elapsed = time.perf_counter() - start
        # The route template (/api/v1/evaluations/{run_id}), never the raw path,
        # so label values stay bounded; unmatched and rejected requests share one.
        route = request.scope.get("route")
        template = str(getattr(route, "path", "unmatched"))
        HTTP_REQUESTS.labels(request.method, template, str(status)).inc()
        HTTP_DURATION.labels(request.method, template).observe(elapsed)
        # The path only: query strings are not logged. Health checks (every 5 s
        # from the compose healthcheck) and scrapes log at DEBUG.
        quiet = request.url.path.startswith("/health") or request.url.path == "/metrics"
        access_log.log(
            logging.DEBUG if quiet else logging.INFO,
            "%s %s %d",
            request.method,
            request.url.path,
            status,
            extra={
                "event": "request",
                "method": request.method,
                "path": request.url.path,
                "status": status,
                "duration_ms": round(elapsed * 1000, 1),
            },
        )
        request_id.reset(token)


app.include_router(documents.router)
app.include_router(models.router)
app.include_router(chat.router)
app.include_router(evaluations.router)
app.include_router(incidents.router)


@app.get("/metrics", include_in_schema=False)
def metrics() -> Response:
    """Prometheus text format; scraped by the prometheus service in compose."""
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)


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
