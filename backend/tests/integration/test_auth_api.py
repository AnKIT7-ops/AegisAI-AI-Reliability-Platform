import logging
import re
from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from app.api import auth
from app.api.auth import PUBLIC_PATHS, SESSION_COOKIE
from app.core.auth import LoginLimiter, SessionStore, hash_password
from app.core.config import Settings, get_settings
from app.main import app

PASSWORD = "correct horse battery"
STORED = hash_password(PASSWORD)
LOGIN = "/api/v1/auth/login"
SESSION = "/api/v1/auth/session"
CHECK = "/api/v1/auth/check"
LOGOUT = "/api/v1/auth/logout"
DOCUMENTS = "/api/v1/documents"


@pytest.fixture
def auth_on(
    settings: Settings, client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> Iterator[Settings]:
    enabled = settings.model_copy(update={"auth_password_hash": STORED})
    monkeypatch.setattr("app.main.get_settings", lambda: enabled)
    monkeypatch.setattr(auth, "sessions", SessionStore())
    monkeypatch.setattr(auth, "limiter", LoginLimiter())
    app.dependency_overrides[get_settings] = lambda: enabled
    yield enabled


def sign_in(client: TestClient) -> str:
    r = client.post(LOGIN, json={"password": PASSWORD})
    assert r.status_code == 200
    token: str = r.json()["csrf_token"]
    return token


def test_auth_off_reports_disabled(client: TestClient) -> None:
    r = client.get(SESSION)
    assert r.json() == {
        "enabled": False,
        "authenticated": False,
        "expires_at": None,
        "csrf_token": None,
    }


def test_auth_off_leaves_routes_open(client: TestClient) -> None:
    assert client.get(DOCUMENTS).status_code == 200
    assert client.get(CHECK).status_code == 204


def test_auth_off_login_sets_no_cookie(client: TestClient) -> None:
    r = client.post(LOGIN, json={"password": "anything"})
    assert r.status_code == 200
    assert "set-cookie" not in r.headers
    assert r.json()["enabled"] is False


def test_auth_off_logout_is_harmless(client: TestClient) -> None:
    assert client.post(LOGOUT).status_code == 204


def every_route() -> list[tuple[str, str]]:
    routes = [
        (method.upper(), re.sub(r"\{[^}]+\}", "x", path))
        for path, operations in app.openapi()["paths"].items()
        for method in operations
    ]
    routes += [("GET", path) for path in ("/docs", "/redoc", "/openapi.json")]
    return [route for route in routes if route[1] not in PUBLIC_PATHS]


def test_the_walk_covers_the_routes_that_matter() -> None:
    paths = {path for _, path in every_route()}
    assert {DOCUMENTS, "/api/v1/chat", LOGOUT, "/docs", "/openapi.json"} <= paths


@pytest.mark.parametrize(("method", "path"), every_route())
def test_every_route_is_closed_by_default(
    auth_on: Settings, client: TestClient, method: str, path: str
) -> None:
    r = client.request(method, path)
    assert (r.status_code, r.json()["error"]["code"]) == (401, "not_authenticated")


@pytest.mark.parametrize("path", ["/nope", "/api/v1/auth/login/", "/API/v1/chat"])
def test_unknown_paths_are_closed(
    auth_on: Settings, client: TestClient, path: str
) -> None:
    assert client.get(path).status_code == 401


def test_traversal_is_not_public(auth_on: Settings, client: TestClient) -> None:
    # httpx normalises dots, so the raw path is set through the ASGI scope.
    request = client.build_request("GET", "http://localhost/health/live/x")
    request.url = request.url.copy_with(raw_path=b"/health/../api/v1/documents")
    assert client.send(request).status_code == 401


@pytest.mark.parametrize("path", ["/health/live", "/metrics", SESSION])
def test_public_paths_need_no_session(
    auth_on: Settings, client: TestClient, path: str
) -> None:
    assert client.get(path).status_code == 200


def test_login_sets_the_session_cookie(auth_on: Settings, client: TestClient) -> None:
    r = client.post(LOGIN, json={"password": PASSWORD})
    assert r.status_code == 200
    body = r.json()
    assert body["enabled"] is True and body["authenticated"] is True
    assert body["expires_at"] and body["csrf_token"]
    cookie = r.headers["set-cookie"]
    assert cookie.startswith(f"{SESSION_COOKIE}=")
    assert "HttpOnly" in cookie
    assert "SameSite=strict" in cookie
    assert "Path=/" in cookie
    assert "Max-Age=28800" in cookie
    assert "Secure" not in cookie


def test_cookie_secure_follows_settings(auth_on: Settings, client: TestClient) -> None:
    secure = auth_on.model_copy(update={"auth_cookie_secure": True})
    app.dependency_overrides[get_settings] = lambda: secure
    r = client.post(LOGIN, json={"password": PASSWORD})
    assert "Secure" in r.headers["set-cookie"]


def test_session_hours_set_the_cookie_age(
    auth_on: Settings, client: TestClient
) -> None:
    short = auth_on.model_copy(update={"auth_session_hours": 1})
    app.dependency_overrides[get_settings] = lambda: short
    r = client.post(LOGIN, json={"password": PASSWORD})
    assert "Max-Age=3600" in r.headers["set-cookie"]


def test_session_reports_the_signed_in_state(
    auth_on: Settings, client: TestClient
) -> None:
    assert client.get(SESSION).json()["authenticated"] is False
    token = sign_in(client)
    state = client.get(SESSION).json()
    assert state["authenticated"] is True and state["csrf_token"] == token


def test_wrong_password_is_refused(auth_on: Settings, client: TestClient) -> None:
    r = client.post(LOGIN, json={"password": "wrong"})
    assert (r.status_code, r.json()["error"]["code"]) == (401, "invalid_credentials")
    assert "set-cookie" not in r.headers


@pytest.mark.parametrize(
    "body",
    [
        {},
        {"password": ""},
        {"password": "x" * 1025},
        {"password": PASSWORD, "remember": True},
    ],
)
def test_invalid_login_bodies_are_rejected(
    auth_on: Settings, client: TestClient, body: dict[str, object]
) -> None:
    assert client.post(LOGIN, json=body).status_code == 422


def test_invalid_login_body_is_not_echoed(
    auth_on: Settings, client: TestClient
) -> None:
    r = client.post(LOGIN, json={"password": "secret-value", "x": 1})
    assert "secret-value" not in r.text


def test_lone_surrogate_password_does_not_crash(
    auth_on: Settings, client: TestClient
) -> None:
    r = client.post(
        LOGIN,
        content=b'{"password": "\\ud800"}',
        headers={"content-type": "application/json"},
    )
    assert r.status_code == 422


def test_sixth_failure_is_limited(auth_on: Settings, client: TestClient) -> None:
    for _ in range(5):
        assert client.post(LOGIN, json={"password": "no"}).status_code == 401
    r = client.post(LOGIN, json={"password": "no"})
    assert (r.status_code, r.json()["error"]["code"]) == (429, "too_many_attempts")
    assert 1 <= int(r.headers["retry-after"]) <= 900


def test_correct_password_is_refused_while_limited(
    auth_on: Settings, client: TestClient
) -> None:
    for _ in range(5):
        client.post(LOGIN, json={"password": "no"})
    assert client.post(LOGIN, json={"password": PASSWORD}).status_code == 429


def test_success_resets_the_count(auth_on: Settings, client: TestClient) -> None:
    for _ in range(4):
        client.post(LOGIN, json={"password": "no"})
    sign_in(client)
    for _ in range(5):
        assert client.post(LOGIN, json={"password": "no"}).status_code == 401


def test_limit_is_per_forwarded_address(auth_on: Settings, client: TestClient) -> None:
    for _ in range(6):
        client.post(LOGIN, json={"password": "no"}, headers={"x-real-ip": "10.0.0.1"})
    r = client.post(
        LOGIN, json={"password": PASSWORD}, headers={"x-real-ip": "10.0.0.2"}
    )
    assert r.status_code == 200


def test_session_opens_the_app(auth_on: Settings, client: TestClient) -> None:
    sign_in(client)
    assert client.get(DOCUMENTS).status_code == 200


def test_expired_session_is_closed(
    auth_on: Settings, client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    now = [1000.0]
    monkeypatch.setattr(auth, "sessions", SessionStore(lambda: now[0]))
    sign_in(client)
    now[0] += auth_on.auth_session_hours * 3600 + 1
    assert client.get(DOCUMENTS).status_code == 401
    assert client.get(CHECK).status_code == 401


def test_a_forged_cookie_is_closed(auth_on: Settings, client: TestClient) -> None:
    client.cookies.set(SESSION_COOKIE, "forged")
    assert client.get(DOCUMENTS).status_code == 401


def test_check_needs_a_session(auth_on: Settings, client: TestClient) -> None:
    assert client.get(CHECK).status_code == 401
    sign_in(client)
    assert client.get(CHECK).status_code == 204


def test_logout_revokes_the_session(auth_on: Settings, client: TestClient) -> None:
    token = sign_in(client)
    stolen = client.cookies[SESSION_COOKIE]
    r = client.post(LOGOUT, headers={"x-csrf-token": token})
    assert r.status_code == 204
    assert f'{SESSION_COOKIE}=""' in r.headers["set-cookie"]
    # The old cookie stops working at once, not when it expires.
    client.cookies.set(SESSION_COOKIE, stolen)
    assert client.get(CHECK).status_code == 401


def test_unsafe_request_needs_the_csrf_token(
    auth_on: Settings, client: TestClient
) -> None:
    sign_in(client)
    r = client.post("/api/v1/chat", json={"question": "q"})
    assert (r.status_code, r.json()["error"]["code"]) == (403, "csrf_failed")


def test_wrong_csrf_token_is_refused(auth_on: Settings, client: TestClient) -> None:
    sign_in(client)
    r = client.post(
        "/api/v1/chat", json={"question": "q"}, headers={"x-csrf-token": "wrong"}
    )
    assert r.status_code == 403


def test_non_ascii_csrf_token_does_not_crash(
    auth_on: Settings, client: TestClient
) -> None:
    sign_in(client)
    r = client.post(
        "/api/v1/chat",
        json={"question": "q"},
        headers={"x-csrf-token": "é".encode("latin-1")},
    )
    assert r.status_code == 403


def test_right_csrf_token_passes(auth_on: Settings, client: TestClient) -> None:
    token = sign_in(client)
    r = client.post(
        "/api/v1/chat", json={"question": "q"}, headers={"x-csrf-token": token}
    )
    assert r.status_code != 403


def test_another_sessions_csrf_token_is_refused(
    auth_on: Settings, client: TestClient
) -> None:
    other = sign_in(client)
    sign_in(client)
    r = client.post(LOGOUT, headers={"x-csrf-token": other})
    assert r.status_code == 403


def test_logout_without_csrf_token_is_refused(
    auth_on: Settings, client: TestClient
) -> None:
    sign_in(client)
    r = client.post(LOGOUT)
    assert (r.status_code, r.json()["error"]["code"]) == (403, "csrf_failed")
    assert client.get(CHECK).status_code == 204


def test_foreign_origin_cannot_sign_in(auth_on: Settings, client: TestClient) -> None:
    r = client.post(
        LOGIN,
        json={"password": PASSWORD},
        headers={"origin": "https://evil.example"},
    )
    assert (r.status_code, r.json()["error"]["code"]) == (403, "forbidden_origin")


def test_foreign_host_cannot_pass_the_nginx_check(
    auth_on: Settings, client: TestClient
) -> None:
    # nginx forwards Host to this check, so it also guards the monitoring paths.
    r = client.get(CHECK, headers={"host": "evil.example"})
    assert r.status_code == 400


def test_failed_sign_in_is_logged_without_the_password(
    auth_on: Settings, client: TestClient, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level(logging.WARNING, "aegis"):
        client.post(LOGIN, json={"password": "secret-value"})
    assert any(getattr(r, "event", None) == "auth_failed" for r in caplog.records)
    assert "secret-value" not in caplog.text
