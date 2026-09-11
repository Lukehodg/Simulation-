from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone

import pytest

from health.features import daily as daily_mod
from health.features import protocol as protocol_features
from health.features import readiness
from health.models import (CycleEvent, ExerciseTemplate, Observation,
                            ProtocolEvent, Records, StrengthSet)

START = date(2026, 5, 1)


def _obs(store, metric: str, values: list[float], start: date = START,
         source: str = "whoop") -> None:
    store.load(Records(observations=[
        Observation(ts=datetime.combine(start + timedelta(days=i), time(6),
                                        tzinfo=timezone.utc),
                    local_date=start + timedelta(days=i), metric=metric,
                    value=v, unit="x", source=source, source_id=f"{metric}-{i}")
        for i, v in enumerate(values) if v is not None]))


# -- sleep debt -----------------------------------------------------------

def test_sleep_need_is_read_off_your_own_well_recovered_nights(store):
    # 40 days: on good-recovery days you had slept ~8h; on bad ones ~6h.
    recovery = [80.0 if i % 2 else 40.0 for i in range(40)]
    sleep = [480.0 if i % 2 else 360.0 for i in range(40)]
    _obs(store, "recovery_score", recovery)
    _obs(store, "sleep_duration", sleep)

    debt = readiness.sleep_debt(store, as_of=START + timedelta(days=39), days=14)

    assert debt.need_hours == pytest.approx(8.0, abs=0.3)
    assert "top-third" in debt.basis
    # The last fortnight alternates 8h and 6h, so it is carrying real debt.
    assert debt.debt_hours > 5


def test_sleep_debt_series_is_one_figure_per_day(store):
    recovery = [80.0 if i % 2 else 40.0 for i in range(40)]
    sleep = [480.0 if i % 2 else 360.0 for i in range(40)]
    _obs(store, "recovery_score", recovery)
    _obs(store, "sleep_duration", sleep)

    series = readiness.sleep_debt_series(store, START + timedelta(days=30),
                                         START + timedelta(days=39))

    assert [d for d, _ in series] == [START + timedelta(days=i) for i in range(30, 40)]
    single = readiness.sleep_debt(store, as_of=START + timedelta(days=39))
    assert series[-1][1] == single.debt_hours


def test_sleep_need_falls_back_to_a_default_when_history_is_thin(store):
    _obs(store, "sleep_duration", [400.0] * 8)

    debt = readiness.sleep_debt(store, as_of=START + timedelta(days=7))

    assert debt.need_hours == 7.5
    assert "default" in debt.basis
    assert debt.note


# -- readiness ----------------------------------------------------------

def _clean_day(store, days: int = 40):
    _obs(store, "hrv_rmssd", [70.0 + (i % 5) - 2 for i in range(days)])
    _obs(store, "resting_hr", [52.0 + (i % 3) - 1 for i in range(days)])
    _obs(store, "recovery_score", [80.0 + (i % 4) for i in range(days)])
    _obs(store, "sleep_duration", [470.0 + (i % 10) for i in range(days)])
    _obs(store, "strain", [10.0 + (i % 3) for i in range(days)])


def test_a_clean_day_gets_a_green_light(store):
    _clean_day(store)
    day = START + timedelta(days=39)

    result = readiness.readiness(store, day)

    assert result.recommendation == readiness.PUSH
    assert not [c for c in result.caveats if "baseline" in c]


def test_a_genuine_zero_acute_load_is_not_read_as_steady(store, monkeypatch):
    # A real rest week (acute load truly 0, chronic > 0 -> ratio == 0.0) must
    # not be coerced into ratio=1.0 ("steady") by a falsy-zero fallback: with
    # every recovery signal clean, this fixture normally earns PUSH (see
    # test_a_clean_day_gets_a_green_light), which requires 0.8 <= ratio <= 1.3.
    # A `ratio or 1.0` bug would satisfy that on a true zero and still say
    # PUSH; the fix must not.
    _clean_day(store)
    day = START + timedelta(days=39)
    monkeypatch.setattr(readiness.daily, "training_load",
                        lambda *a, **k: daily_mod.TrainingLoad(acute=0.0, chronic=10.0,
                                                                days=28, method="ewma"))

    result = readiness.readiness(store, day)

    assert result.recommendation != readiness.PUSH
    assert result.recommendation == readiness.PROCEED


def test_recovery_and_load_together_force_a_pull_back(store):
    _clean_day(store, days=32)
    # Day 33: HRV collapses, resting HR jumps, and a hard block lands.
    _obs(store, "hrv_rmssd", [40.0], start=START + timedelta(days=32))
    _obs(store, "resting_hr", [64.0], start=START + timedelta(days=32))
    _obs(store, "recovery_score", [30.0], start=START + timedelta(days=32))
    _obs(store, "sleep_duration", [470.0], start=START + timedelta(days=32))
    _obs(store, "strain", [19.0, 19.0, 19.0, 19.0, 19.0, 19.0, 19.0],
         start=START + timedelta(days=26))

    result = readiness.readiness(store, START + timedelta(days=32))

    assert result.recommendation == readiness.PULL_BACK
    assert any("HRV" in r and "SD" in r for r in result.reasons)
    assert any("adverse" in r for r in result.reasons)


def test_readiness_is_a_recommendation_not_a_score(store):
    _clean_day(store)

    result = readiness.readiness(store, START + timedelta(days=39)).as_dict()

    assert "score" not in result and "percent" not in result
    assert result["recommendation"] in (
        readiness.PUSH, readiness.PROCEED, readiness.HOLD, readiness.PULL_BACK)
    assert "not a number" in result["no_score"]


def test_a_missing_signal_becomes_a_caveat_not_a_guess(store):
    _obs(store, "resting_hr", [52.0 + (i % 3) for i in range(30)])
    _obs(store, "sleep_duration", [460.0] * 30)

    result = readiness.readiness(store, START + timedelta(days=29))

    assert any("HRV" in c for c in result.caveats)


# -- recovery drivers -------------------------------------------------

def test_recovery_drivers_returns_only_intervals_that_clear_zero(store):
    import random
    random.seed(1)
    # A genuine next-day effect of strain on HRV, plus a pure-noise metric.
    strain = [random.gauss(12, 4) for _ in range(80)]
    hrv = [70.0] + [90 - 1.5 * s + random.gauss(0, 2) for s in strain[:-1]]
    _obs(store, "strain", strain)
    _obs(store, "hrv_rmssd", hrv)
    _obs(store, "steps", [random.gauss(9000, 1500) for _ in range(80)])

    result = readiness.recovery_drivers(
        store, targets=("hrv_rmssd",), days=200,
        end=START + timedelta(days=79))

    assert result["comparisons_made"] >= 2
    survivors = {f["input"] for f in result["survivors"]}
    assert "strain" in survivors
    assert "steps" not in survivors            # noise does not survive
    assert "hypothesis" in result["caveat"]


# -- strength against recovery --------------------------------------

def _lift(store, name: str, rows: list[tuple[date, float, int]]) -> None:
    records = Records(exercise_templates=[ExerciseTemplate(
        source="hevy", template_id="x", title=name, primary_muscle="chest")])
    for i, (day, weight, reps) in enumerate(rows):
        records.strength_sets.append(StrengthSet(
            source="hevy", workout_id=f"w{i}", exercise_idx=0, set_idx=0,
            ts=datetime.combine(day, time(17), tzinfo=timezone.utc), local_date=day,
            exercise=name, exercise_id="x", set_type="normal",
            weight_kg=weight, reps=reps))
    store.load(records)


def test_strength_recovery_link_buckets_sessions_and_keeps_the_n(store):
    # Eight weekly sessions; on the "red" weeks the top set is lighter.
    weeks = [START + timedelta(weeks=i) for i in range(8)]
    red = {2, 5}
    _lift(store, "Bench Press", [
        (w, 90.0 if i in red else 100.0 + i, 5) for i, w in enumerate(weeks)])
    for i, w in enumerate(weeks):
        score = 25.0 if i in red else 80.0
        store.load(Records(observations=[Observation(
            ts=datetime.combine(w, time(6), tzinfo=timezone.utc), local_date=w,
            metric="recovery_score", value=score, unit="%", source="whoop",
            source_id=f"r{i}")]))

    result = readiness.strength_recovery_link(
        store, days=120, end=weeks[-1] + timedelta(days=1))

    bench = next(e for e in result["exercises"] if e["exercise"] == "Bench Press")
    assert bench["by_recovery"]["red"]["n"] == 2
    assert bench["by_recovery"]["green"]["n"] == 6
    # The red days sit below the lift's own trend line.
    assert bench["by_recovery"]["red"]["mean_e1rm_vs_trend_kg"] < \
        bench["by_recovery"]["green"]["mean_e1rm_vs_trend_kg"]


def test_strength_recovery_link_skips_a_thin_exercise(store):
    _lift(store, "Curl", [(START + timedelta(weeks=i), 20.0 + i, 10)
                          for i in range(4)])

    result = readiness.strength_recovery_link(
        store, end=START + timedelta(days=40))

    assert not result["exercises"]


# -- phase-aware plan ----------------------------------------------

def test_phase_plan_says_it_needs_cycle_data_when_there_is_none(store):
    _clean_day(store)

    plan = readiness.phase_training_plan(store, today=START + timedelta(days=20))

    assert plan["available"] is False
    assert "period logs" in plan["note"] or "no period logs" in plan["note"]


def test_phase_plan_puts_the_heavy_window_in_the_follicular_phase(store):
    first = date(2026, 1, 5)
    records = Records()
    for c in range(4):
        cstart = first + timedelta(days=28 * c)
        for offset in range(5):
            records.cycle_events.append(CycleEvent(
                source="apple_health", local_date=cstart + timedelta(days=offset),
                event="flow" if offset else "period_start", flow="medium"))
    store.load(records)
    from health.features import cycle as cyc
    cyc.rebuild(store)

    # Day 8 of the current cycle — squarely follicular.
    today = first + timedelta(days=28 * 3 + 8)
    plan = readiness.phase_training_plan(store, weeks=4, today=today)

    assert plan["available"] is True
    assert plan["windows"][0]["phase"] == "follicular"
    assert "heavy" in plan["windows"][0]["emphasis"]


# -- illness watch & compound context ------------------------------------

def _protocol(store, compound: str, on: date, dose: float = 2.0):
    store.load(Records(protocol_events=[ProtocolEvent(
        source="protocol", local_date=on, event="start", compound=compound,
        dose=dose, unit="mg", freq="weekly")]))
    protocol_features.rebuild(store)


def test_illness_watch_fires_when_the_trio_rises_together(store):
    day = START + timedelta(days=29)
    # 18 calm days, then a 12-day coordinated climb in all three.
    ramp = [0.0] * 18 + [i * 0.5 for i in range(1, 13)]
    _obs(store, "resting_hr", [52 + r for r in ramp])
    _obs(store, "respiratory_rate", [14 + 0.5 * r for r in ramp])
    _obs(store, "skin_temp_deviation", [0.0 + 0.4 * r for r in ramp])

    watch = readiness.illness_watch(store, as_of=day)
    assert watch["flag"] == "watch"
    assert "ahead of an illness" in watch["note"]


def test_illness_watch_subtracts_the_glp1_resting_hr_offset(store):
    day = START + timedelta(days=29)
    for i in range(30):
        _obs(store, "resting_hr", [52.0], start=START + timedelta(days=i))
    # today: resting HR up 4 bpm — but that is the retatrutide offset
    _obs(store, "resting_hr", [56.0], start=day)
    _protocol(store, "retatrutide", START)

    watch = readiness.illness_watch(store, as_of=day)
    rhr = next(s for s in watch["signals"] if s["metric"] == "resting_hr")
    assert rhr["compound_adjusted"] is True
    assert watch["compound_adjustment_bpm"] > 0
    # adjusted z is small, so no flag on resting HR alone
    assert watch["flag"] == "clear"


def test_readiness_context_notes_a_compound_explained_move(store):
    _clean_day(store, days=32)
    _obs(store, "resting_hr", [64.0], start=START + timedelta(days=32))   # well up
    _obs(store, "hrv_rmssd", [70.0], start=START + timedelta(days=32))
    _obs(store, "recovery_score", [80.0], start=START + timedelta(days=32))
    _obs(store, "sleep_duration", [470.0], start=START + timedelta(days=32))
    _protocol(store, "retatrutide", START)

    result = readiness.readiness(store, START + timedelta(days=32))
    assert any("retatrutide" in c and "expected" in c for c in result.context)
    assert result.as_dict()["on"]
