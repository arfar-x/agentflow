#!/usr/bin/env bash
# Thin curl+jq wrapper over Open WebUI's own admin REST API -- signs in as
# WEBUI_ADMIN_EMAIL/WEBUI_ADMIN_PASSWORD from .env, then runs one admin action. Used
# by the `make user-*` targets (see README.md "Managing users"); nothing
# here isn't also reachable by hand through the Admin Panel UI.
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."

# shellcheck disable=SC1091
source .env
: "${WEBUI_ADMIN_EMAIL:?set WEBUI_ADMIN_EMAIL in .env}"
: "${WEBUI_ADMIN_PASSWORD:?set WEBUI_ADMIN_PASSWORD in .env}"
BASE_URL="http://127.0.0.1:${PORT:-3080}"

admin_token() {
  local resp http_code token
  resp="$(mktemp)"
  http_code="$(curl -s -o "${resp}" -w '%{http_code}' -X POST "${BASE_URL}/api/v1/auths/signin" \
    -H 'Content-Type: application/json' \
    -d "$(jq -n --arg email "${WEBUI_ADMIN_EMAIL}" --arg password "${WEBUI_ADMIN_PASSWORD}" '{email:$email,password:$password}')")"
  if [ "${http_code}" != "200" ]; then
    echo "Could not sign in as ${WEBUI_ADMIN_EMAIL} (http ${http_code}) -- check WEBUI_ADMIN_EMAIL/WEBUI_ADMIN_PASSWORD in .env:" >&2
    cat "${resp}" >&2
    rm -f "${resp}"
    exit 1
  fi
  token="$(jq -r .token "${resp}")"
  rm -f "${resp}"
  echo "${token}"
}

# Prints one user's id for a given email, or exits non-zero if not found.
user_id_by_email() {
  local email="$1" token="$2"
  curl -s "${BASE_URL}/api/v1/users/all" -H "Authorization: Bearer ${token}" \
    | jq -r --arg email "${email}" '.[] | select(.email == $email) | .id' | head -1
}

require_found_user() {
  if [ -z "$1" ]; then
    echo "No user with that email." >&2
    exit 1
  fi
}

cmd="${1:?Usage: scripts/openwebui-admin.sh <create|list|ban|unban|delete|reset-password> [args...]}"
shift || true
TOKEN="$(admin_token)"

case "${cmd}" in
  create)
    EMAIL="${1:?Usage: scripts/openwebui-admin.sh create EMAIL NAME [PASSWORD]}"
    NAME="${2:?Usage: scripts/openwebui-admin.sh create EMAIL NAME [PASSWORD]}"
    PASSWORD="${3:-$(openssl rand -hex 12)}"
    resp="$(mktemp)"
    http_code="$(curl -s -o "${resp}" -w '%{http_code}' -X POST "${BASE_URL}/api/v1/auths/add" \
      -H "Authorization: Bearer ${TOKEN}" -H 'Content-Type: application/json' \
      -d "$(jq -n --arg email "${EMAIL}" --arg password "${PASSWORD}" --arg name "${NAME}" '{email:$email,password:$password,name:$name,role:"user"}')")"
    if [ "${http_code}" = "200" ]; then
      echo "Created. Login: ${EMAIL} / ${PASSWORD}"
    else
      echo "Failed (http ${http_code}):" >&2
      cat "${resp}" >&2
      rm -f "${resp}"
      exit 1
    fi
    rm -f "${resp}"
    ;;

  list)
    curl -s "${BASE_URL}/api/v1/users/all" -H "Authorization: Bearer ${TOKEN}" \
      | jq -r '["EMAIL","NAME","ROLE"], (.[] | [.email, .name, .role]) | @tsv' | column -t -s "$(printf '\t')"
    ;;

  ban)
    EMAIL="${1:?Usage: scripts/openwebui-admin.sh ban EMAIL}"
    USER_ID="$(user_id_by_email "${EMAIL}" "${TOKEN}")"
    require_found_user "${USER_ID}"
    curl -s -o /dev/null -w '%{http_code}\n' -X POST "${BASE_URL}/api/v1/users/${USER_ID}/update" \
      -H "Authorization: Bearer ${TOKEN}" -H 'Content-Type: application/json' \
      -d '{"role":"pending"}'
    echo "${EMAIL} set to role 'pending' -- blocked from using the chat until 'make user-unban EMAIL=${EMAIL}'."
    echo "Open WebUI has no auto-expiring ban like LibreChat's MINUTES-based one; this stays until reversed."
    ;;

  unban)
    EMAIL="${1:?Usage: scripts/openwebui-admin.sh unban EMAIL}"
    USER_ID="$(user_id_by_email "${EMAIL}" "${TOKEN}")"
    require_found_user "${USER_ID}"
    curl -s -o /dev/null -w '%{http_code}\n' -X POST "${BASE_URL}/api/v1/users/${USER_ID}/update" \
      -H "Authorization: Bearer ${TOKEN}" -H 'Content-Type: application/json' \
      -d '{"role":"user"}'
    echo "${EMAIL} restored to role 'user'."
    ;;

  delete)
    EMAIL="${1:?Usage: scripts/openwebui-admin.sh delete EMAIL}"
    USER_ID="$(user_id_by_email "${EMAIL}" "${TOKEN}")"
    require_found_user "${USER_ID}"
    read -rp "Delete ${EMAIL} and ALL their data -- irreversible. Type 'yes' to continue: " CONFIRM
    [ "${CONFIRM}" = "yes" ] || { echo "Aborted."; exit 1; }
    curl -s -o /dev/null -w '%{http_code}\n' -X DELETE "${BASE_URL}/api/v1/users/${USER_ID}" \
      -H "Authorization: Bearer ${TOKEN}"
    echo "Deleted ${EMAIL}."
    ;;

  reset-password)
    EMAIL="${1:?Usage: scripts/openwebui-admin.sh reset-password EMAIL [PASSWORD]}"
    NEW_PASSWORD="${2:-$(openssl rand -hex 12)}"
    USER_ID="$(user_id_by_email "${EMAIL}" "${TOKEN}")"
    require_found_user "${USER_ID}"
    curl -s -o /dev/null -w '%{http_code}\n' -X POST "${BASE_URL}/api/v1/users/${USER_ID}/update" \
      -H "Authorization: Bearer ${TOKEN}" -H 'Content-Type: application/json' \
      -d "$(jq -n --arg password "${NEW_PASSWORD}" '{password:$password}')"
    echo "New password for ${EMAIL}: ${NEW_PASSWORD}"
    ;;

  *)
    echo "Unknown command: ${cmd}" >&2
    exit 1
    ;;
esac
