"""Deployment requirements that live in the stack's config, not in this module.

They are checked here because this is the suite that owns the spec they come
from: NFR-DEP-01 is a claim about `docker-compose.yml`, and a claim nobody
checks is how a "separate database" quietly becomes another database inside
LibreChat's during a refactor.

Text matching rather than a YAML parse, deliberately: `yaml` is not a dependency
of this module, and these assertions are about a handful of specific lines.
"""

from __future__ import annotations

from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
COMPOSE = REPO_ROOT / "docker-compose.yml"


@pytest.fixture(scope="module")
def compose() -> str:
    if not COMPOSE.exists():
        pytest.skip("docker-compose.yml not found -- kb checked out on its own?")
    return COMPOSE.read_text(encoding="utf-8")


def _service_block(compose: str, name: str) -> str:
    """The indented block belonging to one service."""
    lines = compose.splitlines()
    start = next(i for i, line in enumerate(lines) if line.strip() == f"{name}:")
    block: list[str] = []
    for line in lines[start + 1 :]:
        if line and not line.startswith("    "):  # next service, or a top-level key
            break
        block.append(line)
    return "\n".join(block)


def test_the_catalog_has_its_own_database_service(compose):
    # Covers: NFR-DEP-01
    # Not a second database inside `vectordb`: an upgrade or restore of
    # LibreChat's RAG store must not be able to touch the catalog.
    assert "\n  kb-db:" in compose
    block = _service_block(compose, "kb-db")
    assert "kb_data:/var/lib/postgresql/data" in block
    assert "KB_POSTGRES_PASSWORD" in block, "kb-db must use its own credential, not POSTGRES_PASSWORD"
    assert "POSTGRES_PASSWORD: ${POSTGRES_PASSWORD" not in block


def test_the_catalog_database_is_internal_only(compose):
    # Covers: NFR-DEP-01
    # Same rule the other internal services follow: reachable on the backend
    # network, never published to the host.
    block = _service_block(compose, "kb-db")
    assert "ports:" not in block
    assert "networks: [backend]" in block


def test_the_catalog_volume_is_backed_up(compose):
    # Covers: NFR-DEP-06
    # The search index rebuilds itself, but the gap log and the sync
    # checkpoints do not -- they live in this volume.
    backup = (REPO_ROOT / "scripts" / "backup.sh").read_text(encoding="utf-8")
    assert "kb_data" in backup


def test_the_two_database_identities_are_documented(compose):
    # Covers: NFR-DEP-05
    # The owner that sync writes as, and the read-only role the query path
    # connects as. A deployment that only knows about one will hand the query
    # path the owner credential.
    env_example = (REPO_ROOT / ".env.example").read_text(encoding="utf-8")
    assert "KB_POSTGRES_PASSWORD" in env_example
    assert "KB_READER_PASSWORD" in env_example
    secrets = (REPO_ROOT / "scripts" / "generate-secrets.sh").read_text(encoding="utf-8")
    assert "KB_POSTGRES_PASSWORD" in secrets and "KB_READER_PASSWORD" in secrets
