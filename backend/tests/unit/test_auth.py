import hashlib
import re

import pytest
from pydantic import ValidationError

from app.core.auth import (
    LoginLimiter,
    SessionStore,
    check_hash,
    hash_password,
    verify_password,
)
from app.core.config import Settings

STORED = hash_password("correct horse battery")


class Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


def test_hash_round_trip() -> None:
    assert verify_password("correct horse battery", STORED)
    assert not verify_password("wrong", STORED)


def test_hash_format_is_safe_for_compose() -> None:
    assert STORED.startswith("scrypt:32768:8:1:")
    assert len(STORED.split(":")) == 6
    assert "$" not in STORED


def test_each_hash_has_its_own_salt() -> None:
    assert hash_password("same") != hash_password("same")


@pytest.mark.parametrize(
    "bad",
    [
        "nonsense-secret",
        "scrypt:2:8:1:AAAA:BBBB",
        STORED.replace("scrypt:32768", "scrypt:16384"),
        STORED.replace("scrypt:", "md5:"),
        STORED[:-4] + "!!!!",
        STORED + ":extra",
        ":".join(STORED.split(":")[:5]) + ":AAAA",
    ],
)
def test_malformed_hash_is_rejected_without_echo(bad: str) -> None:
    with pytest.raises(ValueError) as exc:
        check_hash(bad)
    assert bad not in str(exc.value)


def test_session_expires() -> None:
    clock = Clock()
    store = SessionStore(clock)
    token, session = store.create(60)
    assert store.get(token) == session
    clock.now += 61
    assert store.get(token) is None


def test_revoked_session_is_gone() -> None:
    store = SessionStore()
    token, _ = store.create(60)
    store.revoke(token)
    assert store.get(token) is None


def test_unknown_token_has_no_session() -> None:
    assert SessionStore().get("nope") is None


def test_store_holds_a_digest_not_the_token() -> None:
    store = SessionStore()
    token, _ = store.create(60)
    assert token not in store._sessions  # noqa: SLF001
    assert hashlib.sha256(token.encode()).hexdigest() in store._sessions  # noqa: SLF001


def test_tokens_and_csrf_are_long_and_unique() -> None:
    store = SessionStore()
    a, first = store.create(60)
    b, second = store.create(60)
    assert a != b and first.csrf_token != second.csrf_token
    assert len(a) >= 43 and len(first.csrf_token) >= 43
    assert re.fullmatch(r"[A-Za-z0-9_-]+", a)


def test_expired_sessions_are_dropped_on_create() -> None:
    clock = Clock()
    store = SessionStore(clock)
    store.create(60)
    clock.now += 61
    store.create(60)
    assert len(store._sessions) == 1  # noqa: SLF001


def test_limiter_blocks_the_sixth_attempt() -> None:
    clock = Clock()
    limiter = LoginLimiter(clock)
    assert [limiter.reserve("a") for _ in range(5)] == [0] * 5
    wait = limiter.reserve("a")
    assert 899 <= wait <= 900


def test_limiter_is_per_address() -> None:
    limiter = LoginLimiter()
    for _ in range(6):
        limiter.reserve("a")
    assert limiter.reserve("b") == 0


def test_limiter_window_elapses() -> None:
    clock = Clock()
    limiter = LoginLimiter(clock)
    for _ in range(5):
        limiter.reserve("a")
    clock.now += 901
    assert limiter.reserve("a") == 0


def test_blocked_attempts_do_not_extend_the_lockout() -> None:
    clock = Clock()
    limiter = LoginLimiter(clock)
    for _ in range(5):
        limiter.reserve("a")
    clock.now += 800
    assert limiter.reserve("a") > 0
    clock.now += 101
    assert limiter.reserve("a") == 0


def test_limiter_reset_clears_the_count() -> None:
    limiter = LoginLimiter()
    for _ in range(5):
        limiter.reserve("a")
    limiter.reset("a")
    assert limiter.reserve("a") == 0


def test_limiter_reserves_before_hashing() -> None:
    # The check and the record are one step, so parallel requests cannot all
    # pass the check before any of them has recorded a failure.
    limiter = LoginLimiter()
    allowed = [limiter.reserve("a") == 0 for _ in range(40)]
    assert sum(allowed) == 5


def test_limiter_forgets_idle_addresses() -> None:
    clock = Clock()
    limiter = LoginLimiter(clock)
    limiter.reserve("a")
    clock.now += 901
    limiter.reserve("b")
    assert set(limiter._attempts) == {"b"}  # noqa: SLF001


def test_malformed_hash_stops_settings_without_echo() -> None:
    with pytest.raises(ValidationError) as exc:
        Settings(auth_password_hash="nonsense-secret")
    assert "nonsense-secret" not in str(exc.value)


def test_settings_repr_omits_the_hash() -> None:
    assert STORED not in repr(Settings(auth_password_hash=STORED))


def test_empty_hash_means_authentication_off() -> None:
    assert Settings(auth_password_hash="").auth_password_hash == ""


@pytest.mark.parametrize(
    "bind", ["127.0.0.1", "localhost", "::1", "[::1]", "127.0.0.9"]
)
def test_loopback_bind_needs_nothing(bind: str) -> None:
    Settings(frontend_bind=bind)


@pytest.mark.parametrize("bind", ["0.0.0.0", "192.168.1.5", "example.internal", ""])  # noqa: S104
def test_non_loopback_bind_fails_closed(bind: str) -> None:
    with pytest.raises(ValidationError, match="FRONTEND_BIND"):
        Settings(frontend_bind=bind)
    with pytest.raises(ValidationError, match="FRONTEND_BIND"):
        Settings(frontend_bind=bind, auth_password_hash=STORED)
    with pytest.raises(ValidationError, match="FRONTEND_BIND"):
        Settings(frontend_bind=bind, auth_cookie_secure=True)


def test_non_loopback_bind_with_both_settings_starts() -> None:
    Settings(
        frontend_bind="0.0.0.0",  # noqa: S104
        auth_password_hash=STORED,
        auth_cookie_secure=True,
    )
