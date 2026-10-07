"""Thin wrapper around ``self.session.storage`` (a
``dify_plugin.invocations.storage.StorageInvocation``).

Its real ``get(key)`` raises ``StorageInvocationError`` for a missing key
rather than returning ``None`` -- confirmed by reading
``dify_plugin/invocations/storage.py`` directly (the public docs say
otherwise). Every caller in this plugin wants "missing key" and "storage
backend genuinely failed" to look the same (both mean: treat as absent,
generate/store fresh), so that translation lives here once instead of a
try/except copy-pasted at every call site.
"""

from __future__ import annotations

from typing import Any


def storage_get(storage: Any, key: str) -> bytes | None:
    try:
        if not storage.exist(key):
            return None
        return storage.get(key)
    except Exception:
        return None
