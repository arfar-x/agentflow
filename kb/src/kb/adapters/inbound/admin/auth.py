"""Who may use the console, and how a browser proves it (FR-ADM-01..03).

One shared token from `.env`. A person pastes it into the login screen and gets
a signed cookie; a script sends it as a bearer token. Both end the moment the
token changes, because the cookie's key is derived from it.

The threat this has to hold against is not a clever attacker so much as a page
on another site that makes the operator's browser send a request: so a request
that changes anything must carry a header only this console's own page sets,
and an `Origin` that matches (FR-ADM-03).
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
from collections import defaultdict, deque
from typing import Callable
from urllib.parse import urlparse

from starlette.requests import Request

COOKIE = "kb_admin_session"
#: The header the console's own page adds to every call. A cross-site form or
#: image cannot set a custom header, and a cross-site script is stopped by CORS
#: from setting it without a preflight this server never approves.
CSRF_HEADER = "x-kb-admin"

LOGIN_WINDOW_SECONDS = 300
LOGIN_ATTEMPTS = 5


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _unb64(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


class Auth:
    def __init__(self, token: str, *, session_seconds: float, now: Callable[[], float] = time.time) -> None:
        self._token = token.encode("utf-8")
        # Derived, not the token itself, so a leaked cookie is not the token and
        # a changed token invalidates every cookie.
        self._key = hashlib.sha256(b"kb-admin-session:" + self._token).digest()
        self._ttl = session_seconds
        self._now = now
        self._failures: dict[str, deque[float]] = defaultdict(deque)

    # -- the token -----------------------------------------------------------
    def token_matches(self, candidate: str) -> bool:
        return hmac.compare_digest(candidate.encode("utf-8"), self._token)

    def bearer(self, request: Request) -> bool:
        header = request.headers.get("authorization", "")
        scheme, _, value = header.partition(" ")
        return scheme.lower() == "bearer" and bool(value) and self.token_matches(value.strip())

    # -- the cookie ----------------------------------------------------------
    def issue_cookie(self) -> str:
        payload = json.dumps({"exp": self._now() + self._ttl}, separators=(",", ":")).encode()
        signature = hmac.new(self._key, payload, hashlib.sha256).digest()
        return f"{_b64(payload)}.{_b64(signature)}"

    def cookie_valid(self, value: str | None) -> bool:
        if not value or "." not in value:
            return False
        body, _, signature = value.partition(".")
        try:
            payload, given = _unb64(body), _unb64(signature)
            expected = hmac.new(self._key, payload, hashlib.sha256).digest()
            if not hmac.compare_digest(given, expected):
                return False
            return float(json.loads(payload)["exp"]) > self._now()
        except (ValueError, KeyError, TypeError):
            return False

    @property
    def ttl_seconds(self) -> int:
        return int(self._ttl)

    # -- who is asking -------------------------------------------------------
    def authenticated(self, request: Request) -> str | None:
        """'bearer', 'cookie', or None."""
        if self.bearer(request):
            return "bearer"
        if self.cookie_valid(request.cookies.get(COOKIE)):
            return "cookie"
        return None

    # -- throttling failed logins -------------------------------------------
    def locked_out(self, client: str) -> int:
        """Seconds until this client may try again; 0 if it may now."""
        attempts = self._failures[client]
        cutoff = self._now() - LOGIN_WINDOW_SECONDS
        while attempts and attempts[0] < cutoff:
            attempts.popleft()
        if len(attempts) < LOGIN_ATTEMPTS:
            return 0
        return max(1, int(attempts[0] + LOGIN_WINDOW_SECONDS - self._now()))

    def record_failure(self, client: str) -> None:
        self._failures[client].append(self._now())

    def clear_failures(self, client: str) -> None:
        self._failures.pop(client, None)


def same_origin(request: Request) -> bool:
    """A browser sends `Origin` on anything that changes state; when it is
    there, it must be this console. Absent (a script) is not a browser forging
    a cross-site request."""
    origin = request.headers.get("origin")
    if origin is None:
        return True
    host = request.headers.get("host", "")
    return urlparse(origin).netloc == host


def csrf_ok(request: Request) -> bool:
    return request.headers.get(CSRF_HEADER) == "1" and same_origin(request)
