"""Human corrections layered on top of generated entries.

Everything in the catalog is regenerated from its source, so a correction typed
into the catalog itself would be destroyed by the next sync. Overrides are kept
separately and re-applied on every write instead, which makes "fix this bad
summary" a permanent fix rather than one that survives until the page is next
edited.

Merge rules, chosen for least surprise:

- **Localized fields merge per language.** Overriding the English summary
  leaves the Persian one in place, so an untouched language keeps flowing
  through from the source.
- **List fields replace wholesale.** An override's `tags` are the tags; merging
  would make removing a wrong tag impossible.
- **Scalars replace when set.** `None` means "no opinion", not "clear it" --
  clearing is what `exclude` is for.
"""

from __future__ import annotations

from typing import Mapping

from pydantic import BaseModel, ConfigDict, Field

from .entry import Entry, EntryStatus, EntryType


class Override(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    entry_id: str = Field(min_length=1)
    type: EntryType | None = None
    title: Mapping[str, str] | None = None
    summary: Mapping[str, str] | None = None
    keywords: tuple[str, ...] | None = None
    tags: tuple[str, ...] | None = None
    aliases: tuple[str, ...] | None = None
    owner_team: str | None = None
    audience: tuple[str, ...] | None = None
    status: EntryStatus | None = None
    #: Keep the entry out of search entirely -- the way to retire something
    #: that still exists at the source (a duplicate, a page nobody should be
    #: sent to) without deleting anything upstream.
    exclude: bool = False
    #: Why, for whoever reads this in six months.
    note: str | None = None


def _merge_localized(
    generated: Mapping[str, str], override: Mapping[str, str] | None
) -> Mapping[str, str]:
    if not override:
        return generated
    return {**generated, **{lang: text for lang, text in override.items() if text}}


def apply_override(entry: Entry, override: Override | None) -> Entry:
    """The effective entry: what search indexes and what `kb_get` returns."""
    if override is None:
        return entry
    if override.entry_id != entry.id:
        raise ValueError(f"override for {override.entry_id!r} applied to entry {entry.id!r}")

    changes: dict[str, object] = {
        "title": _merge_localized(entry.title, override.title),
        "summary": _merge_localized(entry.summary, override.summary),
    }
    for field in ("type", "owner_team", "status"):
        value = getattr(override, field)
        if value is not None:
            changes[field] = value
    # List fields: `None` is "no opinion", but an empty tuple is a real
    # instruction to clear the field, so these are checked for None explicitly
    # rather than for truthiness.
    for field in ("keywords", "tags", "aliases", "audience"):
        value = getattr(override, field)
        if value is not None:
            changes[field] = value

    # `model_copy` re-uses the validated entry rather than re-running
    # validation, which is what we want here: an override cannot make a valid
    # entry invalid (it touches no field the reachability rule depends on).
    return entry.model_copy(update=changes)
