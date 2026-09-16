#!/usr/bin/env bash
# One command to bring the stack up from a filled-in .env to a working
# login: docker compose up, wait for `api` to actually be healthy, create
# the admin account via Dify's own /console/api/setup if no user exists
# yet, then push the mcp-agent-skills MCP tool provider into Dify
# declaratively (creating it the first time, updating it on every
# subsequent run) so it's ready to attach to an app without anyone
# clicking through Studio's "Add MCP Server" form by hand.
#
# Idempotent -- safe to rerun. It never creates a second admin account,
# and re-pushing the MCP provider's config on every run is deliberate:
# this repo's .env is always the source of truth for its URL/timeout.
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

# shellcheck disable=SC1091
source .env
: "${DIFY_ADMIN_EMAIL:?set DIFY_ADMIN_EMAIL in .env}"
: "${DIFY_ADMIN_NAME:?set DIFY_ADMIN_NAME in .env}"
: "${DIFY_ADMIN_PASSWORD:?set DIFY_ADMIN_PASSWORD in .env -- scripts/generate-secrets.sh generates one}"

BASE_URL="http://127.0.0.1:${EXPOSE_NGINX_PORT:-80}"

echo "==> Waiting for nginx to route console API requests..."
code=""
for _ in $(seq 1 30); do
  code="$(curl -s -o /dev/null -w '%{http_code}' "${BASE_URL}/console/api/setup" || true)"
  [ "${code}" = "200" ] && break
  sleep 2
done
if [ "${code}" != "200" ]; then
  echo "nginx never returned 200 for ${BASE_URL}/console/api/setup (last status: ${code:-none})." >&2
  echo "Check: docker compose logs nginx api -- and confirm EXPOSE_NGINX_PORT" >&2
  echo "(${EXPOSE_NGINX_PORT:-80}) isn't already bound to something else on this host." >&2
  exit 1
fi

echo "==> Creating admin account (${DIFY_ADMIN_EMAIL}) if none exists yet..."
setup_resp_raw="$(curl -s "${BASE_URL}/console/api/setup")"
if ! printf '%s' "${setup_resp_raw}" | jq empty 2>/dev/null; then
  echo "Unexpected (non-JSON) response from ${BASE_URL}/console/api/setup:" >&2
  printf '%s\n' "${setup_resp_raw}" >&2
  echo "Check docker compose logs nginx api for what's actually answering this port." >&2
  exit 1
fi
setup_step="$(printf '%s' "${setup_resp_raw}" | jq -r '.step // empty')"
if [ "${setup_step}" = "finished" ]; then
  echo "    An account already exists -- skipping creation."
else
  setup_resp="$(mktemp)"
  setup_http_code="$(curl -s -o "${setup_resp}" -w '%{http_code}' \
    -X POST "${BASE_URL}/console/api/setup" \
    -H 'Content-Type: application/json' \
    -d "$(jq -n --arg email "${DIFY_ADMIN_EMAIL}" --arg name "${DIFY_ADMIN_NAME}" --arg password "${DIFY_ADMIN_PASSWORD}" \
      '{email:$email,name:$name,password:$password}')")"
  if [ "${setup_http_code}" = "201" ]; then
    echo "    Created. Log in at the chat host with:"
    echo "      email:    ${DIFY_ADMIN_EMAIL}"
    echo "      password: ${DIFY_ADMIN_PASSWORD}"
  else
    echo "    Setup call returned http ${setup_http_code} (likely already initialized):" >&2
    cat "${setup_resp}" >&2
  fi
  rm -f "${setup_resp}"
fi

echo "==> Signing in as ${DIFY_ADMIN_EMAIL} to push declarative config..."
COOKIE_JAR="$(mktemp)"
signin_resp="$(mktemp)"
signin_http_code="$(curl -s -o "${signin_resp}" -w '%{http_code}' -c "${COOKIE_JAR}" \
  -X POST "${BASE_URL}/console/api/login" \
  -H 'Content-Type: application/json' \
  -d "$(jq -n --arg email "${DIFY_ADMIN_EMAIL}" --arg password "$(printf '%s' "${DIFY_ADMIN_PASSWORD}" | base64 -w0)" \
    '{email:$email,password:$password}')")"
rm -f "${signin_resp}"

if [ "${signin_http_code}" != "200" ]; then
  echo "    Could not sign in as ${DIFY_ADMIN_EMAIL} (http ${signin_http_code}) -- skipping the" >&2
  echo "    declarative MCP/model setup below. This usually means DIFY_ADMIN_PASSWORD in .env" >&2
  echo "    no longer matches a real admin account. Add the MCP server by hand instead:" >&2
  echo "    Studio -> Tools -> MCP -> Add MCP Server, pointing at" >&2
  echo "    http://mcp-agent-skills:8321/mcp" >&2
  rm -f "${COOKIE_JAR}"
  exit 0
fi

CSRF="$(awk '$6 == "csrf_token" { print $7 }' "${COOKIE_JAR}")"
AUTH_CURL=(curl -s -b "${COOKIE_JAR}" -H "X-CSRF-Token: ${CSRF}" -H 'Content-Type: application/json')

echo "==> Registering mcp-agent-skills as a Dify MCP tool provider..."
MCP_BODY="$(jq -n '{
  server_url: "http://mcp-agent-skills:8321/mcp",
  name: "Agent Skills (Jira & Confluence)",
  icon: "🛠️",
  icon_type: "emoji",
  icon_background: "#FFEAD5",
  server_identifier: "agent-skills",
  configuration: {timeout: 90, sse_read_timeout: 90}
}')"
mcp_resp="$(mktemp)"
mcp_http_code="$("${AUTH_CURL[@]}" -o "${mcp_resp}" -w '%{http_code}' \
  -X POST "${BASE_URL}/console/api/workspaces/current/tool-provider/mcp" -d "${MCP_BODY}")"
if [ "${mcp_http_code}" = "200" ]; then
  echo "    Registered (or the identifier was already taken -- see below if so)."
else
  echo "    Could not create the MCP provider (http ${mcp_http_code}):" >&2
  cat "${mcp_resp}" >&2
  echo "    If this says the name/identifier already exists, it's already registered from a" >&2
  echo "    previous run -- open Studio -> Tools -> MCP -> Agent Skills to confirm its tool" >&2
  echo "    list loaded, or delete and rerun this script to recreate it with the current" >&2
  echo "    settings." >&2
fi
rm -f "${mcp_resp}"

echo "==> Checking whether the OpenAI-API-compatible model plugin is installed..."
providers_resp="$(mktemp)"
"${AUTH_CURL[@]}" -o "${providers_resp}" "${BASE_URL}/console/api/workspaces/current/model-providers"
if jq -e '.data[]? | select(.provider == "langgenius/openai_api_compatible/openai_api_compatible")' "${providers_resp}" >/dev/null 2>&1; then
  echo "    Installed. Configuring your VLLM_BASE_URL as a custom model..."
  : "${VLLM_BASE_URL:?set VLLM_BASE_URL in .env}"
  : "${VLLM_MODEL_NAME:?set VLLM_MODEL_NAME in .env}"
  MODEL_BODY="$(jq -n --arg model "${VLLM_MODEL_NAME}" --arg url "${VLLM_BASE_URL}" --arg key "${VLLM_API_KEY:-}" '{
    model: $model,
    model_type: "llm",
    credentials: {endpoint_url: $url, api_key: $key, mode: "chat", context_size: 262144},
    name: "agentflow vLLM"
  }')"
  model_resp="$(mktemp)"
  model_http_code="$("${AUTH_CURL[@]}" -o "${model_resp}" -w '%{http_code}' \
    -X POST "${BASE_URL}/console/api/workspaces/current/model-providers/langgenius/openai_api_compatible/openai_api_compatible/models/credentials" \
    -d "${MODEL_BODY}")"
  if [ "${model_http_code}" = "201" ]; then
    echo "    Configured. Set it as the default LLM in Studio -> Settings -> Model Provider"
    echo "    if it isn't already (a one-time click; no reliable API for this was found --"
    echo "    see docs/CONFIGURATION.md)."
  else
    echo "    Could not save model credentials (http ${model_http_code}):" >&2
    cat "${model_resp}" >&2
  fi
  rm -f "${model_resp}"
else
  echo "    Not installed. This can't be automated safely (no verified marketplace/GitHub" >&2
  echo "    package identifier to pin -- see docs/CONFIGURATION.md 'LLM endpoint'). Install it" >&2
  echo "    once by hand: Studio -> Plugins -> Marketplace -> search \"OpenAI-API-compatible\"" >&2
  echo "    -> Install, then rerun this script to configure it declaratively." >&2
fi
rm -f "${providers_resp}" "${COOKIE_JAR}"

echo "==> Done. docker compose ps:"
docker compose ps

cat <<'MSG'

Next (one-time, in Studio -- https://docs.dify.ai has the full walkthrough
if any of this looks unfamiliar):
  1. If the model plugin wasn't installed above, install it and rerun this
     script (see the message above).
  2. Studio -> Tools -> MCP -> Agent Skills -> confirm its tool list loaded.
  3. Create an app (Chatflow or Agent), pick the configured model, attach
     the Agent Skills MCP tools, and publish it.
MSG
