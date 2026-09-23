#!/usr/bin/env bash
# One command to bring the stack up from a filled-in .env to a working
# login: docker compose up, wait for api to actually be healthy, then
# create the admin account via LibreChat's own config/create-user.js if
# no user exists yet.
#
# Idempotent -- safe to rerun. It never creates a second admin account
# and never touches Mongo directly (the LibreChat app user itself is
# created declaratively by mongo-init/init-librechat-user.sh via the
# official mongo image's /docker-entrypoint-initdb.d/ convention, not by
# this script).
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."

if [ ! -f .env ]; then
  echo "No .env found. Copy .env.example to .env, run scripts/generate-secrets.sh," >&2
  echo "and fill in the rest (see docs/CONFIGURATION.md) before running this." >&2
  exit 1
fi

# shellcheck disable=SC1091
source .env

if [ ! -f searxng/settings.yml ]; then
  echo "==> Generating searxng/settings.yml (gitignored) from its template..."
  cp searxng/settings.yml.example searxng/settings.yml
  sed -i "s/REPLACE_ME_SEARXNG_SECRET_KEY/$(openssl rand -hex 32)/" searxng/settings.yml
fi

scripts/render-librechat-config.sh

echo "==> docker compose up -d --build"
docker compose up -d --build

echo "==> Waiting for api to report healthy..."
for _ in $(seq 1 60); do
  status="$(docker compose ps --format '{{.Health}}' api 2>/dev/null || true)"
  if [ "${status}" = "healthy" ]; then
    break
  fi
  sleep 5
done
if [ "${status}" != "healthy" ]; then
  echo "api did not become healthy in time. Check: docker compose logs api" >&2
  exit 1
fi
echo "    api is healthy."

echo "==> Checking for an existing user..."
user_count="$(docker compose exec -T api node config/list-users.js 2>/dev/null | grep -oE 'Total Users: [0-9]+' | grep -oE '[0-9]+' || echo 0)"

if [ "${user_count}" -gt 0 ]; then
  echo "    ${user_count} user(s) already exist -- skipping account creation."
else
  : "${ADMIN_EMAIL:?set ADMIN_EMAIL in .env}"
  : "${ADMIN_NAME:?set ADMIN_NAME in .env}"
  : "${ADMIN_USERNAME:?set ADMIN_USERNAME in .env}"
  : "${ADMIN_PASSWORD:?set ADMIN_PASSWORD in .env -- scripts/generate-secrets.sh generates one}"
  echo "==> Creating admin account (${ADMIN_EMAIL})..."
  echo "y" | docker compose exec -T api node config/create-user.js \
    "${ADMIN_EMAIL}" "${ADMIN_NAME}" "${ADMIN_USERNAME}" "${ADMIN_PASSWORD}" >/dev/null
  echo "    Created. Log in at the chat host with:"
  echo "      email:    ${ADMIN_EMAIL}"
  echo "      password: ${ADMIN_PASSWORD}"
fi

echo "==> Done. docker compose ps:"
docker compose ps
