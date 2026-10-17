"""Manual records over the console's HTTP door."""

from __future__ import annotations

from datetime import timedelta

from kb.application.ports.summarizer import SummaryDraft
from kb.application.use_cases.reconcile_document import ReconcileDocument
from kb.application.use_cases.sync_source import Mode, SyncSource
from kb.adapters.outbound.form_source import FormKnowledgeSource, PresetSummarizer
from kb.domain.content import ContentPolicy
from tests.adapters.test_admin import CSRF, NOW, anon, client, world  # noqa: F401 - fixtures
from tests.application.fakes import FakeSummarizer

RECORD = {
    "slug": "release-process",
    "title": {"en": "Release process", "fa": "فرایند انتشار"},
    "body": "Releases go out on Tuesdays.",
    "type": "doc",
    "summary": {"en": "How releases ship."},
    "tags": ["release"],
    "owner_team": "platform",
}


def publish(world):
    """What the scheduler does with the request the console queued."""
    reconcile = ReconcileDocument(world.store, PresetSummarizer(FakeSummarizer()), world.clock)
    SyncSource(world.store, reconcile, world.clock).execute(
        FormKnowledgeSource(world.forms), mode=Mode.FULL, content=ContentPolicy(store=True)
    )


def test_a_record_is_created_listed_and_waits_for_the_scheduler_to_publish_it(client, world):
    # Covers: FR-ADM-12
    created = client.post("/api/forms", json=RECORD, headers=CSRF)
    assert created.status_code == 201
    assert created.json()["record"]["publication"] == "pending"
    assert [(r.source_id, r.mode) for r in world.queue.requests] == [("form", "full")]

    listed = client.get("/api/forms").json()
    assert [r["slug"] for r in listed["records"]] == ["release-process"]
    assert listed["languages"] == ["en", "fa"] and listed["summarizer_configured"] is True

    publish(world)
    assert client.get("/api/forms/release-process").json()["record"]["publication"] == "published"


def test_an_edit_makes_the_record_pending_again_until_it_is_published(client, world):
    # Covers: FR-ADM-12
    client.post("/api/forms", json=RECORD, headers=CSRF)
    publish(world)
    edited = client.put("/api/forms/release-process", json={**RECORD, "body": "Wednesdays."}, headers=CSRF)
    assert edited.status_code == 200
    assert edited.json()["record"]["revision"] == 2
    assert edited.json()["record"]["publication"] == "pending"


def test_a_bad_record_is_refused_with_the_field_and_the_reason(client):
    # Covers: FR-ADM-12
    bad = client.post("/api/forms", json={**RECORD, "title": {}, "body": ""}, headers=CSRF)
    assert bad.status_code == 400
    assert bad.json()["error"]["type"] == "invalid_record"
    assert "title" in bad.json()["error"]["message"] and "body" in bad.json()["error"]["message"]


def test_a_slug_cannot_be_reused_and_an_unknown_record_is_not_found(client):
    # Covers: FR-ADM-12
    client.post("/api/forms", json=RECORD, headers=CSRF)
    assert client.post("/api/forms", json=RECORD, headers=CSRF).status_code == 409
    assert client.get("/api/forms/nope").status_code == 404
    assert client.put("/api/forms/nope", json=RECORD, headers=CSRF).status_code == 404
    assert client.delete("/api/forms/nope", headers=CSRF).status_code == 404
    assert client.get("/api/forms/nope/content").status_code == 404


def test_deleting_a_record_removes_it_and_queues_the_pass_that_retires_its_entry(client, world):
    # Covers: FR-ADM-12
    client.post("/api/forms", json=RECORD, headers=CSRF)
    assert client.delete("/api/forms/release-process", headers=CSRF).status_code == 200
    assert client.get("/api/forms").json()["records"] == []
    assert world.queue.requests[-1].source_id == "form"


def test_a_summary_is_drafted_on_request_and_nothing_is_stored(client, world):
    # Covers: FR-ADM-13
    world.draft_summary._summarizer = FakeSummarizer(
        {"draft": SummaryDraft(title={"en": "T"}, summary={"en": "S"}, keywords=("k",))}
    )
    reply = client.post(
        "/api/forms/draft-summary",
        json={"title": {"en": "Release process"}, "body": "Releases go out on Tuesdays."},
        headers=CSRF,
    )
    assert reply.status_code == 200
    assert reply.json() == {
        "draft": {"title": {"en": "T"}, "summary": {"en": "S"}, "keywords": ["k"], "aliases": []},
        "usable": True,
    }
    assert world.forms.list() == [] and world.queue.requests == []


def test_a_draft_that_came_back_empty_is_flagged_not_trusted(client, world):
    # Covers: FR-ADM-13
    world.draft_summary._summarizer = FakeSummarizer({"draft": SummaryDraft(title={"en": "Release process"})})
    reply = client.post("/api/forms/draft-summary", json={"title": {"en": "x"}, "body": "text"}, headers=CSRF)
    assert reply.json()["usable"] is False


def test_drafting_needs_text_and_a_configured_summarizer(client, world):
    # Covers: FR-ADM-13
    nothing = client.post("/api/forms/draft-summary", json={"title": {"en": "x"}, "body": "  "}, headers=CSRF)
    assert nothing.status_code == 400 and nothing.json()["error"]["type"] == "nothing_to_summarize"
    world.draft_summary = None
    unavailable = client.post("/api/forms/draft-summary", json={"title": {"en": "x"}, "body": "t"}, headers=CSRF)
    assert unavailable.status_code == 409 and unavailable.json()["error"]["type"] == "no_summarizer"


def test_a_failing_model_does_not_leak_what_it_said(client, world):
    # Covers: FR-ADM-13
    class Boom:
        def draft(self, document):
            raise RuntimeError("401 from https://llm.internal/v1 key=sk-secret")

    world.draft_summary._summarizer = Boom()
    reply = client.post("/api/forms/draft-summary", json={"title": {"en": "x"}, "body": "t"}, headers=CSRF)
    assert reply.status_code == 502
    assert "sk-secret" not in reply.text and "llm.internal" not in reply.text


def test_a_stored_summary_reaches_the_entry_without_a_model_call(client, world):
    # Covers: FR-ADM-13
    client.post("/api/forms", json=RECORD, headers=CSRF)
    publish(world)
    client.put("/api/forms/release-process", json={**RECORD, "summary": {"en": "Only this changed."}}, headers=CSRF)
    publish(world)
    content = client.get("/api/forms/release-process/content").json()
    assert content["publication"] == "published"
    assert content["entry"]["summary"] == {"en": "Only this changed."}


def test_a_record_is_read_in_pages_the_way_an_agent_reads_it(client, world):
    # Covers: FR-ADM-14
    from kb.domain.content import CONTENT_PAGE_CHARS

    long = "x" * (CONTENT_PAGE_CHARS + 500)
    client.post("/api/forms", json={**RECORD, "body": long}, headers=CSRF)
    assert client.get("/api/forms/release-process/content").json()["content"] is None, "not published yet"

    publish(world)
    first = client.get("/api/forms/release-process/content").json()["content"]
    assert (first["offset"], first["total_chars"], first["next_offset"]) == (0, len(long), CONTENT_PAGE_CHARS)
    assert len(first["text"]) == CONTENT_PAGE_CHARS
    second = client.get(f"/api/forms/release-process/content?offset={first['next_offset']}").json()["content"]
    assert (len(second["text"]), second["next_offset"]) == (500, None)
    assert client.get("/api/forms/release-process/content?offset=abc").status_code == 400


def test_reading_a_record_queues_no_refresh(client, world):
    # Covers: FR-ADM-14
    client.post("/api/forms", json=RECORD, headers=CSRF)
    publish(world)
    world.clock.advance(timedelta(days=400))  # long past stale
    assert client.get("/api/forms/release-process/content").status_code == 200
    # The console's role may not write the refresh queue; asking would fail.
    assert world.store.refresh_queue == {}


def test_manual_records_can_be_synced_but_not_purged_or_retired(client, world):
    # Covers: FR-ADM-04
    assert client.post("/api/sync", json={"source_id": "form", "mode": "full"}, headers=CSRF).status_code == 202
    purge = client.post("/api/sources/form/purge-content", headers=CSRF)
    retire = client.post("/api/sources/form/retire", headers=CSRF)
    assert purge.status_code == 400 and retire.status_code == 400


def test_the_sources_screen_carries_a_card_for_the_manual_records(client, world):
    # Covers: FR-ADM-12
    client.post("/api/forms", json=RECORD, headers=CSRF)
    card = client.get("/api/sources").json()["manual"]
    assert (card["records"], card["pending"]) == (1, 1)
    publish(world)
    assert client.get("/api/sources").json()["manual"]["pending"] == 0
    assert "form" not in {o["source_id"] for o in client.get("/api/sources").json()["orphans"]}
