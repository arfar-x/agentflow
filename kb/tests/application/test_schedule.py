"""What the scheduler decides, without a scheduler.

Every rule here would otherwise be testable only by waiting, which is how
cadence bugs survive for weeks.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from kb.application.use_cases.schedule import Job, JobKind, ScheduleState, due
from kb.sources_config import SourcesConfig

NOON = datetime(2026, 9, 24, 12, 0, tzinfo=timezone.utc)


def config(**overrides) -> SourcesConfig:
    document = {
        "reviewed": True,
        "defaults": {
            "mechanisms": {
                "incremental": {"enabled": True, "every": "15m"},
                "full_scrape": {"enabled": True, "at": "03:00"},
                "webhook": {"enabled": False},
                "lazy_refresh": {"enabled": True, "stale_after": "24h"},
            }
        },
        "sources": [
            {"id": "confluence-eng", "kind": "confluence", "enabled": True, "spaces": ["ENG"]}
        ],
    }
    document.update(overrides)
    return SourcesConfig.model_validate(document)


def kinds(jobs: list[Job]) -> set[JobKind]:
    return {job.kind for job in jobs}


def test_deciding_what_is_due_is_pure():
    # Covers: FR-SCH-02
    # It takes a config, a state and a time, and returns jobs: no database, no
    # clock of its own, nothing to sleep through. That is what lets the
    # nightly-at-03:00 rule be tested in milliseconds.
    import inspect

    from kb.application.use_cases import schedule

    assert set(inspect.signature(schedule.due).parameters) == {
        "config", "state", "now", "refresh_drain_seconds",
    }
    # Same inputs, same answer, however many times it is asked.
    first = due(config(), ScheduleState(), NOON)
    assert all(due(config(), ScheduleState(), NOON) == first for _ in range(3))
    # And nothing it imports can touch the outside world.
    forbidden = {"psycopg", "requests", "httpx", "socket", "time"}
    assert not forbidden & set(vars(schedule))


def test_a_source_that_has_never_run_starts_immediately():
    # Covers: FR-SCH-01
    # Waiting a full interval would mean a source enabled at 09:00 is
    # catalogued at 09:15 at the earliest, and a nightly one tomorrow.
    jobs = due(config(), ScheduleState(), NOON)
    assert JobKind.INCREMENTAL in kinds(jobs)
    assert [job.source_id for job in jobs if job.kind is JobKind.INCREMENTAL] == ["confluence-eng"]


def test_the_incremental_cadence_is_what_the_config_says():
    # Covers: FR-SCH-01, FR-CFG-07
    state = ScheduleState(last_run={"incremental:confluence-eng": NOON})

    assert JobKind.INCREMENTAL not in kinds(due(config(), state, NOON + timedelta(minutes=14)))
    assert JobKind.INCREMENTAL in kinds(due(config(), state, NOON + timedelta(minutes=15)))


def test_a_source_can_override_the_default_cadence():
    # Covers: FR-CFG-07
    faster = config(sources=[{
        "id": "confluence-eng", "kind": "confluence", "enabled": True, "spaces": ["ENG"],
        "mechanisms": {"incremental": {"every": "5m"}},
    }])
    state = ScheduleState(last_run={"incremental:confluence-eng": NOON})
    assert JobKind.INCREMENTAL in kinds(due(faster, state, NOON + timedelta(minutes=5)))


def test_a_disabled_mechanism_produces_no_job_at_all():
    # Covers: FR-SCH-01, FR-CFG-07
    # This is the switch that keeps a nightly full scrape from running on the
    # 15-minute tick.
    off = config(defaults={"mechanisms": {
        "incremental": {"enabled": False},
        "full_scrape": {"enabled": False},
        "lazy_refresh": {"enabled": False},
    }})
    assert due(off, ScheduleState(), NOON) == []


def test_a_disabled_source_is_never_scheduled():
    # Covers: FR-SCH-01
    disabled = config(sources=[
        {"id": "confluence-eng", "kind": "confluence", "enabled": False, "spaces": ["ENG"]}
    ])
    assert due(disabled, ScheduleState(), NOON) == []


def test_nothing_is_scheduled_while_the_config_is_unreviewed():
    # Covers: FR-CFG-05
    # Otherwise the review gate would only stop a human running sync by hand.
    assert due(config(reviewed=False), ScheduleState(), NOON) == []


def test_the_nightly_scrape_runs_after_its_hour_and_only_once_a_day():
    # Covers: FR-SCH-01
    yesterday_run = {"full_scrape:confluence-eng": NOON - timedelta(days=1)}
    state = ScheduleState(last_run=yesterday_run)
    three_am = NOON.replace(hour=3, minute=0)

    assert JobKind.FULL_SCRAPE not in kinds(due(config(), state, three_am - timedelta(minutes=1)))
    assert JobKind.FULL_SCRAPE in kinds(due(config(), state, three_am))

    after_running = ScheduleState(last_run={"full_scrape:confluence-eng": three_am})
    assert JobKind.FULL_SCRAPE not in kinds(due(config(), after_running, three_am + timedelta(hours=8)))


def test_starting_mid_afternoon_does_not_fire_a_missed_nightly_job():
    # A scheduler restarted at 15:00 must not immediately walk every source:
    # that is the one job expensive enough to be worth not running twice.
    assert JobKind.FULL_SCRAPE not in kinds(due(config(), ScheduleState(), NOON.replace(hour=15)))


def test_a_run_still_going_is_not_started_again():
    # Covers: FR-SCH-05
    # A full scrape of a 4,000-page space can outlast its own cadence.
    state = ScheduleState(running=frozenset({"incremental:confluence-eng"}))
    assert JobKind.INCREMENTAL not in kinds(due(config(), state, NOON))


def test_the_refresh_drain_is_scheduled_once_for_the_whole_catalog():
    # Covers: FR-REC-10
    # It works through what readers asked about, so it is not per source.
    jobs = [job for job in due(config(), ScheduleState(), NOON) if job.kind is JobKind.LAZY_REFRESH]
    assert len(jobs) == 1 and jobs[0].source_id is None


def test_the_refresh_drain_respects_its_switch_and_its_interval():
    # Covers: FR-CFG-07
    no_lazy = config(defaults={"mechanisms": {"lazy_refresh": {"enabled": False}}})
    assert JobKind.LAZY_REFRESH not in kinds(due(no_lazy, ScheduleState(), NOON))

    state = ScheduleState(last_run={"lazy_refresh:*": NOON})
    assert JobKind.LAZY_REFRESH not in kinds(due(config(), state, NOON + timedelta(seconds=30)))
    assert JobKind.LAZY_REFRESH in kinds(due(config(), state, NOON + timedelta(seconds=60)))


def test_every_source_is_scheduled_independently():
    # Covers: FR-SCH-03
    # One source falling behind, or failing, must not hold up the others.
    two = config(sources=[
        {"id": "confluence-eng", "kind": "confluence", "enabled": True, "spaces": ["ENG"]},
        {"id": "jira-pay", "kind": "jira", "enabled": True, "jql": "project = PAY"},
    ])
    state = ScheduleState(last_run={"incremental:confluence-eng": NOON})

    jobs = due(two, state, NOON + timedelta(minutes=1))

    assert [j.source_id for j in jobs if j.kind is JobKind.INCREMENTAL] == ["jira-pay"]


def test_a_job_says_why_it_is_due():
    # The log line that explains a run nobody asked for.
    jobs = due(config(), ScheduleState(), NOON)
    assert any("15m" in job.reason for job in jobs)


@pytest.mark.parametrize("bad", ["15mn", "soon", "0x10"])
def test_an_unparseable_cadence_is_an_error_not_silence(bad):
    # Treating "15mn" as "never" would be a scheduler that quietly does nothing.
    from kb.sources_config import ConfigError

    broken = config(defaults={"mechanisms": {"incremental": {"enabled": True, "every": bad}}})
    with pytest.raises(ConfigError):
        due(broken, ScheduleState(), NOON)
