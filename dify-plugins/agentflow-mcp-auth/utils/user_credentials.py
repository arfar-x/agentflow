"""Per-user, per-toolset credential storage on top of ``secret_store``.

One KV entry per (user, toolset), storing exactly the env-var names
agent-skills itself declares (see ``constants.TOOLSET_FIELDS``), encrypted.
The stored dict's keys are used verbatim as the ``X-Agent-Skills-Env-<VAR>``
header names when a call is bridged -- see ``tools/mcp_call_tool.py``.
"""

from __future__ import annotations

import hashlib
from typing import Any

from utils import secret_store
from utils.constants import TOOLSET_FIELDS
from utils.storage_kv import storage_get


def _storage_key(user_id: str, toolset: str) -> str:
    # Hash the user id rather than store it verbatim in the key -- it's an
    # opaque Dify EndUser id already, not a real name/email, but there's no
    # reason for a plugin storage key to hold even that much directly, and
    # hashing sidesteps any doubt about which characters a KV backend's key
    # charset actually allows.
    digest = hashlib.sha256(user_id.encode("utf-8")).hexdigest()
    return f"cred:{toolset}:{digest}"


def save(storage: Any, *, user_id: str, toolset: str, values: dict[str, str]) -> None:
    fields = TOOLSET_FIELDS[toolset]
    cleaned: dict[str, str] = {}
    for env_var, _label, _sensitive, required in fields:
        value = (values.get(env_var) or "").strip()
        if required and not value:
            raise ValueError(f"{env_var} is required")
        if value:
            cleaned[env_var] = value
    blob = secret_store.encrypt_json(storage, cleaned)
    storage.set(_storage_key(user_id, toolset), blob)


def load(storage: Any, *, user_id: str, toolset: str) -> dict[str, str] | None:
    blob = storage_get(storage, _storage_key(user_id, toolset))
    if not blob:
        return None
    return secret_store.decrypt_json(storage, blob)


def clear(storage: Any, *, user_id: str, toolset: str) -> None:
    try:
        storage.delete(_storage_key(user_id, toolset))
    except Exception:
        # Deleting a credential that was never set (or was already cleared)
        # should be a silent no-op from the caller's point of view.
        pass


def has_any(storage: Any, *, user_id: str, toolset: str) -> bool:
    return load(storage, user_id=user_id, toolset=toolset) is not None
