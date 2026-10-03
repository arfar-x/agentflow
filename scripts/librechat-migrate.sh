#!/usr/bin/env bash
# Runs LibreChat's own tenant-index migration (config/migrate-tenant-indexes.js,
# shipped in the api image since v0.8.8) against this stack's MongoDB.
#
# Needed once when moving a database created on v0.8.7 or earlier to v0.8.8+:
# older unique indexes (email_1, name_1, ...) conflict with the tenant-scoped
# ones, and `api` logs "Index build failed" on every start until this runs.
# LibreChat never runs it at startup -- see its UPGRADING.md.
#
# Dry run by default: lists the legacy indexes it would replace and changes
# nothing. `--apply` stops `api` (the only writer here), runs the migration from
# a one-off container of the same image, and starts `api` again. Rerunning
# --apply after it has succeeded is a no-op; after a failure it completes the
# partial migration. Take `make backup` first.
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."

if [ ! -f .env ]; then
  echo "No .env found. Copy .env.example to .env and fill it in first." >&2
  exit 1
fi

apply=0
case "${1:-}" in
  --apply) apply=1 ;;
  "") ;;
  *) echo "Usage: $0 [--apply]" >&2; exit 1 ;;
esac

migrate() {
  # --no-deps: MongoDB must already be up; nothing else may start alongside.
  # -w /app: the npm scripts live in the root package, not /app/api.
  docker compose run --rm --no-deps -w /app api npm run --silent "$@"
}

docker compose up -d mongodb

if [ "${apply}" -eq 0 ]; then
  echo "==> Dry run: legacy indexes the migration would replace (nothing is changed)"
  migrate migrate:tenant-indexes:dry-run
  echo
  echo "To apply: make backup && make librechat-migrate APPLY=1"
  exit 0
fi

echo "==> Stopping api (the migration must run with no writers)"
docker compose stop api

echo "==> Applying the tenant-index migration"
if ! migrate migrate:tenant-indexes; then
  echo >&2
  echo "Migration failed -- api is left STOPPED on purpose. Fix the error above," >&2
  echo "then rerun \`make librechat-migrate APPLY=1\` (it completes a partial run)." >&2
  exit 1
fi

echo "==> Starting api"
docker compose up -d api
echo "Done. Check \`make logs SERVICE=api\` no longer reports \"Index build failed\"."
