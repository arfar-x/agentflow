from __future__ import annotations

import pytest

from kb.domain.entry import Entry, EntryStatus, EntryType, Location
from kb.domain.merge import Override, apply_override
from kb.domain.policies import is_indexable


def make_entry(**overrides) -> Entry:
    base = dict(
        id="e1",
        type=EntryType.DOC,
        title={"en": "Generated title", "fa": "عنوان"},
        summary={"en": "Generated summary", "fa": "خلاصه"},
        keywords=("refund", "retry"),
        tags=("payments",),
        owner_team="payments",
        location=Location(kind="confluence", ref={"page_id": "1"}),
        source_id="confluence-eng",
    )
    return Entry(**{**base, **overrides})


def test_no_override_returns_the_entry_unchanged():
    # Covers: FR-OVR-03
    entry = make_entry()
    assert apply_override(entry, None) is entry


def test_overriding_one_language_leaves_the_other_generated():
    # Covers: FR-OVR-01
    merged = apply_override(make_entry(), Override(entry_id="e1", summary={"en": "Mine"}))
    assert merged.summary["en"] == "Mine"
    assert merged.summary["fa"] == "خلاصه"


def test_list_fields_replace_instead_of_merging():
    # Covers: FR-OVR-02
    # Merging would make removing a wrong tag impossible.
    merged = apply_override(make_entry(), Override(entry_id="e1", tags=("billing",)))
    assert merged.tags == ("billing",)


def test_empty_list_override_clears_the_field():
    # Covers: FR-OVR-02
    merged = apply_override(make_entry(), Override(entry_id="e1", keywords=()))
    assert merged.keywords == ()


def test_unset_scalar_means_no_opinion_not_clear_it():
    # Covers: FR-OVR-03
    merged = apply_override(make_entry(), Override(entry_id="e1", status=EntryStatus.APPROVED))
    assert merged.owner_team == "payments"
    assert merged.status is EntryStatus.APPROVED


def test_exclude_keeps_the_entry_out_of_search_without_deleting_anything():
    # Covers: FR-OVR-04
    entry = make_entry()
    override = Override(entry_id="e1", exclude=True, note="duplicate of e2")
    assert is_indexable(entry, override) is False
    assert is_indexable(entry, None) is True


def test_applying_an_override_to_the_wrong_entry_is_a_programming_error():
    # Covers: FR-OVR-05
    with pytest.raises(ValueError):
        apply_override(make_entry(), Override(entry_id="somewhere-else"))


def test_the_original_entry_is_never_mutated():
    # Covers: FR-ENT-06
    entry = make_entry()
    apply_override(entry, Override(entry_id="e1", title={"en": "Mine"}, tags=()))
    assert entry.title["en"] == "Generated title"
    assert entry.tags == ("payments",)
