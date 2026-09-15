from __future__ import annotations

from datetime import date, datetime, timezone
import pytest

from health.models import Observation, Records, StrengthSet, Workout


def _observation(**overrides) -> Observation:
    base = dict(
        ts=datetime(2026, 9, 1, 6, 30, tzinfo=timezone.utc),
        local_date=date(2026, 9, 1), metric="hrv_rmssd", value=68.4, unit="ms",
        source="whoop", source_id="rec-1",
    )
    base.update(overrides)
    return Observation(**base)


def test_upsert_is_idempotent_and_updates_in_place(store):
    store.load(Records(observations=[_observation()]))
    store.load(Records(observations=[_observation(value=70.1)]))

    rows = store.query("SELECT value FROM observations WHERE source_id = 'rec-1'")
    assert rows == [(70.1,)]


def test_same_metric_from_two_sources_coexists(store):
    store.load(Records(observations=[
        _observation(source="whoop", source_id="w1", value=68.0),
        _observation(source="garmin_fit", source_id="g1", value=44.0),
    ]))
    rows = store.query("SELECT COUNT(*) FROM observations")
    assert rows == [(2,)]

    # ...but the canonical daily series picks WHOOP, per SOURCE_PRIORITY.
    daily = store.query("SELECT source, value FROM daily_metrics WHERE metric = 'hrv_rmssd'")
    assert daily == [("whoop", 68.0)]


def test_daily_view_pivots_metrics(store):
    store.load(Records(observations=[
        _observation(metric="hrv_rmssd", value=68.0, source_id="a"),
        _observation(metric="resting_hr", value=52.0, source_id="b"),
        _observation(metric="recovery_score", value=71.0, source_id="c"),
    ]))
    assert store.query("SELECT hrv, resting_hr, recovery FROM daily") == [(68.0, 52.0, 71.0)]


def test_deleted_workouts_are_removed_with_their_sets(store):
    start = datetime(2026, 9, 1, 17, tzinfo=timezone.utc)
    store.load(Records(
        workouts=[Workout(source="hevy", source_id="hw-1", start_ts=start,
                          local_date=date(2026, 9, 1))],
        strength_sets=[StrengthSet(source="hevy", workout_id="hw-1", exercise_idx=0,
                                   set_idx=0, ts=start, local_date=date(2026, 9, 1),
                                   exercise="Squat", weight_kg=60, reps=5)],
    ))
    store.load(Records(deleted_workouts=["hw-1"]))

    assert store.query("SELECT COUNT(*) FROM workouts") == [(0,)]
    assert store.query("SELECT COUNT(*) FROM strength_sets") == [(0,)]


def test_working_sets_view_excludes_warmups(store):
    start = datetime(2026, 9, 1, 17, tzinfo=timezone.utc)
    store.load(Records(strength_sets=[
        StrengthSet(source="hevy", workout_id="w", exercise_idx=0, set_idx=0, ts=start,
                    local_date=date(2026, 9, 1), exercise="RDL", set_type="warmup",
                    weight_kg=40, reps=8),
        StrengthSet(source="hevy", workout_id="w", exercise_idx=0, set_idx=1, ts=start,
                    local_date=date(2026, 9, 1), exercise="RDL", set_type="normal",
                    weight_kg=90, reps=5),
    ]))
    rows = store.query("SELECT exercise, round(e1rm, 1) FROM working_sets")
    assert rows == [("RDL", 105.0)]  # 90 * (1 + 5/30)


def test_cursor_round_trip(store):
    assert store.get_cursor("whoop") is None
    store.set_cursor("whoop", "2026-09-01T00:00:00Z")
    assert store.get_cursor("whoop") == "2026-09-01T00:00:00Z"
    store.set_cursor("whoop", "2026-09-02T00:00:00Z", ok=False, note="429 from API")
    assert store.get_cursor("whoop") == "2026-09-02T00:00:00Z"
    assert store.query("SELECT note FROM sync_state") == [("429 from API",)]


def test_empty_workout_replaces_previous_sets(store):
    start = datetime(2026, 9, 1, 17, tzinfo=timezone.utc)
    workout = Workout(source="hevy", source_id="w", start_ts=start, local_date=start.date())
    store.load(Records(workouts=[workout], strength_sets=[StrengthSet(
        source="hevy", workout_id="w", exercise_idx=0, set_idx=0, ts=start,
        local_date=start.date(), exercise="Squat", weight_kg=60, reps=5)]))
    store.load(Records(workouts=[workout]))
    assert store.query("SELECT COUNT(*) FROM strength_sets") == [(0,)]
    assert store.query("SELECT COUNT(*) FROM workouts") == [(1,)]


def test_load_rolls_back_deletions_and_partial_inserts(store, monkeypatch):
    store.load(Records(observations=[_observation()]))
    original = store.upsert

    def fail_after_write(table, rows):
        original(table, rows)
        raise RuntimeError("interrupted load")

    monkeypatch.setattr(store, "upsert", fail_after_write)
    with pytest.raises(RuntimeError, match="interrupted"):
        store.load(Records(observations=[_observation(value=99)]))
    assert store.query("SELECT value FROM observations") == [(68.4,)]


def test_caught_inner_failure_cannot_commit_partial_data(store):
    with pytest.raises(RuntimeError, match="inner operation"):
        with store.transaction():
            store.load(Records(observations=[_observation()]))
            try:
                with store.transaction():
                    raise ValueError("failed operation")
            except ValueError:
                pass
    assert store.query("SELECT COUNT(*) FROM observations") == [(0,)]


def test_failed_workout_replacement_restores_deleted_sets(store, monkeypatch):
    start = datetime(2026, 9, 1, 17, tzinfo=timezone.utc)
    workout = Workout(source="hevy", source_id="w", start_ts=start, local_date=start.date())
    store.load(Records(workouts=[workout], strength_sets=[StrengthSet(
        source="hevy", workout_id="w", exercise_idx=0, set_idx=0, ts=start,
        local_date=start.date(), exercise="Squat", weight_kg=60, reps=5)]))
    def fail(*args):
        raise RuntimeError("write failed")
    monkeypatch.setattr(store, "upsert", fail)
    with pytest.raises(RuntimeError, match="write failed"):
        store.load(Records(workouts=[workout]))
    assert store.query("SELECT weight_kg, reps FROM strength_sets") == [(60, 5)]
