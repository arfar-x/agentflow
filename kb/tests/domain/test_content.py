"""Cutting and paging a document's stored text."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from kb.domain.content import (
    CONTENT_PAGE_CHARS,
    DEFAULT_RAW_CONTENT_MAX_BYTES,
    ContentPolicy,
    clamp_offset,
    next_offset,
    prepare,
)


def test_nothing_is_kept_unless_a_source_asks():
    # Covers: FR-CNT-01
    policy = ContentPolicy.off()
    assert policy.store is False
    assert policy.max_bytes == DEFAULT_RAW_CONTENT_MAX_BYTES == 10 * 1024 * 1024


def test_a_document_under_the_cap_is_kept_whole():
    # Covers: FR-CNT-07
    content = prepare("# ADR-7\n\nWe chose Postgres.", max_bytes=1024)
    assert content is not None
    assert content.text == "# ADR-7\n\nWe chose Postgres."
    assert content.truncated is False
    assert content.original_bytes == len("# ADR-7\n\nWe chose Postgres.".encode())


def test_a_document_over_the_cap_is_kept_cut_marked_and_sized():
    # Covers: FR-CNT-07
    body = "x" * 5000
    content = prepare(body, max_bytes=1024)
    assert content is not None
    assert content.text == "x" * 1024
    assert content.truncated is True
    assert content.original_bytes == 5000


def test_the_cut_never_splits_a_persian_character():
    # Covers: FR-CNT-07
    # "س" is two bytes in UTF-8; 1023 ASCII bytes put the next one across the
    # limit, so it has to be dropped whole rather than halved.
    body = "a" * 1023 + "سلام"
    content = prepare(body, max_bytes=1024)
    assert content is not None
    assert content.text == "a" * 1023
    assert content.truncated is True
    content.text.encode("utf-8")  # still valid text
    whole = prepare("سلام دنیا", max_bytes=1024)
    assert whole is not None and whole.text == "سلام دنیا"


def test_an_empty_body_keeps_nothing():
    assert prepare("", max_bytes=1024) is None
    assert prepare("  \n\t", max_bytes=1024) is None
    assert prepare(None, max_bytes=1024) is None


def test_a_cap_too_small_to_be_a_choice_is_rejected():
    # Covers: FR-CNT-07
    with pytest.raises(ValidationError):
        ContentPolicy(store=True, max_bytes=100)


def test_pages_chain_until_the_end_and_then_stop():
    # Covers: FR-CNT-08
    total = 2 * CONTENT_PAGE_CHARS + 10
    assert next_offset(0, CONTENT_PAGE_CHARS, total) == CONTENT_PAGE_CHARS
    assert next_offset(CONTENT_PAGE_CHARS, CONTENT_PAGE_CHARS, total) == 2 * CONTENT_PAGE_CHARS
    assert next_offset(2 * CONTENT_PAGE_CHARS, 10, total) is None


def test_reading_at_or_past_the_end_is_an_empty_last_page():
    # Covers: FR-CNT-08
    assert next_offset(100, 0, 100) is None
    assert next_offset(500, 0, 100) is None


def test_a_negative_offset_reads_from_the_start():
    # Covers: FR-CNT-08
    assert clamp_offset(-5) == 0
    assert clamp_offset(7) == 7
