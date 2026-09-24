"""`EntryStore` backed by Postgres.

The catalog's own database (NFR-DEP-01), never LibreChat's. Three things here
are worth understanding before changing any of it:

**Matching.** Queries arrive already normalized by `kb.domain.text`, and the
indexed columns were normalized the same way when they were written -- both
sides or neither, or the same word stops matching itself. Scoring is per query
term, weighted by field, using pg_trgm's `word_similarity`, which asks "how well
does this term match some run of words in that text" rather than comparing a
short query against a long summary as wholes.

**Ranking stops here.** This returns one ranked list per query; combining
several queries is the domain's job (`ranking.reciprocal_rank_fusion`), so that
part stays testable without a database and identical if this adapter is ever
replaced.

**`entry_search` is derived.** It holds the entry merged with its override,
rebuilt whenever either changes. That is what makes an override survive every
later sync (FR-OVR-06) without sync knowing overrides exist.
"""

from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path
from typing import Iterable, Sequence

import psycopg
from psycopg.rows import dict_row

from kb.domain.entry import Entry, EntryType
from kb.domain.merge import Override, apply_override
from kb.domain.policies import is_indexable
from kb.domain.text import normalize_all

def _default_migrations_dir() -> Path:
    """Where the .sql files live.

    `KB_MIGRATIONS_DIR` wins (the image sets it, since once this package is
    installed into site-packages nothing relative to it leads back to them).
    Otherwise walk up from this file, which finds `kb/migrations/` in a checkout.
    """
    configured = os.environ.get("KB_MIGRATIONS_DIR")
    if configured:
        return Path(configured)
    for parent in Path(__file__).resolve().parents:
        candidate = parent / "migrations"
        if candidate.is_dir():
            return candidate
    return Path("migrations")


MIGRATIONS_DIR = _default_migrations_dir()

#: Trigram indexes cannot help with a fragment shorter than three characters,
#: and a one- or two-letter term matches almost everything. Dropped unless it is
#: all the query had.
MIN_TERM_LENGTH = 3

#: Field weights. A term in the title is the strongest signal that an entry is
#: *about* the thing asked for; a term in a summary only says it is mentioned.
TITLE_WEIGHT = 3.0
KEYWORD_WEIGHT = 2.0
SUMMARY_WEIGHT = 1.0
#: Flat bonus for an exact substring hit anywhere. Product names, error strings
#: and identifiers are matched literally or not at all, and trigram similarity
#: alone under-rewards them.
CONTAINS_BONUS = 1.0

_SEARCH_SQL = f"""
WITH terms AS (SELECT unnest(%(terms)s::text[]) AS term)
SELECT s.entry_id,
       SUM({TITLE_WEIGHT} * word_similarity(t.term, s.title_norm)
         + {KEYWORD_WEIGHT} * word_similarity(t.term, s.keywords_norm)
         + {SUMMARY_WEIGHT} * word_similarity(t.term, s.summary_norm)
         + CASE WHEN s.haystack LIKE '%%' || t.term || '%%' THEN {CONTAINS_BONUS} ELSE 0 END
       ) AS score
  FROM entry_search s CROSS JOIN terms t
 WHERE s.indexable
   AND (%(types)s::text[] IS NULL OR s.type = ANY (%(types)s::text[]))
   AND (%(tags)s::text[] IS NULL OR s.tags && %(tags)s::text[])
   AND (t.term %%> s.haystack OR s.haystack LIKE '%%' || t.term || '%%')
 GROUP BY s.entry_id
HAVING SUM({TITLE_WEIGHT} * word_similarity(t.term, s.title_norm)
         + {KEYWORD_WEIGHT} * word_similarity(t.term, s.keywords_norm)
         + {SUMMARY_WEIGHT} * word_similarity(t.term, s.summary_norm)
         + CASE WHEN s.haystack LIKE '%%' || t.term || '%%' THEN {CONTAINS_BONUS} ELSE 0 END) > 0
 -- entry_id breaks ties so two identical searches never disagree about order.
 ORDER BY score DESC, s.entry_id ASC
 LIMIT %(limit)s
"""


class PostgresEntryStore:
    """Implements `kb.application.ports.EntryStore`."""

    def __init__(self, connection: psycopg.Connection) -> None:
        self._connection = connection
        self._connection.row_factory = dict_row

    @classmethod
    def connect(cls, dsn: str, *, connect_timeout: int = 5) -> "PostgresEntryStore":
        """A search must fail fast rather than hang a tool call, so the connect
        timeout is short and explicit instead of the driver's default.

        **autocommit=True is load-bearing, not a shortcut.** Without it, the
        first read starts an implicit transaction, and every later
        `connection.transaction()` block becomes a nested savepoint that
        releases without ever committing -- so writes are silently rolled back
        when the connection closes. With it, each `transaction()` block below is
        a real BEGIN/COMMIT. It also stops a long-lived reader (the MCP server)
        from holding a transaction open between tool calls.
        """
        return cls(psycopg.connect(dsn, connect_timeout=connect_timeout, autocommit=True))

    def close(self) -> None:
        self._connection.close()

    # -- schema ------------------------------------------------------------
    def migrate(self, migrations_dir: Path | None = None) -> list[str]:
        """Apply every migration not yet recorded. Returns the ones applied, so
        a caller can log "nothing to do" rather than guess. Safe to re-run."""
        directory = migrations_dir or _default_migrations_dir()
        applied: list[str] = []
        with self._connection.transaction(), self._connection.cursor() as cursor:
            cursor.execute(
                "CREATE TABLE IF NOT EXISTS schema_migration ("
                " name TEXT PRIMARY KEY, applied_at TIMESTAMPTZ NOT NULL DEFAULT now())"
            )
            cursor.execute("SELECT name FROM schema_migration")
            done = {row["name"] for row in cursor.fetchall()}
            for path in sorted(directory.glob("*.sql")):
                if path.name in done:
                    continue
                cursor.execute(path.read_text(encoding="utf-8"))
                cursor.execute("INSERT INTO schema_migration (name) VALUES (%s)", (path.name,))
                applied.append(path.name)
        return applied

    # -- reads -------------------------------------------------------------
    def get(self, entry_id: str) -> Entry | None:
        with self._connection.cursor() as cursor:
            cursor.execute("SELECT data FROM entry WHERE id = %s", (entry_id,))
            row = cursor.fetchone()
        return Entry.model_validate(row["data"]) if row else None

    def get_many(self, entry_ids: Sequence[str]) -> dict[str, Entry]:
        if not entry_ids:
            return {}
        with self._connection.cursor() as cursor:
            cursor.execute("SELECT id, data FROM entry WHERE id = ANY(%s)", (list(entry_ids),))
            rows = cursor.fetchall()
        return {row["id"]: Entry.model_validate(row["data"]) for row in rows}

    def get_override(self, entry_id: str) -> Override | None:
        return self.get_overrides([entry_id]).get(entry_id)

    def get_overrides(self, entry_ids: Sequence[str]) -> dict[str, Override]:
        if not entry_ids:
            return {}
        with self._connection.cursor() as cursor:
            cursor.execute(
                "SELECT entry_id, data FROM entry_override WHERE entry_id = ANY(%s)",
                (list(entry_ids),),
            )
            rows = cursor.fetchall()
        return {row["entry_id"]: Override.model_validate(row["data"]) for row in rows}

    def ids_for_source(self, source_id: str, *, include_deleted: bool = False) -> set[str]:
        sql = "SELECT id FROM entry WHERE source_id = %s"
        if not include_deleted:
            sql += " AND deleted_at IS NULL"
        with self._connection.cursor() as cursor:
            cursor.execute(sql, (source_id,))
            return {row["id"] for row in cursor.fetchall()}

    def search(
        self,
        normalized_query: str,
        *,
        types: Sequence[EntryType] | None = None,
        tags: Sequence[str] | None = None,
        limit: int = 30,
    ) -> list[str]:
        terms = [term for term in normalized_query.split() if len(term) >= MIN_TERM_LENGTH]
        if not terms:
            # A query of only short words (an acronym, a Persian preposition)
            # still deserves an answer -- match it whole rather than returning
            # nothing.
            terms = [normalized_query.strip()]
        if not terms[0]:
            return []
        with self._connection.cursor() as cursor:
            cursor.execute(
                _SEARCH_SQL,
                {
                    "terms": terms,
                    "types": [t.value for t in types] if types else None,
                    "tags": list(tags) if tags else None,
                    "limit": limit,
                },
            )
            return [row["entry_id"] for row in cursor.fetchall()]

    # -- writes ------------------------------------------------------------
    def upsert(self, entry: Entry) -> None:
        with self._connection.transaction(), self._connection.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO entry (id, source_id, type, data, content_hash, source_version,
                                   created_at, updated_at, last_seen_at, deleted_at)
                VALUES (%(id)s, %(source_id)s, %(type)s, %(data)s, %(content_hash)s, %(source_version)s,
                        %(created_at)s, %(updated_at)s, %(last_seen_at)s, %(deleted_at)s)
                ON CONFLICT (id) DO UPDATE SET
                    source_id = EXCLUDED.source_id,
                    type = EXCLUDED.type,
                    data = EXCLUDED.data,
                    content_hash = EXCLUDED.content_hash,
                    source_version = EXCLUDED.source_version,
                    updated_at = EXCLUDED.updated_at,
                    last_seen_at = EXCLUDED.last_seen_at,
                    deleted_at = EXCLUDED.deleted_at
                """,
                {
                    "id": entry.id,
                    "source_id": entry.source_id,
                    "type": entry.type.value,
                    "data": json.dumps(entry.model_dump(mode="json")),
                    "content_hash": entry.content_hash,
                    "source_version": entry.source_version,
                    "created_at": entry.created_at,
                    "updated_at": entry.updated_at,
                    "last_seen_at": entry.last_seen_at,
                    "deleted_at": entry.deleted_at,
                },
            )
            self._rebuild_search(cursor, entry.id)

    def touch(self, entry_id: str, *, at: datetime, source_version: str | None = None) -> None:
        """The unchanged-document path. It must not move `updated_at`
        (FR-REC-03), or every sync would look like a change, so this writes the
        two "we checked" fields and nothing else -- in the row and in the JSON,
        which are the same entry seen two ways."""
        with self._connection.transaction(), self._connection.cursor() as cursor:
            cursor.execute(
                """
                UPDATE entry
                   SET last_seen_at = %(at)s,
                       source_version = COALESCE(%(version)s, source_version),
                       data = jsonb_set(
                                jsonb_set(data, '{last_seen_at}', to_jsonb(%(at_text)s::text)),
                                '{source_version}',
                                COALESCE(to_jsonb(%(version_text)s::text), data -> 'source_version'))
                 WHERE id = %(id)s
                """,
                # The timestamp goes in twice -- once as a timestamptz column,
                # once as JSON text -- and Postgres cannot deduce two types for
                # one parameter, so each form is passed separately.
                {
                    "id": entry_id,
                    "at": at,
                    "at_text": at.isoformat(),
                    "version": source_version,
                    "version_text": source_version,
                },
            )

    def mark_missing(self, entry_ids: Iterable[str], *, at: datetime) -> int:
        ids = list(entry_ids)
        if not ids:
            return 0
        with self._connection.transaction(), self._connection.cursor() as cursor:
            cursor.execute(
                """
                UPDATE entry
                   SET deleted_at = %(at)s,
                       data = jsonb_set(data, '{deleted_at}', to_jsonb(%(at_text)s::text))
                 WHERE id = ANY(%(ids)s) AND deleted_at IS NULL
                """,
                {"ids": ids, "at": at, "at_text": at.isoformat()},
            )
            changed = cursor.rowcount
            # Out of search immediately; the row itself stays for history.
            cursor.execute(
                "UPDATE entry_search SET indexable = FALSE WHERE entry_id = ANY(%s)", (ids,)
            )
        return changed

    def revive(self, entry_id: str) -> None:
        with self._connection.transaction(), self._connection.cursor() as cursor:
            cursor.execute(
                """
                UPDATE entry
                   SET deleted_at = NULL,
                       data = jsonb_set(data, '{deleted_at}', 'null'::jsonb)
                 WHERE id = %s
                """,
                (entry_id,),
            )
            self._rebuild_search(cursor, entry_id)

    def log_miss(self, query: str, *, at: datetime) -> None:
        with self._connection.transaction(), self._connection.cursor() as cursor:
            cursor.execute("INSERT INTO search_miss (query, at) VALUES (%s, %s)", (query, at))

    def set_override(self, override: Override) -> None:
        """Stored separately from the entry, and applied when the searchable row
        is built -- which is why sync can overwrite an entry freely without ever
        knowing an override exists."""
        with self._connection.transaction(), self._connection.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO entry_override (entry_id, data, updated_at)
                VALUES (%s, %s, now())
                ON CONFLICT (entry_id) DO UPDATE SET data = EXCLUDED.data, updated_at = now()
                """,
                (override.entry_id, json.dumps(override.model_dump(mode="json"))),
            )
            self._rebuild_search(cursor, override.entry_id)

    def clear_override(self, entry_id: str) -> None:
        with self._connection.transaction(), self._connection.cursor() as cursor:
            cursor.execute("DELETE FROM entry_override WHERE entry_id = %s", (entry_id,))
            self._rebuild_search(cursor, entry_id)

    def rebuild_search_index(self) -> int:
        """Recompute every searchable row from `entry` + `entry_override`.

        The index is derived, so losing it is a rebuild rather than a restore
        (NFR-DEP-06) -- and after a change to normalization, every stored row
        needs recomputing anyway or queries and stored text stop agreeing.
        Returns how many entries were rebuilt.
        """
        with self._connection.transaction(), self._connection.cursor() as cursor:
            cursor.execute("SELECT id FROM entry")
            entry_ids = [row["id"] for row in cursor.fetchall()]
            for entry_id in entry_ids:
                self._rebuild_search(cursor, entry_id)
        return len(entry_ids)

    def top_misses(self, *, limit: int = 20) -> list[dict[str, object]]:
        """The gap log, most frequent first: a ranked list of what the
        organization has not written down."""
        with self._connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT query, count(*) AS times, max(at) AS last_seen
                  FROM search_miss
                 GROUP BY query
                 ORDER BY times DESC, last_seen DESC
                 LIMIT %s
                """,
                (limit,),
            )
            return [dict(row) for row in cursor.fetchall()]

    def status(self) -> dict[str, object]:
        """What the catalog actually contains.

        Exists so "is this thing working?" is answerable without opening psql --
        the question an operator asks first, and the one a README cannot answer.
        """
        with self._connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT count(*) FILTER (WHERE deleted_at IS NULL)  AS live,
                       count(*) FILTER (WHERE deleted_at IS NOT NULL) AS deleted
                  FROM entry
                """
            )
            totals = cursor.fetchone()
            cursor.execute(
                "SELECT source_id, count(*) AS entries FROM entry WHERE deleted_at IS NULL"
                " GROUP BY source_id ORDER BY source_id"
            )
            by_source = [dict(row) for row in cursor.fetchall()]
            cursor.execute(
                "SELECT type, count(*) AS entries FROM entry WHERE deleted_at IS NULL"
                " GROUP BY type ORDER BY type"
            )
            by_type = [dict(row) for row in cursor.fetchall()]
            cursor.execute("SELECT count(*) AS overrides FROM entry_override")
            overrides = cursor.fetchone()["overrides"]
            cursor.execute("SELECT count(*) AS indexed FROM entry_search WHERE indexable")
            indexed = cursor.fetchone()["indexed"]
            cursor.execute("SELECT count(*) AS gaps FROM search_miss")
            gaps = cursor.fetchone()["gaps"]
            cursor.execute("SELECT name FROM schema_migration ORDER BY name")
            migrations = [row["name"] for row in cursor.fetchall()]
            cursor.execute("SELECT source_id, cursor, updated_at FROM source_checkpoint ORDER BY source_id")
            checkpoints = [dict(row) for row in cursor.fetchall()]
        return {
            "entries": {"live": totals["live"], "soft_deleted": totals["deleted"], "searchable": indexed},
            "by_source": by_source,
            "by_type": by_type,
            "overrides": overrides,
            "gaps": gaps,
            "migrations": migrations,
            "checkpoints": checkpoints,
        }

    # -- lazy refresh ------------------------------------------------------
    def queue_refresh(self, entry_id: str, *, at: datetime) -> None:
        """Record that somebody read this entry and it looked stale.

        Idempotent: many readers asking about one entry is still one refresh.
        The earliest request wins, so a document people keep opening does not
        keep getting pushed to the back of the queue.
        """
        with self._connection.transaction(), self._connection.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO refresh_queue (entry_id, requested_at)
                VALUES (%s, %s)
                ON CONFLICT (entry_id) DO NOTHING
                """,
                (entry_id, at),
            )

    def take_refresh_batch(self, *, limit: int = 20, max_attempts: int = 5) -> list[str]:
        """Claim the oldest queued entries, oldest first.

        `attempts` is incremented as they are handed out, so a document that
        fails every time drops out of the queue instead of being retried
        forever -- and the row stays, with its error, for somebody to look at.
        """
        with self._connection.transaction(), self._connection.cursor() as cursor:
            cursor.execute(
                """
                UPDATE refresh_queue
                   SET attempts = attempts + 1
                 WHERE entry_id IN (
                        SELECT entry_id FROM refresh_queue
                         WHERE attempts < %(max_attempts)s
                         ORDER BY requested_at
                         LIMIT %(limit)s
                         FOR UPDATE SKIP LOCKED)
             RETURNING entry_id
                """,
                {"limit": limit, "max_attempts": max_attempts},
            )
            return [row["entry_id"] for row in cursor.fetchall()]

    def finish_refresh(self, entry_id: str, *, error: str | None = None) -> None:
        """Done with it: drop it from the queue, or leave it with its error."""
        with self._connection.transaction(), self._connection.cursor() as cursor:
            if error is None:
                cursor.execute("DELETE FROM refresh_queue WHERE entry_id = %s", (entry_id,))
            else:
                cursor.execute(
                    "UPDATE refresh_queue SET last_error = %s WHERE entry_id = %s",
                    (error[:500], entry_id),
                )

    # -- run history -------------------------------------------------------
    def record_run(
        self,
        *,
        source_id: str,
        mechanism: str,
        started_at: datetime,
        finished_at: datetime,
        counts: dict[str, int] | None = None,
        error: str | None = None,
    ) -> None:
        """One row per run, so "is this still working?" is answerable without
        reading logs that have already rotated away (FR-SCH-04)."""
        counts = counts or {}
        with self._connection.transaction(), self._connection.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO sync_run (source_id, mechanism, started_at, finished_at,
                                      created, updated, unchanged, revived, missing,
                                      summarized, error)
                VALUES (%(source_id)s, %(mechanism)s, %(started_at)s, %(finished_at)s,
                        %(created)s, %(updated)s, %(unchanged)s, %(revived)s, %(missing)s,
                        %(summarized)s, %(error)s)
                """,
                {
                    "source_id": source_id,
                    "mechanism": mechanism,
                    "started_at": started_at,
                    "finished_at": finished_at,
                    "created": counts.get("created", 0),
                    "updated": counts.get("updated", 0),
                    "unchanged": counts.get("unchanged", 0),
                    "revived": counts.get("revived", 0),
                    "missing": counts.get("missing", 0),
                    "summarized": counts.get("summarized", 0),
                    "error": error[:2000] if error else None,
                },
            )

    def recent_runs(self, *, limit: int = 20) -> list[dict[str, object]]:
        with self._connection.cursor() as cursor:
            cursor.execute(
                "SELECT source_id, mechanism, started_at, finished_at, created, updated,"
                " unchanged, revived, missing, summarized, error"
                " FROM sync_run ORDER BY started_at DESC LIMIT %s",
                (limit,),
            )
            return [dict(row) for row in cursor.fetchall()]

    # -- checkpoints (used by the incremental mechanism, phase 7) -----------
    def get_checkpoint(self, source_id: str) -> str | None:
        with self._connection.cursor() as cursor:
            cursor.execute("SELECT cursor FROM source_checkpoint WHERE source_id = %s", (source_id,))
            row = cursor.fetchone()
        return row["cursor"] if row else None

    def set_checkpoint(self, source_id: str, cursor_value: str | None) -> None:
        with self._connection.transaction(), self._connection.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO source_checkpoint (source_id, cursor, updated_at)
                VALUES (%s, %s, now())
                ON CONFLICT (source_id) DO UPDATE SET cursor = EXCLUDED.cursor, updated_at = now()
                """,
                (source_id, cursor_value),
            )

    # -- internals ---------------------------------------------------------
    def _rebuild_search(self, cursor: psycopg.Cursor, entry_id: str) -> None:
        """Recompute one entry's searchable row from the entry and its override.

        Called after anything that could change either. Rebuilding rather than
        patching means there is one definition of what is searchable, and it
        lives in the domain (`apply_override`, `is_indexable`), not in SQL.
        """
        cursor.execute("SELECT data FROM entry WHERE id = %s", (entry_id,))
        row = cursor.fetchone()
        if row is None:
            # An override recorded before its entry exists: nothing to index
            # yet, and the override stays waiting for it.
            return
        entry = Entry.model_validate(row["data"])

        cursor.execute("SELECT data FROM entry_override WHERE entry_id = %s", (entry_id,))
        override_row = cursor.fetchone()
        override = Override.model_validate(override_row["data"]) if override_row else None

        effective = apply_override(entry, override)
        cursor.execute(
            """
            INSERT INTO entry_search (entry_id, type, tags, indexable,
                                      title_norm, keywords_norm, summary_norm, haystack)
            VALUES (%(id)s, %(type)s, %(tags)s, %(indexable)s,
                    %(title)s, %(keywords)s, %(summary)s, %(haystack)s)
            ON CONFLICT (entry_id) DO UPDATE SET
                type = EXCLUDED.type, tags = EXCLUDED.tags, indexable = EXCLUDED.indexable,
                title_norm = EXCLUDED.title_norm, keywords_norm = EXCLUDED.keywords_norm,
                summary_norm = EXCLUDED.summary_norm, haystack = EXCLUDED.haystack
            """,
            _search_row(effective, override),
        )


def _search_row(effective: Entry, override: Override | None) -> dict[str, object]:
    title = normalize_all(effective.title)
    keywords = normalize_all(tuple(effective.keywords) + tuple(effective.aliases))
    summary = normalize_all(effective.summary)
    tags = normalize_all(effective.tags)
    owner = normalize_all(effective.owner_team)
    return {
        "id": effective.id,
        "type": effective.type.value,
        "tags": list(effective.tags),
        "indexable": is_indexable(effective, override),
        "title": title,
        "keywords": keywords,
        "summary": summary,
        "haystack": " ".join(part for part in (title, keywords, summary, tags, owner) if part),
    }
