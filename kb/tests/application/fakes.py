"""In-memory stand-ins for every port.

The point of the hexagonal split is that the use cases can be proven before any
database or HTTP client exists, so these fakes are the only infrastructure the
application tests need. The store's `search` is a deliberately crude substring
match: it must rank plausibly, not replicate Postgres -- ranking quality is the
Postgres adapter's own test.
"""

from __future__ import annotations

from datetime import datetime
from typing import Iterable, Iterator, Mapping, Sequence

from kb.application.ports.entry_store import ContentSlice
from kb.application.ports.heartbeat import Heartbeat
from kb.application.ports.knowledge_source import SourceDocument
from kb.application.ports.run_log import RunRecord, RunStats
from kb.application.ports.sync_queue import RequestStatus, SyncRequest
from kb.application.ports.summarizer import SummaryDraft
from kb.domain.content import RawContent
from kb.domain.entry import Entry, EntryType
from kb.domain.merge import Override
from kb.domain.runs import INTERRUPTED, RunStatus, Trigger, final_status
from kb.domain.text import normalize, normalize_all


class FakeClock:
    def __init__(self, now: datetime) -> None:
        self._now = now

    def now(self) -> datetime:
        return self._now

    def advance(self, delta) -> None:
        self._now = self._now + delta


class FakeEntryStore:
    def __init__(self) -> None:
        self.entries: dict[str, Entry] = {}
        self.overrides: dict[str, Override] = {}
        self.misses: list[tuple[str, datetime]] = []
        self.refresh_queue: dict[str, dict] = {}
        self.checkpoints: dict[str, str | None] = {}
        #: entry id -> (text as stored, the version it was read at)
        self.contents: dict[str, tuple[RawContent, str | None]] = {}
        self.upserts = 0
        self.touches = 0

    # -- reads -------------------------------------------------------------
    def get(self, entry_id: str) -> Entry | None:
        return self.entries.get(entry_id)

    def get_many(self, entry_ids: Sequence[str]) -> dict[str, Entry]:
        return {i: self.entries[i] for i in entry_ids if i in self.entries}

    def get_override(self, entry_id: str) -> Override | None:
        return self.overrides.get(entry_id)

    def get_overrides(self, entry_ids: Sequence[str]) -> dict[str, Override]:
        return {i: self.overrides[i] for i in entry_ids if i in self.overrides}

    def ids_for_source(self, source_id: str, *, include_deleted: bool = False) -> set[str]:
        return {
            entry.id
            for entry in self.entries.values()
            if entry.source_id == source_id and (include_deleted or entry.deleted_at is None)
        }

    def search(
        self,
        normalized_query: str,
        *,
        types: Sequence[EntryType] | None = None,
        tags: Sequence[str] | None = None,
        limit: int = 30,
    ) -> list[str]:
        terms = [t for t in normalized_query.split() if t]
        scored: list[tuple[int, str]] = []
        for entry in self.entries.values():
            if entry.deleted_at is not None:
                continue
            if types and entry.type not in types:
                continue
            if tags and not set(tags) & set(entry.tags):
                continue
            haystack = " ".join(
                [
                    normalize_all(entry.title),
                    normalize_all(entry.summary),
                    normalize_all(entry.keywords),
                    normalize_all(entry.aliases),
                    normalize_all(entry.tags),
                ]
            )
            # Title matches count double, the same bias the real adapter's
            # weighting has.
            title = normalize_all(entry.title)
            score = sum(term in haystack for term in terms) + sum(term in title for term in terms)
            if score:
                scored.append((score, entry.id))
        scored.sort(key=lambda pair: (-pair[0], pair[1]))
        return [entry_id for _, entry_id in scored[:limit]]

    # -- writes ------------------------------------------------------------
    def upsert(self, entry: Entry) -> None:
        self.entries[entry.id] = entry
        self.upserts += 1

    def touch(self, entry_id: str, *, at: datetime, source_version: str | None = None) -> None:
        entry = self.entries[entry_id]
        self.entries[entry_id] = entry.model_copy(
            update={"last_seen_at": at, "source_version": source_version or entry.source_version}
        )
        self.touches += 1

    def mark_missing(self, entry_ids: Iterable[str], *, at: datetime) -> int:
        changed = 0
        for entry_id in entry_ids:
            entry = self.entries.get(entry_id)
            if entry is None or entry.deleted_at is not None:
                continue
            self.entries[entry_id] = entry.model_copy(update={"deleted_at": at})
            self.contents.pop(entry_id, None)
            changed += 1
        return changed

    def revive(self, entry_id: str) -> None:
        self.entries[entry_id] = self.entries[entry_id].model_copy(update={"deleted_at": None})

    # -- raw content -------------------------------------------------------
    def put_content(self, entry_id: str, content: RawContent, *, source_version: str | None) -> bool:
        if entry_id not in self.entries:
            raise KeyError(entry_id)  # the real table's foreign key
        if self.contents.get(entry_id) == (content, source_version):
            return False
        self.contents[entry_id] = (content, source_version)
        return True

    def delete_content(self, entry_id: str) -> None:
        self.contents.pop(entry_id, None)

    def purge_content(self, source_id: str) -> int:
        doomed = [i for i in self.contents if self.entries[i].source_id == source_id]
        for entry_id in doomed:
            del self.contents[entry_id]
        return len(doomed)

    def read_content(self, entry_id: str, *, offset: int, limit: int) -> ContentSlice | None:
        if entry_id not in self.contents:
            return None
        content, version = self.contents[entry_id]
        return ContentSlice(
            text=content.text[offset : offset + limit],
            offset=offset,
            total_chars=len(content.text),
            truncated=content.truncated,
            original_bytes=content.original_bytes,
            source_version=version,
        )

    def content_ids(self, entry_ids: Sequence[str]) -> set[str]:
        return {i for i in entry_ids if i in self.contents}

    def log_miss(self, query: str, *, at: datetime) -> None:
        self.misses.append((query, at))

    def set_override(self, override: Override) -> None:
        self.overrides[override.entry_id] = override

    def clear_override(self, entry_id: str) -> None:
        self.overrides.pop(entry_id, None)

    # -- lazy refresh ------------------------------------------------------
    def queue_refresh(self, entry_id: str, *, at: datetime) -> None:
        self.refresh_queue.setdefault(entry_id, {"requested_at": at, "attempts": 0})

    def take_refresh_batch(self, *, limit: int = 20, max_attempts: int = 5) -> list[str]:
        ready = [
            (entry_id, state)
            for entry_id, state in self.refresh_queue.items()
            if state["attempts"] < max_attempts
        ]
        ready.sort(key=lambda pair: pair[1]["requested_at"])
        taken = []
        for entry_id, state in ready[:limit]:
            state["attempts"] += 1
            taken.append(entry_id)
        return taken

    def finish_refresh(self, entry_id: str, *, error: str | None = None) -> None:
        if error is None:
            self.refresh_queue.pop(entry_id, None)
        elif entry_id in self.refresh_queue:
            self.refresh_queue[entry_id]["last_error"] = error

    # -- checkpoints and run history ---------------------------------------
    def get_checkpoint(self, source_id: str) -> str | None:
        return self.checkpoints.get(source_id)

    def set_checkpoint(self, source_id: str, cursor_value: str | None) -> None:
        self.checkpoints[source_id] = cursor_value



class FakeSummarizer:
    """Counts its calls, because "an unchanged sync makes zero model calls" is
    the property most worth protecting."""

    def __init__(self, drafts: dict[str, SummaryDraft] | None = None) -> None:
        self.calls: list[str] = []
        self._drafts = drafts or {}

    def draft(self, document: SourceDocument) -> SummaryDraft:
        self.calls.append(document.external_id)
        if document.external_id in self._drafts:
            return self._drafts[document.external_id]
        return SummaryDraft(
            title={"en": document.title},
            summary={"en": document.body[:120]},
            keywords=tuple(normalize(document.title).split()[:5]),
        )


class FakeKnowledgeSource:
    def __init__(self, source_id: str, documents: Sequence[SourceDocument], checkpoint: str | None = None) -> None:
        self._source_id = source_id
        self._documents = list(documents)
        self._checkpoint = checkpoint

    @property
    def source_id(self) -> str:
        return self._source_id

    def changed_since(self, checkpoint: str | None) -> Iterator[SourceDocument]:
        yield from self._documents

    def list_all(self) -> Iterator[SourceDocument]:
        yield from self._documents

    def fetch(self, external_id: str) -> SourceDocument | None:
        for document in self._documents:
            if document.external_id == external_id:
                return document
        return None

    def checkpoint(self) -> str | None:
        return self._checkpoint



class FakeRunLog:
    """Run history in a list, behaving like the Postgres one where it matters:
    a run exists from `start_run`, and its status is derived when it ends."""

    def __init__(self) -> None:
        self.runs: list[RunRecord] = []

    def _replace(self, run_id: int, **changes) -> None:
        for index, run in enumerate(self.runs):
            if run.id == run_id:
                self.runs[index] = run.model_copy(update=changes)
                return
        raise KeyError(run_id)

    def start_run(
        self,
        *,
        source_id: str,
        mechanism: str,
        started_at: datetime,
        triggered_by: Trigger = Trigger.SCHEDULED,
        mode: str | None = None,
        dry_run: bool = False,
        forced: bool = False,
    ) -> int:
        run_id = len(self.runs) + 1
        self.runs.append(
            RunRecord(
                id=run_id, source_id=source_id, mechanism=mechanism, status=RunStatus.RUNNING,
                triggered_by=triggered_by, mode=mode, dry_run=dry_run, forced=forced,
                started_at=started_at,
            )
        )
        return run_id

    def finish_run(
        self,
        run_id: int,
        *,
        finished_at: datetime,
        counts: Mapping[str, int],
        rejected: Sequence[str] = (),
        checkpoint: str | None = None,
        error: str | None = None,
    ) -> None:
        numbers = {
            name: int(counts.get(name, 0))
            for name in (
                "seen", "created", "updated", "unchanged", "revived", "missing",
                "summarized", "content_stored", "content_purged",
            )
        }
        self._replace(
            run_id,
            finished_at=finished_at,
            status=final_status(error=error, rejected=rejected),
            rejected=tuple(rejected),
            checkpoint=checkpoint,
            error=error,
            **numbers,
        )

    def close_interrupted(self, *, at: datetime) -> int:
        stale = [run.id for run in self.runs if run.status is RunStatus.RUNNING]
        for run_id in stale:
            self._replace(run_id, status=RunStatus.ERROR, finished_at=at, error=INTERRUPTED)
        return len(stale)

    def running_sources(self) -> set[str]:
        return {run.source_id for run in self.runs if run.status is RunStatus.RUNNING}

    def last_scheduled(self) -> dict[str, datetime]:
        latest: dict[str, datetime] = {}
        for run in self.runs:
            if run.triggered_by is not Trigger.SCHEDULED or run.dry_run:
                continue
            if run.status is RunStatus.RUNNING:
                continue
            key = f"{run.mechanism}:{run.source_id}"
            if key not in latest or run.started_at > latest[key]:
                latest[key] = run.started_at
        return latest

    def list_runs(
        self,
        *,
        source_id: str | None = None,
        status: RunStatus | None = None,
        mode: str | None = None,
        before_id: int | None = None,
        limit: int = 50,
    ) -> list[RunRecord]:
        found = [
            run
            for run in reversed(self.runs)
            if (source_id is None or run.source_id == source_id)
            and (status is None or run.status is status)
            and (mode is None or mode in (run.mode, run.mechanism))
            and (before_id is None or run.id < before_id)
        ]
        return found[:limit]

    def get_run(self, run_id: int) -> RunRecord | None:
        return next((run for run in self.runs if run.id == run_id), None)

    def run_stats(self, *, since: datetime) -> RunStats:
        recent = [run for run in self.runs if run.started_at >= since]
        count = lambda status: sum(1 for run in recent if run.status is status)  # noqa: E731
        return RunStats(
            runs=len(recent),
            succeeded=count(RunStatus.SUCCESS),
            warned=count(RunStatus.WARNING),
            failed=count(RunStatus.ERROR),
            running=count(RunStatus.RUNNING),
        )


class FakeSyncQueue:
    def __init__(self) -> None:
        self.requests: list[SyncRequest] = []

    def _replace(self, request_id: int, **changes) -> None:
        for index, request in enumerate(self.requests):
            if request.id == request_id:
                self.requests[index] = request.model_copy(update=changes)
                return
        raise KeyError(request_id)

    def enqueue(self, request: SyncRequest, *, at: datetime) -> int:
        request_id = len(self.requests) + 1
        self.requests.append(
            request.model_copy(
                update={"id": request_id, "requested_at": at, "status": RequestStatus.QUEUED}
            )
        )
        return request_id

    def claim_next(self, *, at: datetime) -> SyncRequest | None:
        for request in self.requests:
            if request.status is RequestStatus.QUEUED:
                self._replace(request.id, status=RequestStatus.RUNNING, started_at=at)
                return next(r for r in self.requests if r.id == request.id)
        return None

    def finish_request(
        self, request_id: int, *, at: datetime, status: RequestStatus,
        run_ids: Sequence[int] = (), error: str | None = None,
    ) -> None:
        self._replace(request_id, status=status, finished_at=at, run_ids=tuple(run_ids), error=error)

    def cancel(self, request_id: int, *, at: datetime) -> bool:
        request = next((r for r in self.requests if r.id == request_id), None)
        if request is None or request.status is not RequestStatus.QUEUED:
            return False
        self._replace(request_id, status=RequestStatus.CANCELLED, finished_at=at)
        return True

    def list_requests(
        self, *, statuses: Sequence[RequestStatus] | None = None, limit: int = 50
    ) -> list[SyncRequest]:
        found = [r for r in reversed(self.requests) if statuses is None or r.status in statuses]
        return found[:limit]

    def fail_running(self, *, at: datetime, error: str) -> int:
        stuck = [r.id for r in self.requests if r.status is RequestStatus.RUNNING]
        for request_id in stuck:
            self._replace(request_id, status=RequestStatus.FAILED, finished_at=at, error=error)
        return len(stuck)


class FakeHeartbeats:
    def __init__(self) -> None:
        self.beats: list[Heartbeat] = []

    def beat(self, heartbeat: Heartbeat) -> None:
        self.beats.append(heartbeat)

    def last_beat(self) -> Heartbeat | None:
        return self.beats[-1] if self.beats else None
