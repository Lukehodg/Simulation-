from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone

import pytest

from health.features import cycle as cycle_features
from health.features import energy
from health.features import protocol as protocol_features
from health.models import CycleEvent, Observation, ProtocolEvent, Records

START = date(2026, 8, 1)
#: Long enough for `program.weight_trend`'s halves comparison to have something
#: to compare, and the day every watch below is evaluated on.
SPAN = 21
AS_OF = START + timedelta(days=SPAN - 1)


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
    assert result["cycle_leg"] == "tracked"


def _low_ea(store, days: int = 14) -> None:
    _obs(store, "energy_intake", [1400.0] * days)
    _obs(store, "active_energy", [500.0] * days)
    _obs(store, "lean_mass", [55.0] * days)


def test_red_s_watch_runs_on_two_legs_when_no_cycle_is_tracked(store):
    """The gap this closes: with no period logs the old check reported
    `insufficient data` for ever, so it could never fire for anyone who does
    not track one."""
    _low_ea(store)
    _obs(store, "strain", [8.0] * 7 + [19.0] * 7)

    result = energy.red_s_watch(store, as_of=START + timedelta(days=13))

    assert result["flag"] == "watch"
    assert result["cycle_leg"] == "unavailable"
    assert result["legs"]["cycle_disrupted"] is None
    assert "two of three" in result["note"]
    assert "doctor" in result["note"]


def test_two_legs_still_need_both_to_be_true(store):
    _low_ea(store)
    _obs(store, "strain", [8.0] * 14)    # a load to read, but flat, not a ramp
    result = energy.red_s_watch(store, as_of=START + timedelta(days=13))

    assert result["flag"] == "clear"
    assert result["cycle_leg"] == "unavailable"


def test_an_androgen_masks_the_hormonal_screen_and_the_check_says_so(store):
    _low_ea(store)
    _obs(store, "strain", [8.0] * 7 + [19.0] * 7)
    _on(store, "testosterone", 200, start=START - timedelta(weeks=12))

    result = energy.red_s_watch(store, as_of=START + timedelta(days=13))

    assert any("not reassurance" in caveat for caveat in result["caveats"])


# -- underfuelling_watch -------------------------------------------------

def _on(store, compound: str, dose: float, *, start: date,
        freq: str = "weekly", unit: str = "mg") -> None:
    store.load(Records(protocol_events=[
        ProtocolEvent(source="protocol", local_date=start, event="start",
                      compound=compound, dose=dose, unit=unit, freq=freq)]))
    protocol_features.rebuild(store)


def _losing(store, kg_per_day: float, start_kg: float = 92.0) -> None:
    _obs(store, "body_mass", [start_kg - i * kg_per_day for i in range(SPAN)])


def test_underfuelling_needs_a_weight_trend_before_it_says_anything(store):
    _on(store, "retatrutide", 2, start=START)
    result = energy.underfuelling_watch(store, as_of=AS_OF)

    assert result["flag"] == "insufficient data"
    assert "bodyweight trend" in result["missing"][0]


def test_underfuelling_flags_a_glp1_with_the_scale_moving_too_fast(store):
    _losing(store, 0.25)                 # ~0.96 kg/wk against a ~0.61 ceiling
    _on(store, "retatrutide", 2, start=START - timedelta(weeks=6))

    result = energy.underfuelling_watch(store, as_of=AS_OF)

    assert result["flag"] == "watch"
    assert result["legs"]["appetite_suppressed"] is True
    assert result["legs"]["losing_faster_than_ceiling"] is True
    assert result["loss_kg_per_week"] > result["ceiling_kg_per_week"]
    assert "retatrutide" in result["note"] and "doctor" in result["note"]
    assert "not measured energy availability" in result["coarse"]


def test_a_fast_scale_alone_is_not_a_watch(store):
    """No mechanism for the intake being short — losing fast is the plan's
    business, and `step_target` already handles it."""
    _losing(store, 0.25)
    result = energy.underfuelling_watch(store, as_of=AS_OF)

    assert result["flag"] == "clear"
    assert result["legs"]["losing_faster_than_ceiling"] is True
    assert result["legs"]["appetite_suppressed"] is False


def test_a_glp1_inside_the_loss_band_is_not_a_watch(store):
    """Appetite suppression is what the compound is for; on its own it is not
    a finding."""
    _losing(store, 0.1)                  # ~0.39 kg/wk, inside the band
    _on(store, "retatrutide", 2, start=START - timedelta(weeks=6))

    result = energy.underfuelling_watch(store, as_of=AS_OF)

    assert result["flag"] == "clear"
    assert result["legs"]["appetite_suppressed"] is True
    assert result["legs"]["losing_faster_than_ceiling"] is False


def test_the_amplifiers_are_named_when_present(store):
    _losing(store, 0.25)
    _obs(store, "strain", [8.0] * 10 + [19.0] * 11)
    _on(store, "retatrutide", 2, start=START - timedelta(weeks=6))
    for offset in range(1, 7):           # target is 175 g at 87 kg
        _obs(store, "protein", [100.0], start=AS_OF - timedelta(days=offset))

    result = energy.underfuelling_watch(store, as_of=AS_OF)

    assert result["flag"] == "watch"
    assert result["legs"]["protein_short"] is True
    assert result["legs"]["elevated_load"] is True
    assert "training load is elevated" in result["note"]
    assert "protein has been short" in result["note"]
