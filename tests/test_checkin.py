from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone

import pytest

from health.features import checkin
from health.features import protocol as protocol_features
from health.models import Observation, ProtocolEvent, Records
from health.sources.checkin import CheckInSource

START = date(2026, 6, 1)
TODAY = START + timedelta(days=40)


# -- the source: validation and parsing --------------------------------

class _Cfg:
    def __init__(self, tmp_path):
        self.raw_dir = tmp_path / "raw"


def test_add_rejects_an_out_of_range_rating(tmp_path):
    source = CheckInSource(_Cfg(tmp_path))
    with pytest.raises(ValueError, match="1-5"):
        source.add({"date": "2026-06-01", "energy": 9})


def test_add_rejects_an_implausible_bp_reading(tmp_path):
    source = CheckInSource(_Cfg(tmp_path))
    with pytest.raises(ValueError, match="blood pressure"):
        source.add({"date": "2026-06-01", "bp_readings": [[400, 80, None]]})


def test_add_rejects_an_empty_entry(tmp_path):
    source = CheckInSource(_Cfg(tmp_path))
    with pytest.raises(ValueError, match="nothing to log"):
        source.add({"date": "2026-06-01"})


def test_parse_averages_the_bp_readings_and_keeps_the_raw_ones(tmp_path):
    source = CheckInSource(_Cfg(tmp_path))
    path = source.add({"date": "2026-06-01",
                       "bp_readings": [[128, 82, 60], [126, 80, 62]],
                       "energy": 3, "note": "rough night"})
    records = source.parse(path)

    values = {o.metric: o.value for o in records.observations}
    assert values["bp_systolic"] == pytest.approx(127.0)
    assert values["bp_diastolic"] == pytest.approx(81.0)
    assert values["bp_pulse"] == pytest.approx(61.0)
    assert values["energy"] == 3.0
    assert records.checkins[0].note == "rough night"
    assert records.checkins[0].readings == "128/82,126/80"


# -- feature layer -------------------------------------------------------

def _log(store, day: date, *, bp=None, note=None, **ratings):
    # Build records directly rather than through raw/, since these tests don't
    # need a filesystem — the source's own add()/parse() path is covered above.
    day_ts = datetime.combine(day, time(8), tzinfo=timezone.utc)
    records = Records()
    for field, value in ratings.items():
        records.observations.append(Observation(
            ts=day_ts, local_date=day, metric=field, value=float(value),
            unit="1-5", source="checkin", source_id=str(day)))
    if bp:
        import statistics as _s
        records.observations.append(Observation(
            ts=day_ts, local_date=day, metric="bp_systolic",
            value=_s.mean(r[0] for r in bp), unit="mmHg", source="checkin",
            source_id=str(day)))
        records.observations.append(Observation(
            ts=day_ts, local_date=day, metric="bp_diastolic",
            value=_s.mean(r[1] for r in bp), unit="mmHg", source="checkin",
            source_id=str(day)))
    if note or bp:
        from health.models import CheckIn
        records.checkins.append(CheckIn(source="checkin", local_date=day, note=note))
    store.load(records)


def test_latest_returns_none_when_nothing_logged(store):
    assert checkin.latest(store, TODAY) is None


def test_latest_reads_back_ratings_bp_and_note(store):
    _log(store, TODAY, bp=[[128, 82]], energy=3, mood=4, note="tired")

    entry = checkin.latest(store, TODAY)

    assert entry.ratings["energy"] == 3.0
    assert entry.bp_systolic == pytest.approx(128.0)
    assert entry.note == "tired"


def test_blood_pressure_is_ok_below_the_watch_line(store):
    for i in range(7):
        _log(store, START + timedelta(days=i), bp=[[118, 76]])

    result = checkin.blood_pressure(store, as_of=START + timedelta(days=6))

    assert result["verdict"] == "ok"
    assert result["average_systolic"] == pytest.approx(118.0)


def test_blood_pressure_escalates_past_the_hard_line(store):
    for i in range(7):
        _log(store, START + timedelta(days=i), bp=[[144, 92]])

    result = checkin.blood_pressure(store, as_of=START + timedelta(days=6))

    assert result["verdict"] == "see a doctor"
    assert "doctor" in result["note"] or "clinician" in result["note"]


def test_blood_pressure_note_is_compound_aware_and_never_prescribes(store):
    for i in range(7):
        _log(store, START + timedelta(days=i), bp=[[138, 88]])
    store.load(Records(protocol_events=[ProtocolEvent(
        source="protocol", local_date=START, event="start", compound="testosterone",
        dose=200, unit="mg", freq="weekly")]))
    protocol_features.rebuild(store)

    result = checkin.blood_pressure(store, as_of=START + timedelta(days=6))

    assert "testosterone" in result["note"].lower()
    assert "clinician" in result["note"] or "doctor" in result["note"]
    for banned in ("lower your dose", "reduce your dose", "stop taking",
                  "switch to", "add an AI", "take less"):
        assert banned not in result["note"].lower()


def test_blood_pressure_with_nothing_logged_says_so(store):
    result = checkin.blood_pressure(store)
    assert "note" in result and "checkin" in result["note"]


# -- subjective vs objective ----------------------------------------------

def _clean_days(store, n=32):
    # A 3-day cycle centred on the base value, so a day at phase 1 sits exactly
    # on its own median (z=0) while the baseline still has real spread (mad>0)
    # — a day that is unambiguously "the usual", not an artefact of no baseline.
    pattern = [-1.0, 0.0, 1.0]
    for i in range(n):
        day = START + timedelta(days=i)
        offset = pattern[i % 3]
        store.load(Records(observations=[
            Observation(ts=datetime.combine(day, time(6), tzinfo=timezone.utc),
                       local_date=day, metric=m, value=v, unit="x", source="whoop",
                       source_id=f"{m}-{i}")
            for m, v in (("hrv_rmssd", 70.0 + offset),
                        ("resting_hr", 52.0 + offset),
                        ("recovery_score", 80.0 + offset),
                        ("sleep_duration", 470.0 + offset),
                        ("strain", 10.0 + offset))]))


def test_feeling_worse_than_a_green_day_is_flagged(store):
    _clean_days(store)
    day = START + timedelta(days=31)
    _log(store, day, energy=1, mood=1, stress=5)

    result = checkin.subjective_vs_objective(store, day)

    energy_row = next(r for r in result["rows"] if r["dimension"] == "energy")
    assert energy_row["agreement"] == "you feel worse than the data"


def test_no_checkin_today_says_so(store):
    _clean_days(store)
    result = checkin.subjective_vs_objective(store, START + timedelta(days=31))
    assert "no check-in" in result["note"]


def test_low_libido_on_testosterone_is_left_unannotated_not_guessed(store):
    # compounds.py deliberately claims no direction for testosterone's effect
    # on libido (the literature isn't reliably one-directional), so a low
    # rating should not appear with fabricated compound context.
    _clean_days(store)
    day = START + timedelta(days=31)
    _log(store, day, libido=1)
    store.load(Records(protocol_events=[ProtocolEvent(
        source="protocol", local_date=START, event="start", compound="testosterone",
        dose=200, unit="mg", freq="weekly")]))
    protocol_features.rebuild(store)

    result = checkin.subjective_vs_objective(store, day)

    assert not any(r["dimension"] == "libido" for r in result["rows"])


def test_low_gi_comfort_on_a_glp1_is_annotated_with_compound_context(store):
    _clean_days(store)
    day = START + timedelta(days=31)
    _log(store, day, gi_comfort=1)
    store.load(Records(protocol_events=[ProtocolEvent(
        source="protocol", local_date=START, event="start", compound="retatrutide",
        dose=2, unit="mg", freq="weekly")]))
    protocol_features.rebuild(store)

    result = checkin.subjective_vs_objective(store, day)

    gi_row = next(r for r in result["rows"] if r["dimension"] == "GI comfort")
    assert "retatrutide" in gi_row["data_says"].lower()


# -- divergence history ---------------------------------------------------

def test_divergence_history_runs_without_enough_data(store):
    _clean_days(store, n=10)
    result = checkin.divergence_history(store, days=30, as_of=START + timedelta(days=9))
    assert "note" in result
