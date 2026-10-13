#!/usr/bin/env bash
# Creates what the knowledge base needs on disk before any `docker compose`
# command can run: its env file and its source configuration.
#
# The root compose file `include:`s kb/compose.yaml with kb/.env.kb as that
# module's only interpolation source, and bind-mounts kb/config/sources.yaml --
# so both must exist first, or compose fails (or Docker creates the mountpoint
# as an empty directory). Callers: scripts/bootstrap.sh and `make kb-setup`.
#
# Idempotent. It creates what is missing and never overwrites what exists.
#
# On first creation of kb/.env.kb it also:
#   - carries over any knowledge-base values from an older root .env, so an
#     existing deployment keeps its database password (the volume was
#     initialised with it) and its credentials;
#   - generates the two database passwords if there was nothing to carry over.
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/../.."

# shellcheck disable=SC1091
source scripts/lib/env.sh

KB_ENV=kb/.env.kb
KB_SOURCES=kb/config/sources.yaml
LEGACY_ENV="${KB_LEGACY_ENV:-.env}"

say() { printf '    %s\n' "$*"; }

# set_var NAME VALUE FILE -- replace the (possibly commented-out) NAME= line,
# or append one. Done line by line rather than with sed so a value may contain
# any character.
set_var() {
  local name="$1" value="$2" file="$3" tmp line found=0
  tmp="$(mktemp)"
  while IFS= read -r line || [ -n "${line}" ]; do
    if [ "${found}" = 0 ] && [[ "${line}" =~ ^(#\ ?)?${name}= ]]; then
      printf '%s=%s\n' "${name}" "${value}"
      found=1
    else
      printf '%s\n' "${line}"
    fi
  done < "${file}" > "${tmp}"
  [ "${found}" = 1 ] || printf '%s=%s\n' "${name}" "${value}" >> "${tmp}"
  cat "${tmp}" > "${file}"
  rm -f "${tmp}"
}

# legacy_get NAME -- NAME's value in the old root .env, with a lone ${OTHER}
# reference resolved against that same file (the old template defaulted the
# summarizer to ${VLLM_BASE_URL}, which this module no longer sees).
legacy_get() {
  local value
  value="$(env_get "$1" "${LEGACY_ENV}")"
  if [[ "${value}" =~ ^\$\{([A-Za-z_][A-Za-z0-9_]*)\}$ ]]; then
    value="$(env_get "${BASH_REMATCH[1]}" "${LEGACY_ENV}")"
  fi
  printf '%s' "${value}"
}

# --- 1. The source configuration -------------------------------------------
# Docker creates a bind-mount source that doesn't exist as an empty directory;
# recover from that rather than fail opaquely inside `cp`.
if [ -d "${KB_SOURCES}" ]; then
  rmdir "${KB_SOURCES}" 2>/dev/null || {
    echo "${KB_SOURCES} is a directory (not empty) instead of the expected file --" >&2
    echo "remove it manually and rerun." >&2
    exit 1
  }
fi
if [ ! -f "${KB_SOURCES}" ]; then
  if [ -f config/kb-sources.yaml ]; then
    # Where it lived before the module owned its own configuration.
    mv config/kb-sources.yaml "${KB_SOURCES}"
    say "moved config/kb-sources.yaml to ${KB_SOURCES}"
  else
    cp kb/config/sources.yaml.example "${KB_SOURCES}"
    say "created ${KB_SOURCES} from the template (every source disabled)"
  fi
fi

# --- 2. The module's env file ----------------------------------------------
if [ ! -f "${KB_ENV}" ]; then
  cp kb/.env.example "${KB_ENV}"
  chmod 600 "${KB_ENV}"
  carried=0

  if [ -f "${LEGACY_ENV}" ]; then
    for name in \
      KB_POSTGRES_DB KB_POSTGRES_USER KB_POSTGRES_PASSWORD KB_READER_PASSWORD \
      KB_CONFLUENCE_BASE_URL KB_CONFLUENCE_USERNAME KB_CONFLUENCE_PASSWORD KB_CONFLUENCE_PAT \
      KB_CONFLUENCE_DEPLOYMENT_TYPE \
      KB_JIRA_BASE_URL KB_JIRA_USERNAME KB_JIRA_PASSWORD KB_JIRA_PAT \
      GITLAB_BASE_URL GITLAB_TOKEN KB_SOURCES_APPROVED \
      KB_SUMMARIZER_URL KB_SUMMARIZER_MODEL KB_SUMMARIZER_API_KEY \
      KB_SUMMARY_LANGUAGES KB_TIMEZONE KB_WEBHOOK_SECRET KB_WEBHOOK_PORT; do
      value="$(legacy_get "${name}")"
      if [ -n "${value}" ]; then
        set_var "${name}" "${value}" "${KB_ENV}"
        carried=$((carried + 1))
      fi
    done
    # Source credentials were once also accepted under the names agent-skills
    # uses (CONFLUENCE_*, JIRA_*), because the container saw the whole .env.
    # It no longer does, so carry them across under the module's own names.
    for pair in \
      CONFLUENCE_BASE_URL CONFLUENCE_USERNAME CONFLUENCE_PASSWORD CONFLUENCE_PAT CONFLUENCE_DEPLOYMENT_TYPE \
      JIRA_BASE_URL JIRA_USERNAME JIRA_PASSWORD JIRA_PAT; do
      [ -z "$(legacy_get "KB_${pair}")" ] || continue
      value="$(legacy_get "${pair}")"
      if [ -n "${value}" ]; then
        set_var "KB_${pair}" "${value}" "${KB_ENV}"
        carried=$((carried + 1))
      fi
    done
    # The host pin for the model endpoint, which this module used to share
    # with `api` and `rag_api` under the stack-wide names.
    for pair in MODEL_HOSTNAME:KB_SUMMARIZER_HOSTNAME MODEL_HOST_IP:KB_SUMMARIZER_HOST_IP; do
      value="$(legacy_get "${pair%%:*}")"
      if [ -n "${value}" ]; then
        set_var "${pair##*:}" "${value}" "${KB_ENV}"
        carried=$((carried + 1))
      fi
    done
  fi

  for name in KB_POSTGRES_PASSWORD KB_READER_PASSWORD; do
    if [ -z "$(env_get "${name}" "${KB_ENV}")" ]; then
      # hex, not base64: the password lands in a postgresql:// URI, where + / =
      # are reserved.
      set_var "${name}" "$(openssl rand -hex 24)" "${KB_ENV}"
      say "generated ${name}"
    fi
  done

  say "created ${KB_ENV} (carried over ${carried} value(s) from ${LEGACY_ENV})"
  [ "${carried}" = 0 ] || \
    say "the KB_* lines left in ${LEGACY_ENV} are no longer read -- delete them once you have checked ${KB_ENV}"
elif [ -f "${LEGACY_ENV}" ] && grep -qE '^KB_[A-Z_]+=' "${LEGACY_ENV}"; then
  say "note: ${LEGACY_ENV} still has KB_* lines; only ${KB_ENV} is read now"
fi
