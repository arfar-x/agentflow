"""A document's own text, for the sources that choose to keep it.

The catalog describes documents; it does not hold them (`entry.py`). The one
exception is a source an operator marks `store_raw_content`: its documents'
text is kept beside their entries so an agent can read what no tool in the
stack can fetch live -- an ADR in a repository, today. That makes the text
readable by every catalog user, which is a decision about the source, not about
a document, so it is made per source in configuration (spec §6.10, C6).

Kept here, as pure rules, are the two things worth arguing about: how a document
that is too large is cut, and how the text is handed out in pieces a model can
take in one tool result.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

#: Per document. Generous on purpose: an ADR or a spec is a few kilobytes, a
#: long design doc a few hundred, so this only ever cuts something pathological
#: -- a generated reference, an export somebody committed by mistake.
DEFAULT_RAW_CONTENT_MAX_BYTES = 10 * 1024 * 1024

#: Below this a "cap" would truncate ordinary documents, which is a
#: misconfiguration rather than a choice.
MIN_RAW_CONTENT_MAX_BYTES = 1024

#: One page of text per `kb_get`. About 6-8k tokens: one comfortable tool result
#: in a 32k-128k context, and one page for a typical ADR. A database limit this
#: is not -- the model's context is what runs out first.
CONTENT_PAGE_CHARS = 24_000


class ContentPolicy(BaseModel):
    """What a source has decided about keeping its documents' text."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    store: bool = False
    max_bytes: int = Field(default=DEFAULT_RAW_CONTENT_MAX_BYTES, ge=MIN_RAW_CONTENT_MAX_BYTES)

    @classmethod
    def off(cls) -> "ContentPolicy":
        """The default everywhere: nothing is kept unless a source asks."""
        return cls()


class RawContent(BaseModel):
    """The text as it will be stored: possibly cut, and saying so."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    text: str = Field(min_length=1)
    truncated: bool = False
    #: The whole document's size in UTF-8 bytes, so a reader of a truncated
    #: copy knows how much it is missing.
    original_bytes: int = Field(ge=0)


def prepare(body: str | None, max_bytes: int) -> RawContent | None:
    """The text to keep for one document body, or None when there is none.

    The cut is made in bytes, because bytes are what storage costs, and then
    decoded dropping any partial trailing character -- so a Persian letter (two
    bytes) straddling the limit is left out whole rather than split into
    something that is not text.
    """
    if body is None or not body.strip():
        return None
    encoded = body.encode("utf-8")
    if len(encoded) <= max_bytes:
        return RawContent(text=body, original_bytes=len(encoded))
    kept = encoded[:max_bytes].decode("utf-8", errors="ignore")
    return RawContent(text=kept, truncated=True, original_bytes=len(encoded))


class ContentPage(BaseModel):
    """One piece of a stored text, and how to get the next one."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    text: str
    offset: int = Field(ge=0)
    total_chars: int = Field(ge=0)
    #: None on the last page. Following it page by page reproduces the stored
    #: text exactly: no gaps, no overlaps.
    next_offset: int | None = None
    truncated: bool = False
    original_bytes: int = Field(ge=0)


def clamp_offset(offset: int) -> int:
    """A negative offset reads from the start: an agent asking for "the
    beginning" in a creative way is not worth an error."""
    return max(0, offset)


def next_offset(offset: int, returned: int, total: int) -> int | None:
    """Where the page after this one starts, or None when this is the last."""
    end = offset + returned
    return end if returned > 0 and end < total else None
