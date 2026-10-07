"""Encryption-at-rest and link-signing, keyed off one root secret this
plugin generates for itself the first time it's ever invoked and keeps in
its own persistent KV storage (``self.session.storage``).

Nothing here is workspace-admin-configured on purpose: the whole point is
that a per-user Jira/Confluence credential is never typed into a Studio
"provider credentials" screen (which is shared, workspace-wide, and visible
to every admin) -- it's entered once by that one user, through the
Endpoint form, and only ever decrypted for that same user's own outbound
MCP calls.

Storage is scoped per-workspace-per-plugin-install (confirmed in Dify's own
plugin docs: "the KV storage API is isolated per workspace per plugin
install"), and that scope is shared between this plugin's Tool and
Endpoint code alike -- both run as the same plugin installation, so a
secret one writes is exactly the secret the other reads.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import time
from typing import Any

from cryptography.fernet import Fernet, InvalidToken

from utils.storage_kv import storage_get

_ROOT_SECRET_KEY = "_root_secret_v1"


def _get_or_create_root_secret(storage: Any) -> bytes:
    existing = storage_get(storage, _ROOT_SECRET_KEY)
    if existing:
        return bytes(existing)
    generated = os.urandom(32)
    storage.set(_ROOT_SECRET_KEY, generated)
    # Re-read rather than trust our own write: if a concurrent first-ever
    # invocation raced us, whichever one actually landed last is the one
    # every future call must agree on -- there's nothing encrypted with
    # either key yet, so deferring to storage's own value is free and safe.
    return bytes(storage_get(storage, _ROOT_SECRET_KEY) or generated)


def _derive(root: bytes, purpose: bytes) -> bytes:
    return hashlib.sha256(root + b":" + purpose).digest()


def _fernet(storage: Any) -> Fernet:
    root = _get_or_create_root_secret(storage)
    key = base64.urlsafe_b64encode(_derive(root, b"encryption"))
    return Fernet(key)


def encrypt_json(storage: Any, value: dict[str, Any]) -> bytes:
    return _fernet(storage).encrypt(json.dumps(value).encode("utf-8"))


def decrypt_json(storage: Any, blob: bytes) -> dict[str, Any] | None:
    try:
        raw = _fernet(storage).decrypt(bytes(blob))
    except InvalidToken:
        return None
    return json.loads(raw.decode("utf-8"))


def _link_hmac_key(storage: Any) -> bytes:
    return _derive(_get_or_create_root_secret(storage), b"link-signing")


def sign_link_token(storage: Any, *, user_id: str, toolset: str, ttl_seconds: int) -> str:
    """Build an opaque, tamper-evident token binding one specific
    (already-authenticated-by-Dify) end user to one toolset, for a limited
    time. This is what lets the Endpoint -- which has no Dify session of
    its own -- trust that a form submission really is that user's own,
    without the user ever having to type or see a raw secret anywhere the
    LLM could observe it.
    """
    expires_at = int(time.time()) + ttl_seconds
    payload = f"{user_id}\x1f{toolset}\x1f{expires_at}".encode("utf-8")
    signature = hmac.new(_link_hmac_key(storage), payload, hashlib.sha256).digest()
    token = base64.urlsafe_b64encode(payload + b"\x1e" + signature)
    return token.decode("ascii").rstrip("=")


def verify_link_token(storage: Any, token: str) -> tuple[str, str] | None:
    """Returns (user_id, toolset) if the token is genuine and unexpired,
    else None. Never raises -- a forged/expired/malformed token is just
    treated as "not authenticated", same as a missing one.
    """
    try:
        padded = token + "=" * (-len(token) % 4)
        raw = base64.urlsafe_b64decode(padded.encode("ascii"))
        payload, signature = raw.rsplit(b"\x1e", 1)
        expected = hmac.new(_link_hmac_key(storage), payload, hashlib.sha256).digest()
        if not hmac.compare_digest(signature, expected):
            return None
        user_id, toolset, expires_at = payload.decode("utf-8").split("\x1f")
        if int(expires_at) < int(time.time()):
            return None
        return user_id, toolset
    except Exception:
        return None
