"""Previewing and applying a change to the source configuration.

The real YAML editor behind in-memory file and history fakes: what is under test
is the flow -- nothing written without a confirmed preview of the current file,
nothing invalid ever written, every change recorded -- and the words an operator
is shown before they agree.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from kb.adapters.outbound.source_factory import BUILDABLE_KINDS, missing_credentials
from kb.adapters.outbound.sources_editor import RuamelSourceEditor
from kb.application.ports.source_files import CreateSource, DeleteSource, SetApproved, UpdateSource
from kb.application.use_cases.edit_sources import (
    Conflict,
    EditSources,
    InvalidChange,
    NotConfirmed,
    fingerprint,
)
from tests.application.fakes import FakeClock, FakeSourceHistory, FakeSourcesFile

NOW = datetime(2026, 10, 14, 12, 0, tzinfo=timezone.utc)

TEXT = """\
approved: true
sources:
  # The wiki.
  - id: confluence-eng
    kind: confluence
    enabled: true
    spaces: [ENG]
  - id: jira-product
    kind: jira
    enabled: false
    jql: 'project = PAY'
"""


@pytest.fixture()
def world():
    file = FakeSourcesFile(TEXT)
    history = FakeSourceHistory()
    credentials = {"jira-product": {"KB_JIRA_BASE_URL": True, "KB_JIRA_PAT": False, "KB_JIRA_USERNAME": False,
                                    "KB_JIRA_PASSWORD": False, "JIRA_BASE_URL": False, "JIRA_PAT": False}}
    editor = EditSources(
        file, RuamelSourceEditor(), history, FakeClock(NOW),
        credentials=lambda: credentials,
        buildable_kinds=BUILDABLE_KINDS,
        missing_credentials=missing_credentials,
    )
    return editor, file, history


def texts(preview):
    return " ".join(w.text for w in preview.warnings)


def levels(preview):
    return {w.level for w in preview.warnings}


# -- two steps (FR-CFG-10) ---------------------------------------------------------


def test_a_preview_says_what_would_change_and_writes_nothing(world):
    # Covers: FR-CFG-10
    editor, file, history = world
    preview = editor.preview(UpdateSource(id="confluence-eng", config={"spaces": ["ENG", "OPS"]}))

    assert preview.changed and "+    spaces: [ENG, OPS]" in preview.diff and "-    spaces: [ENG]" in preview.diff
    assert preview.etag == file.snapshot().etag
    assert file.text == TEXT and file.writes == 0 and history.rows == []


def test_nothing_is_written_without_an_explicit_confirmation(world):
    # Covers: FR-CFG-10
    editor, file, _ = world
    change = UpdateSource(id="confluence-eng", config={"enabled": False})
    etag = editor.preview(change).etag
    with pytest.raises(NotConfirmed):
        editor.apply(change, etag=etag, actor="admin", confirmed=False)
    assert file.text == TEXT


def test_an_apply_writes_exactly_what_the_preview_showed_and_records_it(world):
    # Covers: FR-CFG-10, FR-CFG-12
    editor, file, history = world
    change = UpdateSource(id="confluence-eng", config={"spaces": ["ENG", "OPS"]})
    preview = editor.preview(change)

    applied = editor.apply(change, etag=preview.etag, actor="admin (10.0.0.5)", confirmed=True)

    assert applied.changed and file.text != TEXT and file.writes == 1
    assert applied.etag == file.snapshot().etag
    [row] = history.rows
    assert (row["action"], row["source_id"], row["actor"]) == ("update", "confluence-eng", "admin (10.0.0.5)")
    assert (row["before_text"], row["after_text"]) == (TEXT, file.text)
    assert row["diff"] == preview.diff and row["after_hash"] == fingerprint(file.text)


def test_a_file_that_changed_since_the_preview_is_refused_not_overwritten(world):
    # Covers: FR-CFG-10
    editor, file, history = world
    change = UpdateSource(id="confluence-eng", config={"enabled": False})
    etag = editor.preview(change).etag
    file.text = TEXT + "# someone edited this by hand\n"  # a hand edit in between

    with pytest.raises(Conflict, match="changed after you previewed"):
        editor.apply(change, etag=etag, actor="admin", confirmed=True)
    assert file.text.endswith("by hand\n") and file.writes == 0 and history.rows == []


def test_a_change_that_changes_nothing_writes_and_records_nothing(world):
    # Covers: FR-CFG-10
    editor, file, history = world
    change = SetApproved(value=True)
    preview = editor.preview(change)
    assert not preview.changed and preview.diff == ""
    applied = editor.apply(change, etag=preview.etag, actor="admin", confirmed=True)
    assert not applied.changed and file.writes == 0 and history.rows == []


@pytest.mark.parametrize("change, message", [
    (CreateSource(config={"id": "confluence-eng", "kind": "jira", "jql": "x"}), "already a source"),
    (CreateSource(config={"id": "form", "kind": "jira", "jql": "x"}), "reserved"),
    (CreateSource(config={"id": "has space", "kind": "jira", "jql": "x"}), "id"),
    (CreateSource(config={"id": "n", "kind": "http_api", "url": "https://x"}), "not built"),
    (CreateSource(config={"id": "n", "kind": "jira"}), "jql"),
    (CreateSource(config={"id": "n", "kind": "gitlab", "base_url": "https://g", "token_env": "glpat-abc123"}), "token_env"),
    (CreateSource(config={"id": "n", "kind": "confluence", "spaces": ["A"], "surprise": 1}), "surprise"),
    (CreateSource(config={"id": "n", "kind": "confluence", "raw_content_max_bytes": 10}), "raw_content_max_bytes"),
    (UpdateSource(id="nope", config={"enabled": True}), "no source"),
    (UpdateSource(id="confluence-eng", config={"kind": "jira"}), "kind cannot be changed"),
    (UpdateSource(id="confluence-eng", config={"id": "other"}), "id cannot be changed"),
    (UpdateSource(id="jira-product", config={"jql": ""}), "jql"),
    (DeleteSource(id="nope"), "no source"),
])
def test_nothing_invalid_can_even_be_previewed_let_alone_saved(world, change, message):
    # Covers: FR-CFG-10, FR-CFG-11
    editor, file, history = world
    with pytest.raises(InvalidChange, match=message):
        editor.preview(change)
    with pytest.raises(InvalidChange):
        editor.apply(change, etag=file.snapshot().etag, actor="admin", confirmed=True)
    assert file.text == TEXT and file.writes == 0 and history.rows == []


def test_a_file_that_cannot_be_read_is_not_edited_from_here(world):
    # Covers: FR-CFG-10
    editor, file, _ = world
    file.text = "sources: [\n"
    with pytest.raises(InvalidChange, match="cannot be read"):
        editor.preview(SetApproved(value=False))


def test_a_secret_pasted_into_a_credential_field_is_refused(world):
    # Covers: FR-CFG-11
    editor, file, _ = world
    with pytest.raises(InvalidChange, match="token_env"):
        editor.preview(CreateSource(config={
            "id": "gl", "kind": "gitlab", "base_url": "https://g", "token_env": "glpat-xxxxxxxxxxxxxxxxxxxx"}))
    assert "glpat" not in file.text


# -- the history and the copy (FR-CFG-12) -------------------------------------------------


def test_a_source_made_in_the_console_is_marked_as_made_there(world):
    # Covers: FR-CFG-12
    editor, file, history = world
    change = CreateSource(config={"id": "wiki-ops", "kind": "confluence", "spaces": ["OPS"]})
    editor.apply(change, etag=editor.preview(change).etag, actor="admin", confirmed=True)
    editor.sync_mirror()
    assert history.origins()["wiki-ops"] == "ui"
    assert history.origins()["confluence-eng"] == "yaml", "the ones the file declared stay that"


def test_a_change_made_to_the_file_by_hand_is_noticed_once_and_recorded(world):
    # Covers: FR-CFG-12
    editor, file, history = world
    editor.sync_mirror()  # the first sight of the file
    assert [r["action"] for r in history.rows] == ["file"] and set(history.origins()) == {"confluence-eng", "jira-product"}

    editor.sync_mirror()
    assert len(history.rows) == 1, "nothing changed, nothing recorded"

    file.text = TEXT.replace("spaces: [ENG]", "spaces: [ENG, BIZ]")
    editor.sync_mirror()
    assert [r["action"] for r in history.rows] == ["file", "file"]
    assert "+    spaces: [ENG, BIZ]" in history.rows[-1]["diff"]
    assert history.rows[-1]["actor"] == "file"


def test_a_half_edited_file_is_not_recorded_and_the_copy_keeps_its_last_good_state(world):
    # Covers: FR-CFG-12
    editor, file, history = world
    editor.sync_mirror()
    file.text = "sources: [\n"
    editor.sync_mirror()
    assert len(history.rows) == 1 and set(history.origins()) == {"confluence-eng", "jira-product"}


def test_the_copy_never_decides_anything_the_file_does_not(world):
    # Covers: FR-CFG-12
    editor, file, history = world
    editor.sync_mirror()
    history._origins["ghost"] = "ui"  # the copy believes in a source the file lacks
    file.text = TEXT.replace("approved: true", "approved: false")
    editor.sync_mirror()
    assert "ghost" not in history.origins(), "the file wins"
    config, error, _ = editor.current()
    assert error is None and config.approved is False


def test_the_history_is_listed_newest_first(world):
    # Covers: FR-CFG-12
    editor, _, _ = world
    for value in (False, True):
        change = SetApproved(value=value)
        editor.apply(change, etag=editor.preview(change).etag, actor="admin", confirmed=True)
    assert [(r.action, r.actor) for r in editor.revisions()] == [("settings", "admin"), ("settings", "admin")]
    assert editor.revisions()[0].id > editor.revisions()[1].id


# -- what an operator is told before agreeing (FR-ADM-11) -------------------------------------


def test_turning_on_stored_text_warns_that_every_user_will_read_it(world):
    # Covers: FR-ADM-11
    editor, _, _ = world
    preview = editor.preview(UpdateSource(id="confluence-eng", config={"store_raw_content": True}))
    assert "danger" in levels(preview)
    assert "every user of the catalog" in texts(preview) and "C6" in texts(preview)


def test_turning_off_stored_text_says_the_next_sync_deletes_it(world):
    # Covers: FR-ADM-11
    editor, file, _ = world
    file.text = TEXT.replace("spaces: [ENG]", "spaces: [ENG]\n    store_raw_content: true")
    preview = editor.preview(UpdateSource(id="confluence-eng", config={"store_raw_content": False}))
    assert "deletes all the text" in texts(preview)


def test_deleting_a_source_warns_that_its_entries_stay_searchable(world):
    # Covers: FR-ADM-11
    editor, _, _ = world
    preview = editor.preview(DeleteSource(id="jira-product"))
    assert "danger" in levels(preview)
    assert "stay in the catalog and stay searchable" in texts(preview)
    assert "-  - id: jira-product" in preview.diff


def test_disabling_and_enabling_are_each_explained(world):
    # Covers: FR-ADM-11
    editor, _, _ = world
    assert "stops every sync" in texts(editor.preview(UpdateSource(id="confluence-eng", config={"enabled": False})))
    enabling = editor.preview(UpdateSource(id="jira-product", config={"enabled": True}))
    assert "KB_JIRA_PAT" in texts(enabling), "the scheduler was reported unable to authenticate it"


def test_a_new_source_is_described_as_disabled_unless_enabled(world):
    # Covers: FR-ADM-11
    editor, _, _ = world
    quiet = editor.preview(CreateSource(config={"id": "n", "kind": "jira", "jql": "x"}))
    assert "disabled: nothing is synced" in texts(quiet) and "dry run" in texts(quiet)
    live = editor.preview(CreateSource(config={"id": "n", "kind": "jira", "jql": "x", "enabled": True}))
    assert "synced on its next tick" in texts(live)


def test_switching_syncing_off_is_a_danger_and_says_how_to_undo_it(world):
    # Covers: FR-ADM-11
    editor, _, _ = world
    off = editor.preview(SetApproved(value=False))
    assert "danger" in levels(off) and "switched off for every source" in texts(off)
    on = editor.preview(SetApproved(value=True))
    assert not on.changed


def test_an_environment_override_of_the_switch_is_called_out(world, monkeypatch):
    # Covers: FR-ADM-11
    editor, _, _ = world
    editor._env = {"KB_SOURCES_APPROVED": "false"}
    assert "overrides this file" in texts(editor.preview(SetApproved(value=True)))


def test_a_very_frequent_cadence_is_flagged(world):
    # Covers: FR-ADM-11
    editor, _, _ = world
    preview = editor.preview(UpdateSource(id="confluence-eng", config={"mechanisms": {"incremental": {"enabled": True, "every": "20s"}}}))
    assert "more than once a minute" in texts(preview)


def test_a_cadence_change_is_an_ordinary_edit_of_the_sources_mechanisms(world):
    # Covers: FR-ADM-11
    editor, file, _ = world
    change = UpdateSource(id="jira-product", config={"mechanisms": {"incremental": {"every": "1h"}, "full_scrape": {"at": "02:30"}}})
    editor.apply(change, etag=editor.preview(change).etag, actor="admin", confirmed=True)
    config, _, _ = editor.current()
    merged = config.mechanisms_for(next(s for s in config.sources if s.id == "jira-product"))
    assert (merged.incremental.every, merged.full_scrape.at) == ("1h", "02:30")


def test_the_file_never_gains_a_resolved_value(world):
    # Covers: FR-CFG-11
    editor, file, _ = world
    editor._env = {"JIRA_BASE_URL": "https://jira.corp", "JIRA_PAT": "pat-SECRET"}
    change = UpdateSource(id="jira-product", config={"enabled": True})
    editor.apply(change, etag=editor.preview(change).etag, actor="admin", confirmed=True)
    assert "pat-SECRET" not in file.text and "jira.corp" not in file.text
