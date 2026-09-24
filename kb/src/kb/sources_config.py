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

Sync runs only against **approved** configuration. `approved` defaults to true,
because the real gate is per source: discovery writes every candidate
`enabled: false`, so nothing it proposes can take effect until somebody enables
it by hand. `approved` is the coarser switch on top of that -- a way to stop all
syncing at once, from the file or from `KB_SOURCES_APPROVED` in the environment,
without editing every source.
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


class NotApproved(ConfigError):
    """Syncing is switched off, in the file or in the environment."""


#: Kept so the rename is a readable error rather than "extra fields not
#: permitted" on a file that was correct last week.
RENAMED_FIELDS = {"reviewed": "approved"}


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

    #: The coarse switch: turn all syncing off without touching each source.
    #: Defaults to on, because per-source `enabled: false` is the gate that
    #: actually protects a freshly discovered space. Overridable by
    #: `KB_SOURCES_APPROVED`, which is the kill switch during an incident.
    approved: bool = True
    #: Where the effective value came from, for the CLI and the scheduler log
    #: -- an override nobody can see is how "why is my file being ignored?"
    #: starts.
    approved_from: str = "default"
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

    def require_approved(self) -> None:
        if not self.approved:
            where = (
                "KB_SOURCES_APPROVED=false in the environment"
                if self.approved_from == "environment"
                else "approved: false in the source configuration"
            )
            raise NotApproved(f"syncing is switched off ({where})")


class Defaults(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mechanisms: Mechanisms = Field(default_factory=Mechanisms)


SourcesConfig.model_rebuild()

DEFAULT_PATH = Path("/opt/kb/config/kb-sources.yaml")

#: Stops every source syncing without editing the file -- the switch you reach
#: for at 2am, not the one you reach for when adding a space.
_APPROVED_ENV = "KB_SOURCES_APPROVED"
_TRUTHY = {"1", "true", "yes", "on"}
_FALSY = {"0", "false", "no", "off"}


def _with_environment_approval(document: dict, env) -> dict:
    """Apply `KB_SOURCES_APPROVED`, and record that it was applied.

    The environment wins over the file on purpose: it is the kill switch, and a
    kill switch that a stale file can overrule is not one. Every reader reports
    where the value came from, so the override is never invisible.
    """
    raw = (env.get(_APPROVED_ENV) or "").strip().lower()
    if not raw:
        document.setdefault("approved_from", "file" if "approved" in document else "default")
        return document
    if raw not in _TRUTHY | _FALSY:
        raise ConfigError(f"{_APPROVED_ENV}={raw!r} is not a boolean -- use true or false")
    document["approved"] = raw in _TRUTHY
    document["approved_from"] = "environment"
    return document


def load(path: Path | None = None, *, env: dict[str, str] | None = None) -> SourcesConfig:
    """Read, interpolate, parse and validate the source configuration."""
    location = path or Path(os.environ.get("KB_SOURCES_FILE", DEFAULT_PATH))
    if not location.exists():
        raise ConfigError("file not found -- copy config/kb-sources.yaml.example to it", path=location)

    environment = os.environ if env is None else env
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

    if isinstance(document, dict):
        for old, new in RENAMED_FIELDS.items():
            if old in document:
                raise ConfigError(
                    f"{old!r} was renamed to {new!r} -- rename the key (the meaning is the same, "
                    f"but it now defaults to true and {_APPROVED_ENV} can override it)",
                    path=location,
                )
        document = _with_environment_approval(document, environment)

    try:
        return SourcesConfig.model_validate(document)
    except ValidationError as exc:
        problems = "; ".join(
            f"{'.'.join(str(part) for part in detail['loc'])}: {detail['msg']}"
            for detail in exc.errors()
        )
        raise ConfigError(problems, path=location) from exc


#: `15m`, `2h`, `90s`, `1d` -- the spellings a human writes in a config file.
_DURATION = re.compile(r"^\s*(\d+(?:\.\d+)?)\s*(s|m|h|d)\s*$", re.IGNORECASE)
_SECONDS_PER = {"s": 1, "m": 60, "h": 3600, "d": 86400}


def parse_duration(value: str | None, *, default: float | None = None) -> float | None:
    """`"15m"` -> 900 seconds.

    Returns `default` for an empty value so a config that omits a field falls
    back rather than failing; an unparseable one is an error, because silently
    treating `15mn` as "never" would be a scheduler that quietly does nothing.
    """
    if value is None or str(value).strip() == "":
        return default
    match = _DURATION.match(str(value))
    if not match:
        raise ConfigError(f"{value!r} is not a duration -- write 30s, 15m, 6h or 1d")
    return float(match.group(1)) * _SECONDS_PER[match.group(2).lower()]


_TIME_OF_DAY = re.compile(r"^\s*([01]?\d|2[0-3]):([0-5]\d)\s*$")


def parse_time_of_day(value: str | None) -> tuple[int, int] | None:
    """`"03:00"` -> (3, 0), in the deployment's own timezone."""
    if value is None or str(value).strip() == "":
        return None
    match = _TIME_OF_DAY.match(str(value))
    if not match:
        raise ConfigError(f"{value!r} is not a time of day -- write HH:MM, e.g. 03:00")
    return int(match.group(1)), int(match.group(2))
