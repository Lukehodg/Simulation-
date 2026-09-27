from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone

import pytest

from health import alerts
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

def test_under_fuelling_becomes_a_flag(store):
    """`energy.py`'s reachable half. A GLP-1's `monitor` entry names energy
    availability, which needs nutrition data nobody has connected — so the
    marker only gets watched if the scale can stand in for it."""
    _obs(store, "body_mass", [92.0 - i * 0.25 for i in range(21)], source="apple_health")
    store.load(Records(protocol_events=[
        ProtocolEvent(source="protocol", local_date=START - timedelta(weeks=6),
                      event="start", compound="retatrutide", dose=2, unit="mg",
                      freq="weekly")]))
    protocol_features.rebuild(store)

    flags = {a.key: a for a in alerts.check(store, as_of=START + timedelta(days=20))}

    assert "underfuelling" in flags
    assert "retatrutide" in flags["underfuelling"].message


def test_a_steady_scale_on_a_glp1_does_not_alert(store):
    _obs(store, "body_mass", [92.0 - i * 0.05 for i in range(21)], source="apple_health")
    store.load(Records(protocol_events=[
        ProtocolEvent(source="protocol", local_date=START - timedelta(weeks=6),
                      event="start", compound="retatrutide", dose=2, unit="mg",
                      freq="weekly")]))
    protocol_features.rebuild(store)

    keys = {a.key for a in alerts.check(store, as_of=START + timedelta(days=20))}

    assert "underfuelling" not in keys


def test_send_delivers_a_new_flag_once_and_stays_quiet_while_it_persists(store):
    sent = []
    for i in range(7):
        _bp(store, START + timedelta(days=i), 144, 92)
    day = START + timedelta(days=6)

    first = alerts.send(store, as_of=day, notifier=sent.append)
    second = alerts.send(store, as_of=day, notifier=sent.append)

    assert len(first) == 1
    assert first[0].key == "bp_escalate"
    assert second == []                        # already alerted, stays quiet
    assert len(sent) == 1                       # only texted once


def test_a_delivery_failure_is_retried_rather_than_marked_sent(store):
    from health.notify import NotifyError

    def unreachable(_text: str) -> None:
        raise NotifyError("Pushover is down")

    for i in range(7):
        _bp(store, START + timedelta(days=i), 144, 92)
    day = START + timedelta(days=6)

    assert alerts.send(store, as_of=day, notifier=unreachable) == []
    assert not store.alerted("bp_escalate")

    landed = []
    assert len(alerts.send(store, as_of=day, notifier=landed.append)) == 1
    assert landed


def test_send_alerts_again_after_a_flag_clears_and_recurs(store):
    quiet = lambda _text: None                              # noqa: E731
    for i in range(7):
        _bp(store, START + timedelta(days=i), 144, 92)
    day = START + timedelta(days=6)
    alerts.send(store, as_of=day, notifier=quiet)
    assert store.alerted("bp_escalate")

    # the condition clears: a quiet week with no BP logged
    clear_day = day + timedelta(days=30)
    cleared = alerts.send(store, as_of=clear_day, notifier=quiet)
    assert cleared == []
    assert not store.alerted("bp_escalate")

    # and recurs
    for i in range(7):
        _bp(store, clear_day + timedelta(days=1 + i), 144, 92)
    recur_day = clear_day + timedelta(days=7)
    again = alerts.send(store, as_of=recur_day, notifier=quiet)

    assert len(again) == 1
    assert again[0].key == "bp_escalate"
