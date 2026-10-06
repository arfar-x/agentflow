"""Reading a kept document's text through `GetEntry`, and where search points."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from kb.application.use_cases.get_entry import GetEntry
from kb.application.use_cases.search_catalog import SearchCatalog
from kb.domain.content import CONTENT_PAGE_CHARS, prepare
from kb.domain.entry import Entry, EntryType, Location
from tests.application.fakes import FakeClock, FakeEntryStore

NOW = datetime(2026, 10, 4, 12, 0, tzinfo=timezone.utc)


def adr(entry_id: str = "gitlab-adrs:7", **overrides) -> Entry:
    base = dict(
        id=entry_id,
        type=EntryType.SPEC,
        title={"en": "ADR-7 storage engine"},
        summary={"en": "Why the catalog uses Postgres."},
        keywords=("storage", "postgres"),
        location=Location(kind="gitlab", ref={"path": "docs/adr-7.md"}, url="https://gitlab/adr-7"),
        source_id="gitlab-adrs",
        source_version="sha-1",
        last_seen_at=NOW,
    )
    return Entry(**{**base, **overrides})


def store_with(entry: Entry, body: str | None, *, version: str = "sha-1", max_bytes: int = 10_000_000):
    store = FakeEntryStore()
    store.upsert(entry)
    if body is not None:
        store.put_content(entry.id, prepare(body, max_bytes), source_version=version)
    return store


def test_pages_chain_through_next_offset_and_rebuild_the_text_exactly():
    # Covers: FR-CNT-08
    body = "".join(f"line {n} — خط {n}\n" for n in range(4000))
    assert len(body) > 2 * CONTENT_PAGE_CHARS
    get = GetEntry(store_with(adr(), body), FakeClock(NOW))

    pieces, offset = [], 0
    while offset is not None:
        page = get.execute("gitlab-adrs:7", offset=offset).content
        assert len(page.text) <= CONTENT_PAGE_CHARS
        assert page.total_chars == len(body)
        pieces.append(page.text)
        offset = page.next_offset
    assert "".join(pieces) == body, "no gaps, no overlaps"
    assert len(pieces) == -(-len(body) // CONTENT_PAGE_CHARS)


def test_a_read_past_the_end_is_an_empty_last_page_and_a_negative_one_starts_over():
    # Covers: FR-CNT-08
    get = GetEntry(store_with(adr(), "short text"), FakeClock(NOW))
    past = get.execute("gitlab-adrs:7", offset=500).content
    assert past.text == "" and past.next_offset is None
    assert get.execute("gitlab-adrs:7", offset=-3).content.text == "short text"


def test_a_truncated_copy_says_so_and_how_big_the_document_was():
    # Covers: FR-CNT-08, FR-CNT-07
    get = GetEntry(store_with(adr(), "q" * 5000, max_bytes=2048), FakeClock(NOW))
    page = get.execute("gitlab-adrs:7").content
    assert page.truncated is True and page.original_bytes == 5000 and page.total_chars == 2048


def test_text_from_another_version_is_never_served():
    # Covers: FR-CNT-11
    get = GetEntry(store_with(adr(source_version="sha-2"), "old text", version="sha-1"), FakeClock(NOW))
    view = get.execute("gitlab-adrs:7")
    assert view.content is None
    assert view.fetch_hint is None, "nothing to read through the catalog; the url is the way"


def test_a_soft_deleted_entry_shows_no_text():
    # Covers: FR-CNT-11
    get = GetEntry(store_with(adr(deleted_at=NOW - timedelta(hours=1)), "text"), FakeClock(NOW))
    view = get.execute("gitlab-adrs:7")
    assert view.hidden is True and view.content is None


def test_an_entry_without_a_live_tool_points_at_the_catalog_when_text_is_kept():
    # Covers: FR-CNT-12
    store = store_with(adr(), "the whole ADR")
    view = GetEntry(store, FakeClock(NOW)).execute("gitlab-adrs:7")
    assert view.fetch_hint == {"tool": "kb_get", "args": {"id": "gitlab-adrs:7"}}

    hit = SearchCatalog(store, FakeClock(NOW)).execute(["storage"]).hits[0]
    assert hit.fetch_hint == {"tool": "kb_get", "args": {"id": "gitlab-adrs:7"}}


def test_a_live_per_user_tool_stays_the_way_to_read():
    # Covers: FR-CNT-12
    page = adr(
        "confluence-eng:9",
        location=Location(kind="confluence", ref={"page_id": "9"}, url="https://wiki/9"),
        source_id="confluence-eng",
    )
    store = store_with(page, "kept text")
    hit = SearchCatalog(store, FakeClock(NOW)).execute(["storage"]).hits[0]
    assert hit.fetch_hint == {"tool": "confluence_get_page", "args": {"page_id": "9"}}
    assert GetEntry(store, FakeClock(NOW)).execute("confluence-eng:9").content is not None


def test_kept_text_changes_neither_which_hits_nor_their_order():
    # Covers: FR-CNT-09
    entries = [
        adr("gitlab-adrs:1", title={"en": "storage engine choice"}),
        adr("gitlab-adrs:2", title={"en": "storage retention"}, keywords=()),
        adr("gitlab-adrs:3", title={"en": "queueing"}, summary={"en": "storage of jobs"}, keywords=()),
    ]
    plain, kept = FakeEntryStore(), FakeEntryStore()
    for entry in entries:
        plain.upsert(entry)
        kept.upsert(entry)
        # Words in the text that appear nowhere in the entry must not match.
        kept.put_content(entry.id, prepare("zebra storage storage storage", 1024), source_version="sha-1")

    def ids(store, query):
        return [h.entry.id for h in SearchCatalog(store, FakeClock(NOW)).execute([query]).hits]

    assert ids(plain, "storage") == ids(kept, "storage")
    assert ids(kept, "zebra") == []
