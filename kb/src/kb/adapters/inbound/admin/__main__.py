"""`python -m kb.adapters.inbound.admin` -- the console's container entrypoint."""

from __future__ import annotations

import logging
from pathlib import Path


def main() -> None:  # pragma: no cover -- wiring and uvicorn
    import uvicorn

    from kb.adapters.inbound.admin.app import AdminServices, build_app
    from kb.config import AdminSettings, MissingSetting, Settings
    from kb.container import build_admin

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    try:
        admin = AdminSettings.from_env()
    except MissingSetting as exc:
        # Same stance as the webhook receiver: refuse, loudly, rather than serve
        # an unauthenticated write surface.
        raise SystemExit(f"{exc.variable} is not set -- refusing to start the console without a token")
    except ValueError as exc:
        raise SystemExit(f"KB_ADMIN_TOKEN: {exc}")

    settings = Settings.from_env()
    container = build_admin(settings)
    services = AdminServices(
        store=container.store,
        runs=container.runs,
        queue=container.queue,
        heartbeats=container.heartbeats,
        clock=container.clock,
        sources_file=Path(settings.sources_file),
        languages=settings.summary_languages,
        summarizer=container.summarizer,
        sources_editor=container.sources_editor,
        forms=container.forms,
        manage_forms=container.manage_forms,
        draft_summary=container.draft_summary,
        get_entry=container.get_entry,
        extras=container.extras,
    )
    try:
        uvicorn.run(
            build_app(settings=admin, services=services),
            host=admin.host,
            port=admin.port,
            log_level="info",
        )
    finally:
        container.close()


if __name__ == "__main__":  # pragma: no cover
    main()
