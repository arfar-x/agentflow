"""The operator's front door: `python -m kb <command>`.

Follows the same contract every CLI in this stack follows, for the same reason:
**exactly one JSON document on stdout, and exit 0 for any handled outcome,
including a reported error.** A human reads it with `jq`, a script parses it,
and neither has to distinguish "the tool failed" from "the tool reported a
failure" by exit code archaeology.

Unhandled means unhandled: a genuine bug still raises and exits non-zero. What
is handled is everything an operator can act on -- no database, a bad argument,
an id that doesn't exist.
"""

from __future__ import annotations

import argparse
import json
import sys
from typing import Any, Sequence

from kb.config import MissingSetting, Settings
from kb.domain.entry import EntryType
from kb.domain.merge import Override


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m kb",
        description="The knowledge catalog: what exists in the organization, and where it lives.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    search = sub.add_parser("search", help="Search the catalog and print ranked hits.")
    search.add_argument(
        "--query",
        action="append",
        required=True,
        metavar="TEXT",
        help="Repeatable. Pass the question in the user's language and the key terms in "
        "the other one; the rankings are fused.",
    )
    search.add_argument("--type", action="append", choices=[t.value for t in EntryType],
                        help="Repeatable. Restrict to these entry types.")
    search.add_argument("--tag", action="append", metavar="TAG", help="Repeatable.")
    search.add_argument("--limit", type=int, help="Maximum hits (default: KB_SEARCH_DEFAULT_LIMIT).")

    get = sub.add_parser("get", help="One entry in full, by id.")
    get.add_argument("--id", required=True)

    override = sub.add_parser("override", help="Record or remove a human correction.")
    override_sub = override.add_subparsers(dest="override_command", required=True)
    override_set = override_sub.add_parser("set", help="Correct or exclude one entry.")
    override_set.add_argument("--id", required=True, help="The entry to correct.")
    override_set.add_argument("--title", metavar="LANG=TEXT", action="append",
                              help="Repeatable, e.g. --title en='Refund policy'.")
    override_set.add_argument("--summary", metavar="LANG=TEXT", action="append", help="Repeatable.")
    override_set.add_argument("--keyword", metavar="WORD", action="append",
                              help="Repeatable. Replaces the generated keywords entirely.")
    override_set.add_argument("--tag", metavar="TAG", action="append",
                              help="Repeatable. Replaces the generated tags entirely.")
    override_set.add_argument("--owner-team")
    override_set.add_argument("--exclude", action="store_true",
                              help="Keep this entry out of search without touching the source.")
    override_set.add_argument("--note", help="Why -- for whoever reads this in six months.")
    override_clear = override_sub.add_parser("clear", help="Remove an override, restoring generated text.")
    override_clear.add_argument("--id", required=True)

    gaps = sub.add_parser("gaps", help="Searches that found nothing, most frequent first.")
    gaps.add_argument("--limit", type=int, default=20)

    sub.add_parser("migrate", help="Apply any pending schema migration. Idempotent.")
    sub.add_parser("status", help="What the catalog contains, and whether it looks healthy.")
    return parser


def _localized(pairs: Sequence[str] | None, flag: str) -> dict[str, str] | None:
    """`--title en=Refund` -> {"en": "Refund"}."""
    if not pairs:
        return None
    parsed: dict[str, str] = {}
    for pair in pairs:
        language, separator, text = pair.partition("=")
        if not separator or not language.strip() or not text.strip():
            raise _Reportable("bad_argument", f"{flag} expects LANG=TEXT, got {pair!r}")
        parsed[language.strip()] = text
    return parsed


class _Reportable(Exception):
    """Something the operator can fix, to be printed as a structured error."""

    def __init__(self, kind: str, message: str, **extra: Any) -> None:
        self.payload = {"error": {"type": kind, "message": message, **extra}}
        super().__init__(message)


def _run(args: argparse.Namespace) -> dict[str, Any]:
    from kb import container as container_module

    try:
        settings = Settings.from_env()
    except MissingSetting as exc:
        raise _Reportable(
            "missing_setting", f"{exc.variable} is not set", variable=exc.variable
        ) from exc
    except ValueError as exc:
        raise _Reportable("invalid_setting", str(exc)) from exc

    try:
        container = container_module.build(settings)
    except Exception as exc:  # psycopg.OperationalError and friends
        # The operator's most common failure by far: the database isn't up, or
        # the DSN is wrong. Say which, rather than printing a driver traceback.
        raise _Reportable("database_unavailable", str(exc).strip()) from exc

    try:
        return _dispatch(args, container)
    finally:
        container.close()


def _dispatch(args: argparse.Namespace, container: Any) -> dict[str, Any]:
    if args.command == "search":
        result = container.search.execute(
            args.query,
            types=[EntryType(t) for t in args.type] if args.type else None,
            tags=args.tag,
            limit=args.limit,
        )
        return {
            "queries": list(result.queries),
            "notes": list(result.notes),
            "hits": [
                {
                    "id": hit.entry.id,
                    "type": hit.entry.type.value,
                    "title": dict(hit.entry.title),
                    "summary": dict(hit.entry.summary),
                    "owner_team": hit.entry.owner_team,
                    "tags": list(hit.entry.tags),
                    "location": hit.entry.location.model_dump(mode="json"),
                    "fetch": hit.fetch_hint,
                    "stale": hit.stale,
                    "score": round(hit.score, 6),
                }
                for hit in result.hits
            ],
        }

    if args.command == "get":
        view = container.get_entry.execute(args.id)
        if view is None:
            raise _Reportable("not_found", f"no entry with id {args.id!r}", id=args.id)
        return {
            "entry": view.entry.model_dump(mode="json"),
            "fetch": view.entry.fetch_hint,
            "stale": view.stale,
            "hidden": view.hidden,
        }

    if args.command == "override":
        if args.override_command == "clear":
            container.store.clear_override(args.id)
            return {"cleared": args.id}
        override = Override(
            entry_id=args.id,
            title=_localized(args.title, "--title"),
            summary=_localized(args.summary, "--summary"),
            keywords=tuple(args.keyword) if args.keyword is not None else None,
            tags=tuple(args.tag) if args.tag is not None else None,
            owner_team=args.owner_team,
            exclude=args.exclude,
            note=args.note,
        )
        container.store.set_override(override)
        return {"override": override.model_dump(mode="json", exclude_none=True)}

    if args.command == "gaps":
        return {"gaps": container.store.top_misses(limit=args.limit)}

    if args.command == "migrate":
        applied = container.store.migrate()
        return {"applied": applied, "pending": []}

    if args.command == "status":
        return container.store.status()

    raise _Reportable("unknown_command", f"no such command: {args.command!r}")


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        payload = _run(args)
    except _Reportable as exc:
        payload = exc.payload
    print(json.dumps(payload, ensure_ascii=False, default=str))
    # Always 0 for a handled outcome, error payloads included -- see the module
    # docstring. A real bug still escapes this function and exits non-zero.
    return 0


if __name__ == "__main__":  # pragma: no cover -- exercised via `python -m kb`
    sys.exit(main())
