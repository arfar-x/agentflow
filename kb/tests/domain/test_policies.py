from __future__ import annotations

from datetime import datetime, timedelta, timezone

from kb.domain.entry import Entry, EntryType, Location
from kb.domain.policies import (
    DEFAULT_STALE_AFTER,
    is_indexable,
    is_missing,
    is_stale,
    needs_resummarize,
)

NOW = datetime(2026, 9, 24, 12, 0, tzinfo=timezone.utc)


def make_entry(**overrides) -> Entry:
    base = dict(
        id="e1",
        type=EntryType.DOC,
        title={"en": "t"},
        location=Location(kind="confluence", ref={"page_id": "1"}),
        source_id="confluence-eng",
        content_hash="hash-v1",
        source_version="42",
        last_seen_at=NOW - timedelta(hours=1),
    )
    return Entry(**{**base, **overrides})


def test_unchanged_content_needs_no_model_call():
    # Covers: FR-REC-02, NFR-PRF-01
    # The whole cost model depends on this: an hourly sync over an unchanged
    # source must not re-summarize anything.
    assert needs_resummarize(make_entry(), "hash-v1") is False
    assert needs_resummarize(make_entry(), "hash-v2") is True


def test_a_document_the_catalog_has_never_seen_is_always_summarized():
    # Covers: FR-REC-02
    assert needs_resummarize(None, "hash-v1") is True
    assert needs_resummarize(make_entry(content_hash=""), "hash-v1") is True


def test_a_source_version_ahead_of_ours_is_stale():
    # Covers: FR-SRCH-05
    entry = make_entry()
    assert is_stale(entry, now=NOW, source_version="42") is False
    assert is_stale(entry, now=NOW, source_version="43") is True


def test_an_entry_nobody_has_verified_recently_is_stale():
    # Covers: FR-SRCH-05
    fresh = make_entry(last_seen_at=NOW - timedelta(hours=1))
    old = make_entry(last_seen_at=NOW - DEFAULT_STALE_AFTER - timedelta(minutes=1))
    assert is_stale(fresh, now=NOW) is False
    assert is_stale(old, now=NOW) is True
    assert is_stale(make_entry(last_seen_at=None), now=NOW) is True


def test_stale_after_is_configurable_per_deployment():
    # Covers: FR-SRCH-05
    entry = make_entry(last_seen_at=NOW - timedelta(hours=2))
    assert is_stale(entry, now=NOW, stale_after=timedelta(hours=1)) is True
    assert is_stale(entry, now=NOW, stale_after=timedelta(hours=6)) is False


def test_soft_deleted_entries_leave_search_but_keep_their_row():
    # Covers: FR-REC-05, FR-SRCH-04
    deleted = make_entry(deleted_at=NOW)
    assert is_missing(deleted) is True
    assert is_indexable(deleted) is False
    assert is_indexable(make_entry()) is True
