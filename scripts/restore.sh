#!/usr/bin/env bash
# Restores volumes/ from a backup.sh snapshot directory. Stops the stack
# first -- restoring into a live volumes/ tree corrupts Postgres data files.
#
# Usage: scripts/restore.sh <backup-dir>
#
# Does NOT restore .env automatically -- confirm the backup's .env matches
# what you intend to run before overwriting your current one by hand. See
# docs/OPERATIONS.md for why SECRET_KEY matters.
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."

BACKUP_DIR="${1:?Usage: scripts/restore.sh <backup-dir>}"
if [ ! -f "${BACKUP_DIR}/volumes.tar.gz" ]; then
  echo "No volumes.tar.gz found in ${BACKUP_DIR}" >&2
  exit 1
fi

echo "This will STOP the stack and OVERWRITE ./volumes/ from ${BACKUP_DIR}/volumes.tar.gz."
read -rp "Type 'yes' to continue: " CONFIRM
[ "${CONFIRM}" = "yes" ] || { echo "Aborted."; exit 1; }

docker compose down

rm -rf volumes
tar xzf "${BACKUP_DIR}/volumes.tar.gz"

echo
echo "volumes/ restored. If this backup's .env differs from your current one"
echo "(especially SECRET_KEY), copy ${BACKUP_DIR}/.env into place BEFORE"
echo "starting the stack, or Postgres's stored credentials"
echo "(model provider keys, tool credentials) will not decrypt."
echo
echo "Then: docker compose up -d"
