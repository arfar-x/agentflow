#!/usr/bin/env bash
# Restores volumes from a backup.sh snapshot directory. Stops the stack
# first -- restoring into live volumes corrupts Mongo/Postgres data files.
#
# Usage: scripts/restore.sh <backup-dir>
#
# Does NOT restore .env automatically -- confirm the backup's .env matches
# what you intend to run before overwriting your current one by hand. See
# docs/OPERATIONS.md for why WEBUI_SECRET_KEY matters.
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."

BACKUP_DIR="${1:?Usage: scripts/restore.sh <backup-dir>}"
if [ ! -d "${BACKUP_DIR}" ]; then
  echo "No such directory: ${BACKUP_DIR}" >&2
  exit 1
fi

echo "This will STOP the stack and OVERWRITE the following volumes from ${BACKUP_DIR}:"
ls "${BACKUP_DIR}"/*.tar.gz 2>/dev/null | sed 's/^/  /' || { echo "No .tar.gz files found in ${BACKUP_DIR}" >&2; exit 1; }
read -rp "Type 'yes' to continue: " CONFIRM
[ "${CONFIRM}" = "yes" ] || { echo "Aborted."; exit 1; }

docker compose down

COMPOSE_PROJECT="$(basename "$(pwd)")"
for archive in "${BACKUP_DIR}"/*.tar.gz; do
  vol="$(basename "${archive}" .tar.gz)"
  full_name="${COMPOSE_PROJECT}_${vol}"
  echo "Restoring ${vol} -> ${full_name}"
  docker volume create "${full_name}" >/dev/null
  docker run --rm \
    -v "${full_name}:/target" \
    -v "$(pwd)/${BACKUP_DIR}:/backup:ro" \
    alpine:3 \
    sh -c "rm -rf /target/* /target/..?* /target/.[!.]* 2>/dev/null; tar xzf /backup/${vol}.tar.gz -C /target"
done

echo
echo "Volumes restored. If this backup's .env differs from your current one"
echo "(especially WEBUI_SECRET_KEY), copy ${BACKUP_DIR}/.env into place"
echo "BEFORE starting the stack, or Postgres's stored credentials"
echo "(including every user's own Jira/Confluence Valves) will not decrypt."
echo
echo "Then: docker compose up -d"
