"""Integration tests need a real Postgres with pg_trgm.

They skip when `KB_TEST_DATABASE_URL` is unset, so the everyday `pytest` run
stays a sub-second, service-free loop. To run them:

    docker run -d --name kb-test-db -e POSTGRES_PASSWORD=kbtest \\
        -e POSTGRES_DB=kb_test -p 55432:5432 pgvector/pgvector:0.8.0-pg15-trixie
    KB_TEST_DATABASE_URL=postgresql://postgres:kbtest@127.0.0.1:55432/kb_test pytest tests/integration
"""

from __future__ import annotations

import os

import pytest

DSN_ENV = "KB_TEST_DATABASE_URL"


@pytest.fixture(scope="session")
def dsn() -> str:
    value = os.environ.get(DSN_ENV)
    if not value:
        pytest.skip(f"{DSN_ENV} is not set -- see tests/integration/conftest.py")
    return value


@pytest.fixture()
def store(dsn):
    """A migrated, empty store per test.

    Truncating rather than recreating keeps each test fast, and `RESTART
    IDENTITY CASCADE` means a test can never see a row another one wrote.
    """
    psycopg = pytest.importorskip("psycopg")
    from kb.adapters.outbound.postgres_store import PostgresEntryStore

    store = PostgresEntryStore.connect(dsn)
    store.migrate()
    with store._connection.transaction(), store._connection.cursor() as cursor:
        cursor.execute(
            "TRUNCATE entry, entry_override, entry_search, search_miss, "
            "source_checkpoint, sync_run RESTART IDENTITY CASCADE"
        )
    try:
        yield store
    finally:
        store.close()
    del psycopg
