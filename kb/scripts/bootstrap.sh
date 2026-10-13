#!/usr/bin/env bash
# Brings the knowledge base to a usable state, and says what is left.
#
# Called by scripts/bootstrap.sh on every `make up`, and safe to run alone.
# Everything here is idempotent: it creates what is missing and leaves what
# exists, so re-running after an upgrade applies a new migration and nothing
# else.
#
# What it does NOT do is enable a source or invent a credential. Those are
# decisions, and the last section prints exactly which ones are still open.
#
# It does not build: `make up` already does that for every service, and
# `make kb-setup` builds before calling this. A kb/ change that isn't in the
# image is a service quietly running last week's code -- which looks exactly
# like "the catalog stopped syncing".
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/../.."

say() { printf '    %s\n' "$*"; }

echo "==> Knowledge base..."

# 1. The env file and the source configuration, from their templates on a first
# run (or moved/seeded from where they lived before this module owned them).
# First, because everything below reads the env file it creates.
kb/scripts/prepare.sh

# The module's own env file -- not the stack's .env.
# shellcheck disable=SC1091
source kb/.env.kb

: "${KB_POSTGRES_DB:=agentflow_kb}"
: "${KB_POSTGRES_USER:=kb}"

# 2. Schema and the least-privilege query role.
kb/scripts/init.sh | sed 's/^/    /'

# 3. What is still missing, in the order it matters.
missing=()
if [ -n "${KB_SUMMARIZER_URL:-}" ] && [ -n "${KB_SUMMARIZER_MODEL:-}" ]; then
  # Configured is not the same as working: ask the endpoint what it serves,
  # rather than finding out after a sync has catalogued 400 undescribed pages.
  if ! docker compose run --rm -T kb-cli check 2>/dev/null | grep -q '"summarizer": {"ok": true'; then
    missing+=("a reachable OpenAI-compatible summarizer -- KB_SUMMARIZER_URL is set but the endpoint did not answer GET \${KB_SUMMARIZER_URL}/models; run 'make kb-check' for the error")
  fi
else
  missing+=("KB_SUMMARIZER_URL + KB_SUMMARIZER_MODEL (+ KB_SUMMARIZER_API_KEY if it needs one) in kb/.env.kb -- without them entries are catalogued under their real titles but undescribed, and cross-language search will not work")
fi

has_source_creds=false
[ -n "${KB_CONFLUENCE_BASE_URL:-${CONFLUENCE_BASE_URL:-}}" ] && has_source_creds=true
[ -n "${KB_JIRA_BASE_URL:-${JIRA_BASE_URL:-}}" ] && has_source_creds=true
[ -n "${GITLAB_BASE_URL:-}" ] && has_source_creds=true
$has_source_creds || \
  missing+=("a read-only service account: KB_CONFLUENCE_* / KB_JIRA_* / GITLAB_TOKEN (in kb/.env.kb) -- sync has to see a space in order to catalog it")

enabled="$(grep -cE '^\s*enabled:\s*true' kb/config/sources.yaml || true)"
if [ "${enabled}" = "0" ]; then
  missing+=("at least one source enabled in kb/config/sources.yaml -- run 'make kb-sources-discover WRITE=1' to see what is there")
fi

if [ ${#missing[@]} -eq 0 ]; then
  say "ready: $(docker compose ps --status running --format '{{.Service}}' 2>/dev/null | grep -c '^kb\|^mcp-kb' || echo 0) service(s) running, ${enabled} source(s) enabled"
  # First time everything lines up, the catalog itself is still empty --
  # don't make the operator wait for the scheduler's first tick (up to 15m)
  # just to see it work. A later `make up` with the catalog already
  # populated skips this: the scheduler owns ongoing freshness from here.
  live="$(docker compose run --rm -T kb-cli status 2>/dev/null | grep -oE '"live": [0-9]+' | grep -oE '[0-9]+' || echo 0)"
  if [ "${live:-0}" = "0" ]; then
    say "catalog is empty -- running the initial sync now (can take a while for large spaces)"
    kb/scripts/sync-all.sh || say "initial sync hit an error above -- fix it and rerun 'make kb-sync-all'"
  fi
  say "the scheduler keeps it fresh from here; 'make kb-status' shows what the catalog holds"
else
  say "usable, but the catalog will stay empty until:"
  for item in "${missing[@]}"; do
    printf '      - %s\n' "${item}"
  done
  say "then: make kb-sources-discover WRITE=1  ->  edit kb/config/sources.yaml  ->  make kb-sync SOURCE=<id>"
fi
