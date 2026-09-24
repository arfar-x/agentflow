#!/usr/bin/env bash
# Renders config/librechat.yaml (gitignored) from config/librechat.yaml.example,
# filling in the REPLACE_ME_* placeholders from .env. Unlike
# searxng/settings.yml's one-shot secret generation, this re-renders every
# time it's called -- there's no secret here to preserve, just values that
# should always reflect the current .env. Safe to rerun; called by
# scripts/bootstrap.sh on every `make up`, and directly by `make
# render-config` when you only need to pick up an .env change (e.g. after
# editing AGENTS_RECURSION_LIMIT) without a full stack restart.
#
# Never hand-edit the generated config/librechat.yaml -- edit
# config/librechat.yaml.example instead, or this script's output gets
# silently overwritten on the next render.
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."

if [ ! -f .env ]; then
  echo "No .env found. Copy .env.example to .env and fill it in first." >&2
  exit 1
fi

# shellcheck disable=SC1091
source .env

: "${AGENTS_RECURSION_LIMIT:=50}"
: "${AGENTS_MAX_RECURSION_LIMIT:=100}"
: "${MODEL_CONTEXT_TOKENS:=262144}"

# summarization.retainRecent.tokens has no ratio field of its own in
# LibreChat's schema (see config/librechat.yaml.example's comment there) --
# derive it here instead, as a fixed 5% of MODEL_CONTEXT_TOKENS, so it
# tracks the real context window automatically rather than needing a
# second number hand-tuned every time MODEL_CONTEXT_TOKENS changes. Floored
# at 200 tokens so a small context (e.g. 4096) doesn't round down to
# something too small to hold anything.
SUMMARIZATION_RETAIN_TOKENS=$(( MODEL_CONTEXT_TOKENS * 5 / 100 ))
if [ "${SUMMARIZATION_RETAIN_TOKENS}" -lt 200 ]; then
  SUMMARIZATION_RETAIN_TOKENS=200
fi

echo "==> Rendering config/librechat.yaml from config/librechat.yaml.example..."
sed \
  -e "s/REPLACE_ME_AGENTS_RECURSION_LIMIT/${AGENTS_RECURSION_LIMIT}/" \
  -e "s/REPLACE_ME_AGENTS_MAX_RECURSION_LIMIT/${AGENTS_MAX_RECURSION_LIMIT}/" \
  -e "s/REPLACE_ME_MODEL_CONTEXT_TOKENS/${MODEL_CONTEXT_TOKENS}/" \
  -e "s/REPLACE_ME_SUMMARIZATION_RETAIN_TOKENS/${SUMMARIZATION_RETAIN_TOKENS}/" \
  config/librechat.yaml.example > config/librechat.yaml.tmp
mv config/librechat.yaml.tmp config/librechat.yaml
echo "    Done (recursionLimit=${AGENTS_RECURSION_LIMIT}, maxRecursionLimit=${AGENTS_MAX_RECURSION_LIMIT}, modelContextTokens=${MODEL_CONTEXT_TOKENS}, summarizationRetainTokens=${SUMMARIZATION_RETAIN_TOKENS})."
