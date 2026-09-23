"""Build a live `KnowledgeSource` from its configuration entry.

Credentials come from the environment, never from `kb-sources.yaml`, which
carries only the *name* of the variable to read. They belong to a **read-only
service account used by sync**, which is a different thing from the per-user
credentials an agent uses to read a document later: sync needs to see a space to
catalog it; a user needs permission to read the page itself, and that check
still happens at the source, as them.

`KB_`-prefixed variables win, falling back to the stack's existing
`CONFLUENCE_*`/`JIRA_*` ones so a deployment that already has a service account
does not configure it twice.
"""

from __future__ import annotations

import os
from typing import Mapping

from kb.application.ports.knowledge_source import KnowledgeSource
from kb.sources_config import (
    AnySource,
    ConfluenceSource,
    GitLabSource,
    HttpApiSource,
    JiraSource,
    LocalFilesSource,
)


class MissingCredential(RuntimeError):
    """A source is enabled but the environment has nothing to authenticate it
    with. Names every variable that would have worked."""

    def __init__(self, source_id: str, variables: tuple[str, ...]) -> None:
        self.source_id = source_id
        self.variables = variables
        super().__init__(
            f"source {source_id!r} needs one of: {', '.join(variables)} -- "
            "sync uses a read-only service account, set it in .env"
        )


def _first(env: Mapping[str, str], *names: str) -> str | None:
    for name in names:
        value = env.get(name)
        if value:
            return value
    return None


def build_source(config: AnySource, env: Mapping[str, str] | None = None) -> KnowledgeSource:
    environment = os.environ if env is None else env

    if isinstance(config, ConfluenceSource):
        from kb.adapters.outbound.confluence_source import ConfluenceKnowledgeSource

        base_url = _first(environment, "KB_CONFLUENCE_BASE_URL", "CONFLUENCE_BASE_URL")
        if not base_url:
            raise MissingCredential(config.id, ("KB_CONFLUENCE_BASE_URL", "CONFLUENCE_BASE_URL"))
        token = _first(environment, "KB_CONFLUENCE_PAT", "CONFLUENCE_PAT")
        username = _first(environment, "KB_CONFLUENCE_USERNAME", "CONFLUENCE_USERNAME")
        password = _first(environment, "KB_CONFLUENCE_PASSWORD", "CONFLUENCE_PASSWORD")
        if not token and not (username and password):
            raise MissingCredential(
                config.id, ("KB_CONFLUENCE_PAT", "KB_CONFLUENCE_USERNAME + KB_CONFLUENCE_PASSWORD")
            )
        return ConfluenceKnowledgeSource(
            config,
            base_url=base_url,
            username=username,
            password=password,
            token=token,
            deployment=_first(environment, "KB_CONFLUENCE_DEPLOYMENT_TYPE", "CONFLUENCE_DEPLOYMENT_TYPE")
            or "server",
        )

    if isinstance(config, JiraSource):
        from kb.adapters.outbound.jira_source import JiraKnowledgeSource

        base_url = _first(environment, "KB_JIRA_BASE_URL", "JIRA_BASE_URL")
        if not base_url:
            raise MissingCredential(config.id, ("KB_JIRA_BASE_URL", "JIRA_BASE_URL"))
        token = _first(environment, "KB_JIRA_PAT", "JIRA_PAT")
        username = _first(environment, "KB_JIRA_USERNAME", "JIRA_USERNAME")
        password = _first(environment, "KB_JIRA_PASSWORD", "JIRA_PASSWORD")
        if not token and not (username and password):
            raise MissingCredential(
                config.id, ("KB_JIRA_PAT", "KB_JIRA_USERNAME + KB_JIRA_PASSWORD")
            )
        return JiraKnowledgeSource(
            config, base_url=base_url, username=username, password=password, token=token
        )

    if isinstance(config, (GitLabSource, HttpApiSource, LocalFilesSource)):
        raise NotImplementedError(
            f"the {config.kind!r} source is specified but not built yet -- see "
            "docs/spec/knowledge-base.md, FR-SRC-03/04/05"
        )

    raise NotImplementedError(f"no adapter for source kind {config.kind!r}")
