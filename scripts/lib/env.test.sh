#!/usr/bin/env bash
# Tests for scripts/lib/env.sh: `bash scripts/lib/env.test.sh`. No services needed.
set -uo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/../.."
source scripts/lib/env.sh

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

# One .env line in, one value out.
while IFS='|' read -r line expected; do
  printf '%s\n' "${line}" >"${tmp}/env"
  check "${line}" "${expected}" "$(env_get X "${tmp}/env")"
done <<'CASES'
X=claude-opus-5 # only model|claude-opus-5
X=claude-opus-5   #no space after hash|claude-opus-5
X=a#b|a#b
X="keep # this"|keep # this
X='keep # that'|keep # that
X="quoted" # note|quoted
X=plain|plain
X=|
X=a=b|a=b
CASES

printf 'X=first\nY=other\nX=last # wins\n' >"${tmp}/env"
check "last assignment wins" "last" "$(env_get X "${tmp}/env")"
check "other names untouched" "other" "$(env_get Y "${tmp}/env")"
check "missing variable" "" "$(env_get NOPE "${tmp}/env")"
check "missing file" "" "$(env_get X "${tmp}/absent")"
printf 'XY=wrong\n' >"${tmp}/env"
check "name is matched exactly" "" "$(env_get X "${tmp}/env")"

printf 'X=from-file # c\n' >"${tmp}/env"
ENV_FILE="${tmp}/env" bash -c '
  source scripts/lib/env.sh
  unset A; env_fill X A
  [ "${X}" = from-file ] || exit 1
  [ -z "${A+set}" ] || exit 2
  X=given; env_fill X
  [ "${X}" = given ] || exit 3'
check "env_fill: fills unset, skips absent, keeps a given value" "0" "$?"

exit "${fail}"
