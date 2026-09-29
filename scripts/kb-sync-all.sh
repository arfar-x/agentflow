#!/usr/bin/env bash
# Full-syncs every source enabled in config/kb-sources.yaml, one at a time.
#
# Called by `make kb-sync-all`, and by scripts/kb-bootstrap.sh right after a
# fresh setup so the catalog is populated immediately instead of waiting for
# the scheduler's first tick. Safe to rerun: each source's own content-hash
# gate makes re-syncing unchanged documents a no-op.
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."

force=()
[ "${1:-}" = "--force" ] && force=(--force)

mapfile -t ids < <(awk '/^[[:space:]]*- id:/{id=$3} /^[[:space:]]*enabled: true/{print id}' config/kb-sources.yaml)

if [ ${#ids[@]} -eq 0 ]; then
  echo "No source is enabled in config/kb-sources.yaml -- see 'make kb-sources'." >&2
  exit 1
fi

for id in "${ids[@]}"; do
  echo "==> Syncing ${id} (full)..."
  docker compose run --rm kb-cli sync --source "${id}" --mode full "${force[@]}"
done
