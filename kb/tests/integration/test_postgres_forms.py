"""Manual records against a real Postgres, and all the way into the catalog."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from kb.application.ports.form_records import FormRecord

psycopg = pytest.importorskip("psycopg")

T0 = datetime(2026, 10, 15, 9, 0, tzinfo=timezone.utc)


@pytest.fixture()
def forms(store):
    from kb.adapters.outbound.postgres_forms import PostgresFormRecords

    return PostgresFormRecords(store.connection)


def record(**changes) -> FormRecord:
    base = dict(
        slug="glossary",
        title={"en": "Glossary", "fa": "واژه‌نامه"},
        body="Terms we use.",
        type="term",
        summary={"en": "Our terms.", "fa": "اصطلاحات ما"},
        tags=("terms",),
        keywords=("glossary",),
        aliases=("dictionary",),
        owner_team="platform",
    )
    return FormRecord(**{**base, **changes})


# Covers: FR-ADM-12
def test_a_record_round_trips_including_persian(forms):
    saved = forms.create(record(), at=T0)
    assert (saved.revision, saved.created_at, saved.updated_at) == (1, T0, T0)
    assert forms.get("glossary") == saved
    assert forms.get("glossary").title["fa"] == "واژه‌نامه"
    assert [r.slug for r in forms.list()] == ["glossary"]


# Covers: FR-ADM-12
def test_an_update_moves_the_revision_and_keeps_the_creation_time(forms):
    forms.create(record(), at=T0)
    changed = forms.update(record(body="More terms."), at=T0 + timedelta(hours=1))
    assert (changed.revision, changed.body) == (2, "More terms.")
    assert (changed.created_at, changed.updated_at) == (T0, T0 + timedelta(hours=1))
    with pytest.raises(LookupError):
        forms.update(record(slug="nope"), at=T0)


# Covers: FR-ADM-12
def test_a_slug_is_unique_and_delete_says_whether_anything_went(forms):
    forms.create(record(), at=T0)
    with pytest.raises(psycopg.errors.UniqueViolation):
        forms.create(record(), at=T0)
    assert forms.delete("glossary") is True
    assert forms.delete("glossary") is False


# Covers: FR-SRC-07
def test_a_record_reaches_the_searchable_catalog_with_its_text_readable_by_page(store, forms):
    from kb.adapters.outbound.form_source import FormKnowledgeSource, PresetSummarizer
    from kb.application.ports.clock import SystemClock
    from kb.application.use_cases.get_entry import GetEntry
    from kb.application.use_cases.reconcile_document import ReconcileDocument
    from kb.application.use_cases.search_catalog import SearchCatalog
    from kb.application.use_cases.sync_source import Mode, SyncSource
    from kb.container import NullSummarizer
    from kb.domain.content import ContentPolicy
    from kb.domain.identity import entry_id_for

    clock = SystemClock(timezone.utc)
    forms.create(record(), at=T0)
    reconcile = ReconcileDocument(store, PresetSummarizer(NullSummarizer()), clock)
    sync = SyncSource(store, reconcile, clock)

    report = sync.execute(FormKnowledgeSource(forms), mode=Mode.FULL, content=ContentPolicy(store=True))
    assert (report.created, report.content_stored, report.rejected) == (1, 1, ())

    found = SearchCatalog(store, clock).execute("dictionary")
    assert [hit.entry.id for hit in found.hits] == [entry_id_for("form", "glossary")]
    view = GetEntry(store, clock, queue_refresh=False).execute(entry_id_for("form", "glossary"))
    assert view.entry.location.kind == "form" and view.content.text == "Terms we use."

    forms.delete("glossary")
    again = sync.execute(FormKnowledgeSource(forms), mode=Mode.FULL, content=ContentPolicy(store=True))
    assert again.missing == 1
    assert SearchCatalog(store, clock).execute("dictionary").hits == ()
