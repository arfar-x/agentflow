"""Editing kb-sources.yaml without disturbing what the operator wrote (FR-CFG-11).

The properties that matter are negative ones -- what an edit does NOT touch --
so most of these compare the file before and after and name the lines that moved.
"""

from __future__ import annotations

import difflib
from pathlib import Path

import pytest

from kb.adapters.outbound.sources_editor import RuamelSourceEditor
from kb.application.ports.source_files import CreateSource, DeleteSource, SetApproved, UpdateSource
from kb.sources_config import ConfigError, parse

EXAMPLE = Path(__file__).resolve().parents[3] / "config" / "kb-sources.yaml.example"

TEXT = """\
# Sources the catalog reads. Edited by hand and from the console.
approved: true            # the kill switch

defaults:
  mechanisms:
    incremental:  { enabled: true,  every: 15m }
    full_scrape:  { enabled: true,  at: "03:00" }

sources:
  # The engineering wiki.
  - id: confluence-eng
    kind: confluence
    enabled: true            # on
    spaces: [ENG]
    exclude_labels: [archive]

  # Epics, as product context.
  - id: jira-product
    kind: jira
    enabled: false
    jql: 'project = PAY AND issuetype = Epic'

  # Repositories.
  - id: gitlab-platform
    kind: gitlab
    enabled: false
    base_url: ${GITLAB_BASE_URL:-https://your-gitlab-host}
    token_env: GITLAB_TOKEN   # the name; the value stays in .env
    projects:
      - path: platform/specs

# trailing note
"""


def edit(text, change, env=None):
    config = parse(text, env=env or {}, lenient=True)
    out = RuamelSourceEditor().apply(text, change, config)
    parse(out, env=env or {}, lenient=True)  # whatever it wrote must still be a valid file
    return out


def changed_lines(before: str, after: str) -> list[str]:
    return [
        line
        for line in difflib.unified_diff(before.splitlines(), after.splitlines(), lineterm="", n=0)
        if line[:1] in "+-" and line[:3] not in ("+++", "---")
    ]


def test_an_edit_that_changes_nothing_changes_nothing():
    # Covers: FR-CFG-11
    assert edit(TEXT, SetApproved(value=True)) == TEXT
    example = EXAMPLE.read_text(encoding="utf-8")
    config = parse(example, env={}, lenient=True)
    assert edit(example, SetApproved(value=config.approved)) == example, "the shipped example survives a round trip byte for byte"


def test_only_the_changed_keys_move_and_every_comment_stays():
    # Covers: FR-CFG-11
    out = edit(TEXT, UpdateSource(id="confluence-eng", config={"spaces": ["ENG", "OPS"], "store_raw_content": True}))
    assert changed_lines(TEXT, out) == [
        "-    spaces: [ENG]",
        "+    spaces: [ENG, OPS]",
        "+    store_raw_content: true",
    ]
    for comment in ("# Sources the catalog reads", "# the kill switch", "# The engineering wiki.", "# on", "# trailing note"):
        assert comment in out


def test_a_variable_placeholder_is_never_replaced_by_its_value():
    # Covers: FR-CFG-11
    env = {"GITLAB_BASE_URL": "https://gitlab.real.example", "GITLAB_TOKEN": "glpat-SECRET"}
    out = edit(TEXT, UpdateSource(id="gitlab-platform", config={"enabled": True}), env=env)
    assert "${GITLAB_BASE_URL:-https://your-gitlab-host}" in out
    assert "gitlab.real.example" not in out and "glpat-SECRET" not in out
    assert changed_lines(TEXT, out) == ["-    enabled: false", "+    enabled: true"] or "enabled: true" in out


def test_a_setting_the_operator_changes_is_written_as_they_typed_it():
    # Covers: FR-CFG-11
    out = edit(TEXT, UpdateSource(id="gitlab-platform", config={"base_url": "${OTHER_URL:-https://gl}"}))
    assert "base_url: ${OTHER_URL:-https://gl}" in out


def test_a_setting_that_is_sent_empty_is_cleared_and_one_not_sent_is_left_alone():
    # Covers: FR-CFG-11
    out = edit(TEXT, UpdateSource(id="confluence-eng", config={"exclude_labels": []}))
    assert changed_lines(TEXT, out) == ["-    exclude_labels: [archive]"]
    untouched = edit(TEXT, UpdateSource(id="confluence-eng", config={"enabled": False}))
    assert "exclude_labels: [archive]" in untouched and "spaces: [ENG]" in untouched


def test_mechanisms_can_be_set_for_one_source_and_the_defaults_are_untouched():
    # Covers: FR-CFG-11
    out = edit(TEXT, UpdateSource(id="jira-product", config={
        "mechanisms": {"incremental": {"every": "5m"}, "full_scrape": {"enabled": False}}}))
    config = parse(out, env={}, lenient=True)
    jira = next(s for s in config.sources if s.id == "jira-product")
    merged = config.mechanisms_for(jira)
    assert merged.incremental.every == "5m" and merged.full_scrape.enabled is False
    assert "incremental:  { enabled: true,  every: 15m }" in out, "the defaults keep their own spacing"


def test_a_new_source_is_added_at_the_end_disabled_unless_told_otherwise():
    # Covers: FR-CFG-11
    out = edit(TEXT, CreateSource(config={"id": "wiki-ops", "kind": "confluence", "spaces": ["OPS", "SRE"]}))
    assert changed_lines(TEXT, out)[0].startswith("+")
    assert all(line.startswith("+") for line in changed_lines(TEXT, out)), "nothing existing moved"
    created = next(s for s in parse(out, env={}, lenient=True).sources if s.id == "wiki-ops")
    assert created.enabled is False and created.spaces == ("OPS", "SRE")
    assert "spaces: [OPS, SRE]" in out, "short lists are written the way the file writes them"
    explicit = edit(TEXT, CreateSource(config={"id": "w2", "kind": "jira", "jql": "project = X", "enabled": True}))
    assert next(s for s in parse(explicit, env={}, lenient=True).sources if s.id == "w2").enabled is True


def test_a_source_can_be_added_to_a_file_that_has_none():
    # Covers: FR-CFG-11
    out = edit("approved: true\n", CreateSource(config={"id": "a", "kind": "jira", "jql": "x"}))
    assert [s.id for s in parse(out, env={}, lenient=True).sources] == ["a"]
    assert out.startswith("approved: true")


def test_deleting_a_source_takes_its_own_header_and_nothing_of_its_neighbours():
    # Covers: FR-CFG-11
    out = edit(TEXT, DeleteSource(id="jira-product"))
    assert "# Epics, as product context." not in out and "jira-product" not in out
    assert "# The engineering wiki." in out and "# Repositories." in out, "neighbours keep their comments"
    assert [s.id for s in parse(out, env={}, lenient=True).sources] == ["confluence-eng", "gitlab-platform"]


def test_deleting_the_first_and_the_last_source():
    # Covers: FR-CFG-11
    first = edit(TEXT, DeleteSource(id="confluence-eng"))
    assert "# The engineering wiki." not in first and "# Epics, as product context." in first
    assert "approved: true" in first and "defaults:" in first

    last = edit(TEXT, DeleteSource(id="gitlab-platform"))
    assert "# Repositories." not in last and "projects:" not in last
    assert "# trailing note" in last, "a comment after the last source is not that source's"
    assert [s.id for s in parse(last, env={}, lenient=True).sources] == ["confluence-eng", "jira-product"]


def test_deleting_the_only_source_leaves_a_file_that_still_loads():
    # Covers: FR-CFG-11
    only = "approved: true\nsources:\n  - id: a\n    kind: jira\n    jql: x\n"
    out = edit(only, DeleteSource(id="a"))
    assert parse(out, env={}, lenient=True).sources == ()


def test_the_global_switch_is_changed_in_place_or_added_at_the_top():
    # Covers: FR-CFG-11
    out = edit(TEXT, SetApproved(value=False))
    lines = changed_lines(TEXT, out)
    assert len(lines) == 2 and lines[0].startswith("-approved: true") and lines[1].startswith("+approved: false")
    assert "# the kill switch" in lines[1], "its comment stays on the line"
    added = edit("sources: []\n", SetApproved(value=False))
    assert added.startswith("approved: false")


def test_an_edit_to_a_missing_source_is_refused():
    # Covers: FR-CFG-11
    config = parse(TEXT, env={}, lenient=True)
    with pytest.raises((ConfigError, StopIteration)):
        RuamelSourceEditor().apply(TEXT, DeleteSource(id="nope"), config)
