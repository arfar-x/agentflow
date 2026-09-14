#!/usr/bin/env bash
# One command to bring the stack up from a filled-in .env to a working
# login: docker compose up, wait for open-webui to actually be healthy
# (open-webui creates the admin account itself on startup, from
# WEBUI_ADMIN_EMAIL/WEBUI_ADMIN_PASSWORD/WEBUI_ADMIN_NAME in .env, if no
# user exists yet -- see docker-compose.yml's comment on that service),
# then push config/tools/agent_skills.py into Open WebUI as that admin's
# Tool (creating it the first time, updating its content on every
# subsequent run) and make it usable by every signed-in user.
#
# Idempotent -- safe to rerun. It never creates a second admin account,
# and re-pushing the Tool's content on every run is deliberate: this
# repo's file is always the source of truth, the same way
# config/librechat.yaml being mounted read-only kept LibreChat's config
# in sync with this repo on every restart.
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."

if [ ! -f .env ]; then
  echo "No .env found. Copy .env.example to .env, run scripts/generate-secrets.sh," >&2
  echo "and fill in the rest (see docs/CONFIGURATION.md) before running this." >&2
  exit 1
fi

if [ ! -f searxng/settings.yml ]; then
  echo "==> Generating searxng/settings.yml (gitignored) from its template..."
  cp searxng/settings.yml.example searxng/settings.yml
  sed -i "s/REPLACE_ME_SEARXNG_SECRET_KEY/$(openssl rand -hex 32)/" searxng/settings.yml
fi

echo "==> docker compose up -d --build"
docker compose up -d --build

echo "==> Waiting for open-webui to report healthy..."
for _ in $(seq 1 60); do
  status="$(docker compose ps --format '{{.Health}}' open-webui 2>/dev/null || true)"
  if [ "${status}" = "healthy" ]; then
    break
  fi
  sleep 5
done
if [ "${status}" != "healthy" ]; then
  echo "open-webui did not become healthy in time. Check: docker compose logs open-webui" >&2
  exit 1
fi
echo "    open-webui is healthy (creates its own admin account on first boot -- see"
echo "    docker compose logs open-webui if ${WEBUI_ADMIN_EMAIL:-your admin email} can't log in)."

# shellcheck disable=SC1091
source .env
: "${WEBUI_ADMIN_EMAIL:?set WEBUI_ADMIN_EMAIL in .env}"
: "${WEBUI_ADMIN_PASSWORD:?set WEBUI_ADMIN_PASSWORD in .env -- scripts/generate-secrets.sh generates one}"

BASE_URL="http://127.0.0.1:${PORT:-3080}"

echo "==> Signing in as ${WEBUI_ADMIN_EMAIL} to push config/tools/agent_skills.py..."
signin_resp="$(mktemp)"
signin_http_code="$(curl -s -o "${signin_resp}" -w '%{http_code}' \
  -X POST "${BASE_URL}/api/v1/auths/signin" \
  -H 'Content-Type: application/json' \
  -d "$(jq -n --arg email "${WEBUI_ADMIN_EMAIL}" --arg password "${WEBUI_ADMIN_PASSWORD}" '{email:$email,password:$password}')")"

if [ "${signin_http_code}" != "200" ]; then
  echo "    Could not sign in as ${WEBUI_ADMIN_EMAIL} (http ${signin_http_code}) -- skipping the" >&2
  echo "    Tools upload. This usually means WEBUI_ADMIN_PASSWORD in .env no longer matches a" >&2
  echo "    real admin account (e.g. it was reset by hand). Import config/tools/agent_skills.py" >&2
  echo "    by hand instead: Admin Panel -> Workspace -> Tools -> Import (upload the file)," >&2
  echo "    then set its sharing to Public so every user can use their own Jira/Confluence Valves." >&2
  rm -f "${signin_resp}"
else
  TOKEN="$(jq -r .token "${signin_resp}")"
  rm -f "${signin_resp}"

  TOOL_ID="agent_skills"
  TOOL_NAME="Agent Skills (Jira & Confluence)"
  BODY="$(jq -n --arg id "${TOOL_ID}" --arg name "${TOOL_NAME}" --rawfile content config/tools/agent_skills.py '{
    id: $id,
    name: $name,
    content: $content,
    meta: {description: "Jira and Confluence tools, backed by this deployment'\''s mcp-agent-skills MCP server."},
    access_grants: [{principal_type: "user", principal_id: "*", permission: "read"}]
  }')"

  tool_resp="$(mktemp)"
  update_http_code="$(curl -s -o "${tool_resp}" -w '%{http_code}' \
    -X POST "${BASE_URL}/api/v1/tools/id/${TOOL_ID}/update" \
    -H "Authorization: Bearer ${TOKEN}" -H 'Content-Type: application/json' \
    -d "${BODY}")"

  if [ "${update_http_code}" = "200" ]; then
    echo "    Updated the existing Agent Skills tool."
  else
    create_http_code="$(curl -s -o "${tool_resp}" -w '%{http_code}' \
      -X POST "${BASE_URL}/api/v1/tools/create" \
      -H "Authorization: Bearer ${TOKEN}" -H 'Content-Type: application/json' \
      -d "${BODY}")"
    if [ "${create_http_code}" = "200" ]; then
      echo "    Created the Agent Skills tool -- every user can now open Workspace -> Tools ->"
      echo "    Agent Skills (Jira & Confluence) -> the wrench icon to enter their own Jira/"
      echo "    Confluence credentials, same one-time step LibreChat's MCP Settings form was."
    else
      echo "    Could not create or update the Agent Skills tool (update: http ${update_http_code}," >&2
      echo "    create: http ${create_http_code}):" >&2
      cat "${tool_resp}" >&2
      echo "    Import config/tools/agent_skills.py by hand instead: Admin Panel -> Workspace ->" >&2
      echo "    Tools -> Import." >&2
    fi
  fi
  rm -f "${tool_resp}"
fi

echo "==> Done. docker compose ps:"
docker compose ps
