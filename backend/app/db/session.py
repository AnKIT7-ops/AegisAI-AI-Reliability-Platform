"""Database connection settings. DATABASE_URL is the only source of the DSN."""

import os
from typing import Any

from sqlalchemy.engine import URL, make_url


def database_url() -> URL:
    # A URL object, not a string: its str() and repr() mask the password.
    raw = os.environ.get("DATABASE_URL")
    if not raw:
        raise RuntimeError("DATABASE_URL is not set")
    url = make_url(raw)
    # Compose passes a plain postgresql:// URL; the installed driver is psycopg 3.
    if url.drivername == "postgresql":
        url = url.set(drivername="postgresql+psycopg")
    return url


def connect_args(url: URL) -> dict[str, Any]:
    """Fail fast instead of hanging on a dead host or a held lock (Postgres only)."""
    if url.get_backend_name() != "postgresql":
        return {}
    return {
        "connect_timeout": 5,
        "options": "-c lock_timeout=10000 -c statement_timeout=60000",
    }
