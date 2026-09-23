"""Normalization is the difference between Persian text matching itself and
not, so every folding rule gets a case written the way the text actually
arrives from a wiki."""

from __future__ import annotations

import pytest

from kb.domain.text import normalize, normalize_all


def test_arabic_and_persian_spellings_of_the_same_word_match():
    # Covers: FR-TXT-01
    # "کتاب" typed with the Arabic kaf (U+0643) vs the Persian keheh (U+06A9)
    assert normalize("كتاب") == normalize("کتاب")
    # "یک" with Arabic yeh (U+064A) vs Farsi yeh (U+06CC)
    assert normalize("يك") == normalize("یک")


def test_alef_and_teh_marbuta_variants_fold():
    # Covers: FR-TXT-01
    assert normalize("آب") == normalize("اب")  # آب / اب
    assert normalize("مجلة") == normalize("مجله")


def test_zwnj_becomes_a_space_so_it_matches_the_spaced_spelling():
    # Covers: FR-TXT-02
    assert normalize("می‌رود") == "می رود"
    assert normalize("می‌رود") == normalize("می رود")


def test_digits_fold_to_ascii():
    # Covers: FR-TXT-01
    assert normalize("۱۲۳") == "123"  # Extended Arabic-Indic
    assert normalize("٠١٢") == "012"  # Arabic-Indic


def test_harakat_and_tatweel_are_dropped():
    # Covers: FR-TXT-01
    # کِتاب (with kasra) and کتاب
    assert normalize("کِتاب") == normalize("کتاب")
    # کــتاب (with tatweel)
    assert normalize("کــتاب") == normalize("کتاب")


def test_punctuation_becomes_a_boundary_and_latin_is_casefolded():
    # Covers: FR-TXT-04
    assert normalize("Refund-Retry (policy)!") == "refund retry policy"
    assert normalize("پرداخت، بازگشت") == "پرداخت بازگشت"


def test_whitespace_collapses_and_empty_input_is_empty():
    # Covers: FR-TXT-04
    assert normalize("  a\t  b \n") == "a b"
    assert normalize("") == ""
    assert normalize("   ") == ""
    assert normalize("!!!") == ""


def test_mixed_language_text_keeps_both_halves():
    # Covers: FR-TXT-04
    assert normalize("Checkout سرویس") == "checkout سرویس"


@pytest.mark.parametrize(
    "raw",
    [
        "می‌رود",
        "Refund-Retry (policy)!",
        "۱۲ کِتاب",
        "",
    ],
)
def test_normalization_is_idempotent(raw):
    # Covers: FR-TXT-03
    # Both sides of a search are normalized, and stored text is normalized
    # again on reindex -- a second pass must not change anything or the two
    # sides would drift apart.
    once = normalize(raw)
    assert normalize(once) == once


def test_normalize_all_handles_strings_lists_and_localized_maps():
    # Covers: FR-TXT-03
    assert normalize_all(None) == ""
    assert normalize_all("Refund") == "refund"
    assert normalize_all(["Refund", "Retry"]) == "refund retry"
    assert normalize_all({"en": "Refund", "fa": "بازپرداخت"}) == (
        "refund بازپرداخت"
    )
    assert normalize_all(["Refund", None, ""]) == "refund"
