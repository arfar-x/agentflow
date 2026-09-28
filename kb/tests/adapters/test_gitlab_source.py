"""GitLab: scopes, glob matching, and reading only what changed.

The scope rules are where this adapter earns its keep, so most of these are
about which files a configuration actually selects -- the thing an operator gets
wrong and only notices when the catalog is full of changelogs.
"""

from __future__ import annotations

import json

import pytest

from kb.adapters.outbound.gitlab_source import (
    GitLabKnowledgeSource,
    glob_match,
    title_from,
)
from kb.application.ports.knowledge_source import KnowledgeSource
from kb.domain.entry import EntryType
from kb.sources_config import GitLabProject, GitLabScope, GitLabSource
from tests.adapters.test_sources import StubResponse, StubSession


class TextResponse(StubResponse):
    """A raw-file response: GitLab returns the bytes, not JSON."""

    def __init__(self, text: str, status_code: int = 200) -> None:
        super().__init__(None, status_code)
        self.text = text


def blob(path: str, sha: str = "abc123") -> dict:
    return {"id": sha, "type": "blob", "path": path}


def source(projects, responses, **kwargs) -> tuple[GitLabKnowledgeSource, StubSession]:
    config = GitLabSource(
        id="gitlab-platform", kind="gitlab", base_url="https://gitlab.internal", projects=projects, **kwargs
    )
    session = StubSession(responses)
    return GitLabKnowledgeSource(config, token="t", session=session), session


# ------------------------------------------------------------------- globs
@pytest.mark.parametrize(
    "path,pattern,expected",
    [
        ("a.md", "**/*.md", True),
        ("deep/nested/a.md", "**/*.md", True),
        ("a.md", "*.md", True),
        # The reason this isn't fnmatch: `*` must not cross a directory
        # boundary, or `docs/*.md` quietly swallows the whole tree.
        ("deep/a.md", "*.md", False),
        ("a.mdx", "**/*.md", False),
        ("CHANGELOG.md", "**/CHANGELOG.md", True),
        ("docs/CHANGELOG.md", "**/CHANGELOG.md", True),
        ("node_modules/x/y.md", "**/node_modules/**", True),
    ],
)
def test_glob_matching(path, pattern, expected):
    # Covers: FR-SRC-03
    assert glob_match(path, pattern) is expected


def test_a_title_comes_from_the_heading_or_the_filename():
    # Covers: FR-SRC-03
    assert title_from("# Use Postgres\n\nBody", "docs/0001-use-postgres.md") == "Use Postgres"
    assert title_from("no heading here", "docs/0001-use-postgres.md") == "0001 use postgres"


# ------------------------------------------------------------------ scopes
def test_a_whole_repository_is_one_scope_with_a_dot():
    # Covers: FR-SRC-03
    project = GitLabProject(path="platform/specs", ref="main", scopes=(GitLabScope(dir="."),))
    gitlab, session = source(
        (project,),
        [
            StubResponse([blob("spec-a.md"), blob("deep/spec-b.md"), blob("logo.png")]),
            TextResponse("# Spec A\nbody"),
            TextResponse("# Spec B\nbody"),
        ],
    )

    documents = list(gitlab.list_all())

    assert [d.external_id for d in documents] == [
        "platform/specs:spec-a.md",
        "platform/specs:deep/spec-b.md",
    ]
    assert session.requests[0][1]["path"] == "", "a whole repo walks from the root"


def test_a_scope_limits_the_walk_to_one_directory_and_labels_what_it_finds():
    # Covers: FR-SRC-03
    project = GitLabProject(
        path="media-service",
        ref="main",
        scopes=(GitLabScope(dir="docs/ADRs", type="spec", tags=("adr",)),),
    )
    gitlab, session = source(
        (project,),
        [StubResponse([blob("docs/ADRs/0001-use-postgres.md")]), TextResponse("# Use Postgres")],
    )

    document = next(iter(gitlab.list_all()))

    assert session.requests[0][1]["path"] == "docs/ADRs"
    assert document.type == EntryType.SPEC, "the scope's type is inherited"
    assert document.tags == ("adr",)
    assert document.location.url == (
        "https://gitlab.internal/media-service/-/blob/main/docs/ADRs/0001-use-postgres.md"
    )


def test_patterns_and_exclusions_decide_what_is_read():
    # Covers: FR-SRC-03
    project = GitLabProject(
        path="payments/core",
        ref="main",
        scopes=(
            GitLabScope(
                dir="docs",
                patterns=("**/*.md", "**/*.mdx"),
                exclude=("**/CHANGELOG.md",),
            ),
        ),
    )
    gitlab, _ = source(
        (project,),
        [
            StubResponse([
                blob("docs/guide.md"),
                blob("docs/api.mdx"),
                blob("docs/CHANGELOG.md"),
                blob("docs/diagram.png"),
            ]),
            TextResponse("# Guide"),
            TextResponse("# API"),
        ],
    )

    assert [d.title for d in gitlab.list_all()] == ["Guide", "API"]


def test_the_most_specific_scope_owns_a_file():
    # A file under docs/ADRs matches both scopes; it must inherit the ADR one,
    # or a nested scope's whole purpose is lost.
    project = GitLabProject(
        path="media-service",
        ref="main",
        scopes=(GitLabScope(dir="docs"), GitLabScope(dir="docs/ADRs", type="spec", tags=("adr",))),
    )
    gitlab, _ = source((project,), [TextResponse("# An ADR")])

    document = gitlab.fetch("media-service:docs/ADRs/0001-x.md")

    assert document.type == EntryType.SPEC and document.tags == ("adr",)


def test_a_blob_sha_is_the_version_so_no_extra_request_is_needed():
    # Covers: FR-SRC-03
    # Asking the commits API per file would turn one sync into hundreds of
    # round trips; the SHA changes exactly when the content does.
    project = GitLabProject(path="platform/specs", ref="main")
    gitlab, session = source(
        (project,), [StubResponse([blob("a.md", sha="deadbeef")]), TextResponse("# A")]
    )

    document = next(iter(gitlab.list_all()))

    assert document.version == "deadbeef"
    assert len(session.requests) == 2, "one tree listing, one file read"


def test_a_missing_directory_is_reported_and_the_run_continues():
    project = GitLabProject(path="platform/specs", ref="main", scopes=(GitLabScope(dir="typo"),))
    gitlab, _ = source((project,), [StubResponse(None, status_code=404)])
    assert list(gitlab.list_all()) == []


def test_the_default_branch_is_used_when_no_ref_is_configured():
    project = GitLabProject(path="platform/specs")
    gitlab, session = source(
        (project,),
        [StubResponse({"default_branch": "trunk"}), StubResponse([blob("a.md")]), TextResponse("# A")],
    )

    document = next(iter(gitlab.list_all()))

    assert document.location.ref["ref"] == "trunk"
    assert session.requests[1][1]["ref"] == "trunk"


# ------------------------------------------------------------- incremental
def test_an_incremental_run_reads_only_the_files_that_changed():
    # Covers: FR-REC-08
    project = GitLabProject(path="platform/specs", ref="main")
    gitlab, session = source(
        (project,),
        [
            StubResponse({"diffs": [{"new_path": "changed.md"}, {"new_path": "logo.png"}]}),
            TextResponse("# Changed"),
        ],
    )

    documents = list(gitlab.changed_since(json.dumps({"platform/specs": "old-sha"})))

    assert [d.title for d in documents] == ["Changed"]
    assert "compare" in session.requests[0][0]
    assert session.requests[0][1] == {"from": "old-sha", "to": "main"}


def test_a_deleted_file_is_not_treated_as_a_change():
    # Only a full pass may conclude that something is gone.
    project = GitLabProject(path="platform/specs", ref="main")
    gitlab, _ = source(
        (project,), [StubResponse({"diffs": [{"new_path": "gone.md", "deleted_file": True}]})]
    )
    assert list(gitlab.changed_since(json.dumps({"platform/specs": "old"}))) == []


def test_a_project_with_no_checkpoint_gets_a_full_walk():
    # A repository somebody just added must not silently catalog nothing.
    project = GitLabProject(path="platform/specs", ref="main")
    gitlab, _ = source((project,), [StubResponse([blob("a.md")]), TextResponse("# A")])
    assert [d.title for d in gitlab.changed_since(None)] == ["A"]


def test_an_unusable_checkpoint_falls_back_instead_of_failing():
    # History gets rewritten and commits get garbage-collected.
    project = GitLabProject(path="platform/specs", ref="main")
    gitlab, _ = source((project,), [StubResponse(None, status_code=404)])
    assert list(gitlab.changed_since(json.dumps({"platform/specs": "gone-sha"}))) == []


def test_the_checkpoint_records_a_commit_per_project():
    # Covers: FR-REC-08
    projects = (GitLabProject(path="a/one", ref="main"), GitLabProject(path="b/two", ref="main"))
    gitlab, _ = source(projects, [StubResponse([{"id": "sha-a"}]), StubResponse([{"id": "sha-b"}])])

    assert json.loads(gitlab.checkpoint()) == {"a/one": "sha-a", "b/two": "sha-b"}


def test_the_gitlab_adapter_satisfies_the_port():
    gitlab, _ = source((), [])
    assert isinstance(gitlab, KnowledgeSource)
