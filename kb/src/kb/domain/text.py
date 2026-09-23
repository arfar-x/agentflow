"""Text normalization, applied identically when an entry is written and when a
query is searched.

Without this, Persian text does not match itself. The same word is routinely
written with the Arabic yeh/kaf instead of the Persian ones, with or without a
zero-width non-joiner, with Arabic-Indic or ASCII digits, and with or without
harakat -- all of which are different byte sequences that a human reads as one
word. Normalizing both sides collapses those into one form.

Two choices here are judgement calls, recorded because they are the ones most
likely to be revisited:

- **ZWNJ becomes a space, not nothing.** "می‌رود" and "می رود" then match, while
  "میرود" (written joined) does not. The opposite choice trades one of those
  misses for the other; trigram matching partially covers whichever we lose.
- **The alef and teh-marbuta variants are folded** (أ إ آ ٱ -> ا, ة -> ه). This
  is wrong for Arabic text proper, where those letters are distinct words apart,
  but right for Persian content where they appear as inconsistent spellings of
  the same word.
"""

from __future__ import annotations

import unicodedata

_ZWNJ = "‌"

#: Dropped entirely: joiners and marks that carry no matching signal. Tatweel
#: (U+0640) is decoration inside a word -- "کــتاب" is "کتاب".
_DROP = frozenset({"‍", "‎", "‏", "﻿", "ـ"})

#: Letter folding plus digit folding, in one table.
_FOLD = {
    # Arabic letters -> their Persian counterparts
    "ي": "ی",  # ARABIC YEH -> FARSI YEH
    "ى": "ی",  # ALEF MAKSURA -> FARSI YEH
    "ۍ": "ی",  # YEH WITH TAIL -> FARSI YEH
    "ك": "ک",  # ARABIC KAF -> KEHEH
    "ڪ": "ک",  # SWASH KAF -> KEHEH
    # Alef variants -> bare alef
    "آ": "ا",  # ALEF WITH MADDA
    "أ": "ا",  # ALEF WITH HAMZA ABOVE
    "إ": "ا",  # ALEF WITH HAMZA BELOW
    "ٱ": "ا",  # ALEF WASLA
    # Teh marbuta -> heh
    "ة": "ه",
    "ۀ": "ه",  # HEH WITH YEH ABOVE
    # Arabic-Indic and Extended Arabic-Indic digits -> ASCII
    **{chr(0x0660 + n): str(n) for n in range(10)},
    **{chr(0x06F0 + n): str(n) for n in range(10)},
}


def normalize(text: str) -> str:
    """Fold `text` to the form used for storage and matching.

    Returns lowercase text with punctuation and separators collapsed to single
    spaces, so the result is directly comparable and trigram-indexable. Empty
    or whitespace-only input returns an empty string.
    """
    if not text:
        return ""

    out: list[str] = []
    # NFKC first: it resolves Arabic presentation forms and ligatures into
    # their base letters, so the folding table below only has to handle the
    # differences NFKC considers meaningful.
    for char in unicodedata.normalize("NFKC", text):
        if char in _DROP:
            continue
        # Mn = non-spacing marks: harakat, sukun, superscript alef. Persian
        # text is usually written without them, so keeping them would make an
        # occasionally-vocalized document unmatchable.
        if unicodedata.category(char) == "Mn":
            continue
        char = _FOLD.get(char, char)
        # Punctuation, symbols, separators and control characters all become
        # word boundaries -- a query typed with different punctuation than the
        # document should still match.
        if char == _ZWNJ or unicodedata.category(char)[0] in {"P", "S", "Z", "C"}:
            out.append(" ")
        else:
            out.append(char)

    return " ".join("".join(out).casefold().split())


def normalize_all(texts: object) -> str:
    """Normalize a string, or any iterable of strings, into one searchable
    blob. Convenience for building the indexed columns, where a field may be a
    single title or a list of keywords.
    """
    if texts is None:
        return ""
    if isinstance(texts, str):
        return normalize(texts)
    if isinstance(texts, dict):  # a localized field: index every language
        return normalize(" ".join(str(v) for v in texts.values() if v))
    return normalize(" ".join(str(t) for t in texts if t))
