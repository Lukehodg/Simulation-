from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

from health.models import Records
from health.store import Store
from health.sync import OVERLAP, replay, sync_source


def _land_everything(land) -> None:
    land("whoop", "sleep", "whoop_sleep.json")
    land("whoop", "recovery", "whoop_recovery.json")
    land("hevy", "workouts", "hevy_workouts.json")
    land("apple_health", "export", "apple_export.json")


def test_replay_rebuilds_every_table_from_raw(config, land):
    _land_everything(land)

    with Store(":memory:") as store:
        store.init_schema()
        reports = replay(store, config)
        first = store.counts()

    assert {r.source for r in reports} == {"whoop", "hevy", "apple_health"}
    assert all(r.error is None for r in reports)
    assert first["observations"] > 0
    assert first["sleeps"] == 3          # WHOOP night + nap, Apple night
    assert first["workouts"] == 1
    assert first["strength_sets"] == 4
    assert first["nutrition_days"] == 1
    assert first["cycle_events"] == 3    # two flow days, one period_start

    # Throwing the database away and replaying must land in the same place:
    # that is the whole reason raw/ is immutable.
    with Store(":memory:") as store:
        store.init_schema()
        replay(store, config)
        assert store.counts() == first


def test_replay_is_idempotent_within_one_database(config, land):
    _land_everything(land)
    with Store(":memory:") as store:
        store.init_schema()
        replay(store, config)
        once = store.counts()
        replay(store, config)
        assert store.counts() == once


def test_replay_survives_a_corrupt_payload(config, land):
    land("whoop", "recovery", "whoop_recovery.json")
    broken = config.raw_dir / "whoop" / "recovery" / "2020-01"
    broken.mkdir(parents=True, exist_ok=True)
    (broken / "20200101T000000000.json").write_text("{not json")

    with Store(":memory:") as store:
        store.init_schema()
        reports = replay(store, config)
        # The good payload still loads; the bad one is reported, not fatal.
        assert store.counts()["observations"] > 0
    assert "JSONDecodeError" in (reports[0].error or "")


def test_incremental_sync_refetches_an_overlap(config, monkeypatch):
    """WHOOP re-scores recoveries hours later, so the cursor must look back."""
    seen: dict = {}

    class FakeSource:
        pollable = True

        def __init__(self, cfg):
            pass

        def fetch(self, since=None, until=None) -> list[Path]:
            seen["since"] = since
            return []

        def parse(self, path):
            return Records()

    monkeypatch.setattr("health.sync.build_source", lambda name, cfg: FakeSource(cfg))

    cursor = datetime(2026, 9, 5, 12, 0, tzinfo=timezone.utc)
    with Store(":memory:") as store:
        store.init_schema()
        store.set_cursor("whoop", cursor.isoformat())
        sync_source(store, config, "whoop")

    assert seen["since"] == cursor - OVERLAP["whoop"]


def test_a_failing_source_records_the_error_and_keeps_the_cursor(config, monkeypatch):
    class BrokenSource:
        pollable = True

        def __init__(self, cfg):
            pass

        def fetch(self, since=None, until=None):
            raise RuntimeError("401 Unauthorized")

        def parse(self, path):
            return Records()

    monkeypatch.setattr("health.sync.build_source", lambda name, cfg: BrokenSource(cfg))

    with Store(":memory:") as store:
        store.init_schema()
        store.set_cursor("whoop", "2026-09-01T00:00:00Z")
        last_ok_before = store.query("SELECT last_ok FROM sync_state")[0][0]

        report = sync_source(store, config, "whoop")

        assert "401 Unauthorized" in report.error
        # The cursor must not advance past data we never actually fetched...
        assert store.get_cursor("whoop") == "2026-09-01T00:00:00Z"
        # ...and "last successful sync" must still mean the last *successful*
        # one, so `health status` shows the data going stale.
        assert store.query("SELECT last_ok FROM sync_state") == [(last_ok_before,)]
        assert "401 Unauthorized" in store.query("SELECT note FROM sync_state")[0][0]
