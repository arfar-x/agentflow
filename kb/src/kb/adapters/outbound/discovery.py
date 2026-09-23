"""Drafting `kb-sources.yaml` from what the systems actually contain.

Configuring sources by hand means knowing every space key and project key
up front, and re-checking periodically for new ones. Discovery asks instead,
and writes what it finds as **disabled candidates** with the sizes attached, so
the decision an operator makes is "yes, catalog this one" rather than "what
exists?".

Two rules make this safe to re-run:

- **Everything it proposes is `enabled: false`, and the file keeps
  `reviewed: false` until a human sets it.** Nothing discovery writes can take
  effect unseen.
- **It appends; it never rewrites.** Existing text is left byte for byte, so a
  re-run adds only sources that are not already in the file -- which makes
  discovery double as "what is new that the catalog isn't watching?".
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping

logger = logging.getLogger("kb.discovery")

HEADER = """\
# Which spaces, repositories, projects and endpoints the catalog reads.
#
# Drafted by `make kb-sources-discover`. Everything it proposes is disabled:
# read it, delete what does not belong, enable what does, then set
# reviewed: true -- sync refuses to run until you do.
#
# ${VAR} and ${VAR:-default} work anywhere. Credentials are named, never
# written here: token_env: GITLAB_TOKEN, with the value in .env.

reviewed: false

defaults:
  mechanisms:
    incremental:  { enabled: true,  every: 15m }
    full_scrape:  { enabled: true,  at: "03:00" }
    webhook:      { enabled: false }
    lazy_refresh: { enabled: true,  stale_after: 24h }

sources:
"""


@dataclass(frozen=True, slots=True)
class Candidate:
    """One proposed source, with the evidence for deciding about it."""

    id: str
    kind: str
    #: What this would catalog, e.g. "412 pages, last edited 2026-09-20".
    size: str
    body: str  # the YAML block, minus the leading comment

    def render(self) -> str:
        return f"  # discovered: {self.size}\n{self.body}"


def discover_confluence(session: Any, base_url: str, *, deployment: str = "server") -> list[Candidate]:
    """Every space the service account can see, with its page count."""
    api = f"{base_url.rstrip('/')}/wiki/rest/api" if deployment == "cloud" else f"{base_url.rstrip('/')}/rest/api"
    candidates: list[Candidate] = []
    start = 0
    while True:
        response = session.get(f"{api}/space", params={"limit": 50, "start": start, "type": "global"}, timeout=30)
        response.raise_for_status()
        payload = response.json()
        results = payload.get("results", [])
        for space in results:
            key = space.get("key")
            if not key:
                continue
            name = (space.get("name") or key).replace('"', "'")
            pages = _count_pages(session, api, key)
            candidates.append(
                Candidate(
                    id=f"confluence-{key.lower()}",
                    kind="confluence",
                    size=f"{pages if pages is not None else '?'} pages -- {name}",
                    body=(
                        f"  - id: confluence-{key.lower()}\n"
                        f"    kind: confluence\n"
                        f"    enabled: false\n"
                        f"    spaces: [{key}]\n"
                        f"    # type_from_labels: {{ kb-glossary: term, kb-product: product, kb-team: team }}\n"
                        f"    # exclude_labels: [archive, draft]\n"
                    ),
                )
            )
        if len(results) < 50:
            break
        start += 50
    return candidates


def _count_pages(session: Any, api: str, space_key: str) -> int | None:
    try:
        response = session.get(
            f"{api}/content/search",
            params={"cql": f'type = page and space = "{space_key}"', "limit": 1},
            timeout=30,
        )
        response.raise_for_status()
        return response.json().get("totalSize") or response.json().get("size")
    except Exception as exc:  # a count is nice to have, not worth failing over
        logger.debug("could not count pages in %s: %s", space_key, exc)
        return None


def discover_jira(session: Any, base_url: str) -> list[Candidate]:
    """Every project the service account can see.

    The proposed JQL selects epics only: most issues are work items, not
    knowledge, and cataloguing a sprint's worth of tasks would bury the
    features they belong to.
    """
    response = session.get(f"{base_url.rstrip('/')}/rest/api/2/project", timeout=30)
    response.raise_for_status()
    candidates: list[Candidate] = []
    for project in response.json() or []:
        key = project.get("key")
        if not key:
            continue
        name = (project.get("name") or key).replace('"', "'")
        candidates.append(
            Candidate(
                id=f"jira-{key.lower()}",
                kind="jira",
                size=f"project {key} -- {name}",
                body=(
                    f"  - id: jira-{key.lower()}\n"
                    f"    kind: jira\n"
                    f"    enabled: false\n"
                    f"    jql: 'project = {key} AND issuetype = Epic'\n"
                ),
            )
        )
    return candidates


def existing_source_ids(text: str) -> set[str]:
    """Ids already in the file, however they were written.

    Deliberately a scan rather than a YAML parse: the file may contain a
    `${VAR}` that cannot be resolved right now, and discovery still has to be
    able to append to it.
    """
    ids: set[str] = set()
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("- id:") or stripped.startswith("id:"):
            ids.add(stripped.split(":", 1)[1].strip().strip("'\""))
    return ids


def merge_into(path: Path, candidates: Iterable[Candidate]) -> tuple[list[str], list[str]]:
    """Append the candidates the file does not already have.

    Returns (added, skipped) ids. Existing content is never rewritten -- an
    operator's edits, comments and decisions survive every re-run.
    """
    existing_text = path.read_text(encoding="utf-8") if path.exists() else ""
    known = existing_source_ids(existing_text)

    added: list[str] = []
    skipped: list[str] = []
    blocks: list[str] = []
    for candidate in candidates:
        if candidate.id in known:
            skipped.append(candidate.id)
            continue
        blocks.append(candidate.render())
        added.append(candidate.id)

    if not blocks:
        return added, skipped

    if not existing_text.strip():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(HEADER + "\n".join(blocks), encoding="utf-8")
        return added, skipped

    separator = "" if existing_text.endswith("\n") else "\n"
    path.write_text(existing_text + separator + "\n".join(blocks), encoding="utf-8")
    return added, skipped


def discover_all(env: Mapping[str, str], *, session_factory: Any = None) -> list[Candidate]:
    """Ask every system the environment has credentials for."""
    import requests

    def make_session(token: str | None, username: str | None, password: str | None) -> Any:
        if session_factory is not None:
            return session_factory(token, username, password)
        session = requests.Session()
        if token:
            session.headers.update({"Authorization": f"Bearer {token}"})
        elif username and password:
            session.auth = (username, password)
        return session

    candidates: list[Candidate] = []

    confluence_url = env.get("KB_CONFLUENCE_BASE_URL") or env.get("CONFLUENCE_BASE_URL")
    if confluence_url:
        session = make_session(
            env.get("KB_CONFLUENCE_PAT") or env.get("CONFLUENCE_PAT"),
            env.get("KB_CONFLUENCE_USERNAME") or env.get("CONFLUENCE_USERNAME"),
            env.get("KB_CONFLUENCE_PASSWORD") or env.get("CONFLUENCE_PASSWORD"),
        )
        deployment = env.get("KB_CONFLUENCE_DEPLOYMENT_TYPE") or env.get("CONFLUENCE_DEPLOYMENT_TYPE") or "server"
        candidates += discover_confluence(session, confluence_url, deployment=deployment)

    jira_url = env.get("KB_JIRA_BASE_URL") or env.get("JIRA_BASE_URL")
    if jira_url:
        session = make_session(
            env.get("KB_JIRA_PAT") or env.get("JIRA_PAT"),
            env.get("KB_JIRA_USERNAME") or env.get("JIRA_USERNAME"),
            env.get("KB_JIRA_PASSWORD") or env.get("JIRA_PASSWORD"),
        )
        candidates += discover_jira(session, jira_url)

    return candidates
