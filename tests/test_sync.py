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
        # A corrupt archive cannot replace the database with a partial rebuild.
        assert store.counts()["observations"] == 0
    assert "JSONDecodeError" in (reports[0].error or "")


def test_replay_removes_obsolete_rows_and_preserves_other_sources(config, land):
    from health.models import Observation
    land("whoop", "recovery", "whoop_recovery.json")
    with Store(":memory:") as store:
        store.init_schema()
        now = datetime.now(timezone.utc)
        store.load(Records(observations=[Observation(
            ts=now, local_date=now.date(), source=source, source_id="obsolete",
            metric="steps", value=123) for source in ("whoop", "apple_health")]))
        replay(store, config, "whoop")
        assert store.query("SELECT source FROM observations WHERE source_id = 'obsolete'") == [("apple_health",)]


def test_replay_failure_preserves_existing_data(config, land):
    land("whoop", "recovery", "whoop_recovery.json")
    with Store(":memory:") as store:
        store.init_schema()
        replay(store, config)
        before = store.query("SELECT * FROM observations ORDER BY source_id")
        broken = config.raw_dir / "whoop" / "recovery" / "2026-10" / "20261001T000000000.json"
        broken.parent.mkdir(parents=True)
        broken.write_text("{broken")
        reports = replay(store, config)
        assert reports[0].error
        assert store.query("SELECT * FROM observations ORDER BY source_id") == before


def test_replay_applies_deletions_after_older_workout_payload(config, land):
    from health import raw
    path = land("hevy", "workouts", "hevy_workouts.json")
    payload = raw.RawFile(path, "hevy", "workouts", datetime.now(timezone.utc)).load()
    workout_id = payload["workouts"][0]["id"]
    raw.write(config.raw_dir, "hevy", "events",
              {"events": [{"type": "deleted", "id": workout_id}]},
              fetched_at=datetime.now(timezone.utc) + timedelta(days=1))
    with Store(":memory:") as store:
        store.init_schema()
        assert not any(r.error for r in replay(store, config))
        assert store.query("SELECT COUNT(*) FROM workouts") == [(0,)]


def test_parse_failure_is_reported_without_advancing_cursor(config, monkeypatch):
    class BrokenParser:
        pollable = True
        def fetch(self, since=None):
            return [Path("broken.json")]
        def parse(self, path):
            raise ValueError("invalid payload")
    monkeypatch.setattr("health.sync.build_source", lambda *args: BrokenParser())
    with Store(":memory:") as store:
        store.init_schema()
        cursor = "2026-09-01T00:00:00Z"
        store.set_cursor("whoop", cursor)
        result = sync_source(store, config, "whoop")
        assert "invalid payload" in result.error
        assert store.get_cursor("whoop") == cursor


def test_raw_order_uses_arrival_stamp_and_numeric_collision_suffix(config):
    from health import raw
    moment = datetime(2026, 9, 1, tzinfo=timezone.utc)
    for i in range(12):
        raw.write(config.raw_dir, "hevy", "events", {"sequence": i}, fetched_at=moment)
    files = list(raw.iter_raw(config.raw_dir))
    assert [f.load()["sequence"] for f in files] == list(range(12))
    assert all(f.fetched_at == moment for f in files)


def test_empty_replay_preserves_data_and_operational_state(config, land):
    land("whoop", "recovery", "whoop_recovery.json")
    with Store(":memory:") as store:
        store.init_schema()
        store.set_cursor("whoop", "2026-09-01T00:00:00Z")
        store.mark_alerted("watch")
        replay(store, config)
        before = store.counts()
        assert replay(store, config, "hevy") == []
        assert store.counts() == before
        assert store.get_cursor("whoop") == "2026-09-01T00:00:00Z"
        assert store.alerted("watch")


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


def test_a_first_sync_backfills_the_way_each_source_actually_backfills(config, monkeypatch):
    """Hevy pages its whole workout collection; its events feed reports what
    changed since a moment and is not a backfill. Handing it a default window
    on a first sync sent it down the wrong path entirely."""
    seen: dict = {}

    class Recorder:
        pollable = True

        def __init__(self, cfg, windowed):
            self.windowed_backfill = windowed

        def fetch(self, since=None, until=None):
            seen[self.windowed_backfill] = since
            return []

        def parse(self, path):
            return Records()

    monkeypatch.setattr("health.sync.build_source",
                        lambda name, cfg: Recorder(cfg, name == "whoop"))

    with Store(":memory:") as store:
        store.init_schema()
        sync_source(store, config, "whoop")     # windowed: wants a date range
        sync_source(store, config, "hevy")      # paged: must not get one

    assert seen[True] is not None
    assert seen[False] is None
