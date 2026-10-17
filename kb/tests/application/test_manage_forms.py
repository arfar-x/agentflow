"""Manual records: writing them, publishing them, drafting their summaries."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from kb.application.ports.sync_queue import RequestKind
from kb.application.use_cases.draft_summary import DraftSummary, NothingToSummarize
from kb.application.use_cases.manage_forms import (
    FormExists,
    FormInvalid,
    FormNotFound,
    ManageForms,
    publication_state,
)
from kb.application.use_cases.request_sync import RequestSync
from kb.application.ports.summarizer import SummaryDraft
from kb.domain.entry import EntryType
from tests.application.fakes import (
    FakeClock,
    FakeEntryStore,
    FakeFormRecords,
    FakeSummarizer,
    FakeSyncQueue,
)

NOW = datetime(2026, 10, 15, 9, 0, tzinfo=timezone.utc)

RECORD = {
    "slug": "release-process",
    "title": {"en": "Release process", "fa": "فرایند انتشار"},
    "body": "Releases go out on Tuesdays.",
    "type": "spec",
    "tags": ["release", "release", " "],
    "owner_team": "platform",
}


def wire():
    records, queue, clock = FakeFormRecords(), FakeSyncQueue(), FakeClock(NOW)
    return records, queue, ManageForms(records, RequestSync(queue, clock), clock)


def test_saving_a_record_stores_it_and_queues_the_sync_that_publishes_it():
    # Covers: FR-ADM-12
    records, queue, manage = wire()
    saved = manage.create(RECORD)
    assert records.get("release-process") == saved
    assert saved.tags == ("release",)  # tidied: no repeats, no blanks
    waiting = queue.list_requests(limit=10)
    assert [(r.kind, r.source_id, r.mode) for r in waiting] == [(RequestKind.SYNC, "form", "full")]


def test_saving_twice_before_the_scheduler_looks_queues_one_sync():
    # Covers: FR-ADM-12
    _, queue, manage = wire()
    manage.create(RECORD)
    manage.update("release-process", {**RECORD, "body": "Releases go out on Wednesdays."})
    assert len(queue.list_requests(limit=10)) == 1


def test_a_record_needs_a_title_a_body_and_a_well_formed_slug():
    # Covers: FR-ADM-12
    _, _, manage = wire()
    with pytest.raises(FormInvalid, match="title"):
        manage.create({**RECORD, "title": {"en": "  "}})
    with pytest.raises(FormInvalid, match="body"):
        manage.create({**RECORD, "body": ""})
    with pytest.raises(FormInvalid, match="slug"):
        manage.create({**RECORD, "slug": "Not A Slug"})
    with pytest.raises(FormInvalid, match="language"):
        manage.create({**RECORD, "title": {"English": "x"}})
    with pytest.raises(FormInvalid, match="type"):
        manage.create({**RECORD, "type": "poem"})


def test_two_records_cannot_share_a_slug_and_an_unknown_one_cannot_be_changed():
    # Covers: FR-ADM-12
    _, _, manage = wire()
    manage.create(RECORD)
    with pytest.raises(FormExists):
        manage.create(RECORD)
    with pytest.raises(FormNotFound):
        manage.update("nope", RECORD)
    with pytest.raises(FormNotFound):
        manage.delete("nope")


def test_an_edit_moves_the_revision_but_never_the_slug():
    # Covers: FR-ADM-12
    records, _, manage = wire()
    manage.create(RECORD)
    edited = manage.update("release-process", {**RECORD, "slug": "something-else", "body": "New."})
    assert (edited.slug, edited.revision, edited.body) == ("release-process", 2, "New.")
    assert records.get("something-else") is None


def test_deleting_a_record_queues_the_full_pass_that_retires_its_entry():
    # Covers: FR-ADM-12
    records, queue, manage = wire()
    manage.create(RECORD)
    queue.claim_next(at=NOW)  # the scheduler picks up the first
    manage.delete("release-process")
    assert records.get("release-process") is None
    assert [r.source_id for r in queue.list_requests(limit=10)][0] == "form"
    assert len(queue.list_requests(limit=10)) == 2


def test_a_record_is_pending_until_the_catalog_carries_its_revision():
    # Covers: FR-ADM-12
    from kb.application.ports.form_records import FormRecord
    from kb.domain.entry import Entry, Location
    from kb.domain.identity import entry_id_for

    store = FakeEntryStore()
    record = FormRecord.model_validate({**RECORD, "revision": 2})
    assert publication_state(record, store) == "pending"

    def entry(version: str) -> Entry:
        return Entry(
            id=entry_id_for("form", record.slug), type=EntryType.SPEC, title={"en": "x"},
            location=Location(kind="form", ref={"record": record.slug}), source_id="form",
            external_id=record.slug, source_version=version, content_hash="h",
            created_at=NOW, updated_at=NOW, last_seen_at=NOW,
        )

    store.upsert(entry("1"))
    assert publication_state(record, store) == "pending"
    store.upsert(entry("2"))
    assert publication_state(record, store) == "published"


def test_a_draft_summary_is_returned_and_stores_nothing():
    # Covers: FR-ADM-13
    records, queue, _ = wire()
    drafted = SummaryDraft(summary={"en": "How releases ship."}, keywords=("release",))
    summarizer = FakeSummarizer({"draft": drafted})
    result = DraftSummary(summarizer).execute(
        title={"en": "Release process"}, body="Releases go out on Tuesdays.", entry_type=EntryType.SPEC
    )
    assert result == drafted
    assert records.list() == [] and queue.list_requests(limit=10) == []


def test_a_draft_needs_text_to_describe():
    # Covers: FR-ADM-13
    with pytest.raises(NothingToSummarize):
        DraftSummary(FakeSummarizer()).execute(title={"en": "x"}, body="   ")
