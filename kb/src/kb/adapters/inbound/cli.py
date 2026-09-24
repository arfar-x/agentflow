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

    sync = sub.add_parser("sync", help="Bring the catalog in line with one configured source.")
    sync.add_argument("--source", required=True, metavar="ID", help="A source id from kb-sources.yaml.")
    sync.add_argument("--mode", choices=["full", "incremental"], default="full",
                      help="full lists everything (and detects deletions); incremental asks only "
                           "for what changed since the stored checkpoint.")
    sync.add_argument("--dry-run", action="store_true",
                      help="Read the source and report what would change. Writes nothing, "
                           "summarizes nothing, and does not advance the checkpoint.")

    sub.add_parser("sources", help="The configured sources, and whether each is enabled.")

    discover = sub.add_parser(
        "discover", help="Propose sources from what the connected systems contain."
    )
    discover.add_argument("--write", action="store_true",
                          help="Append the candidates to kb-sources.yaml (disabled, for review). "
                               "Without it, they are only printed.")

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

    if args.command in {"sync", "sources", "discover"}:
        return _sources_command(args, container)

    if args.command == "gaps":
        return {"gaps": container.store.top_misses(limit=args.limit)}

    if args.command == "migrate":
        applied = container.store.migrate()
        return {"applied": applied, "pending": []}

    if args.command == "status":
        return container.store.status()

    raise _Reportable("unknown_command", f"no such command: {args.command!r}")


def _sources_command(args: argparse.Namespace, container: Any) -> dict[str, Any]:
    import os
    from pathlib import Path

    from kb.adapters.outbound import discovery
    from kb.adapters.outbound.source_factory import MissingCredential, build_source
    from kb.sources_config import ConfigError, NotApproved, load

    if args.command == "discover":
        candidates = discovery.discover_all(os.environ)
        payload: dict[str, Any] = {
            "candidates": [
                {"id": c.id, "kind": c.kind, "found": c.size} for c in candidates
            ]
        }
        if args.write:
            path = Path(container.settings.sources_file)
            added, skipped = discovery.merge_into(path, candidates)
            payload |= {"file": str(path), "added": added, "already_configured": skipped}
            # Said plainly, because the next question is always "why is nothing
            # being synced?".
            payload["next"] = f"edit {path}: set `enabled: true` on the sources you want"
        return payload

    from pathlib import Path

    try:
        config = load(Path(container.settings.sources_file))
    except ConfigError as exc:
        raise _Reportable("bad_source_config", str(exc)) from exc

    if args.command == "sources":
        return {
            "file": container.settings.sources_file,
            "approved": config.approved,
            # Where the value came from, so an environment override is never
            # invisible to somebody reading their file and wondering.
            "approved_from": config.approved_from,
            "sources": [
                {
                    "id": source.id,
                    "kind": source.kind,
                    "enabled": source.enabled,
                    "mechanisms": config.mechanisms_for(source).model_dump(exclude_none=True),
                }
                for source in config.sources
            ],
        }

    # sync
    try:
        config.require_approved()
    except NotApproved as exc:
        raise _Reportable("not_approved", str(exc)) from exc
    try:
        source_config = config.source(args.source)
    except ConfigError as exc:
        raise _Reportable("unknown_source", str(exc)) from exc
    if not source_config.enabled:
        raise _Reportable(
            "source_disabled",
            f"source {source_config.id!r} is disabled in {container.settings.sources_file}",
        )
    try:
        source = build_source(source_config)
    except MissingCredential as exc:
        raise _Reportable("missing_credential", str(exc), variables=list(exc.variables)) from exc
    except NotImplementedError as exc:
        raise _Reportable("unsupported_source", str(exc)) from exc

    from kb.application.use_cases.sync_source import Mode

    mode = Mode(args.mode)
    checkpoint = container.store.get_checkpoint(source.source_id)
    try:
        report = container.sync.execute(
            source, mode=mode, dry_run=args.dry_run, checkpoint=checkpoint
        )
    except Exception as exc:  # the source is unreachable, auth failed, ...
        raise _Reportable("source_unavailable", str(exc).strip()) from exc

    if report.checkpoint and not args.dry_run:
        # Only after a successful run: a checkpoint advanced past a failure
        # would skip whatever that run never saw.
        container.store.set_checkpoint(source.source_id, report.checkpoint)
    return report.model_dump(mode="json")


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
