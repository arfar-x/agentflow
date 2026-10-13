#!/usr/bin/env bash
# Tests for kb/scripts/prepare.sh: `bash kb/scripts/prepare.test.sh`. No services
# needed -- it runs the script against a scratch copy of the files it touches.
set -uo pipefail
repo="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
source "${repo}/scripts/lib/env.sh"

tmp="$(mktemp -d)"
trap 'rm -rf "${tmp}"' EXIT
fail=0

check() { # check DESCRIPTION EXPECTED ACTUAL
  if [ "$2" = "$3" ]; then
    printf 'ok    %s\n' "$1"
  else
    printf 'FAIL  %s: expected [%s], got [%s]\n' "$1" "$2" "$3"
    fail=1
  fi
}

# A fresh scratch checkout: just the files prepare.sh reads.
fresh() {
  rm -rf "${tmp}/w" && mkdir -p "${tmp}/w/scripts/lib" "${tmp}/w/kb/scripts" "${tmp}/w/kb/config" "${tmp}/w/config"
  cp "${repo}/scripts/lib/env.sh" "${tmp}/w/scripts/lib/"
  cp "${repo}/kb/scripts/prepare.sh" "${tmp}/w/kb/scripts/"
  cp "${repo}/kb/.env.example" "${tmp}/w/kb/"
  cp "${repo}/kb/config/sources.yaml.example" "${tmp}/w/kb/config/"
  cd "${tmp}/w" || exit 1
}
kb() { env_get "$1" "${tmp}/w/kb/.env"; }

# --- a first run with nothing to carry over -----------------------------------
fresh
bash kb/scripts/prepare.sh >/dev/null
check "env file created" "yes" "$([ -f kb/.env ] && echo yes)"
check "env file is private" "600" "$(stat -c %a kb/.env)"
check "sources file created" "yes" "$([ -f kb/config/sources.yaml ] && echo yes)"
check "owner password generated" "48" "$(kb KB_POSTGRES_PASSWORD | wc -c | tr -d ' ')"
check "reader password generated" "48" "$(kb KB_READER_PASSWORD | wc -c | tr -d ' ')"
check "the two passwords differ" "no" "$([ "$(kb KB_POSTGRES_PASSWORD)" = "$(kb KB_READER_PASSWORD)" ] && echo yes || echo no)"

# --- rerun changes nothing ----------------------------------------------------
before="$(cat kb/.env)"
bash kb/scripts/prepare.sh >/dev/null
check "rerun leaves an existing env file alone" "${before}" "$(cat kb/.env)"

# --- an upgrade from the root .env --------------------------------------------
fresh
cat > .env <<'ENV'
KB_POSTGRES_PASSWORD=ownerpw
KB_READER_PASSWORD=readerpw # trailing note
KB_CONFLUENCE_BASE_URL=https://wiki.example
JIRA_BASE_URL=https://jira.example
JIRA_PAT=legacy-pat
GITLAB_TOKEN=glpat#1
KB_SUMMARIZER_URL=${VLLM_BASE_URL}
KB_SUMMARIZER_API_KEY=${VLLM_API_KEY}
KB_SUMMARIZER_MODEL=m1
VLLM_BASE_URL=https://llm.example/v1
VLLM_API_KEY=secret-key
MODEL_HOSTNAME=llm.example
MODEL_HOST_IP=10.1.2.3
JWT_SECRET=must-not-leak
ENV
echo 'approved: true' > config/kb-sources.yaml
bash kb/scripts/prepare.sh >/dev/null
check "existing owner password kept" "ownerpw" "$(kb KB_POSTGRES_PASSWORD)"
check "inline comment not carried" "readerpw" "$(kb KB_READER_PASSWORD)"
check "commented template line is uncommented" "https://wiki.example" "$(kb KB_CONFLUENCE_BASE_URL)"
check "agent-skills-style name carried under the module's own" "https://jira.example" "$(kb KB_JIRA_BASE_URL)"
check "agent-skills-style pat carried" "legacy-pat" "$(kb KB_JIRA_PAT)"
check "value containing #" "glpat#1" "$(kb GITLAB_TOKEN)"
check "summarizer url reference resolved" "https://llm.example/v1" "$(kb KB_SUMMARIZER_URL)"
check "summarizer key reference resolved" "secret-key" "$(kb KB_SUMMARIZER_API_KEY)"
check "model carried" "m1" "$(kb KB_SUMMARIZER_MODEL)"
check "host pin renamed" "llm.example" "$(kb KB_SUMMARIZER_HOSTNAME)"
check "host ip renamed" "10.1.2.3" "$(kb KB_SUMMARIZER_HOST_IP)"
check "nothing from other modules leaks in" "" "$(grep -c 'JWT_SECRET\|must-not-leak' kb/.env | grep -v '^0$')"
check "legacy sources file moved, not regenerated" "approved: true" "$(cat kb/config/sources.yaml)"
check "legacy sources file gone from config/" "no" "$([ -e config/kb-sources.yaml ] && echo yes || echo no)"

# --- a directory where the sources file should be (Docker's doing) -------------
fresh
mkdir kb/config/sources.yaml
bash kb/scripts/prepare.sh >/dev/null
check "empty mountpoint directory replaced by the file" "yes" "$([ -f kb/config/sources.yaml ] && echo yes)"

exit "${fail}"
