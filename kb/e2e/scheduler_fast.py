"""The real scheduler with a one-second tick, so a scenario need not wait ten."""

import logging
from pathlib import Path

from kb.adapters.inbound.scheduler import Scheduler
from kb.config import Settings
from kb.container import build, build_summarizer

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
settings = Settings.from_env()
container = build(settings)
check = build_summarizer(settings).check if settings.summarizer_configured else None
Scheduler(
    container, sources_file=Path(settings.sources_file), check_summarizer=check, tick_seconds=1.0
).run_forever()
