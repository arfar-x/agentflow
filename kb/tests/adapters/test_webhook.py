"""The webhook receiver: the only thing here that accepts an inbound request.

So the tests are mostly about what an untrusted caller cannot make it do.
"""

from __future__ import annotations

import pytest
from starlette.testclient import TestClient

from kb.adapters.inbound.webhook import MAX_PATHS_PER_EVENT, build_app, changed_paths

SECRET = "shared-secret"


def push(project: str = "platform/specs", *, added=(), modified=(), removed=()) -> dict:
    return {
        "project": {"path_with_namespace": project},
        "commits": [{"added": list(added), "modified": list(modified), "removed": list(removed)}],
    }


@pytest.fixture()
def client():
    queued: list[tuple[str, str]] = []

    def enqueue(source_id: str, external_id: str) -> bool:
        if external_id.endswith(".png"):
            return False  # not catalogued
        queued.append((source_id, external_id))
        return True

    app = build_app(
        secret=SECRET,
        enqueue=enqueue,
        source_ids_for_project=lambda p: ["gitlab-platform"] if p == "platform/specs" else [],
    )
    return TestClient(app), queued


def test_a_push_queues_the_files_it_changed(client):
    # Covers: FR-REC-11
    http, queued = client

    response = http.post(
        "/gitlab",
        json=push(added=["new.md"], modified=["docs/guide.md"]),
        headers={"X-Gitlab-Token": SECRET, "X-Gitlab-Event": "Push Hook"},
    )

    assert response.status_code == 200
    assert response.json()["queued"] == 2
    assert queued == [
        ("gitlab-platform", "platform/specs:new.md"),
        ("gitlab-platform", "platform/specs:docs/guide.md"),
    ]


def test_a_request_without_the_secret_is_rejected_before_anything_is_parsed(client):
    # Covers: FR-REC-11
    http, queued = client

    for headers in ({}, {"X-Gitlab-Token": "wrong"}):
        response = http.post("/gitlab", json=push(added=["a.md"]), headers=headers)
        assert response.status_code == 401

    assert queued == [], "an unauthenticated caller makes this process do nothing"


def test_only_the_push_event_is_acted_on(client):
    # Tag pushes and pipeline events are acknowledged, so GitLab does not mark
    # the hook as failing, but nothing is queued.
    http, queued = client

    response = http.post(
        "/gitlab",
        json=push(added=["a.md"]),
        headers={"X-Gitlab-Token": SECRET, "X-Gitlab-Event": "Pipeline Hook"},
    )

    assert response.status_code == 200 and response.json()["ignored"] == "Pipeline Hook"
    assert queued == []


def test_a_repository_nothing_watches_is_acknowledged_and_ignored(client):
    # Hooks outlive the configuration that justified them.
    http, queued = client

    response = http.post(
        "/gitlab",
        json=push("some/other-repo", added=["a.md"]),
        headers={"X-Gitlab-Token": SECRET},
    )

    assert response.json() == {"project": "some/other-repo", "watching": False, "queued": 0}
    assert queued == []


def test_a_path_that_is_not_catalogued_is_not_queued(client):
    # A new file is picked up by the incremental run, which knows the scopes.
    http, queued = client

    response = http.post(
        "/gitlab",
        json=push(added=["diagram.png"], modified=["docs/guide.md"]),
        headers={"X-Gitlab-Token": SECRET},
    )

    assert response.json()["queued"] == 1
    assert [external for _, external in queued] == ["platform/specs:docs/guide.md"]


def test_malformed_json_is_a_bad_request_not_a_crash(client):
    http, _ = client
    response = http.post("/gitlab", content=b"{not json", headers={"X-Gitlab-Token": SECRET})
    assert response.status_code == 400


def test_health_needs_no_secret(client):
    http, _ = client
    assert http.get("/health").json() == {"status": "ok"}


def test_removed_files_are_not_treated_as_changes():
    # Covers: FR-REC-11
    # Only a full pass may conclude a document is gone, and a file deleted on
    # a branch is not deleted on the default one.
    assert changed_paths(push(added=["a.md"], removed=["b.md"])) == ["a.md"]


def test_a_path_touched_by_several_commits_is_queued_once():
    payload = {
        "project": {"path_with_namespace": "x/y"},
        "commits": [
            {"modified": ["docs/a.md"]},
            {"modified": ["docs/a.md", "docs/b.md"]},
        ],
    }
    assert changed_paths(payload) == ["docs/a.md", "docs/b.md"]


def test_an_enormous_push_is_capped():
    # One merge can touch thousands of files; past a point the incremental tick
    # is the cheaper way to catch up.
    payload = {
        "project": {"path_with_namespace": "x/y"},
        "commits": [{"added": [f"docs/{n}.md" for n in range(MAX_PATHS_PER_EVENT + 50)]}],
    }
    assert len(changed_paths(payload)) == MAX_PATHS_PER_EVENT
