"""The spec is executable: this test is what makes it so.

`docs/spec/knowledge-base.md` defines every requirement with a stable id and a
status. A test claims one with a `Covers: FR-ENT-02, FR-REC-05` line. This test
fails when:

- a requirement marked *done* has no test claiming it -- "implemented" without
  evidence is a claim, not a fact; or
- a test claims an id the spec does not define -- usually a requirement that was
  renamed or dropped while the test kept its old label.

Both failures are the same underlying problem: the spec and the code disagreeing
about what was built. Catching it here makes that a build failure rather than
something a reader notices six months later.
"""

from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
SPEC = REPO_ROOT / "docs" / "spec" / "knowledge-base.md"
TESTS = Path(__file__).resolve().parent

#: A requirement row: `| FR-ENT-01 | The entry MUST ... | done |`
REQUIREMENT_ROW = re.compile(
    r"^\|\s*((?:FR|NFR|AS)-[A-Z]*-?\d{2})\s*\|.*\|\s*(done|partial|planned)\s*\|\s*$",
    re.MULTILINE,
)
#: A claim: `# Covers: FR-ENT-01, FR-REC-05`
CLAIM = re.compile(r"Covers:\s*([A-Z][A-Z-]*-\d{2}(?:\s*,\s*[A-Z][A-Z-]*-\d{2})*)")


def spec_requirements() -> dict[str, str]:
    text = SPEC.read_text(encoding="utf-8")
    return {match.group(1): match.group(2) for match in REQUIREMENT_ROW.finditer(text)}


def claims() -> dict[str, set[str]]:
    """requirement id -> the test files claiming it."""
    found: dict[str, set[str]] = {}
    for path in sorted(TESTS.rglob("test_*.py")):
        for match in CLAIM.finditer(path.read_text(encoding="utf-8")):
            for requirement in (part.strip() for part in match.group(1).split(",")):
                found.setdefault(requirement, set()).add(path.name)
    return found


def test_the_spec_defines_requirements_this_test_can_read():
    # A parser that silently matches nothing would make every check below pass
    # vacuously, which is worse than no check at all.
    requirements = spec_requirements()
    assert len(requirements) > 40, "spec parsed into too few requirements -- has the table format changed?"
    assert "FR-ENT-01" in requirements


def test_every_implemented_requirement_has_a_test_claiming_it():
    implemented = {rid for rid, status in spec_requirements().items() if status == "done"}
    unclaimed = sorted(implemented - set(claims()))
    assert not unclaimed, (
        "marked done in docs/spec/knowledge-base.md but no test claims them: "
        + ", ".join(unclaimed)
    )


def test_no_test_claims_a_requirement_the_spec_does_not_define():
    defined = set(spec_requirements())
    unknown = sorted(
        f"{rid} (claimed by {', '.join(sorted(files))})"
        for rid, files in claims().items()
        if rid not in defined
    )
    assert not unknown, "claimed by tests but not defined in the spec: " + "; ".join(unknown)


def test_planned_requirements_are_not_quietly_implemented():
    """A planned requirement with tests is not an error -- it means the spec's
    status is stale. Failing here forces the status to be updated in the same
    change that implements it, which is the whole point of tracking status."""
    planned = {rid for rid, status in spec_requirements().items() if status == "planned"}
    claimed_but_planned = sorted(planned & set(claims()))
    assert not claimed_but_planned, (
        "tests cover these, but the spec still says planned -- update the status: "
        + ", ".join(claimed_but_planned)
    )
