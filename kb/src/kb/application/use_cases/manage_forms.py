"""Write, change and remove manual records, and have them published.

A saved record is not yet in the catalog: it reaches it through the scheduler,
like everything else (FR-ADM-04, FR-SRC-07), so saving also asks for that. The
operator is told it is queued, not that it is done.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Mapping

from pydantic import ValidationError

from kb.application.ports.clock import Clock
from kb.application.ports.entry_store import EntryStore
from kb.application.ports.form_records import FormRecord, FormRecords
from kb.application.use_cases.request_sync import RequestSync
from kb.domain.identity import entry_id_for
from kb.sources_config import FORM_SOURCE_ID


class FormNotFound(LookupError):
    pass


class FormExists(ValueError):
    pass


class FormInvalid(ValueError):
    """The record is not acceptable; the message says which field and why."""


def _problems(exc: ValidationError) -> str:
    return "; ".join(
        f"{'.'.join(str(part) for part in d['loc']) or 'record'}: {d['msg'].removeprefix('Value error, ')}"
        for d in exc.errors()
    )


def publication_state(record: FormRecord, store: EntryStore) -> str:
    """Whether the catalog says what the record says: `published`, or `pending`
    until the scheduler has caught up with the last save."""
    entry = store.get(entry_id_for(FORM_SOURCE_ID, record.slug))
    if entry is None or entry.deleted_at is not None:
        return "pending"
    return "published" if entry.source_version == str(record.revision) else "pending"


class ManageForms:
    def __init__(self, records: FormRecords, requests: RequestSync, clock: Clock) -> None:
        self._records = records
        self._requests = requests
        self._clock = clock

    def create(self, data: Mapping[str, Any]) -> FormRecord:
        record = self._build(data)
        if self._records.get(record.slug) is not None:
            raise FormExists(f"there is already a record called {record.slug!r}")
        saved = self._records.create(record, at=self._clock.now())
        self._publish()
        return saved

    def update(self, slug: str, data: Mapping[str, Any]) -> FormRecord:
        existing = self._records.get(slug)
        if existing is None:
            raise FormNotFound(slug)
        # The slug names the entry and cannot move; everything else may.
        record = self._build({**data, "slug": slug}, created_at=existing.created_at)
        saved = self._records.update(
            record.model_copy(update={"revision": existing.revision}), at=self._clock.now()
        )
        self._publish()
        return saved

    def delete(self, slug: str) -> None:
        if not self._records.delete(slug):
            raise FormNotFound(slug)
        # The full pass that follows finds the entry no longer yielded and
        # soft-deletes it (FR-REC-05, FR-SRC-07).
        self._publish()

    @staticmethod
    def _build(data: Mapping[str, Any], *, created_at: datetime | None = None) -> FormRecord:
        try:
            return FormRecord.model_validate({**data, "created_at": created_at})
        except ValidationError as exc:
            raise FormInvalid(_problems(exc)) from exc

    def _publish(self) -> None:
        self._requests.publish_forms()
