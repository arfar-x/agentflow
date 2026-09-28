"""Settings, read from the environment once and validated.

Deliberately not in `application/`: reading `os.environ` is I/O, and a use case
that reaches for a variable is a use case that can't be tested without one.
Everything here is resolved at startup and passed inward as arguments.

`kb-sources.yaml` (the per-source configuration, with its own `${VAR}`
interpolation) arrives in phase 6 and will be loaded here too.
"""

from __future__ import annotations

import os
from datetime import timedelta, timezone, tzinfo
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, ConfigDict, Field, field_validator


class Settings(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    #: Postgres DSN for the catalog. The query path should be given the
    #: least-privilege role's credential (kb_reader), not the owner's -- see
    #: scripts/kb-init.sh.
    database_url: str = Field(min_length=1)
    #: How many hits `kb_search` returns when the caller doesn't say.
    default_limit: int = Field(default=8, ge=1, le=50)
    #: After this long unverified, an entry is reported as stale. Matches the
    #: `lazy_refresh.stale_after` default in the source config.
    stale_after_hours: float = Field(default=24.0, gt=0)
    #: Bind address for the MCP server. Defaults to loopback: the container
    #: needs 0.0.0.0 to be reachable from `api`, and that is set explicitly in
    #: docker-compose.yml rather than being the default here.
    mcp_host: str = "127.0.0.1"
    mcp_port: int = Field(default=8322, ge=1, le=65535)

    #: Where kb-sources.yaml lives. Only sync and discovery read it; the MCP
    #: server never does, so a malformed source config cannot break search.
    sources_file: str = "/opt/kb/config/kb-sources.yaml"
    #: The summarizer: any OpenAI-compatible endpoint. Unset means sync still
    #: runs and catalogs documents under their real titles, undescribed --
    #: degraded rather than blocked.
    summarizer_url: str | None = None
    summarizer_model: str | None = None
    summarizer_api_key: str | None = None
    #: Which languages every entry is catalogued in. Two languages is what lets
    #: a question in one find a document written in the other.
    summary_languages: tuple[str, ...] = ("en",)
    #: IANA name, e.g. `Asia/Tehran`. The only thing that depends on it is a
    #: scheduled time of day: `at: "03:00"` means 03:00 here, not 03:00 UTC.
    #: Everything else compares instants and is unaffected.
    timezone: str = "UTC"

    @field_validator("summarizer_url")
    @classmethod
    def _openai_compatible_base(cls, value: str | None) -> str | None:
        """A *base* URL, the way every OpenAI-compatible client means it.

        `https://host/v1` -- not the endpoint itself. Pasting
        `.../v1/chat/completions` out of a curl example is the obvious mistake,
        and appending the path again would produce a 404 at the first sync
        rather than at startup, so it is normalized here instead of rejected.
        """
        if value is None or not value.strip():
            return None
        url = value.strip().rstrip("/")
        for suffix in ("/chat/completions", "/completions"):
            if url.endswith(suffix):
                url = url[: -len(suffix)]
        if not url.startswith(("http://", "https://")):
            raise ValueError(
                f"{value!r} is not a URL -- point it at an OpenAI-compatible base, "
                "e.g. https://your-endpoint/v1"
            )
        return url

    @field_validator("timezone")
    @classmethod
    def _known_zone(cls, value: str) -> str:
        """Fail at startup with the name that was wrong, rather than scheduling
        a nightly job at an hour nobody chose."""
        try:
            ZoneInfo(value)
        except (ZoneInfoNotFoundError, ValueError) as exc:
            raise ValueError(
                f"{value!r} is not a known timezone -- use an IANA name like "
                "UTC or Asia/Tehran"
            ) from exc
        return value

    @field_validator("database_url")
    @classmethod
    def _looks_like_a_dsn(cls, value: str) -> str:
        if not value.startswith(("postgres://", "postgresql://")):
            raise ValueError("must be a postgresql:// DSN")
        return value

    @property
    def stale_after(self) -> timedelta:
        return timedelta(hours=self.stale_after_hours)

    @classmethod
    def from_env(cls, env: dict[str, str] | None = None) -> "Settings":
        """Build from `KB_*` variables.

        Missing or malformed values fail here, at startup, with the variable
        named -- not on the first tool call hours later.
        """
        source = os.environ if env is None else env
        values: dict[str, object] = {}
        for field, variable in (
            ("database_url", "KB_DATABASE_URL"),
            ("default_limit", "KB_SEARCH_DEFAULT_LIMIT"),
            ("stale_after_hours", "KB_STALE_AFTER_HOURS"),
            ("mcp_host", "KB_MCP_HOST"),
            ("mcp_port", "KB_MCP_PORT"),
            ("sources_file", "KB_SOURCES_FILE"),
            ("summarizer_url", "KB_SUMMARIZER_URL"),
            ("summarizer_model", "KB_SUMMARIZER_MODEL"),
            ("summarizer_api_key", "KB_SUMMARIZER_API_KEY"),
            ("summary_languages", "KB_SUMMARY_LANGUAGES"),
            ("timezone", "KB_TIMEZONE"),
        ):
            raw = source.get(variable)
            if raw not in (None, ""):
                values[field] = raw
        if "database_url" not in values:
            raise MissingSetting("KB_DATABASE_URL")
        if isinstance(values.get("summary_languages"), str):
            # "en,fa" -- a list in an environment variable has to be spelled
            # somehow, and comma-separated is what every other tool here uses.
            values["summary_languages"] = tuple(
                part.strip() for part in values["summary_languages"].split(",") if part.strip()
            )
        return cls.model_validate(values)

    @property
    def tzinfo(self) -> tzinfo:
        return timezone.utc if self.timezone.upper() == "UTC" else ZoneInfo(self.timezone)

    @property
    def summarizer_configured(self) -> bool:
        return bool(self.summarizer_url and self.summarizer_model)


class MissingSetting(RuntimeError):
    """A required variable is unset. Carries the variable's name so both front
    doors can report it the same way."""

    def __init__(self, variable: str) -> None:
        self.variable = variable
        super().__init__(f"{variable} is not set")
