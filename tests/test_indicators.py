from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone

import pytest

from health.analysis import SYSTEM, redacted_payload
from health.features import indicators, training
from health.models import (ExerciseTemplate, LabResult, Observation, Records,
                           Sleep, StrengthSet)

START = date(2026, 5, 1)
TODAY = START + timedelta(days=59)


def _seed_metrics(store, hrv=70.0, rhr=52.0):
    records = Records()
    for i in range(60):
        day = START + timedelta(days=i)
        for metric, value in (("hrv_rmssd", hrv + (i % 5) - 2),
                              ("resting_hr", rhr + (i % 3) - 1),
                              ("sleep_duration", 450 + (i % 20)),
                              ("steps", 9000 + (i % 500))):
            records.observations.append(Observation(
                ts=datetime.combine(day, time(6), tzinfo=timezone.utc), local_date=day,
                metric=metric, value=float(value), unit="x", source="whoop",
                source_id=f"{metric}-{i}"))
        start = datetime.combine(day, time(23), tzinfo=timezone.utc)
        records.sleeps.append(Sleep(
            source="whoop", source_id=f"s{i}", start_ts=start,
            end_ts=start + timedelta(hours=7.6), local_date=day + timedelta(days=1),
            duration_min=456, is_nap=False))
    store.load(records)


def _seed_lifts(store, weekly_gain=1.5, sessions=10, exercise="Squat"):
    records = Records(exercise_templates=[ExerciseTemplate(
        source="hevy", template_id="sq", title=exercise, primary_muscle="legs")])
    for i in range(sessions):
        day = START + timedelta(weeks=i)
        records.strength_sets.append(StrengthSet(
            source="hevy", workout_id=f"w{i}", exercise_idx=0, set_idx=0,
            ts=datetime.combine(day, time(17), tzinfo=timezone.utc), local_date=day,
            exercise=exercise, exercise_id="sq", set_type="normal",
            weight_kg=100 + weekly_gain * i, reps=5))
    store.load(records)


def _seed_bloods(store, ggt=114.2, vitamin_d=41.0):
    store.load(Records(lab_results=[
        LabResult(source="labs", panel_id="p1", analyte="ggt", local_date=TODAY,
                  value=ggt, unit="U/L", ref_low=10, ref_high=71, ref_source="lab",
                  flag="high" if ggt > 71 else "normal", converted=True),
        LabResult(source="labs", panel_id="p1", analyte="alt", local_date=TODAY,
                  value=39.0, unit="U/L", ref_high=55, ref_source="lab",
                  flag="normal", converted=True),
        LabResult(source="labs", panel_id="p1", analyte="vitamin_d", local_date=TODAY,
                  value=vitamin_d, unit="nmol/L", ref_low=50, ref_high=125,
                  ref_source="generic", flag="low", converted=True),
    ]))


# -- the deliberate absence of a score --------------------------------------

def test_there_is_no_overall_score_and_the_absence_is_explained(store):
    _seed_metrics(store)
    _seed_bloods(store)

    result = indicators.all_indicators(store, as_of=TODAY)

    assert "score" not in result
    assert "total" not in result
    assert "arbitrary" in result["no_total"]


def test_indicators_carry_the_strength_of_evidence_behind_them(store):
    _seed_metrics(store)
    _seed_bloods(store)

    found = {i["name"]: i for i in indicators.all_indicators(store, TODAY)["indicators"]}

    # A lab's own reference interval is the strongest evidence here...
    assert found["Liver"]["evidence"] == indicators.STRONG
    assert found["Liver"]["status"] == indicators.STATUS_ATTENTION
    assert "GGT" in found["Liver"]["detail"]
    # ...a generic population range is weaker, and says so.
    assert found["Vitamins"]["evidence"] == indicators.MODERATE
    # ...and acute:chronic load is widely used but weakly evidenced.
    assert found["Training load"]["evidence"] == indicators.MODERATE


def test_completeness_measures_coverage_not_health(store):
    _seed_metrics(store)

    result = indicators.completeness(store, as_of=TODAY)

    assert result["days_covered"]["recovery (HRV, resting HR)"] == 28
    assert result["days_covered"]["nutrition"] == 0
    assert "nutrition" in result["missing"]
    assert "blood tests" in result["missing"]
    assert 0 < result["percent"] < 100


def test_an_indicator_with_no_data_says_unknown_rather_than_ok(store):
    result = indicators.recovery_indicator(store, as_of=TODAY)

    assert result.status == indicators.STATUS_UNKNOWN
    assert "no HRV" in result.detail


# -- training observations --------------------------------------------------

def test_a_progressing_lift_is_left_alone(store):
    _seed_lifts(store, weekly_gain=1.5)

    found = {o["subject"]: o for o in training.observations(store, as_of=TODAY)}

    assert found["Squat"]["kind"] == "progressing"
    assert "leave this one alone" in found["Squat"]["suggestion"]


def test_a_stalled_lift_is_named_with_the_levers_that_move_it(store):
    _seed_lifts(store, weekly_gain=0.0)

    found = {o["subject"]: o for o in training.observations(store, as_of=TODAY)}

    assert found["Squat"]["kind"] == "stalled"
    assert "rep-range" in found["Squat"]["suggestion"]
    assert "r²" in found["Squat"]["basis"]


def test_a_lift_you_stopped_doing_is_surfaced(store):
    _seed_lifts(store, sessions=4)   # last session is four weeks in, then nothing

    found = [o for o in training.observations(store, as_of=TODAY)
             if o["kind"] == "dormant"]

    assert found and found[0]["subject"] == "Squat"
    assert "not trained for" in found[0]["detail"]


def test_too_few_sessions_produces_no_verdict_at_all(store):
    _seed_lifts(store, sessions=3)
    recent = [o for o in training.observations(store, as_of=START + timedelta(days=15))]

    assert not [o for o in recent if o["kind"] in ("stalled", "progressing")]


# -- what gets sent ---------------------------------------------------------

def test_the_payload_carries_context_without_carrying_identity(store):
    _seed_metrics(store)
    _seed_lifts(store)
    _seed_bloods(store)

    payload = redacted_payload(store)

    assert payload["metrics_around_draw"]["28d_before"]["hrv_rmssd"]["days"] == 28
    assert payload["training"]
    assert payload["indicators"]["no_total"]
    assert "name" not in str(payload["results"]).lower()


def test_context_can_be_left_out_entirely(store):
    _seed_metrics(store)
    _seed_bloods(store)

    payload = redacted_payload(store, with_context=False)

    assert "metrics_around_draw" not in payload
    assert payload["results"]


def test_the_prompt_separates_reporting_evidence_from_prescribing(store):
    """Supplements: what trials used, not what to take."""
    assert "Never suggest supplementing an analyte that is inside its range" in SYSTEM
    assert "Do not tell them what to take, or at what dose" in SYSTEM
    assert "trials used X" in SYSTEM
    assert "Do not invent an overall health score" in SYSTEM


def test_the_prompt_reports_compound_effects_but_never_advises_on_the_protocol(store):
    assert "COMPOUNDS." in SYSTEM
    assert "not the dose, not ancillary" in SYSTEM
    assert "not whether to run it" in SYSTEM


def test_the_payload_carries_the_protocol_and_a_pre_panel_list(store):
    from datetime import date as _date

    from health.features import protocol as protocol_features
    from health.models import LabResult, ProtocolEvent

    drawn = _date(2026, 8, 1)
    store.load(Records(
        protocol_events=[ProtocolEvent(source="protocol", local_date=_date(2026, 7, 1),
                                       event="start", compound="testosterone",
                                       dose=200, unit="mg", freq="weekly")],
        lab_results=[LabResult(source="labs", panel_id="p", analyte="hdl",
                               local_date=drawn, value=1.1, unit="mmol/L",
                               ref_low=1.2, ref_source="lab", flag="low",
                               converted=True)]))
    protocol_features.rebuild(store)

    payload = redacted_payload(store)
    assert payload["protocol"]["on"][0]["compound"] == "testosterone"
    assert payload["pre_panel_for_next_time"]


def test_blood_pressure_indicator_status_follows_the_verdict(store, monkeypatch):
    from health.features import checkin as checkin_features

    monkeypatch.setattr(checkin_features, "blood_pressure",
                        lambda s, as_of=None, days=7: {
                            "verdict": "see a doctor", "average_systolic": 144,
                            "average_diastolic": 92, "days": 7, "readings": 7,
                            "note": "worth a clinician's read"})

    found = indicators.blood_pressure_indicator(store)

    assert found.status == indicators.STATUS_ATTENTION
    assert "144/92" in found.detail


def test_blood_pressure_indicator_absent_when_nothing_logged(store):
    assert indicators.blood_pressure_indicator(store) is None
