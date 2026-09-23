"""The decisions that drive sync and search, in one place.

These are the rules most likely to be argued about later ("why did that entry
disappear?", "why is this being re-summarized every hour?"), so they live as
named functions rather than as conditions scattered through the use cases.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from .entry import Entry
from .merge import Override

#: How long an entry may go unverified before a read triggers a refresh. The
#: config's `lazy_refresh.stale_after` overrides this per deployment.
DEFAULT_STALE_AFTER = timedelta(hours=24)


def is_indexable(entry: Entry, override: Override | None = None) -> bool:
    """Whether the entry belongs in search results at all."""
    if entry.deleted_at is not None:
        return False
    if override is not None and override.exclude:
        return False
    return True


def needs_resummarize(entry: Entry | None, content_hash: str) -> bool:
    """Whether a source document's content has actually changed.

    This is the check that keeps sync cheap: a run over an unchanged source
    makes no model calls and writes nothing. Everything else -- timestamps,
    version numbers, "last modified" filters -- can move without the text
    changing, so the hash of the content is the only sound signal.
    """
    if entry is None:
        return True
    if not entry.content_hash:
        return True
    return entry.content_hash != content_hash


def is_stale(
    entry: Entry,
    *,
    now: datetime,
    stale_after: timedelta = DEFAULT_STALE_AFTER,
    source_version: str | None = None,
) -> bool:
    """Whether this entry should be treated as possibly out of date.

    A stale entry is still returned by search -- it is flagged, not hidden,
    because the agent reads the live document anyway and a stale pointer is far
    better than no pointer. Two ways to be stale: the source is known to have
    moved past the version we recorded, or nobody has verified the entry
    recently enough.
    """
    if source_version is not None and entry.source_version != source_version:
        return True
    if entry.last_seen_at is None:
        return True
    return (now - entry.last_seen_at) > stale_after


def is_missing(entry: Entry) -> bool:
    """Soft-deleted: the source no longer lists it.

    Never a hard delete. A page can vanish from a listing because it was moved,
    because a permission changed, or because the source had a bad day -- and
    all three come back.
    """
    return entry.deleted_at is not None
