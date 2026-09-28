from __future__ import annotations

import pytest

from kb.domain.identity import content_hash, entry_id_for, slug


def test_ids_are_stable_across_runs_and_mechanisms():
    # Covers: FR-ENT-05
    # Incremental sync, nightly reconciliation and a webhook all derive the id
    # themselves; if they disagreed, one document would be catalogued twice.
    assert entry_id_for("confluence-eng", "123456") == entry_id_for("confluence-eng", "123456")
    assert entry_id_for("confluence-eng", "123456") == "confluence-eng:123456"


def test_the_source_prefix_keeps_two_sources_from_colliding():
    # Covers: FR-ENT-05
    assert entry_id_for("wiki-a", "1") != entry_id_for("wiki-b", "1")


def test_file_paths_survive_as_readable_ids():
    # Covers: FR-ENT-05
    assert entry_id_for("gitlab-platform", "docs/ADRs/0001-use-postgres.md") == (
        "gitlab-platform:docs-adrs-0001-use-postgres.md"
    )


def test_a_non_latin_external_id_still_produces_a_usable_handle():
    # Covers: FR-ENT-05
    entry_id = entry_id_for("wiki", "بازپرداخت")
    assert entry_id.startswith("wiki:")
    assert entry_id.isascii() and len(entry_id) > len("wiki:")
    # …and it is still deterministic.
    assert entry_id == entry_id_for("wiki", "بازپرداخت")


def test_missing_parts_are_a_programming_error():
    # Covers: FR-ENT-05
    with pytest.raises(ValueError):
        entry_id_for("", "1")
    with pytest.raises(ValueError):
        entry_id_for("wiki", "")


def test_slug_trims_and_bounds_length():
    # Covers: FR-ENT-05
    assert slug("  Hello, World!  ") == "hello-world"
    assert len(slug("a" * 200)) == 80


def test_content_hash_changes_with_any_part():
    # Covers: NFR-PRF-01
    base = content_hash("title", "body", "42")
    assert base == content_hash("title", "body", "42")
    assert base != content_hash("title", "body", "43")
    assert base != content_hash("title", "body changed", "42")


def test_concatenation_cannot_forge_an_equal_hash():
    # Covers: NFR-PRF-01
    # Without a separator, ("ab", "c") and ("a", "bc") would hash alike and a
    # changed document could look unchanged.
    assert content_hash("ab", "c") != content_hash("a", "bc")


def test_missing_parts_hash_as_empty_rather_than_failing():
    # Covers: NFR-PRF-01
    assert content_hash(None, "body") == content_hash("", "body")
