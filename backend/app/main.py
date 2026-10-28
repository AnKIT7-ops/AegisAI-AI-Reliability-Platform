"""AegisAI API entry point."""

import os

import psycopg
from fastapi import FastAPI
from fastapi.responses import JSONResponse

from app.api import chat, documents, models
from app.core.errors import install_error_handlers

DB_CONNECT_TIMEOUT_S = int(os.environ.get("DB_CONNECT_TIMEOUT_S", "2"))

app = FastAPI(title="AegisAI")
install_error_handlers(app)
app.include_router(documents.router)
app.include_router(models.router)
app.include_router(chat.router)


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
