"""Stable entry ids and content hashes.

Both are pure functions of the source document, deliberately: an entry's id
must come out the same on every run, from every mechanism (incremental sync,
nightly reconciliation, a webhook), or the same document would be catalogued
twice under two ids.
"""

from __future__ import annotations

import hashlib
import re
import unicodedata

_UNSAFE = re.compile(r"[^a-z0-9._-]+")


def slug(value: str, *, max_length: int = 80) -> str:
    """A filesystem- and URL-safe fragment of an id.

    Non-ASCII is transliterated away rather than kept, because an id is a
    handle the agent quotes back in tool calls and a log line -- readability in
    ASCII beats fidelity here. The text itself is preserved in `title`.
    """
    decomposed = unicodedata.normalize("NFKD", value)
    ascii_only = decomposed.encode("ascii", "ignore").decode("ascii").lower()
    cleaned = _UNSAFE.sub("-", ascii_only).strip("-.")
    return cleaned[:max_length].strip("-.")


def entry_id_for(source_id: str, external_id: str) -> str:
    """`<source>:<id-in-that-source>`.

    The source id prefix is what makes ids unique across sources -- two wikis
    can both have a page 123456 -- and it is what lets reconciliation ask "every
    entry this source produced" without a join.

    Falls back to a hash when the external id slugs away to nothing (a title in
    a non-Latin script used as the id, for example), so the result is always a
    usable, stable handle.
    """
    if not source_id:
        raise ValueError("source_id is required for a stable entry id")
    if not external_id:
        raise ValueError("external_id is required for a stable entry id")
    tail = slug(external_id) or hashlib.sha256(external_id.encode("utf-8")).hexdigest()[:16]
    return f"{slug(source_id)}:{tail}"


def content_hash(*parts: str | None) -> str:
    """Hash of the source content a summary was generated from.

    Equal hash means no model call and no write, which is the entire cost model
    of sync (see `policies.needs_resummarize`). Parts are separated by a byte
    that cannot appear in text, so concatenation can't make two different
    documents hash alike.
    """
    digest = hashlib.sha256()
    for part in parts:
        digest.update((part or "").encode("utf-8"))
        digest.update(b"\x00")
    return digest.hexdigest()
