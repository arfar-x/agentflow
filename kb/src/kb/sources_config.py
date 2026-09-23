"""Loading `kb-sources.yaml`: which spaces, repositories, projects and endpoints
the catalog reads.

Two things here are deliberate and worth keeping:

**`${VAR}` interpolation works the way Docker Compose's does** -- anywhere in the
file, for any variable, with `${VAR:-default}` for a fallback. An unset `${VAR}`
is an error naming the file, line and variable, never a silent empty string that
turns into a source pointed at nowhere. Values are then coerced by the model, so
`enabled: ${KB_X_ENABLED:-false}` is a boolean and not the string `"false"`.

**Credentials are referenced by variable name, never written here** (`token_env:
GITLAB_TOKEN`). The file is meant to be readable and reviewable; the secret
stays in `.env`.

Sync refuses to run while `reviewed: false`, which is what makes discovery safe:
it can propose candidates into this file without any of them taking effect until
a human has looked.
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Annotated, Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError

#: ${VAR} or ${VAR:-default}
_INTERPOLATION = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)(?::-([^}]*))?\}")


class ConfigError(RuntimeError):
    """Something in the file is wrong, described so it can be fixed without
    guessing: the path, and where in it."""

    def __init__(self, message: str, *, path: Path | None = None, line: int | None = None) -> None:
        location = f"{path}" if path else "kb-sources.yaml"
        if line is not None:
            location += f":{line}"
        self.path = path
        self.line = line
        super().__init__(f"{location}: {message}")


class NotReviewed(ConfigError):
    """The file says `reviewed: false`. Discovery writes candidates in that
    state on purpose, so nothing it proposes can take effect unseen."""


def _comment_starts_at(line: str) -> int | None:
    """Where a YAML comment begins on this line, ignoring `#` inside quotes.

    Needed because interpolation runs before parsing, so without this a `${VAR}`
    written in a comment -- including the one in the file's own header
    explaining the feature -- would be resolved, and fail.
    """
    quote: str | None = None
    for index, char in enumerate(line):
        if quote:
            if char == quote:
                quote = None
        elif char in "'\"":
            quote = char
        elif char == "#" and (index == 0 or line[index - 1] in " \t"):
            return index
    return None


def interpolate(text: str, env: dict[str, str] | None = None, *, path: Path | None = None) -> str:
    """Resolve every `${VAR}` in `text`, before YAML parsing.

    Before parsing, not after, so a variable may supply any part of the
    document -- a value, a list item, or a whole block -- as Compose behaves.
    Comments are left alone: they are documentation, not configuration.
    """
    source = os.environ if env is None else env
    offset = 0  # characters consumed so far, for reporting the right line

    def replace(match: re.Match[str]) -> str:
        name, default = match.group(1), match.group(2)
        value = source.get(name)
        if value is None or value == "":
            if default is not None:
                return default
            line = text[: offset + match.start()].count("\n") + 1
            raise ConfigError(
                f"${{{name}}} is not set and has no default (write ${{{name}:-…}} if it is optional)",
                path=path,
                line=line,
            )
        return value

    out: list[str] = []
    for line in text.splitlines(keepends=True):
        comment_at = _comment_starts_at(line)
        code = line if comment_at is None else line[:comment_at]
        comment = "" if comment_at is None else line[comment_at:]
        out.append(_INTERPOLATION.sub(replace, code) + comment)
        offset += len(line)
    return "".join(out)


class MechanismConfig(BaseModel):
    """One freshness mechanism's switch. Everything is optional so a source can
    override a single field of the defaults without repeating the rest."""

    model_config = ConfigDict(extra="forbid")

    enabled: bool | None = None
    every: str | None = None        # incremental: "15m"
    at: str | None = None           # full scrape: "03:00"
    stale_after: str | None = None  # lazy refresh: "24h"


class Mechanisms(BaseModel):
    model_config = ConfigDict(extra="forbid")

    incremental: MechanismConfig = Field(default_factory=MechanismConfig)
    full_scrape: MechanismConfig = Field(default_factory=MechanismConfig)
    webhook: MechanismConfig = Field(default_factory=MechanismConfig)
    lazy_refresh: MechanismConfig = Field(default_factory=MechanismConfig)

    def merged_with(self, other: "Mechanisms") -> "Mechanisms":
        """`other` (a source's own block) wins field by field over `self` (the
        defaults), so overriding one switch doesn't silently reset the rest."""
        merged: dict[str, MechanismConfig] = {}
        for name in type(self).model_fields:
            base: MechanismConfig = getattr(self, name)
            override: MechanismConfig = getattr(other, name)
            values = base.model_dump(exclude_none=True)
            values.update(override.model_dump(exclude_none=True))
            merged[name] = MechanismConfig.model_validate(values)
        return Mechanisms.model_validate(merged)


class _BaseSource(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1)
    enabled: bool = False  # discovery writes candidates disabled; opt in by hand
    mechanisms: Mechanisms = Field(default_factory=Mechanisms)


class ConfluenceSource(_BaseSource):
    kind: Literal["confluence"]
    spaces: tuple[str, ...] = ()
    #: A page labelled `kb-glossary` becomes a `term` entry: curation happens by
    #: labelling at the source, not by editing files here.
    type_from_labels: dict[str, str] = Field(default_factory=dict)
    exclude_labels: tuple[str, ...] = ()


class JiraSource(_BaseSource):
    kind: Literal["jira"]
    jql: str = Field(min_length=1)


class GitLabScope(BaseModel):
    """A directory to walk. `dir: "."` is the whole repository, so one
    directory, a whole repo and a pattern-limited subtree are all the same
    mechanism with different arguments."""

    model_config = ConfigDict(extra="forbid")

    dir: str = "."
    patterns: tuple[str, ...] = ("**/*.md",)
    exclude: tuple[str, ...] = ()
    #: Inherited by every entry found beneath this scope -- how `docs/ADRs`
    #: becomes a set of specs without labelling each one.
    type: str | None = None
    tags: tuple[str, ...] = ()


class GitLabProject(BaseModel):
    model_config = ConfigDict(extra="forbid")

    path: str = Field(min_length=1)
    ref: str | None = None  # defaults to the project's default branch
    scopes: tuple[GitLabScope, ...] = (GitLabScope(),)


class GitLabSource(_BaseSource):
    kind: Literal["gitlab"]
    base_url: str = Field(min_length=1)
    token_env: str = "GITLAB_TOKEN"
    projects: tuple[GitLabProject, ...] = ()


class HttpApiSource(_BaseSource):
    kind: Literal["http_api"]
    url: str = Field(min_length=1)
    token_env: str | None = None
    mapping: dict[str, str] = Field(default_factory=dict)


class LocalFilesSource(_BaseSource):
    kind: Literal["local_files"]
    paths: tuple[str, ...] = ()
    patterns: tuple[str, ...] = ("**/*.md",)


AnySource = Annotated[
    ConfluenceSource | JiraSource | GitLabSource | HttpApiSource | LocalFilesSource,
    Field(discriminator="kind"),
]


class SourcesConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    #: Sync refuses to run until an operator sets this. See `require_reviewed`.
    reviewed: bool = False
    defaults: "Defaults" = Field(default_factory=lambda: Defaults())
    sources: tuple[AnySource, ...] = ()

    def enabled_sources(self) -> tuple[AnySource, ...]:
        return tuple(source for source in self.sources if source.enabled)

    def source(self, source_id: str) -> AnySource:
        for source in self.sources:
            if source.id == source_id:
                return source
        known = ", ".join(s.id for s in self.sources) or "none configured"
        raise ConfigError(f"no source with id {source_id!r} (known: {known})")

    def mechanisms_for(self, source: AnySource) -> Mechanisms:
        return self.defaults.mechanisms.merged_with(source.mechanisms)

    def require_reviewed(self) -> None:
        if not self.reviewed:
            raise NotReviewed(
                "reviewed is false -- read the file, enable the sources you want, "
                "then set reviewed: true"
            )


class Defaults(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mechanisms: Mechanisms = Field(default_factory=Mechanisms)


SourcesConfig.model_rebuild()

DEFAULT_PATH = Path("/opt/kb/config/kb-sources.yaml")


def load(path: Path | None = None, *, env: dict[str, str] | None = None) -> SourcesConfig:
    """Read, interpolate, parse and validate the source configuration."""
    location = path or Path(os.environ.get("KB_SOURCES_FILE", DEFAULT_PATH))
    if not location.exists():
        raise ConfigError("file not found -- copy config/kb-sources.yaml.example to it", path=location)

    raw = interpolate(location.read_text(encoding="utf-8"), env, path=location)
    try:
        document: Any = yaml.safe_load(raw) or {}
    except yaml.YAMLError as exc:
        mark = getattr(exc, "problem_mark", None)
        raise ConfigError(
            getattr(exc, "problem", str(exc)) or str(exc),
            path=location,
            line=(mark.line + 1) if mark else None,
        ) from exc

    try:
        return SourcesConfig.model_validate(document)
    except ValidationError as exc:
        problems = "; ".join(
            f"{'.'.join(str(part) for part in detail['loc'])}: {detail['msg']}"
            for detail in exc.errors()
        )
        raise ConfigError(problems, path=location) from exc
