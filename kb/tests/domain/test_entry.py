"""An invalid entry must be impossible to construct, not merely detectable --
every path that builds one (a source adapter, a database row, a JSON payload)
goes through this validation."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from kb.domain.entry import Entry, EntryStatus, EntryType, Location, fetch_hint
from kb.domain.errors import describe, describe_for


def make_entry(**overrides) -> Entry:
    base = dict(
        id="confluence-eng:123456",
        type=EntryType.DOC,
        title={"en": "Refund retry policy"},
        location=Location(kind="confluence", ref={"page_id": "123456"}, url="https://wiki/x"),
        source_id="confluence-eng",
    )
    return Entry(**{**base, **overrides})


def test_fetch_hint_names_the_tool_that_reads_the_document():
    # Covers: FR-ENT-09
    assert fetch_hint(Location(kind="confluence", ref={"page_id": "123456"})) == {
        "tool": "confluence_get_page",
        "args": {"page_id": "123456"},
    }
    assert fetch_hint(Location(kind="jira", ref={"issue_key": "PAY-1"})) == {
        "tool": "jira_issue_summary",
        "args": {"issue_key": "PAY-1"},
    }


def test_fetch_hint_is_none_when_no_tool_can_read_the_source():
    # Covers: FR-ENT-09
    # A GitLab file has no tool in this stack today -- the agent gets the url
    # instead, and must never be handed a hint naming a tool that doesn't exist.
    assert fetch_hint(Location(kind="gitlab", ref={"path": "docs/a.md"}, url="https://gitlab/x")) is None
    assert fetch_hint(Location(kind="confluence", ref={})) is None


def test_a_well_formed_entry_is_accepted():
    # Covers: FR-ENT-10
    entry = make_entry()
    assert entry.fetch_hint == {"tool": "confluence_get_page", "args": {"page_id": "123456"}}
    assert entry.audience == ("all",)


def test_entries_are_immutable():
    # Covers: FR-ENT-06
    entry = make_entry()
    with pytest.raises(ValidationError):
        entry.title = {"en": "Changed"}


def test_entry_without_usable_title_text_is_rejected():
    # Covers: FR-ENT-03
    with pytest.raises(ValidationError):
        make_entry(title={})
    with pytest.raises(ValidationError):
        make_entry(title={"en": "   "})


def test_confluence_entry_without_a_page_id_is_rejected():
    # Covers: FR-ENT-02
    # Findable but unreadable is the one outcome the catalog exists to prevent:
    # the agent would be told the answer exists with no way to reach it.
    with pytest.raises(ValidationError) as excinfo:
        make_entry(location=Location(kind="confluence", url="https://wiki/x"))
    assert any("page_id" in message for message in describe(excinfo.value))


def test_a_source_with_no_fetch_tool_needs_a_url():
    # Covers: FR-ENT-02
    make_entry(location=Location(kind="gitlab", url="https://gitlab/x"))  # fine
    with pytest.raises(ValidationError):
        make_entry(location=Location(kind="gitlab"))


def test_ids_must_be_present_and_free_of_whitespace():
    # Covers: FR-ENT-04
    with pytest.raises(ValidationError):
        make_entry(id="")
    with pytest.raises(ValidationError):
        make_entry(id="has space")
    with pytest.raises(ValidationError):
        make_entry(source_id="")


def test_empty_audience_is_rejected():
    # Covers: FR-ENT-10
    with pytest.raises(ValidationError):
        make_entry(audience=())


def test_unknown_type_or_status_is_rejected():
    # Covers: FR-ENT-06
    with pytest.raises(ValidationError):
        make_entry(type="diagram")
    with pytest.raises(ValidationError):
        make_entry(status="shipped")
    assert make_entry(status=EntryStatus.APPROVED).status is EntryStatus.APPROVED


def test_unexpected_fields_are_rejected_rather_than_silently_dropped():
    # Covers: FR-ENT-06, NFR-ARC-03
    # A source adapter that renames a field should fail loudly, not produce
    # entries missing the data it thought it was setting.
    with pytest.raises(ValidationError):
        make_entry(owner="payments")


def test_one_bad_document_produces_one_complete_report():
    # Covers: FR-ENT-06
    with pytest.raises(ValidationError) as excinfo:
        make_entry(id="", title={}, audience=())
    problems = describe(excinfo.value)
    assert len(problems) == 3
    line = describe_for("confluence-eng:123456", excinfo.value)
    assert line.startswith("confluence-eng:123456: ")
    assert "title" in line and "audience" in line


def test_entries_round_trip_through_json():
    # Covers: FR-ENT-07, NFR-ARC-03
    # The Postgres adapter and the MCP tool both move entries as JSON; a type
    # that survives the trip is what lets them share one schema.
    entry = make_entry(keywords=("refund", "بازپرداخت"))
    assert Entry.model_validate_json(entry.model_dump_json()) == entry


def test_an_entry_has_nowhere_to_put_a_document_body():
    # Covers: FR-ENT-01
    # The catalog points at documents, it does not hold them: no body field
    # exists, and extra="forbid" means one cannot be smuggled in either.
    assert "body" not in Entry.model_fields
    assert not {"body", "content", "text"} & set(Entry.model_fields)
    with pytest.raises(ValidationError):
        make_entry(body="the whole page")
