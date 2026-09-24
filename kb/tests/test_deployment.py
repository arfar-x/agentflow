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


def test_the_mcp_server_is_internal_only(compose):
    # Covers: NFR-DEP-02
    # Same rule as mcp-agent-skills: MCP's HTTP transport has no auth of its
    # own, so reachability from `api` is the only access control there is.
    assert "\n  mcp-kb:" in compose
    block = _service_block(compose, "mcp-kb")
    assert "ports:" not in block
    assert "networks: [backend]" in block


def test_the_mcp_server_connects_as_the_read_only_role(compose):
    # Covers: NFR-DEP-05
    block = _service_block(compose, "mcp-kb")
    assert "postgresql://kb_reader:" in block
    assert "KB_POSTGRES_PASSWORD" not in block, "the query path must not get the owner credential"


def test_librechat_is_told_about_the_catalog_and_asks_it_for_no_credentials():
    # Covers: FR-MCP-04, FR-AGT-01
    template_path = REPO_ROOT / "config" / "librechat.yaml.example"
    if not template_path.exists():
        pytest.skip("LibreChat template not present -- kb checked out on its own?")
    template = template_path.read_text(encoding="utf-8")
    assert "http://mcp-kb:8322/mcp" in template
    assert "'mcp-kb:8322'" in template, "SSRF allowlist entry is required for an internal address"

    kb_block = template[template.index("  kb:\n    type: streamable-http") :]
    kb_block = kb_block[: kb_block.index("\nwebSearch:")]
    # The catalog is shared: nothing per-user to inject, so nothing to spoof.
    assert "customUserVars" not in kb_block and "headers" not in kb_block
    # Both tools are read-only, so neither belongs in the approval list.
    assert "kb_search" not in template and "kb_get" not in template


def test_the_scheduler_is_its_own_service(compose):
    # Covers: NFR-DEP-03
    # A full scrape runs for minutes; a search must never wait behind one.
    assert "\n  kb-scheduler:" in compose
    block = _service_block(compose, "kb-scheduler")
    assert "ports:" not in block, "it accepts nothing inbound"
    assert "kb.adapters.inbound.scheduler" in block
    # It writes, so it gets the owner credential -- unlike the query path.
    assert "KB_POSTGRES_PASSWORD" in block


def test_the_webhook_receiver_is_separate_and_off_by_default(compose):
    # Covers: NFR-DEP-04
    assert "\n  kb-webhook:" in compose
    block = _service_block(compose, "kb-webhook")
    assert 'profiles: ["webhook"]' in block, "opt-in: it is the only inbound port"
    assert '127.0.0.1:' in block, "bound to loopback, not published to the world"
    assert "KB_WEBHOOK_SECRET" not in block, "the secret comes from .env, never the compose file"

    mcp = _service_block(compose, "mcp-kb")
    assert "webhook" not in mcp, "mcp-kb keeps its no-published-port rule"


AGENT_DIR = REPO_ROOT / "agents"


def _agent(name: str) -> str:
    path = AGENT_DIR / f"{name}.yaml"
    if not path.exists():
        pytest.skip(f"{path} not present")
    return path.read_text(encoding="utf-8")


def test_the_front_door_agent_must_search_before_it_assumes():
    # Covers: FR-AGT-02
    # The whole module exists because an agent that guesses sounds exactly
    # like one that knows.
    text = _agent("product-assistant")
    assert "kb_search_mcp_kb" in text and "kb_get_mcp_kb" in text

    instructions = text.split("  tools:")[0]
    assert "before delegating" in instructions
    assert "language the user wrote in" in instructions
    assert "never answer from your own assumptions" in instructions.lower()
    assert "pointer, not a source" in instructions
    assert "Name the document you used" in instructions


@pytest.mark.parametrize("agent", ["plan", "doc-gen", "prepare-for-dev", "docs-manager"])
def test_document_producing_agents_ground_their_drafts(agent):
    # Covers: FR-AGT-03
    text = _agent(agent)
    assert "kb_search_mcp_kb" in text
    instructions = text.split("  tools:")[0]
    assert "kb_search" in instructions
    assert "instead of inventing" in instructions


def test_the_catalog_tools_need_no_approval_gate():
    # Covers: FR-AGT-01
    # Both are read-only, so an approval prompt would be noise that teaches
    # people to click through prompts.
    template = (REPO_ROOT / "config" / "librechat.yaml.example")
    if not template.exists():
        pytest.skip("LibreChat template not present")
    text = template.read_text(encoding="utf-8")
    ask_list = text.split("ask:")[1].split("reason:")[0] if "ask:" in text else ""
    assert "kb_search" not in ask_list and "kb_get" not in ask_list
