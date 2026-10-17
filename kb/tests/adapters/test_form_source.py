"""The `form` source: manual records going through the one funnel."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from kb.adapters.outbound.form_source import FormKnowledgeSource, PresetSummarizer, to_document
from kb.application.ports.form_records import FormRecord
from kb.application.use_cases.reconcile_document import ReconcileDocument
from kb.application.use_cases.sync_source import Mode, SyncSource
from kb.domain.content import ContentPolicy
from kb.domain.entry import EntryType
from kb.domain.identity import entry_id_for
from tests.application.fakes import FakeClock, FakeEntryStore, FakeFormRecords, FakeSummarizer

NOW = datetime(2026, 10, 15, 9, 0, tzinfo=timezone.utc)
KEEP = ContentPolicy(store=True)


def record(**changes) -> FormRecord:
    base = dict(
        slug="release-process",
        title={"en": "Release process", "fa": "فرایند انتشار"},
        body="Releases go out on Tuesdays.",
        type=EntryType.SPEC,
        summary={"en": "How releases ship.", "fa": "نحوه انتشار"},
        keywords=("release",),
        aliases=("deploy day",),
        owner_team="platform",
        revision=1,
    )
    return FormRecord(**{**base, **changes})


def wire():
    records, store, clock = FakeFormRecords(), FakeEntryStore(), FakeClock(NOW)
    model = FakeSummarizer()
    reconcile = ReconcileDocument(store, PresetSummarizer(model), clock)
    return records, store, model, FormKnowledgeSource(records), SyncSource(store, reconcile, clock), clock


def publish(records, source, sync):
    return sync.execute(source, mode=Mode.FULL, content=KEEP)


def test_a_record_becomes_a_document_a_reader_can_follow_without_a_url():
    # Covers: FR-SRC-07
    document = to_document(record())
    assert (document.source_id, document.external_id) == ("form", "release-process")
    assert document.location.kind == "form" and document.location.url is None
    assert document.location.ref == {"record": "release-process"}
    assert document.version == "1"


def test_a_published_record_carries_the_wording_the_operator_approved_and_keeps_its_text():
    # Covers: FR-SRC-07, FR-ADM-13
    records, store, model, source, sync, _ = wire()
    records.create(record(), at=NOW)
    report = publish(records, source, sync)
    assert (report.created, report.content_stored, report.rejected) == (1, 1, ())
    entry = store.get(entry_id_for("form", "release-process"))
    assert entry.summary == {"en": "How releases ship.", "fa": "نحوه انتشار"}
    assert entry.title["fa"] == "فرایند انتشار"
    assert entry.aliases == ("deploy day",) and entry.keywords == ("release",)
    assert model.calls == []  # an approved draft is not summarized again
    assert store.read_content(entry.id, offset=0, limit=100).text == "Releases go out on Tuesdays."


def test_saving_only_a_new_summary_still_updates_the_entry_with_no_model_call():
    # Covers: FR-ADM-13
    records, store, model, source, sync, _ = wire()
    records.create(record(), at=NOW)
    publish(records, source, sync)
    records.update(record(summary={"en": "Releases ship on Tuesdays."}), at=NOW)  # revision 2
    report = publish(records, source, sync)
    assert (report.updated, report.unchanged) == (1, 0)
    assert store.get(entry_id_for("form", "release-process")).summary == {"en": "Releases ship on Tuesdays."}
    assert model.calls == []


def test_nothing_changed_means_nothing_rewritten():
    # Covers: FR-SRC-07
    records, _, _, source, sync, _ = wire()
    records.create(record(), at=NOW)
    publish(records, source, sync)
    report = publish(records, source, sync)
    assert (report.unchanged, report.updated, report.created) == (1, 0, 0)


def test_a_deleted_record_is_soft_deleted_by_the_next_full_pass():
    # Covers: FR-SRC-07
    records, store, _, source, sync, _ = wire()
    records.create(record(), at=NOW)
    publish(records, source, sync)
    records.delete("release-process")
    report = publish(records, source, sync)
    assert report.missing == 1
    assert store.get(entry_id_for("form", "release-process")).deleted_at is not None


def test_a_record_without_an_approved_summary_is_still_catalogued_under_its_title():
    # Covers: FR-SRC-07
    records, store, _, source, sync, _ = wire()
    records.create(record(summary={}, keywords=(), aliases=()), at=NOW)
    publish(records, source, sync)
    entry = store.get(entry_id_for("form", "release-process"))
    assert entry.summary == {} and entry.title["en"] == "Release process"


def test_other_sources_documents_still_reach_the_real_summarizer():
    # Covers: FR-SRC-07
    from kb.application.ports.knowledge_source import SourceDocument
    from kb.domain.entry import Location

    model = FakeSummarizer()
    other = SourceDocument(
        source_id="wiki", external_id="1", title="A page", body="text",
        location=Location(kind="confluence", ref={"page": "1"}, url="https://wiki/1"),
        extra={"draft": '{"title": {"en": "hijack"}}'},  # only `form` honours this
    )
    assert PresetSummarizer(model).draft(other).title == {"en": "A page"}
    assert model.calls == ["1"]


def test_fetch_finds_one_record_and_only_that_one():
    # Covers: FR-SRC-07
    records, _, _, source, _, _ = wire()
    records.create(record(), at=NOW)
    assert source.fetch("release-process").title == "Release process"
    assert source.fetch("nope") is None
    assert source.checkpoint() is None

