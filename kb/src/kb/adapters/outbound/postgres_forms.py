"""Manual records, in Postgres.

Written by the console as `kb_admin` and read by the scheduler (through the
`form` source) as the owner role -- the one table both sides share, and the
reason the grants give `kb_admin` write access to it and nothing else of the
catalog's side (kb/grants/admin.sql).
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Sequence

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from kb.application.ports.form_records import FormRecord

_COLUMNS = (
    "slug, title, body, type, tags, keywords, aliases, owner_team, revision, summary,"
    " created_at, updated_at"
)


def _record(row: dict[str, Any]) -> FormRecord:
    return FormRecord(
        slug=row["slug"],
        title=row["title"],
        body=row["body"],
        type=row["type"],
        tags=tuple(row["tags"]),
        keywords=tuple(row["keywords"]),
        aliases=tuple(row["aliases"]),
        owner_team=row["owner_team"],
        revision=row["revision"],
        summary=row["summary"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


class PostgresFormRecords:
    """Implements `kb.application.ports.form_records.FormRecords`."""

    def __init__(self, connection: psycopg.Connection) -> None:
        self._connection = connection
        self._connection.row_factory = dict_row

    def list(self) -> Sequence[FormRecord]:
        with self._connection.cursor() as cursor:
            cursor.execute(f"SELECT {_COLUMNS} FROM form_record ORDER BY updated_at DESC, slug")
            return [_record(row) for row in cursor.fetchall()]

    def get(self, slug: str) -> FormRecord | None:
        with self._connection.cursor() as cursor:
            cursor.execute(f"SELECT {_COLUMNS} FROM form_record WHERE slug = %s", (slug,))
            row = cursor.fetchone()
        return _record(row) if row else None

    def create(self, record: FormRecord, *, at: datetime) -> FormRecord:
        with self._connection.transaction(), self._connection.cursor() as cursor:
            cursor.execute(
                f"""
                INSERT INTO form_record (slug, title, body, type, tags, keywords, aliases,
                                         owner_team, revision, summary, created_at, updated_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, 1, %s, %s, %s)
                RETURNING {_COLUMNS}
                """,
                (
                    record.slug, Jsonb(dict(record.title)), record.body, record.type.value,
                    list(record.tags), list(record.keywords), list(record.aliases),
                    record.owner_team, Jsonb(dict(record.summary)), at, at,
                ),
            )
            return _record(cursor.fetchone())

    def update(self, record: FormRecord, *, at: datetime) -> FormRecord:
        with self._connection.transaction(), self._connection.cursor() as cursor:
            cursor.execute(
                f"""
                UPDATE form_record
                   SET title = %s, body = %s, type = %s, tags = %s, keywords = %s,
                       aliases = %s, owner_team = %s, summary = %s,
                       revision = revision + 1, updated_at = %s
                 WHERE slug = %s
                RETURNING {_COLUMNS}
                """,
                (
                    Jsonb(dict(record.title)), record.body, record.type.value,
                    list(record.tags), list(record.keywords), list(record.aliases),
                    record.owner_team, Jsonb(dict(record.summary)), at, record.slug,
                ),
            )
            row = cursor.fetchone()
        if row is None:
            raise LookupError(record.slug)
        return _record(row)

    def delete(self, slug: str) -> bool:
        with self._connection.transaction(), self._connection.cursor() as cursor:
            cursor.execute("DELETE FROM form_record WHERE slug = %s", (slug,))
            return cursor.rowcount > 0
