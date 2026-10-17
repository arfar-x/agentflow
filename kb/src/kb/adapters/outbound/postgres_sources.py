"""The console's copy of the source configuration, and its history, in Postgres.

Written only by the console, as `kb_admin`; the scheduler never reads it. The
file is the authority (FR-CFG-12) -- this exists so the console can ask "what
changed, when and by whom" and show where a source came from, without parsing
a file on every request.
"""

from __future__ import annotations

from datetime import datetime
from typing import Collection

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from kb.application.ports.source_history import Revision
from kb.sources_config import SourcesConfig


class PostgresSourceHistory:
    """Implements `kb.application.ports.source_history.SourceHistory`."""

    def __init__(self, connection: psycopg.Connection) -> None:
        self._connection = connection
        self._connection.row_factory = dict_row

    def record(
        self,
        *,
        action: str,
        source_id: str | None,
        actor: str,
        at: datetime,
        before_text: str,
        after_text: str,
        diff: str,
        after_hash: str,
    ) -> int:
        with self._connection.transaction(), self._connection.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO kb_source_revision (action, source_id, actor, at, before_text,
                                                after_text, diff, after_hash)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                RETURNING id
                """,
                (action, source_id, actor, at, before_text, after_text, diff, after_hash),
            )
            return cursor.fetchone()["id"]

    def last_hash(self) -> str | None:
        with self._connection.cursor() as cursor:
            cursor.execute("SELECT after_hash FROM kb_source_revision ORDER BY id DESC LIMIT 1")
            row = cursor.fetchone()
        return row["after_hash"] if row else None

    def last_text(self) -> str | None:
        with self._connection.cursor() as cursor:
            cursor.execute("SELECT after_text FROM kb_source_revision ORDER BY id DESC LIMIT 1")
            row = cursor.fetchone()
        return row["after_text"] if row else None

    def revisions(self, *, limit: int = 50) -> list[Revision]:
        with self._connection.cursor() as cursor:
            cursor.execute(
                "SELECT id, action, source_id, actor, at, diff FROM kb_source_revision"
                " ORDER BY id DESC LIMIT %s",
                (max(1, min(limit, 200)),),
            )
            return [Revision.model_validate(dict(row)) for row in cursor.fetchall()]

    def mirror(self, config: SourcesConfig, *, created: Collection[str], at: datetime) -> None:
        with self._connection.transaction(), self._connection.cursor() as cursor:
            ids = [source.id for source in config.sources]
            for source in config.sources:
                cursor.execute(
                    """
                    INSERT INTO kb_source (id, kind, origin, enabled, config, updated_at)
                    VALUES (%(id)s, %(kind)s, %(origin)s, %(enabled)s, %(config)s, %(at)s)
                    ON CONFLICT (id) DO UPDATE SET
                        kind = EXCLUDED.kind, enabled = EXCLUDED.enabled,
                        config = EXCLUDED.config, updated_at = EXCLUDED.updated_at
                    """,
                    {
                        "id": source.id,
                        "kind": source.kind,
                        "origin": "ui" if source.id in created else "yaml",
                        "enabled": source.enabled,
                        "config": Jsonb(source.model_dump(mode="json")),
                        "at": at,
                    },
                )
            cursor.execute("DELETE FROM kb_source WHERE id <> ALL(%s)", (ids,))

    def origins(self) -> dict[str, str]:
        with self._connection.cursor() as cursor:
            cursor.execute("SELECT id, origin FROM kb_source")
            return {row["id"]: row["origin"] for row in cursor.fetchall()}
