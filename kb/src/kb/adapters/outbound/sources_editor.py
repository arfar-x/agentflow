"""Edit kb-sources.yaml without disturbing what the operator wrote.

PyYAML would rewrite the whole file: comments gone, `${VAR}` placeholders
resolved to whatever this process happens to see. ruamel.yaml's round-trip mode
keeps both, so an edit touches only the lines it is about. The headline rules:

- **Edit the raw tree, never the interpolated one.** A value the operator did
  not change keeps its `${VAR:-default}` exactly as written, and no resolved
  secret can ever be written back (FR-CFG-11).
- **Write only what changed.** The new settings are compared with the source as
  the scheduler will read it; unchanged keys are left alone, comments and all.
"""

from __future__ import annotations

import difflib
import io
import re
from typing import Any, Mapping

from pydantic import TypeAdapter
from ruamel.yaml import YAML
from ruamel.yaml.comments import CommentedMap, CommentedSeq

from kb.application.ports.source_files import (
    CreateSource,
    DeleteSource,
    SetApproved,
    UpdateSource,
)
from kb.application.use_cases.edit_sources import is_cleared, merge_settings
from kb.sources_config import AnySource, ConfigError, SourcesConfig

#: Written first, in this order, so a new block reads like the ones beside it.
_LEADING = ("id", "kind", "enabled")


def _yaml() -> YAML:
    yaml = YAML()  # round-trip
    yaml.preserve_quotes = True
    # The file's own style: block mappings, and sequences indented under their key.
    yaml.indent(mapping=2, sequence=4, offset=2)
    # Never re-wrap: a long JQL is one line in the file and stays one line.
    yaml.width = 100_000
    return yaml


def _to_ruamel(value: Any) -> Any:
    """Plain Python into ruamel's types, short lists of scalars in flow style
    (`spaces: [ENG, BIZ]`) as the file writes them."""
    if isinstance(value, Mapping):
        out = CommentedMap()
        for key, item in value.items():
            out[key] = _to_ruamel(item)
        return out
    if isinstance(value, (list, tuple)):
        seq = CommentedSeq(_to_ruamel(item) for item in value)
        if all(not isinstance(item, (Mapping, list, tuple)) for item in value):
            seq.fa.set_flow_style()
        return seq
    return value


def _clean(value: Any) -> Any:
    """What the form sent, minus what it left empty: None and empty containers
    are 'not set', not a value to write."""
    if isinstance(value, Mapping):
        cleaned = {k: _clean(v) for k, v in value.items()}
        return {k: v for k, v in cleaned.items() if v is not None and v != {} and v != []}
    if isinstance(value, (list, tuple)):
        return [_clean(v) for v in value]
    return value


_WHITESPACE = re.compile(r"\s+")


def _keep_original_spacing(original: str, rendered: str) -> str:
    """Put back the operator's spacing wherever the only thing the round trip
    changed is spacing.

    ruamel keeps comments and quoting but tidies the inside of `{ a: b,  c: d }`
    and the columns of aligned values. Harmless to the file, but it would put
    lines the edit never touched into every diff the operator is asked to read.
    Lines that really changed keep the new rendering.
    """
    old, new = original.splitlines(keepends=True), rendered.splitlines(keepends=True)
    squash = lambda line: _WHITESPACE.sub("", line)  # noqa: E731
    out: list[str] = []
    matcher = difflib.SequenceMatcher(a=[squash(l) for l in old], b=[squash(l) for l in new], autojunk=False)
    for tag, a1, a2, b1, b2 in matcher.get_opcodes():
        # Lines equal but for spacing: the operator's own. Anything else is the edit.
        out.extend(old[a1:a2] if tag == "equal" else new[b1:b2])
    return "".join(out)


def _indent(line: str) -> int:
    return len(line) - len(line.lstrip(" "))


def _is_comment_or_blank(line: str) -> bool:
    stripped = line.strip()
    return not stripped or stripped.startswith("#")


def _delete_lines(text: str, data: CommentedMap, source_id: str) -> str:
    """Remove one source's lines and nothing around them.

    Not done through the parser's dump: ruamel files the comment lines between
    two items under the *earlier* one, so dropping it would take the header of
    the next source with it. The source's own header comment -- the comment
    lines directly above it -- goes with it; the next source's stays.
    """
    sources = data.get("sources")
    if not isinstance(sources, CommentedSeq):
        raise ConfigError(f"no source with id {source_id!r}")
    index = next(
        (i for i, item in enumerate(sources) if isinstance(item, Mapping) and item.get("id") == source_id),
        None,
    )
    if index is None:
        raise ConfigError(f"no source with id {source_id!r}")

    lines = text.splitlines(keepends=True)

    def header_start(i: int) -> int:
        start = sources.lc.item(i)[0]
        while start > 0 and lines[start - 1].lstrip().startswith("#"):
            start -= 1
        # The one blank line that separates it from what is above.
        if start > 0 and not lines[start - 1].strip():
            start -= 1
        return start

    start = header_start(index)
    if index + 1 < len(sources):
        end = header_start(index + 1)
    else:
        # The last source: up to where its own content stops. Anything after
        # that -- a trailing comment, the next top-level key -- is not its.
        item_line = sources.lc.item(index)[0]
        dash_indent = _indent(lines[item_line])
        end = item_line + 1
        for number in range(item_line + 1, len(lines)):
            line = lines[number]
            if _is_comment_or_blank(line):
                continue
            if _indent(line) <= dash_indent and not line.lstrip().startswith("-"):
                break
            end = number + 1
    remaining = lines[:start] + lines[end:]
    return "".join(remaining)


class RuamelSourceEditor:
    """Implements `kb.application.ports.source_files.SourceTextEditor`."""

    def apply(
        self,
        text: str,
        change: CreateSource | UpdateSource | DeleteSource | SetApproved,
        current: SourcesConfig,
    ) -> str:
        yaml = _yaml()
        data = yaml.load(text)
        if data is None:
            data = CommentedMap()
        if not isinstance(data, CommentedMap):
            raise ConfigError("the file must be a mapping at its top level")

        if isinstance(change, SetApproved):
            if "approved" in data:
                data["approved"] = change.value
            else:
                data.insert(0, "approved", change.value)
        elif isinstance(change, CreateSource):
            self._create(data, change.config)
        elif isinstance(change, UpdateSource):
            self._update(data, change, current)
        elif isinstance(change, DeleteSource):
            sources = data.get("sources")
            if isinstance(sources, CommentedSeq) and len(sources) == 1:
                # Splicing out the only one would leave `sources:` empty, which
                # YAML reads as null and the loader rightly refuses.
                if sources[0].get("id") != change.id:
                    raise ConfigError(f"no source with id {change.id!r}")
                data["sources"] = _to_ruamel([])
            else:
                return _delete_lines(text, data, change.id)

        out = io.StringIO()
        yaml.dump(data, out)
        rendered = out.getvalue()
        rendered = rendered if rendered.endswith("\n") else rendered + "\n"
        return _keep_original_spacing(text, rendered)

    # -- the three edits to a source ------------------------------------------
    @staticmethod
    def _sources(data: CommentedMap) -> CommentedSeq:
        sources = data.get("sources")
        if not isinstance(sources, CommentedSeq):
            sources = CommentedSeq()
            data["sources"] = sources
        return sources

    def _create(self, data: CommentedMap, config: Mapping[str, Any]) -> None:
        cleaned = _clean(config)
        block = CommentedMap()
        for key in _LEADING:
            if key in cleaned:
                block[key] = cleaned[key]
        block.setdefault("enabled", False)  # a new source starts off unless told otherwise
        for key, value in cleaned.items():
            if key not in block:
                block[key] = _to_ruamel(value)
        self._sources(data).append(block)

    def _update(self, data: CommentedMap, change: UpdateSource, current: SourcesConfig) -> None:
        """Only the keys the change names are touched; naming one with no value
        (null, `{}`, `[]`) clears it. Everything else -- including a `${VAR}`
        the operator wrote -- stays exactly as it was."""
        block = self._find(data, change.id)
        source = next(s for s in current.sources if s.id == change.id)
        existing = source.model_dump(mode="json")

        sent = {k: v for k, v in change.config.items() if k not in ("id", "kind")}
        cleaned = {k: _clean(v) for k, v in sent.items()}
        cleared = {k for k, v in cleaned.items() if is_cleared(v)}
        # What the whole source would be, to compare like with like.
        wanted = (
            TypeAdapter(AnySource)
            .validate_python({**merge_settings(existing, cleaned), "id": change.id, "kind": source.kind})
            .model_dump(mode="json")
        )

        for key, value in cleaned.items():
            if key in cleared:
                if key in block:
                    del block[key]
            elif wanted.get(key) != existing.get(key):
                block[key] = _to_ruamel(value)

    def _delete(self, data: CommentedMap, source_id: str) -> None:
        raise NotImplementedError  # deletion splices text; see apply()

    @staticmethod
    def _find(data: CommentedMap, source_id: str) -> CommentedMap:
        for item in data.get("sources") or []:
            if isinstance(item, CommentedMap) and item.get("id") == source_id:
                return item
        raise ConfigError(f"no source with id {source_id!r}")
