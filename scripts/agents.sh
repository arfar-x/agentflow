#!/usr/bin/env bash
# Declarative agent management -- wrapper behind `make agent-export` and
# `make agent-import`. The real work is scripts/agent-sync.js, streamed into
# the running `api` container (it needs LibreChat's own models); this
# script only moves files across the container boundary.
#
#   scripts/agents.sh export    database -> agents/*.yaml (replaces them)
#   scripts/agents.sh import    agents/*.yaml -> database. Optional, per run:
#                                 DRY_RUN=1         preview, write nothing
#                                 OWNER_EMAIL=...   force that account as owner
#                                 MODEL_PROVIDER=... / MODEL_NAME=...
#                                                   use these for every agent instead
#                                                   of what the files say
#                                 ALLOW_RENAME=1    let a file rename an existing agent
#                               OWNER_EMAIL, MODEL_PROVIDER and MODEL_NAME may also be
#                               set in .env (gitignored), so they apply without editing
#                               a tracked file; a value given on the command line wins.
#
# AGENTS_DIR (default: agents) is the host-side directory. See
# docs/AGENT_SYNC.md for the file format and semantics.
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."

mode="${1:-}"
case "${mode}" in export | import) ;; *)
  echo "Usage: scripts/agents.sh export|import   (DRY_RUN=1 for import preview)" >&2
  exit 2
  ;;
esac

host_dir="${AGENTS_DIR:-agents}"

# Fill a variable from .env only when it wasn't given (or is empty) for this
# run. .env is gitignored, so this is where per-deployment choices belong.
env_file="${ENV_FILE:-.env}"
from_env_file() {
  local name="$1" value
  if [ -z "${!name:-}" ] && [ -f "${env_file}" ]; then
    value="$(grep -E "^${name}=" "${env_file}" | tail -1 | cut -d= -f2- || true)"
    value="${value%\"}"; value="${value#\"}"; value="${value%\'}"; value="${value#\'}"
    export "${name}=${value}"
  fi
}
for v in OWNER_EMAIL MODEL_PROVIDER MODEL_NAME; do from_env_file "${v}"; done
remote_dir=/tmp/agentflow-agents

if [ -z "$(docker compose ps --status running --quiet api 2>/dev/null)" ]; then
  echo "The api container isn't running. Start the stack first: make up" >&2
  exit 1
fi

stage="$(mktemp -d)"
cleanup() {
  rm -rf "${stage}"
  docker compose exec -T api rm -rf "${remote_dir}" >/dev/null 2>&1 || true
}
trap cleanup EXIT
docker compose exec -T api rm -rf "${remote_dir}"

run_sync() {
  docker compose exec -T -w /app \
    -e AGENTS_MODE="${mode}" -e AGENTS_DIR="${remote_dir}" -e DRY_RUN="${DRY_RUN:-0}" -e OWNER_EMAIL="${OWNER_EMAIL:-}" \
    -e MODEL_PROVIDER="${MODEL_PROVIDER:-}" -e MODEL_NAME="${MODEL_NAME:-}" -e ALLOW_RENAME="${ALLOW_RENAME:-0}" \
    api node - <scripts/agent-sync.js
}

if [ "${mode}" = "export" ]; then
  run_sync
  docker compose cp "api:${remote_dir}/." "${stage}/"
  mkdir -p "${host_dir}"
  # The export mirrors the database: agents deleted there stop having a file here.
  # Only *.yaml is replaced -- a README or anything else in the directory stays.
  removed=0
  for f in "${host_dir}"/*.yaml; do
    [ -e "${f}" ] || continue
    if [ ! -e "${stage}/$(basename "${f}")" ]; then
      echo "removed   ${f} (no longer in the database)"
      removed=$((removed + 1))
    fi
    rm -f "${f}"
  done
  cp "${stage}"/*.yaml "${host_dir}/"
  echo "Wrote $(find "${stage}" -name '*.yaml' | wc -l | tr -d ' ') file(s) to ${host_dir}/."
else
  shopt -s nullglob
  files=("${host_dir}"/*.yaml "${host_dir}"/*.yml)
  if [ "${#files[@]}" -eq 0 ]; then
    echo "No agent files in ${host_dir}/ -- run \`make agent-export\` first." >&2
    exit 1
  fi
  cp "${files[@]}" "${stage}/"
  docker compose cp "${stage}/." "api:${remote_dir}"
  run_sync
fi
