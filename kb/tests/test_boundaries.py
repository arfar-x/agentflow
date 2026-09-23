"""The hexagonal boundary, enforced.

Without a test, "I/O only at the edges" degrades into "mostly at the edges"
within a month: someone imports psycopg in a use case because it's convenient,
and the layer stops being testable without a database. This walks the source
and fails naming the file and the import.
"""

from __future__ import annotations

import ast
from pathlib import Path

SRC = Path(__file__).resolve().parent.parent / "src" / "kb"

#: Anything that talks to the outside world. The domain and application layers
#: may import from each other, the standard library and pydantic -- pydantic is
#: validation, not I/O, and nothing it does requires a database or a network.
FORBIDDEN_PREFIXES = (
    "psycopg",
    "requests",
    "httpx",
    "fastmcp",
    "yaml",
    "urllib3",
    "kb.adapters",
)

#: The standard library modules that would let a pure layer do I/O anyway.
FORBIDDEN_STDLIB = {"socket", "urllib", "sqlite3", "http", "subprocess", "asyncio"}


def _imports(path: Path) -> list[tuple[int, str]]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    found: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.extend((node.lineno, alias.name) for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            found.append((node.lineno, node.module))
    return found


def _violations(layer: str) -> list[str]:
    problems: list[str] = []
    for path in sorted((SRC / layer).rglob("*.py")):
        for lineno, module in _imports(path):
            root = module.split(".")[0]
            if module.startswith(FORBIDDEN_PREFIXES) or root in FORBIDDEN_STDLIB:
                problems.append(f"{path.relative_to(SRC.parent)}:{lineno} imports {module}")
    return problems


def test_domain_imports_nothing_that_touches_the_outside_world():
    # Covers: NFR-ARC-01
    assert _violations("domain") == []


def test_application_imports_nothing_that_touches_the_outside_world():
    # Covers: NFR-ARC-01
    # Use cases depend on ports (protocols), never on an implementation --
    # this is what lets them be tested with in-memory fakes.
    assert _violations("application") == []


def test_domain_does_not_depend_on_the_application_layer():
    # Covers: NFR-ARC-02
    inward = [
        f"{path.name}:{lineno}"
        for path in sorted((SRC / "domain").rglob("*.py"))
        for lineno, module in _imports(path)
        if module.startswith("kb.application")
    ]
    assert inward == []
