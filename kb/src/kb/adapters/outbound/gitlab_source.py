"""GitLab as a knowledge source: specs, ADRs and docs that live beside code.

A project lists **scopes**, and a scope is a directory walked recursively with
file patterns. `dir: "."` is the whole repository, so "one directory inside a
big repo", "a whole specs repo" and "only the Markdown under docs/, minus the
changelog" are the same mechanism with different arguments. A scope's `type` and
`tags` are inherited by everything beneath it, which is how `docs/ADRs` becomes
a set of specs without anyone labelling them one at a time.

**A file's blob SHA is its version.** GitLab hands it over in the tree listing,
it changes exactly when the content changes, and using it costs no extra request
-- unlike asking the commits API per file, which would turn one sync into
hundreds of round trips.

**The incremental checkpoint is a commit SHA per project**, compared with the
`compare` endpoint so a run reads only the files that actually changed. Deleted
paths are reported too, but sync ignores them in incremental mode on purpose:
only a full pass may conclude that something is gone.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any, Iterable, Iterator
from urllib.parse import quote

import requests

from kb.application.ports.knowledge_source import SourceDocument
from kb.domain.entry import EntryType, Location
from kb.sources_config import GitLabProject, GitLabScope
from kb.sources_config import GitLabSource as GitLabSourceConfig

logger = logging.getLogger("kb.sources.gitlab")

PER_PAGE = 100
_HEADING = re.compile(r"^\s{0,3}#\s+(.+?)\s*$", re.MULTILINE)


def glob_match(path: str, pattern: str) -> bool:
    """Match a repository-relative path against a glob.

    Hand-rolled because `PurePath.match` only learned full `**` semantics in
    3.13, and `fnmatch` lets `*` cross directory boundaries -- which would make
    `docs/*.md` quietly match `docs/a/b.md`.
    """
    regex: list[str] = []
    index = 0
    while index < len(pattern):
        char = pattern[index]
        if pattern.startswith("**/", index):
            regex.append("(?:.*/)?")  # zero or more directories
            index += 3
        elif pattern.startswith("**", index):
            regex.append(".*")
            index += 2
        elif char == "*":
            regex.append("[^/]*")
            index += 1
        elif char == "?":
            regex.append("[^/]")
            index += 1
        else:
            regex.append(re.escape(char))
            index += 1
    return re.fullmatch("".join(regex), path) is not None


def title_from(markdown: str, file_path: str) -> str:
    """The first heading, or the filename.

    A document's own title beats `0001-use-postgres.md`, but a file with no
    heading still has to be catalogued under something a human recognizes.
    """
    match = _HEADING.search(markdown)
    if match:
        return match.group(1).strip()
    name = file_path.rsplit("/", 1)[-1]
    stem = name.rsplit(".", 1)[0]
    return stem.replace("-", " ").replace("_", " ").strip() or name


class GitLabKnowledgeSource:
    """Implements `kb.application.ports.KnowledgeSource`."""

    def __init__(
        self,
        config: GitLabSourceConfig,
        *,
        token: str | None = None,
        session: Any | None = None,
        timeout: float = 30.0,
    ) -> None:
        self._config = config
        self._base_url = config.base_url.rstrip("/")
        self._api = f"{self._base_url}/api/v4"
        self._timeout = timeout
        self._session = session or requests.Session()
        if token:
            self._session.headers.update({"PRIVATE-TOKEN": token})
        #: project path -> the ref actually used, filled in as projects are read
        #: and reported in the checkpoint.
        self._heads: dict[str, str] = {}

    @property
    def source_id(self) -> str:
        return self._config.id

    # -- listing -----------------------------------------------------------
    def list_all(self) -> Iterator[SourceDocument]:
        for project in self._config.projects:
            ref = self._ref_for(project)
            for scope in project.scopes:
                for path in self._tree(project.path, scope, ref):
                    document = self._read(project, scope, ref, path)
                    if document is not None:
                        yield document

    def changed_since(self, checkpoint: str | None) -> Iterator[SourceDocument]:
        """Only files touched since the recorded commit, per project.

        A project with no usable checkpoint falls back to a full walk of its
        scopes -- the alternative is silently cataloguing nothing for a
        repository somebody just added.
        """
        marks = self._parse_checkpoint(checkpoint)
        for project in self._config.projects:
            ref = self._ref_for(project)
            since = marks.get(project.path)
            if not since:
                for scope in project.scopes:
                    for path in self._tree(project.path, scope, ref):
                        document = self._read(project, scope, ref, path)
                        if document is not None:
                            yield document
                continue

            for path in self._changed_paths(project.path, since, ref):
                scope = self._scope_for(project, path)
                if scope is None:
                    continue
                document = self._read(project, scope, ref, path)
                if document is not None:
                    yield document

    def fetch(self, external_id: str) -> SourceDocument | None:
        project_path, _, file_path = external_id.partition(":")
        project = next((p for p in self._config.projects if p.path == project_path), None)
        if project is None or not file_path:
            return None
        scope = self._scope_for(project, file_path)
        if scope is None:
            return None
        return self._read(project, scope, self._ref_for(project), file_path)

    def _parse_checkpoint(self, checkpoint: str | None) -> dict[str, str]:
        if not checkpoint:
            return {}
        try:
            marks = json.loads(checkpoint)
        except (TypeError, ValueError):
            logger.warning("ignoring an unreadable checkpoint; walking instead")
            return {}
        return marks if isinstance(marks, dict) else {}

    def checkpoint(self) -> str | None:
        """Each project's current head, as JSON. Opaque to the caller, which is
        the point: only this adapter knows what its own cursor means."""
        marks: dict[str, str] = {}
        for project in self._config.projects:
            head = self._head_commit(project.path, self._ref_for(project))
            if head:
                marks[project.path] = head
        return json.dumps(marks) if marks else None

    # -- internals ---------------------------------------------------------
    def _project_id(self, path: str) -> str:
        return quote(path, safe="")

    def _ref_for(self, project: GitLabProject) -> str:
        if project.ref:
            return project.ref
        cached = self._heads.get(f"ref:{project.path}")
        if cached:
            return cached
        response = self._session.get(
            f"{self._api}/projects/{self._project_id(project.path)}", timeout=self._timeout
        )
        response.raise_for_status()
        ref = response.json().get("default_branch") or "main"
        self._heads[f"ref:{project.path}"] = ref
        return ref

    def _head_commit(self, project_path: str, ref: str) -> str | None:
        response = self._session.get(
            f"{self._api}/projects/{self._project_id(project_path)}/repository/commits",
            params={"ref_name": ref, "per_page": 1},
            timeout=self._timeout,
        )
        response.raise_for_status()
        commits = response.json() or []
        return commits[0].get("id") if commits else None

    def _tree(self, project_path: str, scope: GitLabScope, ref: str) -> Iterator[str]:
        """Every file path under a scope that matches its patterns."""
        directory = "" if scope.dir in {".", "", "/"} else scope.dir.strip("/")
        page = 1
        while True:
            response = self._session.get(
                f"{self._api}/projects/{self._project_id(project_path)}/repository/tree",
                params={
                    "ref": ref,
                    "path": directory,
                    "recursive": True,
                    "per_page": PER_PAGE,
                    "page": page,
                },
                timeout=self._timeout,
            )
            if response.status_code == 404:
                # A directory that does not exist in this repository: report it
                # and carry on, rather than failing a sync over one typo.
                logger.warning("%s: no such path %r on %s", project_path, directory, ref)
                return
            response.raise_for_status()
            entries = response.json() or []
            for entry in entries:
                if entry.get("type") != "blob":
                    continue
                path = entry.get("path") or ""
                if self._matches(path, scope, directory):
                    self._heads[f"blob:{project_path}:{path}"] = entry.get("id") or ""
                    yield path
            if len(entries) < PER_PAGE:
                return
            page += 1

    def _matches(self, path: str, scope: GitLabScope, directory: str) -> bool:
        relative = path[len(directory) + 1 :] if directory and path.startswith(directory + "/") else path
        if not any(glob_match(relative, pattern) for pattern in scope.patterns):
            return False
        return not any(
            glob_match(relative, pattern) or glob_match(path, pattern) for pattern in scope.exclude
        )

    def _scope_for(self, project: GitLabProject, path: str) -> GitLabScope | None:
        """Which scope owns a path -- the most specific one, so a file under
        `docs/ADRs` inherits the ADR scope's type rather than the broader
        `docs` scope that also matches it."""
        candidates = []
        for scope in project.scopes:
            directory = "" if scope.dir in {".", "", "/"} else scope.dir.strip("/")
            if directory and not path.startswith(directory + "/"):
                continue
            if self._matches(path, scope, directory):
                candidates.append((len(directory), scope))
        if not candidates:
            return None
        return max(candidates, key=lambda pair: pair[0])[1]

    def _changed_paths(self, project_path: str, since: str, ref: str) -> Iterable[str]:
        response = self._session.get(
            f"{self._api}/projects/{self._project_id(project_path)}/repository/compare",
            params={"from": since, "to": ref},
            timeout=self._timeout,
        )
        if response.status_code >= 400:
            # A rewritten or garbage-collected history makes the old SHA
            # unusable; a full walk is the honest fallback.
            logger.warning("%s: cannot compare from %s (%s); walking instead",
                           project_path, since, response.status_code)
            return []
        paths: list[str] = []
        for diff in response.json().get("diffs", []) or []:
            if diff.get("deleted_file"):
                continue  # only a full pass may decide something is gone
            path = diff.get("new_path") or diff.get("old_path")
            if path:
                paths.append(path)
        return paths

    def _read(
        self, project: GitLabProject, scope: GitLabScope, ref: str, path: str
    ) -> SourceDocument | None:
        response = self._session.get(
            f"{self._api}/projects/{self._project_id(project.path)}/repository/files/"
            f"{quote(path, safe='')}/raw",
            params={"ref": ref},
            timeout=self._timeout,
        )
        if response.status_code == 404:
            return None
        response.raise_for_status()
        body = response.text or ""

        entry_type = EntryType(scope.type) if scope.type else EntryType.DOC
        return SourceDocument(
            source_id=self.source_id,
            # project:path -- unique across projects, and readable in a log.
            external_id=f"{project.path}:{path}",
            title=title_from(body, path),
            body=body,
            location=Location(
                kind="gitlab",
                ref={"project": project.path, "path": path, "ref": ref},
                url=f"{self._base_url}/{project.path}/-/blob/{ref}/{path}",
            ),
            type=entry_type,
            # The blob SHA when the tree gave us one: it changes exactly when
            # the file does, and costs no extra request.
            version=self._heads.get(f"blob:{project.path}:{path}") or None,
            tags=scope.tags,
            extra={"project": project.path, "ref": ref},
        )
