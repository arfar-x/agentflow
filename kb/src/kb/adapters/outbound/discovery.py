"""Drafting `kb-sources.yaml` from what the systems actually contain.

Configuring sources by hand means knowing every space key and project key
up front, and re-checking periodically for new ones. Discovery asks instead,
and writes what it finds as **disabled candidates** with the sizes attached, so
the decision an operator makes is "yes, catalog this one" rather than "what
exists?".

Two rules make this safe to re-run:

- **Everything it proposes is `enabled: false`.** A candidate is a suggestion
  until somebody enables it by hand, so nothing discovery writes can take effect
  unseen -- which is what makes it safe to run against a wiki nobody has
  audited.
- **It appends; it never rewrites.** Existing text is left byte for byte, so a
  re-run adds only sources that are not already in the file -- which makes
  discovery double as "what is new that the catalog isn't watching?".
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping
from urllib.parse import quote

logger = logging.getLogger("kb.discovery")

HEADER = """\
# Which spaces, repositories, projects and endpoints the catalog reads.
#
# Drafted by `make kb-sources-discover`. Everything it proposes is disabled:
# read it, delete what does not belong, and set `enabled: true` on what does.
#
# `approved: false` (or KB_SOURCES_APPROVED=false) stops all syncing at once --
# the switch for an incident, not for adding a space.
#
# ${VAR} and ${VAR:-default} work anywhere. Credentials are named, never
# written here: token_env: GITLAB_TOKEN, with the value in .env.

approved: true

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


#: Below this, a directory isn't a documentation home -- it's a stray README,
#: and proposing a scope for it is noise in a file meant to be read.
MIN_FILES_PER_SCOPE = 3


def discover_gitlab(session: Any, base_url: str, *, max_projects: int = 50) -> list[Candidate]:
    """Projects the token can reach, **with proposed scopes**.

    Reporting the directories where Markdown actually clusters
    (`docs/ADRs: 14 files`) is the whole point: it answers "which part of this
    repo is documentation?" so an operator doesn't have to go looking.
    """
    api = f"{base_url.rstrip('/')}/api/v4"
    response = session.get(
        f"{api}/projects", params={"membership": True, "per_page": max_projects, "simple": True}, timeout=30
    )
    response.raise_for_status()

    candidates: list[Candidate] = []
    for project in response.json() or []:
        path = project.get("path_with_namespace")
        if not path:
            continue
        clusters = _markdown_clusters(session, api, project.get("id") or quote(path, safe=""))
        if not clusters:
            continue
        total = sum(clusters.values())
        scopes, described = _propose_scopes(clusters)
        slug = path.replace("/", "-").lower()
        candidates.append(
            Candidate(
                id=f"gitlab-{slug}",
                kind="gitlab",
                size=f"{total} markdown files -- {described}",
                body=(
                    f"  - id: gitlab-{slug}\n"
                    f"    kind: gitlab\n"
                    f"    enabled: false\n"
                    f"    base_url: ${{GITLAB_BASE_URL}}\n"
                    f"    token_env: GITLAB_TOKEN\n"
                    f"    projects:\n"
                    f"      - path: {path}\n"
                    f"        scopes:\n" + scopes
                ),
            )
        )
    return candidates


def _markdown_clusters(session: Any, api: str, project_id: Any) -> dict[str, int]:
    """Markdown file counts per top-level directory ('.' for the root)."""
    counts: dict[str, int] = {}
    page = 1
    while page <= 5:  # a bounded look: this is a proposal, not an index
        try:
            response = session.get(
                f"{api}/projects/{project_id}/repository/tree",
                params={"recursive": True, "per_page": 100, "page": page},
                timeout=30,
            )
            response.raise_for_status()
        except Exception as exc:
            logger.debug("could not list %s: %s", project_id, exc)
            return counts
        entries = response.json() or []
        for entry in entries:
            path = entry.get("path") or ""
            if entry.get("type") != "blob" or not path.endswith(".md"):
                continue
            directory = path.rsplit("/", 1)[0] if "/" in path else "."
            counts[directory] = counts.get(directory, 0) + 1
        if len(entries) < 100:
            break
        page += 1
    return counts


def _propose_scopes(clusters: dict[str, int]) -> tuple[str, str]:
    """The YAML for the scopes, plus a one-line description of what was found."""
    ranked = sorted(clusters.items(), key=lambda pair: (-pair[1], pair[0]))
    worthwhile = [(d, n) for d, n in ranked if n >= MIN_FILES_PER_SCOPE and d != "."]
    if not worthwhile:
        return ('          - { dir: "." }\n', "scattered; proposing the whole repository")
    lines = "".join(f"          - {{ dir: {directory} }}\n" for directory, _ in worthwhile[:5])
    described = ", ".join(f"{directory}: {count} files" for directory, count in worthwhile[:5])
    return lines, described


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

    gitlab_url = env.get("GITLAB_BASE_URL") or env.get("KB_GITLAB_BASE_URL")
    gitlab_token = env.get("GITLAB_TOKEN") or env.get("KB_GITLAB_TOKEN")
    if gitlab_url and gitlab_token:
        session = make_session(None, None, None)
        session.headers.update({"PRIVATE-TOKEN": gitlab_token})
        candidates += discover_gitlab(session, gitlab_url)

    jira_url = env.get("KB_JIRA_BASE_URL") or env.get("JIRA_BASE_URL")
    if jira_url:
        session = make_session(
            env.get("KB_JIRA_PAT") or env.get("JIRA_PAT"),
            env.get("KB_JIRA_USERNAME") or env.get("JIRA_USERNAME"),
            env.get("KB_JIRA_PASSWORD") or env.get("JIRA_PASSWORD"),
        )
        candidates += discover_jira(session, jira_url)

    return candidates
