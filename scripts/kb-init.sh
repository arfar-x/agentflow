#!/usr/bin/env bash
# Prepares the knowledge base's database: applies every migration in
# kb/migrations/, and creates the least-privilege role the query path runs as.
#
# Idempotent -- safe to rerun after every upgrade, and the normal way to apply a
# new migration. Migrations are recorded in the schema_migration table, so an
# already-applied one is skipped rather than reapplied.
#
# Runs psql inside the kb-db container, so it needs no client tools on the host
# and no published port on that service (it has none, by design).
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."

if [ ! -f .env ]; then
  echo "No .env found. Copy .env.example to .env and fill it in first." >&2
  exit 1
fi

# shellcheck disable=SC1091
source .env

: "${KB_POSTGRES_DB:=agentflow_kb}"
: "${KB_POSTGRES_USER:=kb}"
: "${KB_READER_PASSWORD:?set KB_READER_PASSWORD in .env -- scripts/generate-secrets.sh generates one}"

psql_kb() {
  docker compose exec -T kb-db psql -v ON_ERROR_STOP=1 -U "${KB_POSTGRES_USER}" -d "${KB_POSTGRES_DB}" "$@"
}

echo "==> Waiting for kb-db..."
for _ in $(seq 1 30); do
  if docker compose exec -T kb-db pg_isready -U "${KB_POSTGRES_USER}" -d "${KB_POSTGRES_DB}" -q; then
    break
  fi
  sleep 2
done
docker compose exec -T kb-db pg_isready -U "${KB_POSTGRES_USER}" -d "${KB_POSTGRES_DB}" -q || {
  echo "kb-db did not become ready. Check: docker compose logs kb-db" >&2
  exit 1
}

# The role is created before the migrations run, because the migration's own
# GRANT block is skipped when the role doesn't exist yet (it has to be, so the
# same file also applies to a throwaway test database).
echo "==> Ensuring the kb_reader role exists..."
psql_kb -q <<SQL
DO \$\$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'kb_reader') THEN
        EXECUTE format('ALTER ROLE kb_reader WITH LOGIN PASSWORD %L', '${KB_READER_PASSWORD}');
    ELSE
        EXECUTE format('CREATE ROLE kb_reader WITH LOGIN PASSWORD %L', '${KB_READER_PASSWORD}');
    END IF;
END
\$\$;
SQL

echo "==> Applying migrations..."
psql_kb -q -c "CREATE TABLE IF NOT EXISTS schema_migration (name TEXT PRIMARY KEY, applied_at TIMESTAMPTZ NOT NULL DEFAULT now())"

applied=0
for migration in kb/migrations/*.sql; do
  name="$(basename "${migration}")"
  already="$(psql_kb -tAc "SELECT 1 FROM schema_migration WHERE name = '${name}'")"
  if [ "${already}" = "1" ]; then
    echo "    skip ${name} (already applied)"
    continue
  fi
  echo "    apply ${name}"
  psql_kb -q -f - < "${migration}"
  psql_kb -q -c "INSERT INTO schema_migration (name) VALUES ('${name}')"
  applied=$((applied + 1))
done

echo "==> Done (${applied} migration(s) applied)."
echo "    The query path connects as kb_reader (read-only, plus the gap log);"
echo "    sync connects as ${KB_POSTGRES_USER}. See docs/CONFIGURATION.md."
