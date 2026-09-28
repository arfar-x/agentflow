"""Jira as a knowledge source.

Not every issue is knowledge -- most are work items with a half-line
description -- so what gets catalogued is whatever the configured JQL selects,
typically epics. The config carries the query rather than this adapter guessing
a filter, because which issues count as product context differs per
organization.

An issue's value to the catalog is its description: what a feature is and why.
Comments and worklogs are deliberately left out; an agent that needs those reads
the issue live through agent-skills.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, Iterator

import requests

from kb.application.ports.knowledge_source import SourceDocument
from kb.domain.entry import EntryType, Location
from kb.sources_config import JiraSource as JiraSourceConfig

logger = logging.getLogger("kb.sources.jira")

PAGE_SIZE = 50
FIELDS = "summary,description,updated,created,labels,project,issuetype,status"


def _parse_timestamp(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def adf_to_text(description: Any) -> str:
    """Jira Cloud returns rich text as an Atlassian Document Format tree; Server
    returns plain text or wiki markup. Handle both rather than assuming a
    deployment, and walk the tree for `text` nodes -- enough for a summarizer,
    which is all this text is for."""
    if description is None:
        return ""
    if isinstance(description, str):
        return description.strip()

    collected: list[str] = []

    def walk(node: Any) -> None:
        if isinstance(node, dict):
            text = node.get("text")
            if isinstance(text, str):
                collected.append(text)
            for child in node.get("content") or []:
                walk(child)
        elif isinstance(node, list):
            for child in node:
                walk(child)

    walk(description)
    return " ".join(collected).strip()


class JiraKnowledgeSource:
    """Implements `kb.application.ports.KnowledgeSource`."""

    def __init__(
        self,
        config: JiraSourceConfig,
        *,
        base_url: str,
        username: str | None = None,
        password: str | None = None,
        token: str | None = None,
        session: Any | None = None,
        timeout: float = 30.0,
    ) -> None:
        self._config = config
        self._base_url = base_url.rstrip("/")
        self._api = f"{self._base_url}/rest/api/2"
        self._timeout = timeout
        self._session = session or requests.Session()
        if token:
            self._session.headers.update({"Authorization": f"Bearer {token}"})
        elif username and password:
            self._session.auth = (username, password)

    @property
    def source_id(self) -> str:
        return self._config.id

    def list_all(self) -> Iterator[SourceDocument]:
        yield from self._search(self._config.jql)

    def changed_since(self, checkpoint: str | None) -> Iterator[SourceDocument]:
        jql = self._config.jql
        if checkpoint:
            # Parenthesised: the configured JQL may itself contain an OR, and
            # appending a bare AND would silently change which issues match.
            jql = f'({jql}) AND updated >= "{checkpoint}"'
        yield from self._search(jql)

    def fetch(self, external_id: str) -> SourceDocument | None:
        response = self._session.get(
            f"{self._api}/issue/{external_id}", params={"fields": FIELDS}, timeout=self._timeout
        )
        if response.status_code == 404:
            return None
        response.raise_for_status()
        return self._to_document(response.json())

    def checkpoint(self) -> str | None:
        return datetime.now().strftime("%Y-%m-%d %H:%M")

    # -- internals ---------------------------------------------------------
    def _search(self, jql: str) -> Iterator[SourceDocument]:
        start = 0
        while True:
            response = self._session.get(
                f"{self._api}/search",
                params={"jql": jql, "startAt": start, "maxResults": PAGE_SIZE, "fields": FIELDS},
                timeout=self._timeout,
            )
            response.raise_for_status()
            payload = response.json()
            issues = payload.get("issues", [])
            for issue in issues:
                document = self._to_document(issue)
                if document is not None:
                    yield document
            start += len(issues)
            if not issues or start >= payload.get("total", 0):
                return

    def _to_document(self, issue: dict[str, Any]) -> SourceDocument | None:
        key = issue.get("key")
        fields = issue.get("fields") or {}
        summary = (fields.get("summary") or "").strip()
        if not key or not summary:
            logger.warning("skipping a Jira result with no key or summary: %r", issue.get("id"))
            return None

        project = (fields.get("project") or {}).get("key")
        labels = tuple(fields.get("labels") or ())
        return SourceDocument(
            source_id=self.source_id,
            external_id=key,
            title=summary,
            body=adf_to_text(fields.get("description")),
            location=Location(
                kind="jira", ref={"issue_key": key}, url=f"{self._base_url}/browse/{key}"
            ),
            # Product context, not a document: an epic describes a feature, and
            # that is what makes it worth cataloguing at all.
            type=EntryType.PRODUCT,
            # Jira has no version counter, so the updated timestamp is the
            # change marker. Content hashing still decides whether anything is
            # actually re-summarized.
            version=fields.get("updated"),
            created_at=_parse_timestamp(fields.get("created")),
            updated_at=_parse_timestamp(fields.get("updated")),
            tags=labels,
            extra={"project": project} if project else {},
        )
