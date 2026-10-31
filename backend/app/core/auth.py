"""Shared-password authentication: hashing, sessions and the login limit.

Standard library only, so the hash generator runs without any configuration:

    docker compose run --rm --no-deps backend python -m app.core.auth
"""

import base64
import hashlib
import hmac
import math
import re
import secrets
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from getpass import getpass

SCRYPT_N = 2**15
SCRYPT_R = 8
SCRYPT_P = 1
# hashlib's default limit is 32 MiB, which is exactly what these settings need.
SCRYPT_MAXMEM = 64 * 1024 * 1024
SALT_BYTES = 16
HASH_BYTES = 32
MIN_PASSWORD_CHARS = 12
MAX_ATTEMPTS = 5
ATTEMPT_WINDOW_S = 15 * 60

# Separated by ":" because Compose would interpolate the usual "$".
_PREFIX = f"scrypt:{SCRYPT_N}:{SCRYPT_R}:{SCRYPT_P}:"
_FORMAT = re.compile(re.escape(_PREFIX) + r"([A-Za-z0-9_-]{22}):([A-Za-z0-9_-]{43})")


def _encode(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def _decode(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def _scrypt(password: str, salt: bytes) -> bytes:
    return hashlib.scrypt(
        password.encode(),
        salt=salt,
        n=SCRYPT_N,
        r=SCRYPT_R,
        p=SCRYPT_P,
        maxmem=SCRYPT_MAXMEM,
        dklen=HASH_BYTES,
    )


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(SALT_BYTES)
    return f"{_PREFIX}{_encode(salt)}:{_encode(_scrypt(password, salt))}"


def check_hash(stored: str) -> None:
    """Refuse a malformed hash. The message never contains the value."""
    if not _FORMAT.fullmatch(stored):
        raise ValueError(
            "AEGIS_AUTH_PASSWORD_HASH is malformed; generate it with "
            "python -m app.core.auth"
        )


def verify_password(password: str, stored: str) -> bool:
    match = _FORMAT.fullmatch(stored)
    if match is None:
        return False
    salt, expected = match.groups()
    return hmac.compare_digest(_scrypt(password, _decode(salt)), _decode(expected))


@dataclass(frozen=True)
class Session:
    expires_at: float
    csrf_token: str


def _digest(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


class SessionStore:
    """Sessions by token digest, so a memory dump holds no usable cookie.

    ponytail: in memory, so one uvicorn process only and a restart signs
    everyone out. Sign-in survives a database outage this way; move this to
    PostgreSQL before adding workers.
    """

    def __init__(self, clock: Callable[[], float] = time.time) -> None:
        self._clock = clock
        self._sessions: dict[str, Session] = {}
        # Middleware runs on the event loop, login in the threadpool.
        self._lock = threading.Lock()

    def create(self, ttl_s: float) -> tuple[str, Session]:
        token = secrets.token_urlsafe(32)
        now = self._clock()
        session = Session(now + ttl_s, secrets.token_urlsafe(32))
        with self._lock:
            self._sessions = {
                key: kept
                for key, kept in self._sessions.items()
                if kept.expires_at > now
            }
            self._sessions[_digest(token)] = session
        return token, session

    def get(self, token: str) -> Session | None:
        key = _digest(token)
        with self._lock:
            session = self._sessions.get(key)
            if session is not None and session.expires_at <= self._clock():
                del self._sessions[key]
                return None
        return session

    def revoke(self, token: str) -> None:
        with self._lock:
            self._sessions.pop(_digest(token), None)


class LoginLimiter:
    """At most MAX_ATTEMPTS per address in a sliding ATTEMPT_WINDOW_S."""

    def __init__(self, clock: Callable[[], float] = time.time) -> None:
        self._clock = clock
        self._attempts: dict[str, list[float]] = {}
        self._lock = threading.Lock()

    def reserve(self, address: str) -> int:
        """Record an attempt and return 0, or the seconds to wait if refused.

        Recorded before hashing and cleared on success: parallel requests
        cannot all pass the check before any has counted as a failure.
        """
        now = self._clock()
        cutoff = now - ATTEMPT_WINDOW_S
        with self._lock:
            self._attempts = {
                key: recent
                for key, times in self._attempts.items()
                if (recent := [t for t in times if t > cutoff])
            }
            times = self._attempts.setdefault(address, [])
            if len(times) >= MAX_ATTEMPTS:
                return math.ceil(times[0] + ATTEMPT_WINDOW_S - now)
            times.append(now)
        return 0

    def reset(self, address: str) -> None:
        with self._lock:
            self._attempts.pop(address, None)


def main() -> None:
    password = getpass("New password: ")
    if len(password) < MIN_PASSWORD_CHARS:
        raise SystemExit(f"Use at least {MIN_PASSWORD_CHARS} characters.")
    if password != getpass("Repeat it: "):
        raise SystemExit("The passwords do not match.")
    print(hash_password(password))


if __name__ == "__main__":
    main()
