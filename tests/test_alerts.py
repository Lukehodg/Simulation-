from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone

import pytest

from health import alerts
from health import notify
from health.features import protocol as protocol_features
from health.models import Observation, ProtocolEvent, Records

START = date(2026, 7, 1)


def _obs(store, metric, values, start=START, source="whoop"):
    store.load(Records(observations=[
        Observation(ts=datetime.combine(start + timedelta(days=i), time(6),
                                        tzinfo=timezone.utc),
                    local_date=start + timedelta(days=i), metric=metric,
                    value=v, unit="x", source=source,
                    source_id=f"{metric}-{(start + timedelta(days=i)).isoformat()}")
        for i, v in enumerate(values)]))


def _bp(store, day, systolic, diastolic):
    day_ts = datetime.combine(day, time(8), tzinfo=timezone.utc)
    store.load(Records(observations=[
        Observation(ts=day_ts, local_date=day, metric="bp_systolic",
                    value=systolic, unit="mmHg", source="checkin", source_id=str(day)),
        Observation(ts=day_ts, local_date=day, metric="bp_diastolic",
                    value=diastolic, unit="mmHg", source="checkin", source_id=str(day)),
    ]))


# -- check() ----------------------------------------------------------------

def test_a_quiet_store_flags_nothing(store):
    assert alerts.check(store, as_of=START + timedelta(days=30)) == []


def test_illness_watch_becomes_a_flag(store):
    day = START + timedelta(days=29)
    ramp = [0.0] * 18 + [i * 0.5 for i in range(1, 13)]
    _obs(store, "resting_hr", [52 + r for r in ramp])
    _obs(store, "respiratory_rate", [14 + 0.5 * r for r in ramp])
    _obs(store, "skin_temp_deviation", [0.0 + 0.4 * r for r in ramp])

    flags = {a.key: a for a in alerts.check(store, as_of=day)}

    assert "illness_watch" in flags
    assert "illness" in flags["illness_watch"].message.lower()


def test_bp_escalation_becomes_a_flag_but_a_mere_watch_does_not(store):
    for i in range(7):
        _bp(store, START + timedelta(days=i), 144, 92)   # past BP_ESCALATE

    flags = {a.key: a for a in alerts.check(store, as_of=START + timedelta(days=6))}

    assert "bp_escalate" in flags
    assert "144" in flags["bp_escalate"].message or "doctor" in flags["bp_escalate"].message.lower()


def test_a_watch_verdict_alone_does_not_escalate(store):
    for i in range(7):
        _bp(store, START + timedelta(days=i), 137, 86)   # watch, not escalate

    flags = {a.key: a for a in alerts.check(store, as_of=START + timedelta(days=6))}

    assert "bp_escalate" not in flags


def test_a_trending_monitoring_marker_becomes_a_flag(store):
    _obs(store, "bp_systolic", [118.0 + 0.2 * i for i in range(60)], source="checkin")
    store.load(Records(protocol_events=[ProtocolEvent(
        source="protocol", local_date=START, event="start", compound="testosterone",
        dose=200, unit="mg", freq="weekly")]))
    protocol_features.rebuild(store)

    flags = {a.key: a for a in alerts.check(store, as_of=START + timedelta(days=59))}

    key = "monitor:testosterone:blood_pressure"
    assert key in flags
    assert "testosterone" in flags[key].message


# -- send() -------------------------------------------------------------

def test_send_texts_a_new_flag_once_and_stays_quiet_while_it_persists(store, monkeypatch):
    sent = []
    monkeypatch.setattr(notify, "send_imessage",
                        lambda handle, text: sent.append((handle, text)))
    for i in range(7):
        _bp(store, START + timedelta(days=i), 144, 92)
    day = START + timedelta(days=6)

    first = alerts.send(store, as_of=day, handle="+15551234567")
    second = alerts.send(store, as_of=day, handle="+15551234567")

    assert len(first) == 1
    assert first[0].key == "bp_escalate"
    assert second == []                        # already alerted, stays quiet
    assert len(sent) == 1                       # only texted once


def test_send_alerts_again_after_a_flag_clears_and_recurs(store, monkeypatch):
    monkeypatch.setattr(notify, "send_imessage", lambda handle, text: None)
    for i in range(7):
        _bp(store, START + timedelta(days=i), 144, 92)
    day = START + timedelta(days=6)
    alerts.send(store, as_of=day, handle="+15551234567")
    assert store.alerted("bp_escalate")

    # the condition clears: a quiet week with no BP logged
    clear_day = day + timedelta(days=30)
    cleared = alerts.send(store, as_of=clear_day, handle="+15551234567")
    assert cleared == []
    assert not store.alerted("bp_escalate")

    # and recurs
    for i in range(7):
        _bp(store, clear_day + timedelta(days=1 + i), 144, 92)
    recur_day = clear_day + timedelta(days=7)
    again = alerts.send(store, as_of=recur_day, handle="+15551234567")

    assert len(again) == 1
    assert again[0].key == "bp_escalate"
