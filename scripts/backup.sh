#!/usr/bin/env bash
# Snapshots every bind-mounted volume this stack persists state in, plus
# .env (which holds the secret that makes the db_postgres snapshot
# readable). Unlike the rest of this repo's history, Dify's own official
# docker-compose.yaml keeps durable state in host-directory bind mounts
# under ./volumes/, not named Docker volumes -- kept as-is here (not
# converted) to stay consistent with Dify's own tested layout and
# upstream backup/restore documentation.
#
# Usage: scripts/backup.sh [output-dir]   (default: ./backups/<timestamp>)
#
# Restoring without the matching .env from the SAME backup is a documented
# way to end up with a Postgres database that opens but whose stored
# credentials are permanently unreadable -- see docs/OPERATIONS.md.
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."

TIMESTAMP="$(date -u +%Y%m%d-%H%M%S)"
OUT_DIR="${1:-backups/${TIMESTAMP}}"
mkdir -p "${OUT_DIR}"

echo "Backing up to ${OUT_DIR}/"

if [ -d volumes ]; then
  echo "  volumes/ -> volumes.tar.gz"
  tar czf "${OUT_DIR}/volumes.tar.gz" volumes/
else
  echo "  WARNING: no volumes/ directory found (has the stack ever been started?)" >&2
fi

if [ -f .env ]; then
  cp .env "${OUT_DIR}/.env"
  chmod 600 "${OUT_DIR}/.env"
  echo "  .env -> ${OUT_DIR}/.env (contains secrets -- store this backup securely)"
else
  echo "  WARNING: no .env found to back up" >&2
fi

echo "Done: ${OUT_DIR}"
