"""The composition root: the one place adapters are constructed.

Everywhere else takes its dependencies as arguments, which is what lets the use
cases be tested with fakes and the adapters be swapped without touching
behavior. If you find yourself importing an adapter anywhere but here, that is
the design telling you something is in the wrong layer.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Callable

from kb.application.ports.clock import SystemClock
from kb.application.ports.summarizer import SummaryDraft
from kb.application.use_cases.get_entry import GetEntry
from kb.application.use_cases.reconcile_document import ReconcileDocument
from kb.application.use_cases.search_catalog import SearchCatalog
from kb.application.use_cases.sync_source import SyncSource
from kb.config import Settings

if TYPE_CHECKING:  # the adapter is imported lazily, see below
    from kb.adapters.outbound.postgres_store import PostgresEntryStore


class NullSummarizer:
    """Used when no model endpoint is configured.

    Sync then still runs: documents are catalogued under their real titles and
    remain findable by title and location, just undescribed. Blocking instead
    would make the catalog hostage to an endpoint it only needs while writing.
    """

    def draft(self, document) -> SummaryDraft:  # noqa: ANN001 - port shape
        return SummaryDraft()


@dataclass(slots=True)
class Container:
    settings: Settings
    store: "PostgresEntryStore"
    search: SearchCatalog
    get_entry: GetEntry
    sync: SyncSource
    #: Shared by every mechanism, so the scheduler reconciles through the same
    #: funnel sync does rather than building a second one.
    reconcile: ReconcileDocument
    clock: SystemClock
    #: Run history, the request queue and the scheduler's heartbeat: operational
    #: state beside the catalog (see adapters/outbound/postgres_runs.py).
    runs: Any = None
    queue: Any = None
    heartbeats: Any = None
    #: Closes whatever else was opened for the above.
    _closers: list[Callable[[], None]] = field(default_factory=list)

    def close(self) -> None:
        for close in self._closers:
            try:
                close()
            except Exception:  # shutting down; nothing useful left to do with it
                pass
        self.store.close()


def build(settings: Settings | None = None) -> Container:
    """Wire everything up against a real database.

    `psycopg` is imported here rather than at module scope so that importing
    `kb.container` -- which the CLI does to read `--help`, and tests do to check
    wiring -- doesn't require the driver to be installed.
    """
    import psycopg

    from kb.adapters.outbound.postgres_runs import (
        PostgresHeartbeat,
        PostgresRunLog,
        PostgresSyncQueue,
    )
    from kb.adapters.outbound.postgres_store import PostgresEntryStore

    settings = settings or Settings.from_env()
    store = PostgresEntryStore.connect(settings.database_url)
    # The heartbeat is written from its own thread while a long sync holds the
    # main connection, so it gets a connection of its own rather than sharing
    # one across threads mid-transaction.
    beat_connection = psycopg.connect(settings.database_url, connect_timeout=5, autocommit=True)
    clock = SystemClock(settings.tzinfo)
    reconcile = ReconcileDocument(store, build_summarizer(settings), clock)
    return Container(
        settings=settings,
        store=store,
        search=SearchCatalog(
            store, clock, default_limit=settings.default_limit, stale_after=settings.stale_after
        ),
        get_entry=GetEntry(store, clock, stale_after=settings.stale_after),
        sync=SyncSource(store, reconcile, clock),
        reconcile=reconcile,
        clock=clock,
        runs=PostgresRunLog(store.connection),
        queue=PostgresSyncQueue(store.connection),
        heartbeats=PostgresHeartbeat(beat_connection),
        _closers=[beat_connection.close],
    )


def build_summarizer(settings: Settings):
    """The model endpoint, or a stand-in that describes nothing."""
    if not settings.summarizer_configured:
        return NullSummarizer()
    from kb.adapters.outbound.llm_summarizer import LlmSummarizer

    return LlmSummarizer(
        base_url=settings.summarizer_url,
        model=settings.summarizer_model,
        api_key=settings.summarizer_api_key,
        languages=settings.summary_languages,
    )


@dataclass(slots=True)
class AdminContainer:
    """What the admin console is wired to: reads of everything, and the few
    tables it may write -- never the catalog (FR-ADM-04), which the database
    role enforces whatever this object is handed."""

    settings: Settings
    store: "PostgresEntryStore"
    runs: Any
    queue: Any
    heartbeats: Any
    clock: SystemClock
    #: Has `check()`; None when no summarizer is configured.
    summarizer: Any = None
    #: Room for what later parts of the console add (forms, sources).
    extras: dict[str, Any] = field(default_factory=dict)

    def close(self) -> None:
        self.store.close()


def build_admin(settings: Settings | None = None) -> AdminContainer:
    """Wire the console against the database as `kb_admin`.

    One connection, used from one event-loop thread (see admin/app.py), so no
    pool and no cross-thread transactions.
    """
    from kb.adapters.outbound.postgres_runs import (
        PostgresHeartbeat,
        PostgresRunLog,
        PostgresSyncQueue,
    )
    from kb.adapters.outbound.postgres_store import PostgresEntryStore

    settings = settings or Settings.from_env()
    store = PostgresEntryStore.connect(settings.database_url)
    return AdminContainer(
        settings=settings,
        store=store,
        runs=PostgresRunLog(store.connection),
        queue=PostgresSyncQueue(store.connection),
        heartbeats=PostgresHeartbeat(store.connection),
        clock=SystemClock(settings.tzinfo),
        summarizer=build_summarizer(settings) if settings.summarizer_configured else None,
    )
