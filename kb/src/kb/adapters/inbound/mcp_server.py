"""The agent's front door: two read-only MCP tools over HTTP.

Internal-only, with no published port and no authentication of its own -- the
same trust model `mcp-agent-skills` runs under, and for the same reason:
reachability from `api` on the backend network is the access control. Unlike
that server, this one takes **no credentials at all**. The catalog is shared, so
there is nothing per-user for a caller to supply, and nothing a caller could
supply that would change what it sees.

Both tools return a structured error instead of raising. An agent that gets
`{"error": {...}}` can tell the user the catalog is unavailable and carry on;
an exception would fail the whole turn over a search that was only ever meant to
add context.
"""

from __future__ import annotations

import logging
from typing import Any

from fastmcp import FastMCP

from kb.container import Container
from kb.domain.entry import EntryType

logger = logging.getLogger("kb.mcp")

INSTRUCTIONS = """\
The catalog of what knowledge exists in this organization and where it lives.

It holds pointers and summaries, never document text. Search it to find out
*which* document answers a question, then read that document with the tool named
in the result's `fetch` field -- the summary is a pointer, not a source.

Search in the user's language AND with the same key terms in the other language
in one call: documents are catalogued in both, and the rankings are combined.
"""


def _hit_payload(hit: Any) -> dict[str, Any]:
    return {
        "id": hit.entry.id,
        "type": hit.entry.type.value,
        "title": dict(hit.entry.title),
        "summary": dict(hit.entry.summary),
        "owner_team": hit.entry.owner_team,
        "tags": list(hit.entry.tags),
        "url": hit.entry.location.url,
        # The ready-made next call. Without it a smaller model has to work out
        # which tool reads a wiki page versus an issue, which is the most
        # likely way a two-step retrieval goes wrong.
        "fetch": hit.fetch_hint,
        "stale": hit.stale,
    }


#: fastmcp takes only a docstring's summary line as the tool description, so
#: anything a model must know to call the tool correctly is passed explicitly
#: here. (Per-argument help still comes from the docstring's Args section,
#: which does reach the schema.)
SEARCH_DESCRIPTION = """\
Find which documents answer a question, and where they live.

Returns hits with a `fetch` field naming the tool and arguments that read the
real document -- always read it before answering; a catalog summary is a
pointer, not a source. Search in the user's language AND with the key terms in
the other language in the same call: documents are catalogued in both, and the
rankings are combined. A hit marked `stale` may have an out-of-date summary,
but the document itself is always current.
"""

GET_DESCRIPTION = """\
Everything the catalog knows about one entry, by its id.

Use after kb_search when you need an entry's full keywords, ownership or
location. This does not return the document's text either: read it with the
tool named in `fetch`.
"""


def build_app(container: Container) -> FastMCP:
    app = FastMCP(name="kb", instructions=INSTRUCTIONS)

    @app.tool(description=SEARCH_DESCRIPTION)
    def kb_search(
        queries: list[str],
        types: list[str] | None = None,
        tags: list[str] | None = None,
        limit: int | None = None,
    ) -> dict[str, Any]:
        """Find which documents answer a question.

        Args:
            queries: One or more search phrases. Pass the user's question in
                their own language plus its key terms in the other language --
                both are searched and the rankings combined.
            types: Optional filter: doc, spec, product, system, team, person,
                term.
            tags: Optional filter on an entry's tags.
            limit: Maximum number of results.

        Returns hits with a `fetch` field naming the tool and arguments that
        read the real document. Always read that document before answering from
        a summary. A hit marked `stale` may have an out-of-date summary; the
        document itself is always current.
        """
        try:
            parsed_types = [EntryType(t) for t in types] if types else None
        except ValueError as exc:
            return {"error": {"type": "bad_argument", "message": str(exc)}}
        try:
            result = container.search.execute(queries, types=parsed_types, tags=tags, limit=limit)
        except Exception as exc:  # database down, connection dropped, ...
            logger.exception("kb_search failed")
            return {"error": {"type": "catalog_unavailable", "message": str(exc).strip()}}
        return {
            "hits": [_hit_payload(hit) for hit in result.hits],
            "queries": list(result.queries),
            "notes": list(result.notes),
        }

    @app.tool(description=GET_DESCRIPTION)
    def kb_get(id: str) -> dict[str, Any]:
        """Everything the catalog knows about one entry, by its id.

        Use after `kb_search` when you need an entry's full keywords, ownership
        or location. This still does not return the document's text: read it
        with the tool named in `fetch`.
        """
        try:
            view = container.get_entry.execute(id)
        except Exception as exc:
            logger.exception("kb_get failed")
            return {"error": {"type": "catalog_unavailable", "message": str(exc).strip()}}
        if view is None:
            return {"error": {"type": "not_found", "message": f"no entry with id {id!r}"}}
        return {
            "entry": view.entry.model_dump(mode="json"),
            "fetch": view.entry.fetch_hint,
            "stale": view.stale,
            "hidden": view.hidden,
        }

    return app


def main() -> None:  # pragma: no cover -- the container's entrypoint
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    from kb.config import Settings
    from kb.container import build

    settings = Settings.from_env()
    container = build(settings)
    app = build_app(container)
    logger.info("kb mcp server listening on %s:%s/mcp", settings.mcp_host, settings.mcp_port)
    app.run(transport="http", host=settings.mcp_host, port=settings.mcp_port, path="/mcp")


if __name__ == "__main__":  # pragma: no cover
    main()
