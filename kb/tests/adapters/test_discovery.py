"""Discovery: proposing sources from what the systems contain.

The safety properties are the whole point. Nothing it writes may take effect
unseen, and re-running it must never cost an operator the edits they made last
time.
"""

from __future__ import annotations

from pathlib import Path

from kb.adapters.outbound.discovery import (
    Candidate,
    discover_confluence,
    discover_jira,
    existing_source_ids,
    merge_into,
)
from kb.sources_config import load
from tests.adapters.test_sources import StubResponse, StubSession


def test_confluence_spaces_are_proposed_with_their_size():
    # Covers: FR-CFG-06
    # "412 pages" is the difference between deciding and guessing.
    session = StubSession([
        StubResponse({"results": [{"key": "ENG", "name": "Engineering"}]}),
        StubResponse({"totalSize": 412}),
    ])

    candidates = discover_confluence(session, "https://wiki.internal")

    assert [c.id for c in candidates] == ["confluence-eng"]
    assert "412 pages" in candidates[0].size and "Engineering" in candidates[0].size
    assert "enabled: false" in candidates[0].body
    assert "spaces: [ENG]" in candidates[0].body


def test_a_space_whose_pages_cannot_be_counted_is_still_proposed():
    # A count is nice to have; failing the whole discovery over it is not.
    session = StubSession([
        StubResponse({"results": [{"key": "ENG", "name": "Engineering"}]}),
        StubResponse(None, status_code=500),
    ])
    assert discover_confluence(session, "https://wiki.internal")[0].size.startswith("? pages")


def test_jira_projects_are_proposed_with_an_epics_only_query():
    # Covers: FR-CFG-06
    # Cataloguing every task would bury the features they belong to.
    session = StubSession([StubResponse([{"key": "PAY", "name": "Payments"}])])

    candidates = discover_jira(session, "https://jira.internal")

    assert candidates[0].id == "jira-pay"
    assert "issuetype = Epic" in candidates[0].body
    assert "enabled: false" in candidates[0].body


def test_a_new_file_is_written_with_everything_disabled_and_unreviewed(tmp_path):
    # Covers: FR-CFG-06, FR-CFG-05, AS-07
    path = tmp_path / "kb-sources.yaml"
    added, skipped = merge_into(path, [
        Candidate(id="confluence-eng", kind="confluence", size="1 page",
                  body="  - id: confluence-eng\n    kind: confluence\n    enabled: false\n    spaces: [ENG]\n"),
    ])

    assert added == ["confluence-eng"] and skipped == []
    config = load(path)
    assert config.enabled_sources() == (), "nothing discovery proposes takes effect unseen"
    assert config.enabled_sources() == ()
    assert config.source("confluence-eng").kind == "confluence"


def test_re_running_adds_only_what_is_new_and_never_rewrites_a_line(tmp_path):
    # Covers: FR-CFG-06
    # An operator's edits, comments and decisions have to survive, or discovery
    # becomes something you run once and never dare run again.
    path = tmp_path / "kb-sources.yaml"
    edited = (
        "approved: true\n"
        "sources:\n"
        "  # we only want the architecture space here\n"
        "  - id: confluence-eng\n"
        "    kind: confluence\n"
        "    enabled: true\n"
        "    spaces: [ARCH]\n"
    )
    path.write_text(edited, encoding="utf-8")

    added, skipped = merge_into(path, [
        Candidate(id="confluence-eng", kind="confluence", size="412 pages",
                  body="  - id: confluence-eng\n    kind: confluence\n    enabled: false\n    spaces: [ENG]\n"),
        Candidate(id="jira-pay", kind="jira", size="project PAY",
                  body="  - id: jira-pay\n    kind: jira\n    enabled: false\n    jql: 'project = PAY'\n"),
    ])

    text = path.read_text(encoding="utf-8")
    assert added == ["jira-pay"] and skipped == ["confluence-eng"]
    assert text.startswith(edited), "existing content is untouched, byte for byte"
    assert "we only want the architecture space here" in text
    config = load(path)
    assert config.source("confluence-eng").spaces == ("ARCH",), "the operator's choice survived"
    assert config.source("jira-pay").enabled is False


def test_discovered_sizes_are_written_as_comments_not_config(tmp_path):
    path = tmp_path / "kb-sources.yaml"
    merge_into(path, [
        Candidate(id="jira-pay", kind="jira", size="project PAY -- Payments",
                  body="  - id: jira-pay\n    kind: jira\n    enabled: false\n    jql: 'project = PAY'\n"),
    ])
    assert "# discovered: project PAY -- Payments" in path.read_text(encoding="utf-8")
    load(path)  # the comment must not make the file unparseable


def test_existing_ids_are_found_even_when_the_file_cannot_be_parsed():
    # The file may hold a ${VAR} that isn't set right now; discovery still has
    # to be able to append to it without duplicating a source.
    text = "sources:\n  - id: confluence-eng\n    base_url: ${NOT_SET}\n"
    assert existing_source_ids(text) == {"confluence-eng"}


def test_gitlab_projects_are_proposed_with_the_directories_that_hold_docs():
    # Covers: FR-CFG-06
    # "which part of this repo is documentation?" is the question discovery
    # exists to answer -- otherwise an operator goes spelunking for it.
    from kb.adapters.outbound.discovery import discover_gitlab

    tree = [{"type": "blob", "path": p} for p in (
        *[f"docs/ADRs/{n}.md" for n in range(14)],
        *[f"specs/{n}.md" for n in range(5)],
        "README.md",
        "src/main.py",
    )]
    session = StubSession([
        StubResponse([{"id": 7, "path_with_namespace": "platform/media-service"}]),
        StubResponse(tree),
    ])

    candidate = discover_gitlab(session, "https://gitlab.internal")[0]

    assert candidate.id == "gitlab-platform-media-service"
    assert "docs/ADRs: 14 files" in candidate.size and "specs: 5 files" in candidate.size
    assert "{ dir: docs/ADRs }" in candidate.body and "{ dir: specs }" in candidate.body
    assert "token_env: GITLAB_TOKEN" in candidate.body, "the name, never the secret"
    assert "enabled: false" in candidate.body


def test_a_repository_with_scattered_markdown_is_proposed_whole():
    from kb.adapters.outbound.discovery import discover_gitlab

    session = StubSession([
        StubResponse([{"id": 8, "path_with_namespace": "platform/specs"}]),
        StubResponse([{"type": "blob", "path": "one.md"}, {"type": "blob", "path": "two.md"}]),
    ])

    candidate = discover_gitlab(session, "https://gitlab.internal")[0]

    assert 'dir: "."' in candidate.body
    assert "whole repository" in candidate.size


def test_a_repository_with_no_markdown_is_not_proposed_at_all():
    from kb.adapters.outbound.discovery import discover_gitlab

    session = StubSession([
        StubResponse([{"id": 9, "path_with_namespace": "platform/binary"}]),
        StubResponse([{"type": "blob", "path": "src/main.py"}]),
    ])
    assert discover_gitlab(session, "https://gitlab.internal") == []
