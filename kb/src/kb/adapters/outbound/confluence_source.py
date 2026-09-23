"""Confluence as a knowledge source.

Reads pages from the configured spaces and hands them over as
`SourceDocument`s. It never stores what it reads: the body exists only long
enough to be summarized, and what lands in the catalog is a pointer plus that
summary.

**A page's labels decide its type.** `type_from_labels` maps a label to an entry
type, so a glossary page is catalogued as a `term` because somebody labelled it
`kb-glossary` in Confluence -- curation happens at the source, in the tool the
authors already use, not in a file here.

**Credentials are a read-only service account's**, from the environment, used
only by sync. Nothing here touches a user's credentials: when an agent reads the
real page later, it does so as the user, through agent-skills.
"""

from __future__ import annotations

import html
import logging
import re
from datetime import datetime
from typing import Any, Iterator

import requests

from kb.application.ports.knowledge_source import SourceDocument
from kb.domain.entry import EntryType, Location
from kb.sources_config import ConfluenceSource as ConfluenceSourceConfig

logger = logging.getLogger("kb.sources.confluence")

_TAG = re.compile(r"<[^>]+>")
_WHITESPACE = re.compile(r"\s+")
PAGE_SIZE = 50


def storage_to_text(storage: str) -> str:
    """Confluence storage format (XHTML) to plain text.

    Good enough on purpose: this text is read once by a summarizer and then
    thrown away, so the bar is "conveys what the page says", not faithful
    rendering. Structure markers become spaces rather than being deleted, so
    words from adjacent cells or list items don't run together into one.
    """
    if not storage:
        return ""
    without_markup = _TAG.sub(" ", storage)
    return _WHITESPACE.sub(" ", html.unescape(without_markup)).strip()


def _parse_timestamp(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


class ConfluenceKnowledgeSource:
    """Implements `kb.application.ports.KnowledgeSource`."""

    def __init__(
        self,
        config: ConfluenceSourceConfig,
        *,
        base_url: str,
        username: str | None = None,
        password: str | None = None,
        token: str | None = None,
        deployment: str = "server",
        session: Any | None = None,
        timeout: float = 30.0,
    ) -> None:
        self._config = config
        self._base_url = base_url.rstrip("/")
        # Cloud and Server mount the REST API at different paths -- the same
        # split agent-skills' Confluence toolset has to make.
        self._api = f"{self._base_url}/wiki/rest/api" if deployment == "cloud" else f"{self._base_url}/rest/api"
        self._timeout = timeout
        self._session = session or requests.Session()
        if token:
            self._session.headers.update({"Authorization": f"Bearer {token}"})
        elif username and password:
            self._session.auth = (username, password)

    @property
    def source_id(self) -> str:
        return self._config.id

    # -- listing -----------------------------------------------------------
    def list_all(self) -> Iterator[SourceDocument]:
        yield from self._search(self._cql())

    def changed_since(self, checkpoint: str | None) -> Iterator[SourceDocument]:
        """Only what changed since the last run.

        The checkpoint is a Confluence timestamp; CQL compares it at minute
        resolution, so a run may re-see a page it already has. That is
        harmless: an unchanged page is detected by content hash and costs
        neither a model call nor a write.
        """
        cql = self._cql()
        if checkpoint:
            cql += f' and lastmodified >= "{checkpoint}"'
        yield from self._search(cql)

    def fetch(self, external_id: str) -> SourceDocument | None:
        response = self._session.get(
            f"{self._api}/content/{external_id}",
            params={"expand": "body.storage,version,space,metadata.labels,history"},
            timeout=self._timeout,
        )
        if response.status_code == 404:
            return None
        response.raise_for_status()
        return self._to_document(response.json())

    def checkpoint(self) -> str | None:
        """Confluence's own clock would be better, but CQL exposes no "now".
        The caller stores this after a successful run; a few seconds of overlap
        is safer than a gap, and re-seeing a page is free."""
        return datetime.now().strftime("%Y-%m-%d %H:%M")

    # -- internals ---------------------------------------------------------
    def _cql(self) -> str:
        clauses = ["type = page"]
        if self._config.spaces:
            spaces = ", ".join(f'"{space}"' for space in self._config.spaces)
            clauses.append(f"space in ({spaces})")
        for label in self._config.exclude_labels:
            clauses.append(f'label != "{label}"')
        return " and ".join(clauses)

    def _search(self, cql: str) -> Iterator[SourceDocument]:
        start = 0
        while True:
            response = self._session.get(
                f"{self._api}/content/search",
                params={
                    "cql": cql,
                    "limit": PAGE_SIZE,
                    "start": start,
                    "expand": "body.storage,version,space,metadata.labels,history",
                },
                timeout=self._timeout,
            )
            response.raise_for_status()
            payload = response.json()
            results = payload.get("results", [])
            for page in results:
                document = self._to_document(page)
                if document is not None:
                    yield document
            if len(results) < PAGE_SIZE:
                return
            start += PAGE_SIZE

    def _labels(self, page: dict[str, Any]) -> tuple[str, ...]:
        labels = (page.get("metadata") or {}).get("labels") or {}
        return tuple(label["name"] for label in labels.get("results", []) if label.get("name"))

    def _to_document(self, page: dict[str, Any]) -> SourceDocument | None:
        page_id = str(page.get("id") or "")
        title = (page.get("title") or "").strip()
        if not page_id or not title:
            # A page with neither could never be catalogued into something
            # readable; skip it loudly rather than storing a broken pointer.
            logger.warning("skipping a Confluence result with no id or title: %r", page.get("id"))
            return None

        labels = self._labels(page)
        if set(labels) & set(self._config.exclude_labels):
            return None

        entry_type = EntryType.DOC
        for label in labels:
            mapped = self._config.type_from_labels.get(label)
            if mapped:
                entry_type = EntryType(mapped)
                break

        body = storage_to_text(((page.get("body") or {}).get("storage") or {}).get("value", ""))
        version = (page.get("version") or {}).get("number")
        history = page.get("history") or {}
        space = (page.get("space") or {}).get("key")
        web_ui = ((page.get("_links") or {}).get("webui")) or f"/pages/{page_id}"

        return SourceDocument(
            source_id=self.source_id,
            external_id=page_id,
            title=title,
            body=body,
            location=Location(
                kind="confluence",
                ref={"page_id": page_id},
                url=f"{self._base_url}{web_ui}",
            ),
            type=entry_type,
            version=str(version) if version is not None else None,
            created_at=_parse_timestamp((history.get("createdDate"))),
            updated_at=_parse_timestamp(((page.get("version") or {}).get("when"))),
            # Labels become tags, so the same labelling that decides a type also
            # gives an operator something to filter searches by.
            tags=labels,
            extra={"space": space} if space else {},
        )
