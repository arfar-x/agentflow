#!/usr/bin/env bash
# Runs kb's full suite -- including the Postgres-backed tests that are skipped
# by a bare `pytest` -- against a throwaway database container.
#
# Nothing here touches the stack's own kb-db: the container is disposable, on a
# random free port, and removed when the run ends (including on failure), so
# this is safe to run against a live deployment.
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."

IMAGE="pgvector/pgvector:0.8.0-pg15-trixie"   # same image as the kb-db service
CONTAINER="kb-test-db-$$"
PORT="${KB_TEST_PORT:-55432}"

cleanup() { docker rm -f "${CONTAINER}" >/dev/null 2>&1 || true; }
trap cleanup EXIT

echo "==> Starting a throwaway Postgres (${CONTAINER}) on port ${PORT}..."
docker run -d --name "${CONTAINER}" \
  -e POSTGRES_PASSWORD=kbtest -e POSTGRES_DB=kb_test \
  -p "127.0.0.1:${PORT}:5432" "${IMAGE}" >/dev/null

for _ in $(seq 1 30); do
  docker exec "${CONTAINER}" pg_isready -U postgres -q && break
  sleep 1
done
docker exec "${CONTAINER}" pg_isready -U postgres -q || {
  echo "the test database never became ready" >&2
  exit 1
}

export KB_TEST_DATABASE_URL="postgresql://postgres:kbtest@127.0.0.1:${PORT}/kb_test"

echo "==> Running kb's test suite..."
# Prefer the developer's own virtualenv if there is one; otherwise a `pip
# install -e '.[dev]'` environment on PATH (see kb/README.md).
PYTHON="${PYTHON:-}"
if [ -z "${PYTHON}" ] && [ -x kb/.venv/bin/python ]; then
  PYTHON="kb/.venv/bin/python"
fi
PYTHON="${PYTHON:-python3}"

cd kb && "${PYTHON}" -m pytest "$@"
