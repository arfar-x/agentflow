"""The composition root: the one place adapters are constructed.

Everywhere else takes its dependencies as arguments, which is what lets the use
cases be tested with fakes and the adapters be swapped without touching
behavior. If you find yourself importing an adapter anywhere but here, that is
the design telling you something is in the wrong layer.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

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

    def close(self) -> None:
        self.store.close()


def build(settings: Settings | None = None) -> Container:
    """Wire everything up against a real database.

    `psycopg` is imported here rather than at module scope so that importing
    `kb.container` -- which the CLI does to read `--help`, and tests do to check
    wiring -- doesn't require the driver to be installed.
    """
    from kb.adapters.outbound.postgres_store import PostgresEntryStore

    settings = settings or Settings.from_env()
    store = PostgresEntryStore.connect(settings.database_url)
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
