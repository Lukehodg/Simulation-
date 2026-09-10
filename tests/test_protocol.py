from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone

from health.features import protocol
from health.models import Observation, ProtocolEvent, Records

START = date(2026, 6, 1)
TODAY = START + timedelta(days=70)


def _event(store, **kw):
    kw.setdefault("source", "protocol")
    store.load(Records(protocol_events=[ProtocolEvent(**kw)]))


def _anchor_observation(store, day: date = TODAY):
    # protocol_days projects to the last observation or today; give it an anchor.
    store.load(Records(observations=[Observation(
        ts=datetime.combine(day, time(6), tzinfo=timezone.utc), local_date=day,
        metric="hrv_rmssd", value=50.0, unit="ms", source="whoop", source_id="a")]))


def test_a_start_event_fills_days_forward_with_weeks_on(store):
    _anchor_observation(store)
    _event(store, local_date=START, event="start", compound="testosterone",
           dose=200, unit="mg", freq="weekly")
    protocol.rebuild(store)

    rows = dict((c, (wd, w)) for _, c, wd, _, w in store.query(
        "SELECT local_date, compound, weekly_dose, unit, weeks_on "
        "FROM protocol_days WHERE local_date = ?", [START + timedelta(days=14)]))
    assert rows["testosterone"][0] == 200
    assert rows["testosterone"][1] == 2.0     # two weeks in


def test_dose_is_normalised_to_a_weekly_figure(store):
    _anchor_observation(store)
    _event(store, local_date=START, event="start", compound="testosterone",
           dose=100, unit="mg", freq="e3d")
    protocol.rebuild(store)
    on = protocol.active(store, START + timedelta(days=10))
    assert on[0].weekly_dose == round(100 * 7 / 3, 3)


def test_a_stop_event_ends_the_timeline(store):
    _anchor_observation(store)
    _event(store, local_date=START, event="start", compound="retatrutide",
           dose=2, unit="mg", freq="weekly")
    _event(store, local_date=START + timedelta(days=20), event="stop",
           compound="retatrutide")
    protocol.rebuild(store)

    assert protocol.active(store, START + timedelta(days=10))
    assert not protocol.active(store, START + timedelta(days=30))


def test_context_calls_an_expected_move_expected(store):
    _anchor_observation(store)
    _event(store, local_date=START, event="start", compound="retatrutide",
           dose=2, unit="mg", freq="weekly")
    protocol.rebuild(store)

    # resting HR up (+z) — retatrutide raises it
    said = protocol.context(store, "resting_hr", TODAY, z=1.6)
    assert said and "expected" in said[0]
    assert "retatrutide" in said[0]


def test_context_flags_a_move_against_the_compound(store):
    _anchor_observation(store)
    _event(store, local_date=START, event="start", compound="retatrutide",
           dose=2, unit="mg", freq="weekly")
    protocol.rebuild(store)

    # resting HR *down* — the opposite of what retatrutide predicts
    said = protocol.context(store, "resting_hr", TODAY, z=-1.4)
    assert said and "opposite" in said[0]
    assert "more informative" in said[0]


def test_strength_note_fires_only_for_a_strength_confounding_compound(store):
    _anchor_observation(store)
    _event(store, local_date=START, event="start", compound="retatrutide",
           dose=2, unit="mg", freq="weekly")
    protocol.rebuild(store)
    assert protocol.strength_note(store, TODAY) is None

    _event(store, local_date=START, event="start", compound="testosterone",
           dose=200, unit="mg", freq="weekly")
    protocol.rebuild(store)
    note = protocol.strength_note(store, TODAY)
    assert note and "testosterone" in note and str(START) in note


def test_summary_carries_the_no_advice_boundary(store):
    _anchor_observation(store)
    _event(store, local_date=START, event="start", compound="testosterone",
           dose=200, unit="mg", freq="weekly")
    protocol.rebuild(store)
    summary = protocol.summary(store, today=TODAY)

    assert "no dosing, ancillary or PCT guidance" in summary["boundary"]
    assert summary["pre_panel"]
    assert any(m["marker"] == "haematocrit" for m in summary["monitoring"])
