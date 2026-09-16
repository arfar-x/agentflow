#!/usr/bin/env bash
# Generates the secrets .env needs and prints them for you to paste in.
# Never overwrites .env directly and never regenerates values for a live
# deployment -- see docs/OPERATIONS.md for why rotating SECRET_KEY on an
# existing Postgres volume makes stored credentials unrecoverable.
set -euo pipefail

if [ -f .env ] && grep -qE '^SECRET_KEY=.+' .env 2>/dev/null; then
  echo "Refusing to run: .env already has secrets set." >&2
  echo "Regenerating these on a live deployment makes stored credentials unreadable." >&2
  echo "If you really mean to rotate, do it deliberately by hand -- see docs/OPERATIONS.md." >&2
  exit 1
fi

REDIS_PASSWORD="$(openssl rand -hex 24)"
PGVECTOR_PASSWORD="$(openssl rand -hex 24)"
SANDBOX_KEY="$(openssl rand -hex 24)"

echo "# Paste these into .env (see .env.example for placement):"
echo
echo "SECRET_KEY=$(openssl rand -hex 32)"
echo "DB_PASSWORD=$(openssl rand -hex 24)"
echo
echo "REDIS_PASSWORD=${REDIS_PASSWORD}"
# Dify does not derive this from REDIS_PASSWORD -- it's a separate,
# fully-spelled-out connection string that embeds the same password again.
echo "CELERY_BROKER_URL=redis://:${REDIS_PASSWORD}@redis:6379/1"
echo
# Same value on both sides deliberately -- one configures the pgvector
# container's own postgres user, the other is how api/worker connect to it.
echo "PGVECTOR_PASSWORD=${PGVECTOR_PASSWORD}"
echo "PGVECTOR_POSTGRES_PASSWORD=${PGVECTOR_PASSWORD}"
echo
echo "PLUGIN_DAEMON_KEY=$(openssl rand -hex 24)"
echo "PLUGIN_DIFY_INNER_API_KEY=$(openssl rand -hex 24)"
echo
# Same value on both sides -- one configures the sandbox service itself,
# the other is how api/worker authenticate to it.
echo "SANDBOX_API_KEY=${SANDBOX_KEY}"
echo "CODE_EXECUTION_API_KEY=${SANDBOX_KEY}"
echo
echo "DIFY_AGENT_API_TOKEN=$(openssl rand -hex 24)"
echo "DIFY_AGENT_SERVER_SECRET_KEY=$(openssl rand -hex 24)"
echo
echo "DIFY_ADMIN_PASSWORD=$(openssl rand -hex 12)"
echo
echo "Back these up somewhere other than this host, separately from volume"
echo "backups -- see docs/OPERATIONS.md 'Secrets' section."
