"""Deployment requirements that live in the module's compose file and env files.

They are checked here because this is the suite that owns the spec they come
from: NFR-DEP-01 is a claim about `kb/compose.yaml`, and a claim nobody
checks is how a "separate database" quietly becomes another database inside
LibreChat's during a refactor.

Text matching rather than a YAML parse, deliberately: `yaml` is not a dependency
of this module, and these assertions are about a handful of specific lines.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

KB_ROOT = Path(__file__).resolve().parent.parent
REPO_ROOT = KB_ROOT.parent
COMPOSE = KB_ROOT / "compose.yaml"
ROOT_COMPOSE = REPO_ROOT / "docker-compose.yml"
SRC = KB_ROOT / "src" / "kb"


@pytest.fixture(scope="module")
def compose() -> str:
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
    # ... and so is the env file whose passwords the restored database accepts.
    assert ".env.*" in backup


def test_the_two_database_identities_are_documented(compose):
    # Covers: NFR-DEP-05
    # The owner that sync writes as, and the read-only role the query path
    # connects as. A deployment that only knows about one will hand the query
    # path the owner credential.
    env_example = (KB_ROOT / ".env.example").read_text(encoding="utf-8")
    assert "KB_POSTGRES_PASSWORD" in env_example
    assert "KB_READER_PASSWORD" in env_example
    # The module generates its own, on first creation of kb/.env.kb.
    prepare = (KB_ROOT / "scripts" / "prepare.sh").read_text(encoding="utf-8")
    assert "KB_POSTGRES_PASSWORD" in prepare and "KB_READER_PASSWORD" in prepare
    assert "openssl rand" in prepare


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
    assert "KB_WEBHOOK_SECRET" not in block, "the secret comes from kb/.env.kb, never the compose file"

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
    # It searches before answering itself, and hands work over without a
    # duplicate search -- the specialist grounds it (FR-AGT-03).
    assert "search the knowledge base first" in instructions
    assert "Do not search" in instructions
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


# --- Configuration and environment (NFR-CFG) ---------------------------------

#: Variables the code accepts that are deliberately not operator-facing: they
#: only restate a default, and nothing in the stack sets them. Adding a name
#: here is a decision that it should stay that way -- otherwise document it in
#: kb/.env.example.
UNDOCUMENTED_TUNABLES = {"KB_SEARCH_DEFAULT_LIMIT", "KB_STALE_AFTER_HOURS", "KB_TEST_DATABASE_URL"}


def _variables_the_code_reads() -> set[str]:
    names: set[str] = set()
    for path in SRC.rglob("*.py"):
        names |= set(re.findall(r'"((?:KB|GITLAB)_[A-Z0-9_]+)"', path.read_text(encoding="utf-8")))
    return names


def _assigned_or_commented(text: str) -> set[str]:
    """Names appearing as `NAME=` in an env template, commented out or not."""
    return set(re.findall(r"^(?:#\s?)?([A-Z][A-Z0-9_]*)=", text, re.MULTILINE))


def test_the_module_documents_every_variable_it_reads():
    # Covers: NFR-CFG-02
    documented = _assigned_or_commented((KB_ROOT / ".env.example").read_text(encoding="utf-8"))
    # Set by compose or the image, so an operator never supplies them.
    provided = set(re.findall(r"\b(KB_[A-Z0-9_]+):", COMPOSE.read_text(encoding="utf-8")))
    provided |= set(re.findall(r"\b(KB_[A-Z0-9_]+)=", (KB_ROOT / "Dockerfile").read_text(encoding="utf-8")))
    # Only names the code spells in full are checked, so an alias must be listed
    # in the template too (KB_GITLAB_TOKEN beside GITLAB_TOKEN).
    missing = _variables_the_code_reads() - documented - provided - UNDOCUMENTED_TUNABLES
    assert not missing, f"read by the code but not documented in kb/.env.example: {sorted(missing)}"


def test_the_stack_env_template_holds_none_of_the_modules_variables():
    # Covers: NFR-CFG-02
    root = (REPO_ROOT / ".env.example")
    if not root.exists():
        pytest.skip("stack .env.example not present -- kb checked out on its own?")
    leaked = sorted(name for name in _assigned_or_commented(root.read_text(encoding="utf-8")) if name.startswith("KB_"))
    assert not leaked, f"module-owned variables in the stack's .env.example: {leaked}"


def test_the_query_path_gets_a_short_explicit_list_and_no_env_file():
    # Covers: NFR-CFG-03
    block = _service_block(compose_text(), "mcp-kb")
    assert "env_file" not in block, "an env file would hand the query path the owner and source credentials"
    environment = block.split("environment:", 1)[1].split("networks:", 1)[0]
    names = set(re.findall(r"^\s+([A-Z][A-Z0-9_]*):", environment, re.MULTILINE))
    assert names == {"KB_DATABASE_URL", "KB_MCP_HOST", "KB_MCP_PORT"}


def test_the_syncing_services_read_the_modules_env_file_only():
    # Covers: NFR-CFG-04
    text = compose_text()
    for service in ("kb-cli", "kb-scheduler", "kb-webhook"):
        assert re.search(r"^\s+env_file: \.env\.kb$", _service_block(text, service), re.MULTILINE), service
    # Nothing in this file may reach for the stack's .env.
    assert not re.search(r"env_file:\s*(\.\./)?\.env\s*$", text, re.MULTILINE)


def test_the_stack_includes_the_module_with_its_own_env_file_alone():
    # Covers: NFR-CFG-04
    if not ROOT_COMPOSE.exists():
        pytest.skip("docker-compose.yml not present -- kb checked out on its own?")
    text = ROOT_COMPOSE.read_text(encoding="utf-8")
    include = re.search(r"^include:\n((?:[ \t]+.*\n)+)", text, re.MULTILINE)
    assert include, "the root compose file must include the module"
    entry = include.group(1)
    assert "path: kb/compose.yaml" in entry
    assert re.search(r"env_file: kb/\.env\.kb\s*$", entry, re.MULTILINE), (
        "the module interpolates from its own env file only, not the stack's .env"
    )
    assert "kb-db:" not in re.sub(r"^include:\n(?:[ \t]+.*\n)+", "", text, flags=re.MULTILINE), (
        "the module's services belong in kb/compose.yaml"
    )


def test_the_module_is_self_contained_on_disk():
    # Covers: NFR-CFG-05
    for relative in (
        "compose.yaml",
        "module.mk",
        ".env.example",
        "config/sources.yaml.example",
        "scripts/prepare.sh",
        "scripts/init.sh",
        "scripts/bootstrap.sh",
        "docs/SPEC.md",
        "docs/OPERATOR_GUIDE.md",
        "README.md",
        "AGENTS.md",
    ):
        assert (KB_ROOT / relative).exists(), f"kb/{relative} is missing"
    # The root files only reference the module; none of its pieces live there.
    for stray in ("config/kb-sources.yaml.example", "scripts/kb-init.sh", "docs/KNOWLEDGE_BASE.md"):
        assert not (REPO_ROOT / stray).exists(), f"{stray} should live under kb/"
    makefile = REPO_ROOT / "Makefile"
    if makefile.exists():
        assert "include kb/module.mk" in makefile.read_text(encoding="utf-8")


def compose_text() -> str:
    return COMPOSE.read_text(encoding="utf-8")
