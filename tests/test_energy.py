from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone

import pytest

from health.features import cycle as cycle_features
from health.features import energy
from health.models import CycleEvent, Observation, Records

START = date(2026, 8, 1)


def _obs(store, metric, values, start=START, source="apple_health"):
    store.load(Records(observations=[
        Observation(ts=datetime.combine(start + timedelta(days=i), time(6),
                                        tzinfo=timezone.utc),
                    local_date=start + timedelta(days=i), metric=metric,
                    value=v, unit="x", source=source,
                    source_id=f"{metric}-{(start + timedelta(days=i)).isoformat()}")
        for i, v in enumerate(values) if v is not None]))


# -- energy_availability -----------------------------------------------

def test_unavailable_without_nutrition_and_body_comp_data(store):
    result = energy.energy_availability(store, as_of=START + timedelta(days=6))

    assert result.available is False
    assert "not connected yet" in result.note


def test_partial_data_still_reports_unavailable(store):
    # intake logged, but no active energy or body composition at all
    _obs(store, "energy_intake", [2400.0] * 7)

    result = energy.energy_availability(store, as_of=START + timedelta(days=6))

    assert result.available is False


def test_available_and_optimal_from_lean_mass_directly(store):
    _obs(store, "energy_intake", [2800.0] * 7)
    _obs(store, "active_energy", [400.0] * 7)
    _obs(store, "lean_mass", [55.0] * 7)   # (2800-400)/55 ~= 43.6 kcal/kg FFM

    result = energy.energy_availability(store, as_of=START + timedelta(days=6))

    assert result.available is True
    assert result.usable_days == 7
    assert result.ea == pytest.approx(43.6, abs=0.2)
    assert result.verdict == "reduced"          # 30 <= 43.6 < 45


def test_optimal_verdict_above_the_line(store):
    _obs(store, "energy_intake", [3200.0] * 7)
    _obs(store, "active_energy", [300.0] * 7)
    _obs(store, "lean_mass", [55.0] * 7)         # (3200-300)/55 ~= 52.7

    result = energy.energy_availability(store, as_of=START + timedelta(days=6))

    assert result.verdict == "optimal"


def test_low_verdict_below_the_reduced_line(store):
    _obs(store, "energy_intake", [1600.0] * 7)
    _obs(store, "active_energy", [500.0] * 7)
    _obs(store, "lean_mass", [55.0] * 7)         # (1600-500)/55 ~= 20

    result = energy.energy_availability(store, as_of=START + timedelta(days=6))

    assert result.verdict == "low"


def test_ffm_is_derived_from_body_mass_and_body_fat_when_lean_mass_is_absent(store):
    _obs(store, "energy_intake", [2800.0] * 7)
    _obs(store, "active_energy", [400.0] * 7)
    _obs(store, "body_mass", [80.0] * 7)
    _obs(store, "body_fat_pct", [31.25] * 7)   # FFM = 80 * (1-0.3125) = 55.0

    result = energy.energy_availability(store, as_of=START + timedelta(days=6))

    assert result.available is True
    assert result.ea == pytest.approx(43.6, abs=0.2)


# -- red_s_watch ---------------------------------------------------------

def _cycle(store, starts):
    records = Records()
    for s in starts:
        records.cycle_events.append(CycleEvent(
            source="apple_health", local_date=s, event="period_start", flow="medium"))
        for offset in range(1, 4):
            records.cycle_events.append(CycleEvent(
                source="apple_health", local_date=s + timedelta(days=offset),
                event="flow", flow="light"))
    store.load(records)
    cycle_features.rebuild(store)


def test_red_s_watch_reports_what_is_missing_when_data_is_incomplete(store):
    result = energy.red_s_watch(store, as_of=START + timedelta(days=20))

    assert result["flag"] == "insufficient data"
    assert result["missing"]


def test_red_s_watch_clears_when_only_one_leg_is_true(store):
    # low EA, sustained, but no training load or cycle disruption present
    _obs(store, "energy_intake", [1400.0] * 14)
    _obs(store, "active_energy", [500.0] * 14)
    _obs(store, "lean_mass", [55.0] * 14)
    from health.models import StrengthSet
    store.load(Records(strength_sets=[StrengthSet(
        source="hevy", workout_id="w0", exercise_idx=0, set_idx=0,
        ts=datetime.combine(START, time(17), tzinfo=timezone.utc), local_date=START,
        exercise="Squat", set_type="normal", weight_kg=60, reps=5)]))
    _cycle(store, [START - timedelta(days=28), START])

    result = energy.red_s_watch(store, as_of=START + timedelta(days=13))

    assert result["flag"] == "clear"
    assert result["legs"]["low_energy_availability"] is True
    assert result["legs"]["elevated_load"] is False


def test_red_s_watch_flags_when_all_three_legs_are_true_together(store):
    _obs(store, "energy_intake", [1400.0] * 14)
    _obs(store, "active_energy", [500.0] * 14)
    _obs(store, "lean_mass", [55.0] * 14)
    # a hard ramp in training load
    _obs(store, "strain", [8.0] * 7 + [19.0] * 7)
    # an irregular cycle history: completed lengths 32 and 22 days -> a
    # 10-day spread, past cycle.summary's own >8-day "irregular" threshold
    _cycle(store, [START - timedelta(days=54), START - timedelta(days=22), START])

    result = energy.red_s_watch(store, as_of=START + timedelta(days=13))

    assert result["flag"] == "watch"
    assert "doctor" in result["note"]
