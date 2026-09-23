#!/usr/bin/env bash
# Generates the secrets .env needs and prints them for you to paste in.
# Never overwrites .env directly and never regenerates values for a live
# deployment -- see docs/OPERATIONS.md for why CREDS_KEY/CREDS_IV rotation
# on an existing Mongo volume makes stored credentials unrecoverable.
set -euo pipefail

if [ -f .env ] && grep -qE '^(CREDS_KEY|JWT_SECRET)=.+' .env 2>/dev/null; then
  echo "Refusing to run: .env already has secrets set." >&2
  echo "Regenerating these on a live deployment makes stored credentials unreadable." >&2
  echo "If you really mean to rotate, do it deliberately by hand -- see docs/OPERATIONS.md." >&2
  exit 1
fi

echo "# Paste these into .env (see .env.example for placement):"
echo
echo "CREDS_KEY=$(openssl rand -hex 32)"
echo "CREDS_IV=$(openssl rand -hex 16)"
echo "JWT_SECRET=$(openssl rand -hex 32)"
echo "JWT_REFRESH_SECRET=$(openssl rand -hex 32)"
# hex, not base64 -- MONGO_APP_PASSWORD gets interpolated directly into a
# mongodb://user:pass@host URI in docker-compose.yml, and base64's + / =
# are reserved there ("Password contains unescaped characters"). Hex has
# no character that's ever unsafe in a URI, a shell arg, or a config
# file, so every password here uses it, not just the one that needs to.
echo "MONGO_ROOT_PASSWORD=$(openssl rand -hex 24)"
echo "MONGO_APP_PASSWORD=$(openssl rand -hex 24)"
echo "MEILI_MASTER_KEY=$(openssl rand -hex 32)"
echo "POSTGRES_PASSWORD=$(openssl rand -hex 24)"
# The knowledge base has its own database and its own two identities: the owner
# that sync writes as, and the read-only role the query path runs as.
echo "KB_POSTGRES_PASSWORD=$(openssl rand -hex 24)"
echo "KB_READER_PASSWORD=$(openssl rand -hex 24)"
echo "OPENID_SESSION_SECRET=$(openssl rand -hex 32)  # only needed if using Keycloak"
echo "ADMIN_PASSWORD=$(openssl rand -hex 12)"
echo "ADMIN_PANEL_SESSION_SECRET=$(openssl rand -hex 32)"
echo
echo "Back these up somewhere other than this host, separately from volume"
echo "backups -- see docs/OPERATIONS.md 'Secrets' section."
