"""Change the source configuration, carefully (FR-CFG-10..12).

Every change takes two steps. A **preview** makes the edit in memory, checks the
result with the very rules the scheduler loads the file with, and says in words
what it would do -- including what an operator would regret not being told. An
**apply** then repeats the edit against the file as it is *now*, and writes it
only if the file still has the fingerprint the preview was made from and the
operator confirmed. Nothing invalid can be written, and a hand edit made in
between is never silently overwritten.
"""

from __future__ import annotations

import difflib
import hashlib
from datetime import datetime
from typing import Any, Callable, Collection, Literal, Mapping

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, ValidationError

from kb.application.ports.clock import Clock
from kb.application.ports.source_files import (
    CreateSource,
    DeleteSource,
    FileChanged,
    SetApproved,
    SourceChange,
    SourcesFile,
    SourceTextEditor,
    UpdateSource,
)
from kb.application.ports.source_history import Revision, SourceHistory
from kb.sources_config import AnySource, ConfigError, SourcesConfig, parse, parse_duration

Level = Literal["info", "warning", "danger"]

class InvalidChange(ValueError):
    """The change cannot be made; the message says why, for the operator."""


class Conflict(RuntimeError):
    """The file changed after the preview. Look again before applying."""


class NotConfirmed(ValueError):
    """An apply without the operator's explicit confirmation."""


class Warn(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    level: Level
    text: str


class Preview(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    action: str
    source_id: str | None
    summary: str
    #: Unified diff of the file; empty when the change changes nothing.
    diff: str
    changed: bool
    warnings: tuple[Warn, ...] = Field(default_factory=tuple)
    etag: str


class Applied(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    changed: bool
    etag: str
    revision_id: int | None = None


def is_cleared(value: Any) -> bool:
    """A setting sent with no value -- null or an empty container -- means
    "clear it", not "set it to nothing"."""
    return value is None or value == {} or value == []


def merge_settings(existing: Mapping[str, Any], sent: Mapping[str, Any]) -> dict[str, Any]:
    """What a source would be after a partial update: what it is now, with the
    keys the change names replaced, and those it names empty removed. Keys the
    change does not mention stay as they are."""
    merged = dict(existing)
    for key, value in sent.items():
        if key in ("id", "kind"):
            continue
        if is_cleared(value):
            merged.pop(key, None)
        else:
            merged[key] = value
    return merged


def fingerprint(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:32]


def _unified(before: str, after: str) -> str:
    return "".join(
        difflib.unified_diff(
            before.splitlines(keepends=True),
            after.splitlines(keepends=True),
            fromfile="kb-sources.yaml (now)",
            tofile="kb-sources.yaml (after)",
            n=3,
        )
    )


class EditSources:
    def __init__(
        self,
        file: SourcesFile,
        editor: SourceTextEditor,
        history: SourceHistory,
        clock: Clock,
        *,
        env: Mapping[str, str] | None = None,
        credentials: Callable[[], Mapping[str, Mapping[str, bool]]] | None = None,
        buildable_kinds: Collection[str] = (),
        missing_credentials: Callable[[AnySource, Mapping[str, bool]], list[str]] | None = None,
    ) -> None:
        self._file = file
        self._editor = editor
        self._history = history
        self._clock = clock
        self._env = dict(env or {})
        #: What the scheduler can see (heartbeat): source id -> variable -> set.
        self._credentials = credentials or (lambda: {})
        #: Which kinds of source have an adapter, and what each one needs from
        #: the environment. Both are the adapters' knowledge, handed in: no
        #: source's name belongs in this layer (FR-SRC-06).
        self._buildable = tuple(buildable_kinds)
        self._missing = missing_credentials or (lambda source, seen: [])

    # -- reading -------------------------------------------------------------
    def writable(self) -> bool:
        return self._file.writable()

    def current(self) -> tuple[SourcesConfig | None, str | None, str]:
        """(config, why it cannot be read, etag)."""
        snap = self._file.snapshot()
        try:
            return self._read(snap.text), None, snap.etag
        except ConfigError as exc:
            return None, str(exc), snap.etag

    def revisions(self, *, limit: int = 50) -> list[Revision]:
        return self._history.revisions(limit=limit)

    def origins(self) -> dict[str, str]:
        return self._history.origins()

    def sync_mirror(self) -> None:
        """Notice a change made to the file by hand and catch the copy up (the
        file always wins). Called whenever the console reads the sources."""
        snap = self._file.snapshot()
        digest = fingerprint(snap.text)
        if self._history.last_hash() == digest:
            return
        try:
            config = self._read(snap.text)
        except ConfigError:
            # A half-edited file: record nothing, keep the copy as it was. The
            # next read after it is fixed records the change.
            return
        before = self._history.last_text() or ""
        self._history.record(
            action="file",
            source_id=None,
            actor="file",
            at=self._clock.now(),
            before_text=before,
            after_text=snap.text,
            diff=_unified(before, snap.text),
            after_hash=digest,
        )
        self._history.mirror(config, created=(), at=self._clock.now())

    # -- the two steps -------------------------------------------------------
    def preview(self, change: SourceChange) -> Preview:
        snap, before, after, new_text = self._edit(change)
        return Preview(
            action=change.action,
            source_id=_source_id(change),
            summary=_summary(change),
            diff=_unified(snap.text, new_text),
            changed=new_text != snap.text,
            warnings=tuple(self._review(before, after, change)),
            etag=snap.etag,
        )

    def apply(self, change: SourceChange, *, etag: str, actor: str, confirmed: bool) -> Applied:
        if not confirmed:
            raise NotConfirmed("the change was not confirmed")
        snap, before, after, new_text = self._edit(change)
        if snap.etag != etag:
            raise Conflict(
                "The file changed after you previewed this. Review the change again "
                "against the current file."
            )
        if new_text == snap.text:
            return Applied(changed=False, etag=snap.etag)
        try:
            written = self._file.write(new_text, expected_etag=etag)
        except FileChanged as exc:
            raise Conflict(str(exc)) from exc
        revision = self._history.record(
            action=change.action if change.action != "approved" else "settings",
            source_id=_source_id(change),
            actor=actor,
            at=self._clock.now(),
            before_text=snap.text,
            after_text=new_text,
            diff=_unified(snap.text, new_text),
            after_hash=fingerprint(new_text),
        )
        created = (change.config.get("id"),) if isinstance(change, CreateSource) else ()
        self._history.mirror(after, created=[c for c in created if c], at=self._clock.now())
        return Applied(changed=True, etag=written.etag, revision_id=revision)

    # -- internals -----------------------------------------------------------
    def _read(self, text: str) -> SourcesConfig:
        # Lenient, like the console's every other reading: it cannot resolve
        # the variables that carry source credentials, and the scheduler is the
        # authority on whether those are set.
        return parse(text, env=self._env, lenient=True)

    def _edit(self, change: SourceChange):
        snap = self._file.snapshot()
        try:
            before = self._read(snap.text)
        except ConfigError as exc:
            raise InvalidChange(
                f"The file cannot be read as it is, so it will not be edited from here: {exc}"
            ) from exc
        self._validate_change(change, before)
        try:
            new_text = self._editor.apply(snap.text, change, before)
            after = self._read(new_text)
        except ConfigError as exc:
            raise InvalidChange(str(exc)) from exc
        return snap, before, after, new_text

    def _validate_change(self, change: SourceChange, before: SourcesConfig) -> None:
        known = {s.id for s in before.sources}
        if isinstance(change, CreateSource):
            new_id = str(change.config.get("id", ""))
            kind = change.config.get("kind")
            if kind not in self._buildable:
                raise InvalidChange(
                    f"kind must be one of {', '.join(self._buildable)}; "
                    "the others are specified but not built"
                )
            if new_id in known:
                raise InvalidChange(f"there is already a source called {new_id!r}")
            self._validate_source(change.config)
        elif isinstance(change, UpdateSource):
            if change.id not in known:
                raise InvalidChange(f"no source with id {change.id!r}")
            if change.config.get("id", change.id) != change.id:
                raise InvalidChange("a source's id cannot be changed: its entries are named after it")
            current = next(s for s in before.sources if s.id == change.id)
            if change.config.get("kind", current.kind) != current.kind:
                raise InvalidChange("a source's kind cannot be changed; add a new source instead")
            merged = merge_settings(current.model_dump(mode="json"), change.config)
            self._validate_source({**merged, "id": change.id, "kind": current.kind})
        elif isinstance(change, DeleteSource):
            if change.id not in known:
                raise InvalidChange(f"no source with id {change.id!r}")

    @staticmethod
    def _validate_source(config: Mapping[str, Any]) -> None:
        try:
            TypeAdapter(AnySource).validate_python(dict(config))
        except ValidationError as exc:
            problems = "; ".join(
                f"{'.'.join(str(p) for p in d['loc'][1:] or d['loc'])}: {d['msg']}" for d in exc.errors()
            )
            raise InvalidChange(problems) from exc

    # -- saying what a change means -----------------------------------------
    def _review(self, before: SourcesConfig, after: SourcesConfig, change: SourceChange) -> list[Warn]:
        notes: list[Warn] = []
        if isinstance(change, SetApproved):
            if not change.value:
                notes.append(Warn(level="danger", text=(
                    "Syncing is switched off for every source. Nothing is scheduled, and syncs "
                    "requested from the console or the CLI are refused, until it is switched back on. "
                    "(KB_SOURCES_APPROVED in the environment overrides this setting.)")))
            else:
                notes.append(Warn(level="info", text="Syncing is switched on; enabled sources run on their schedules."))
            if after.approved_from == "environment":
                notes.append(Warn(level="warning", text=(
                    "KB_SOURCES_APPROVED is set in the environment and overrides this file, "
                    "so this change will not take effect until it is removed.")))
            return notes

        if isinstance(change, DeleteSource):
            gone = next(s for s in before.sources if s.id == change.id)
            notes.append(Warn(level="danger", text=(
                f"Deleting {change.id!r} removes it from the file and stops its schedule. "
                "Its entries stay in the catalog and stay searchable -- nothing syncs a source "
                "that is no longer configured, so nothing retires them. Remove them too, or "
                "they will go stale.")))
            if gone.store_raw_content:
                notes.append(Warn(level="warning", text=(
                    "Its stored document text is not removed by deleting the source; "
                    "removing its entries removes the text with them.")))
            return notes

        source = next(s for s in after.sources if s.id == _source_id(change))
        old = next((s for s in before.sources if s.id == source.id), None)

        if old is None:
            notes.append(Warn(level="info", text=(
                f"A new {source.kind} source, "
                + ("enabled: it is synced on its next tick." if source.enabled
                   else "disabled: nothing is synced until you enable it. "
                        "Run it as a dry run first to see what it would catalogue."))))
        if source.store_raw_content and not (old and old.store_raw_content):
            notes.append(Warn(level="danger", text=(
                "Turning on stored text makes every document's full text readable, through the "
                "catalog, by every user of the catalog -- whatever their access to the original in "
                f"{source.kind}. Do this only for sources whose content anyone may read (spec C6).")))
        if old and old.store_raw_content and not source.store_raw_content:
            notes.append(Warn(level="warning", text=(
                "Turning off stored text means the next sync of this source deletes all the text "
                "kept for it. Entries and summaries stay.")))
        if old and old.enabled and not source.enabled:
            notes.append(Warn(level="warning", text=(
                "Disabling stops every sync of this source on its schedule. Its entries stay "
                "searchable and go stale.")))
        if source.enabled and not (old and old.enabled):
            missing = self._missing_credentials(source)
            if missing:
                notes.append(Warn(level="warning", text=(
                    f"The scheduler cannot see {', '.join(missing)}, so syncs of this source will "
                    "fail until it is set in .env and the scheduler restarted.")))
        merged = after.mechanisms_for(source)
        every = parse_duration(merged.incremental.every, default=None) if merged.incremental.enabled else None
        if every is not None and every < 60:
            notes.append(Warn(level="warning", text=(
                f"Incremental every {merged.incremental.every} asks the source for changes more than "
                "once a minute; that is a lot of requests for little freshness.")))
        if not after.approved:
            notes.append(Warn(level="warning", text="Syncing is switched off globally, so nothing here runs yet."))
        return notes

    def _missing_credentials(self, source: AnySource) -> list[str]:
        seen = self._credentials().get(source.id)
        if not seen:
            return []  # the scheduler has not reported them; say nothing
        return self._missing(source, seen)


def _source_id(change: SourceChange) -> str | None:
    if isinstance(change, CreateSource):
        return str(change.config.get("id") or "") or None
    if isinstance(change, (UpdateSource, DeleteSource)):
        return change.id
    return None


def _summary(change: SourceChange) -> str:
    if isinstance(change, CreateSource):
        return f"Add the source {_source_id(change)!r}"
    if isinstance(change, UpdateSource):
        return f"Change the source {change.id!r}"
    if isinstance(change, DeleteSource):
        return f"Delete the source {change.id!r}"
    return "Switch syncing on" if change.value else "Switch syncing off"
