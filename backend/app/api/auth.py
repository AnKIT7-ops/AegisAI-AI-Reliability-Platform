"""Sign-in routes and the default-deny check every request passes through."""

import hmac
import logging
from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Request, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from app.core.auth import LoginLimiter, Session, SessionStore, verify_password
from app.core.config import Settings, get_settings
from app.core.errors import ApiError, error_response

SESSION_COOKIE = "aegis_session"  # noqa: S105
CSRF_HEADER = "x-csrf-token"
SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}
# Exact paths, never a prefix: anything added later stays closed by default.
PUBLIC_PATHS = {
    "/health/live",
    "/health/ready",
    "/metrics",
    "/api/v1/auth/login",
    "/api/v1/auth/session",
    "/api/v1/auth/check",
}

log = logging.getLogger("aegis")
router = APIRouter(prefix="/api/v1/auth", tags=["auth"])
sessions = SessionStore()
limiter = LoginLimiter()


class LoginRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    password: str = Field(min_length=1, max_length=1024)


class SessionState(BaseModel):
    enabled: bool
    authenticated: bool
    expires_at: datetime | None
    csrf_token: str | None


def current_session(request: Request) -> Session | None:
    token = request.cookies.get(SESSION_COOKIE)
    return sessions.get(token) if token else None


def auth_denial(request: Request) -> Response | None:
    """Why this request may not proceed, or None. Only runs with auth on."""
    if request.url.path in PUBLIC_PATHS:
        return None
    session = current_session(request)
    if session is None:
        return error_response(401, "not_authenticated", "sign in first")
    if request.method not in SAFE_METHODS and not hmac.compare_digest(
        request.headers.get(CSRF_HEADER, "").encode(), session.csrf_token.encode()
    ):
        return error_response(403, "csrf_failed", "missing or wrong CSRF token")
    return None


def _state(settings: Settings, session: Session | None) -> SessionState:
    return SessionState(
        enabled=bool(settings.auth_password_hash),
        authenticated=session is not None,
        expires_at=datetime.fromtimestamp(session.expires_at, UTC) if session else None,
        csrf_token=session.csrf_token if session else None,
    )


def _client_address(request: Request) -> str:
    # nginx overwrites X-Real-IP, and only nginx can reach the backend.
    real_ip = request.headers.get("x-real-ip")
    return real_ip or (request.client.host if request.client else "unknown")


@router.get("/session")
def session_state(
    request: Request, settings: Annotated[Settings, Depends(get_settings)]
) -> SessionState:
    return _state(settings, current_session(request))


# Sync def: scrypt is slow on purpose, so it runs in the threadpool.
@router.post("/login", response_model=SessionState)
def login(
    body: LoginRequest,
    request: Request,
    response: Response,
    settings: Annotated[Settings, Depends(get_settings)],
) -> SessionState | JSONResponse:
    if not settings.auth_password_hash:
        return _state(settings, None)
    address = _client_address(request)
    if wait := limiter.reserve(address):
        log.warning(
            "sign-in refused",
            extra={"event": "auth_failed", "reason": "too_many_attempts"},
        )
        return error_response(
            429,
            "too_many_attempts",
            "too many failed attempts; try again later",
            {"Retry-After": str(wait)},
        )
    if not verify_password(body.password, settings.auth_password_hash):
        log.warning(
            "sign-in failed",
            extra={"event": "auth_failed", "reason": "invalid_credentials"},
        )
        raise ApiError(401, "invalid_credentials", "wrong password")
    limiter.reset(address)
    ttl_s = settings.auth_session_hours * 3600
    token, session = sessions.create(ttl_s)
    response.set_cookie(
        SESSION_COOKIE,
        token,
        max_age=ttl_s,
        path="/",
        httponly=True,
        samesite="strict",
        secure=settings.auth_cookie_secure,
    )
    return _state(settings, session)


@router.post("/logout", status_code=204)
def logout(
    request: Request,
    response: Response,
    settings: Annotated[Settings, Depends(get_settings)],
) -> None:
    if token := request.cookies.get(SESSION_COOKIE):
        sessions.revoke(token)
    response.delete_cookie(
        SESSION_COOKIE,
        path="/",
        httponly=True,
        samesite="strict",
        secure=settings.auth_cookie_secure,
    )


@router.get("/check", status_code=204)
def check(
    request: Request, settings: Annotated[Settings, Depends(get_settings)]
) -> None:
    """nginx auth_request target for the monitoring paths."""
    if settings.auth_password_hash and current_session(request) is None:
        raise ApiError(401, "not_authenticated", "sign in first")
