# Shared .env reading for scripts/*.sh. Source it, don't execute it:
#
#   source "$(dirname "${BASH_SOURCE[0]}")/lib/env.sh"
#
# Why this exists: pulling one variable out of .env with grep/cut keeps an
# inline comment as part of the value (`FOO=bar # note` -> "bar # note"), and
# a script that then exports it passes the comment on to whatever it drives.
# These helpers read a value the way a shell reads a .env line:
#
#   FOO=bar # note       -> bar          unquoted: a # after whitespace starts a comment
#   FOO=a#b              -> a#b          a # with no whitespace before it is part of the value
#   FOO="a # b" # note   -> a # b        double-quoted: ends at the closing quote
#   FOO='a # b'          -> a # b        single-quoted: same
#
# Not handled (use `source .env` when a script wants the whole file): escapes
# inside quotes, multi-line values, `export FOO=...`, ${VAR} expansion.
#
# The file is ENV_FILE if set, else .env, relative to the working directory
# (every script cd's to the repo root first).

# env_parse_value RAW  -- print the value for one RAW right-hand side of NAME=
env_parse_value() {
  local value="$1"
  if [[ "${value}" =~ ^\"([^\"]*)\" ]]; then
    value="${BASH_REMATCH[1]}"
  elif [[ "${value}" =~ ^\'([^\']*)\' ]]; then
    value="${BASH_REMATCH[1]}"
  else
    value="${value%%[[:space:]]#*}"
    value="${value%"${value##*[![:space:]]}"}"
  fi
  printf '%s' "${value}"
}

# env_get NAME [FILE]  -- print NAME's value from the file (last assignment
# wins); nothing, and still success, when the file or the variable is missing.
env_get() {
  local name="$1" file="${2:-${ENV_FILE:-.env}}" raw
  [ -f "${file}" ] || return 0
  raw="$(grep -E "^${name}=" "${file}" | tail -1 | cut -d= -f2- || true)"
  env_parse_value "${raw}"
}

# env_fill NAME...  -- for each NAME that is unset or empty in the environment,
# export it from the file. A value already given for this run (the command
# line, the caller's environment) always wins; a name absent from the file is
# left unset.
env_fill() {
  local name value
  for name in "$@"; do
    if [ -z "${!name:-}" ]; then
      value="$(env_get "${name}")"
      [ -z "${value}" ] || export "${name}=${value}"
    fi
  done
}
