#!/usr/bin/env bash
# Generates the secrets .env needs and prints them for you to paste in.
# Never overwrites .env directly and never regenerates values for a live
# deployment -- see docs/OPERATIONS.md for why rotating WEBUI_SECRET_KEY
# on an existing Postgres volume makes stored credentials unrecoverable.
set -euo pipefail

if [ -f .env ] && grep -qE '^WEBUI_SECRET_KEY=.+' .env 2>/dev/null; then
  echo "Refusing to run: .env already has secrets set." >&2
  echo "Regenerating these on a live deployment makes stored credentials unreadable." >&2
  echo "If you really mean to rotate, do it deliberately by hand -- see docs/OPERATIONS.md." >&2
  exit 1
fi

echo "# Paste these into .env (see .env.example for placement):"
echo
# Signs session tokens AND encrypts every credential Open WebUI stores
# (including each user's own Jira/Confluence Valves in
# config/tools/agent_skills.py) -- Open WebUI's single secret, where
# LibreChat needed four (CREDS_KEY/CREDS_IV/JWT_SECRET/JWT_REFRESH_SECRET).
echo "WEBUI_SECRET_KEY=$(openssl rand -hex 32)"
# hex, not base64 -- POSTGRES_PASSWORD gets interpolated directly into a
# postgresql://user:pass@host URI in docker-compose.yml, and base64's + /
# = are reserved there. Hex has no character that's ever unsafe in a URI,
# a shell arg, or a config file.
echo "POSTGRES_PASSWORD=$(openssl rand -hex 24)"
echo "WEBUI_ADMIN_PASSWORD=$(openssl rand -hex 12)"
echo
echo "Back these up somewhere other than this host, separately from volume"
echo "backups -- see docs/OPERATIONS.md 'Secrets' section."
