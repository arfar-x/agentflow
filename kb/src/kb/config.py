"""Settings, read from the environment once and validated.

Deliberately not in `application/`: reading `os.environ` is I/O, and a use case
that reaches for a variable is a use case that can't be tested without one.
Everything here is resolved at startup and passed inward as arguments.

`kb-sources.yaml` (the per-source configuration, with its own `${VAR}`
interpolation) arrives in phase 6 and will be loaded here too.
"""

from __future__ import annotations

import os
from datetime import timedelta

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
        ):
            raw = source.get(variable)
            if raw not in (None, ""):
                values[field] = raw
        if "database_url" not in values:
            raise MissingSetting("KB_DATABASE_URL")
        return cls.model_validate(values)


class MissingSetting(RuntimeError):
    """A required variable is unset. Carries the variable's name so both front
    doors can report it the same way."""

    def __init__(self, variable: str) -> None:
        self.variable = variable
        super().__init__(f"{variable} is not set")
