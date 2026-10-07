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

echo "==> Preparing the agentflow-mcp-auth plugin (per-user Jira/Confluence credentials)..."
# This is this repo's own Dify plugin (dify-plugins/agentflow-mcp-auth), not a
# Marketplace one -- see docs/CONFIGURATION.md "agentflow-mcp-auth plugin" for
# what it does and why. Packaged, self-signed, uploaded, and installed here,
# every run: repackaging from the same source produces the same signed
# package, so re-running this against an already-installed copy is a no-op
# (the daemon reports all_installed=true and this script skips the wait).
PLUGIN_SRC_DIR="dify-plugins/agentflow-mcp-auth"
PLUGIN_KEYS_DIR="volumes/plugin_signing"
PLUGIN_PROVIDER="agentflow/agentflow_mcp_auth/agentflow_mcp_auth"
PLUGIN_ENDPOINT_NAME="agentflow-mcp-auth-credentials"
mkdir -p "${PLUGIN_KEYS_DIR}"

PLUGIN_DAEMON_IMAGE="$(docker compose config --images plugin_daemon 2>/dev/null | tail -n1)"
: "${PLUGIN_DAEMON_IMAGE:=langgenius/dify-plugin-daemon:0.6.10-local}"

if [ ! -f "${PLUGIN_KEYS_DIR}/agentflow.private.pem" ]; then
  echo "    Generating a signing keypair (first run only; kept in ${PLUGIN_KEYS_DIR}/,"
  echo "    gitignored -- back it up separately, see docs/OPERATIONS.md 'Secrets')..."
  docker run --rm -v "$(pwd)/${PLUGIN_KEYS_DIR}:/keys" -w /keys "${PLUGIN_DAEMON_IMAGE}" \
    sh -c "/app/commandline signature generate -f agentflow && chmod 600 /keys/agentflow.private.pem"
fi

PLUGIN_PKG_DIR="$(mktemp -d)"
echo "    Packaging..."
docker run --rm -v "$(pwd)/${PLUGIN_SRC_DIR}:/plugin:ro" -v "${PLUGIN_PKG_DIR}:/out" "${PLUGIN_DAEMON_IMAGE}" \
  /app/commandline plugin package /plugin -o /out/agentflow-mcp-auth.difypkg >/dev/null
echo "    Signing (category: community -- trusted only via this deployment's own"
echo "    THIRD_PARTY_SIGNATURE_VERIFICATION_PUBLIC_KEYS, not langgenius' Marketplace key)..."
docker run --rm -v "${PLUGIN_PKG_DIR}:/out" -v "$(pwd)/${PLUGIN_KEYS_DIR}:/keys:ro" "${PLUGIN_DAEMON_IMAGE}" \
  /app/commandline signature sign /out/agentflow-mcp-auth.difypkg -p /keys/agentflow.private.pem -c community >/dev/null
PLUGIN_SIGNED_PKG="${PLUGIN_PKG_DIR}/agentflow-mcp-auth.signed.difypkg"

echo "    Uploading..."
plugin_upload_resp="$(mktemp)"
plugin_upload_http_code="$(curl -s -o "${plugin_upload_resp}" -w '%{http_code}' -b "${COOKIE_JAR}" -H "X-CSRF-Token: ${CSRF}" \
  -X POST "${BASE_URL}/console/api/workspaces/current/plugin/upload/pkg" \
  -F "pkg=@${PLUGIN_SIGNED_PKG};type=application/octet-stream")"
if [ "${plugin_upload_http_code}" != "200" ]; then
  echo "    Could not upload the plugin package (http ${plugin_upload_http_code}):" >&2
  cat "${plugin_upload_resp}" >&2
  rm -f "${plugin_upload_resp}"
  rm -rf "${PLUGIN_PKG_DIR}"
else
  PLUGIN_UNIQUE_ID="$(jq -r '.unique_identifier' "${plugin_upload_resp}")"
  rm -f "${plugin_upload_resp}"
  rm -rf "${PLUGIN_PKG_DIR}"

  echo "    Installing (${PLUGIN_UNIQUE_ID})..."
  plugin_install_resp="$(mktemp)"
  plugin_install_http_code="$("${AUTH_CURL[@]}" -o "${plugin_install_resp}" -w '%{http_code}' \
    -X POST "${BASE_URL}/console/api/workspaces/current/plugin/install/pkg" \
    -d "$(jq -n --arg id "${PLUGIN_UNIQUE_ID}" '{plugin_unique_identifiers:[$id]}')")"
  plugin_all_installed="$(jq -r '.all_installed // false' "${plugin_install_resp}" 2>/dev/null)"
  plugin_task_id="$(jq -r '.task_id // empty' "${plugin_install_resp}" 2>/dev/null)"
  rm -f "${plugin_install_resp}"

  if [ "${plugin_install_http_code}" != "200" ]; then
    echo "    Could not start the plugin install (http ${plugin_install_http_code})." >&2
  elif [ "${plugin_all_installed}" = "true" ]; then
    echo "    Already installed."
  elif [ -z "${plugin_task_id}" ]; then
    echo "    Install did not return a task id -- check Studio -> Plugins for its status." >&2
  else
    echo "    Waiting for it to finish installing (first run builds a Python venv, can take a minute)..."
    plugin_task_status=""
    plugin_task_resp=""
    for _ in $(seq 1 60); do
      plugin_task_resp="$("${AUTH_CURL[@]}" "${BASE_URL}/console/api/workspaces/current/plugin/tasks/${plugin_task_id}")"
      plugin_task_status="$(printf '%s' "${plugin_task_resp}" | jq -r '.task.status // empty')"
      { [ "${plugin_task_status}" = "success" ] || [ "${plugin_task_status}" = "failed" ]; } && break
      sleep 3
    done
    if [ "${plugin_task_status}" != "success" ]; then
      echo "    Plugin install did not succeed (status: ${plugin_task_status:-unknown}):" >&2
      printf '%s' "${plugin_task_resp}" | jq -r '.task.plugins[]?.message // empty' >&2
    else
      echo "    Installed."
    fi
  fi

  echo "    Ensuring its credentials Endpoint exists..."
  plugin_endpoints_resp="$("${AUTH_CURL[@]}" \
    "${BASE_URL}/console/api/workspaces/current/endpoints/list/plugin?plugin_id=agentflow/agentflow_mcp_auth&page=1&page_size=10")"
  PLUGIN_HOOK_ID="$(printf '%s' "${plugin_endpoints_resp}" | jq -r --arg name "${PLUGIN_ENDPOINT_NAME}" '.endpoints[]? | select(.name==$name) | .hook_id' | head -n1)"
  if [ -z "${PLUGIN_HOOK_ID}" ]; then
    plugin_endpoint_create_resp="$(mktemp)"
    "${AUTH_CURL[@]}" -o "${plugin_endpoint_create_resp}" -w '%{http_code}' \
      -X POST "${BASE_URL}/console/api/workspaces/current/endpoints" \
      -d "$(jq -n --arg id "${PLUGIN_UNIQUE_ID}" --arg name "${PLUGIN_ENDPOINT_NAME}" \
        '{plugin_unique_identifier: $id, settings: {}, name: $name}')" >/dev/null
    rm -f "${plugin_endpoint_create_resp}"
    plugin_endpoints_resp="$("${AUTH_CURL[@]}" \
      "${BASE_URL}/console/api/workspaces/current/endpoints/list/plugin?plugin_id=agentflow/agentflow_mcp_auth&page=1&page_size=10")"
    PLUGIN_HOOK_ID="$(printf '%s' "${plugin_endpoints_resp}" | jq -r --arg name "${PLUGIN_ENDPOINT_NAME}" '.endpoints[]? | select(.name==$name) | .hook_id' | head -n1)"
  fi

  if [ -z "${PLUGIN_HOOK_ID}" ]; then
    echo "    Could not find or create the credentials Endpoint -- check Studio -> Plugins ->" >&2
    echo "    agentflow MCP Auth Bridge -> Endpoints by hand." >&2
  else
    # Deliberately NOT the "url" field the endpoints API itself returns --
    # that's plugin_daemon's own internal address (http://plugin_daemon:5002/e/<hook_id>),
    # unreachable from a user's browser. nginx's own /e/ location block (see
    # nginx/conf.d/default.conf.template) is what actually proxies this
    # path through to plugin_daemon, so the real, externally-reachable link
    # is this deployment's own BASE_URL with that same hook_id -- confirmed
    # against a live server, not assumed.
    PLUGIN_CREDENTIALS_URL="${BASE_URL}/e/${PLUGIN_HOOK_ID}/credentials"
    echo "    Credentials Endpoint: ${PLUGIN_CREDENTIALS_URL}"

    echo "    Configuring the provider's Credentials Endpoint URL..."
    plugin_creds_resp="$("${AUTH_CURL[@]}" "${BASE_URL}/console/api/workspaces/current/tool-provider/builtin/${PLUGIN_PROVIDER}/credentials")"
    PLUGIN_CREDENTIAL_ID="$(printf '%s' "${plugin_creds_resp}" | jq -r '.[0].id // empty' 2>/dev/null)"
    plugin_provider_resp="$(mktemp)"
    if [ -z "${PLUGIN_CREDENTIAL_ID}" ]; then
      plugin_provider_http_code="$("${AUTH_CURL[@]}" -o "${plugin_provider_resp}" -w '%{http_code}' \
        -X POST "${BASE_URL}/console/api/workspaces/current/tool-provider/builtin/${PLUGIN_PROVIDER}/add" \
        -d "$(jq -n --arg url "${PLUGIN_CREDENTIALS_URL}" \
          '{credentials: {credentials_endpoint_url: $url}, name: "agentflow-mcp-auth", type: "api-key"}')")"
    else
      plugin_provider_http_code="$("${AUTH_CURL[@]}" -o "${plugin_provider_resp}" -w '%{http_code}' \
        -X POST "${BASE_URL}/console/api/workspaces/current/tool-provider/builtin/${PLUGIN_PROVIDER}/update" \
        -d "$(jq -n --arg id "${PLUGIN_CREDENTIAL_ID}" --arg url "${PLUGIN_CREDENTIALS_URL}" \
          '{credential_id: $id, credentials: {credentials_endpoint_url: $url}}')")"
    fi
    if [ "${plugin_provider_http_code}" = "200" ]; then
      echo "    Configured."
    else
      echo "    Could not set credentials_endpoint_url (http ${plugin_provider_http_code}):" >&2
      cat "${plugin_provider_resp}" >&2
    fi
    rm -f "${plugin_provider_resp}"
  fi
fi

echo "==> Checking whether the OpenAI-API-compatible model plugin is installed..."
providers_resp="$(mktemp)"
"${AUTH_CURL[@]}" -o "${providers_resp}" "${BASE_URL}/console/api/workspaces/current/model-providers"
if jq -e '.data[]? | select(.provider == "langgenius/openai_api_compatible/openai_api_compatible")' "${providers_resp}" >/dev/null 2>&1; then
  echo "    Installed. Configuring your VLLM_BASE_URL as a custom model..."
  : "${VLLM_BASE_URL:?set VLLM_BASE_URL in .env}"
  : "${VLLM_MODEL_NAME:?set VLLM_MODEL_NAME in .env}"
  # context_size must be a JSON string, not a number -- the plugin's own
  # credential schema validator does a strict isinstance(str) check on it
  # regardless of the form field's declared "number" type, and rejects an
  # int with "Variable context_size should be string".
  MODEL_BODY="$(jq -n --arg model "${VLLM_MODEL_NAME}" --arg url "${VLLM_BASE_URL}" --arg key "${VLLM_API_KEY:-}" '{
    model: $model,
    model_type: "llm",
    credentials: {endpoint_url: $url, api_key: $key, mode: "chat", context_size: "262144"},
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
  3. Studio -> Plugins -> agentflow MCP Auth Bridge -> confirm its 4 tools
     loaded (mcp_call_tool, mcp_list_tools, get_credentials_link,
     clear_my_credentials) and its Endpoint is enabled.
  4. Create an app (Chatflow or Agent), pick the configured model, attach
     agentflow-mcp-auth's tools (per-user Jira/Confluence credentials) and/or
     the Agent Skills MCP tools directly (shared/fallback identity), and
     publish it. See docs/CONFIGURATION.md "agentflow-mcp-auth plugin" for
     the difference between the two.
MSG
