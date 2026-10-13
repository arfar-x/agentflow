#!/usr/bin/env bash
# Snapshots every named volume this stack persists state in, plus .env and each
# module's env file (kb/.env, ...) -- the secrets that make the snapshots
# readable: Mongo's stored credentials are encrypted with .env's keys, and the
# catalog database was initialised with kb/.env's passwords.
#
# Usage: scripts/backup.sh [output-dir]   (default: ./backups/<timestamp>)
#
# Restoring without the matching .env from the SAME backup is a documented
# way to end up with a Mongo database that opens but whose stored
# credentials are permanently unreadable -- see docs/OPERATIONS.md.
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."

TIMESTAMP="$(date -u +%Y%m%d-%H%M%S)"
OUT_DIR="${1:-backups/${TIMESTAMP}}"
mkdir -p "${OUT_DIR}"

VOLUMES=(mongodb_data pgvector_data kb_data meili_data librechat_uploads librechat_images librechat_logs)
COMPOSE_PROJECT="$(basename "$(pwd)")"

echo "Backing up to ${OUT_DIR}/"

for vol in "${VOLUMES[@]}"; do
  full_name="${COMPOSE_PROJECT}_${vol}"
  if ! docker volume inspect "${full_name}" >/dev/null 2>&1; then
    echo "  skip ${vol} (volume ${full_name} does not exist)"
    continue
  fi
  echo "  ${vol} -> ${vol}.tar.gz"
  docker run --rm \
    -v "${full_name}:/source:ro" \
    -v "$(pwd)/${OUT_DIR}:/backup" \
    alpine:3 \
    tar czf "/backup/${vol}.tar.gz" -C /source .
done

if [ -f .env ]; then
  cp .env "${OUT_DIR}/.env"
  chmod 600 "${OUT_DIR}/.env"
  echo "  .env -> ${OUT_DIR}/.env (contains secrets -- store this backup securely)"
else
  echo "  WARNING: no .env found to back up" >&2
fi

# Module env files: <module>/.env (the tracked template is .env.example).
for module_env in */.env; do
  [ -f "${module_env}" ] || continue
  mkdir -p "${OUT_DIR}/$(dirname "${module_env}")"
  cp "${module_env}" "${OUT_DIR}/${module_env}"
  chmod 600 "${OUT_DIR}/${module_env}"
  echo "  ${module_env} -> ${OUT_DIR}/${module_env} (contains secrets)"
done

echo "Done: ${OUT_DIR}"
