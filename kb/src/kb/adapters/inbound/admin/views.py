"""Shaping what the console shows.

Plain functions from domain objects to JSON-ready dicts, kept apart from the
routes so what a card or a table contains can be read, and tested, without a
web framework in the way.
"""

from __future__ import annotations

import json
from datetime import date, datetime
from enum import Enum
from pathlib import PurePath
from typing import Any, Mapping

from pydantic import BaseModel

from kb.application.ports.heartbeat import Heartbeat
from kb.application.ports.run_log import RunRecord
from kb.sources_config import SourcesConfig

#: Kept apart from a source's own settings in the card, which shows the rest.
_COMMON_FIELDS = {"id", "kind", "enabled", "mechanisms", "store_raw_content", "raw_content_max_bytes"}


def to_json(value: Any) -> str:
    return json.dumps(value, default=_default, ensure_ascii=False)


def _default(value: Any) -> Any:
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json")
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, (set, frozenset, tuple)):
        return list(value)
    if isinstance(value, PurePath):
        return str(value)
    raise TypeError(f"{type(value).__name__} is not JSON serializable")


def run_summary(run: RunRecord | None) -> dict[str, Any] | None:
    """A run as a line in a table or a source card; the full record is
    `run_detail`."""
    if run is None:
        return None
    return {
        "id": run.id,
        "source_id": run.source_id,
        "mechanism": run.mechanism,
        "mode": run.mode,
        "status": run.status,
        "triggered_by": run.triggered_by,
        "dry_run": run.dry_run,
        "forced": run.forced,
        "started_at": run.started_at,
        "finished_at": run.finished_at,
        "duration_ms": run.duration_ms,
        "seen": run.seen,
        "created": run.created,
        "updated": run.updated,
        "unchanged": run.unchanged,
        "revived": run.revived,
        "missing": run.missing,
        "summarized": run.summarized,
        "content_stored": run.content_stored,
        "content_purged": run.content_purged,
        "rejected_count": len(run.rejected),
        "error": run.error,
    }


def run_detail(run: RunRecord) -> dict[str, Any]:
    return {**run_summary(run), "rejected": list(run.rejected), "checkpoint": run.checkpoint}


def heartbeat_view(beat: Heartbeat | None, alive: bool) -> dict[str, Any] | None:
    if beat is None:
        return None
    return {
        "alive": alive,
        "beat_at": beat.beat_at,
        "working_on": beat.working_on,
        "summarizer": beat.summarizer,
        "summarizer_error": beat.summarizer_error,
    }


def source_card(
    source: Any,
    config: SourcesConfig,
    *,
    entries: int,
    content: Mapping[str, int] | None,
    last_run: RunRecord | None,
    credentials: Mapping[str, bool] | None,
) -> dict[str, Any]:
    policy = config.content_policy_for(source)
    mechanisms = config.mechanisms_for(source).model_dump(exclude_none=True)
    return {
        "id": source.id,
        "kind": source.kind,
        "enabled": source.enabled,
        "mechanisms": mechanisms,
        # Said per source because turning it on shares the full text with every
        # catalog user whatever their access to the original (spec C6).
        "store_raw_content": policy.store,
        "raw_content_max_bytes": policy.max_bytes,
        "settings": source.model_dump(mode="json", exclude=_COMMON_FIELDS),
        "entries": entries,
        "content": {
            "documents": (content or {}).get("documents", 0),
            "text_bytes": (content or {}).get("text_bytes", 0),
        },
        "last_run": run_summary(last_run),
        # Which credential variables the scheduler can see -- never a value.
        "credentials": dict(credentials or {}),
        "origin": "yaml",
    }
