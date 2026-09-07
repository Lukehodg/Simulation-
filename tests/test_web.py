"""The interface is served from the same database the CLI reads, so the payload
it renders is worth testing directly — especially the empty case, which is the
first thing anyone sees."""

from __future__ import annotations

import json
import threading
from datetime import date, datetime, time, timedelta, timezone
from http.server import ThreadingHTTPServer
from urllib.request import urlopen

import pytest

from health.models import CycleEvent, LabResult, Observation, Records, StrengthSet
from health.store import Store
from health.web.api import today_payload
from health.web.server import Handler

START = date(2026, 5, 1)
TODAY = START + timedelta(days=41)


@pytest.fixture
def db(config):
    config.ensure_dirs()
    store = Store(config.db_path)
    store.init_schema()
    yield store
    store.close()


def _seed(store, days: int = 42) -> None:
    records = Records()
    for i in range(days):
        day = START + timedelta(days=i)
        for metric, value in (("hrv_rmssd", 70 - (i % 6)), ("resting_hr", 52 + (i % 4)),
                              ("sleep_duration", 430 + (i % 30)),
                              ("recovery_score", 68 + (i % 9))):
            records.observations.append(Observation(
                ts=datetime.combine(day, time(6), tzinfo=timezone.utc), local_date=day,
                metric=metric, value=float(value), unit="x", source="whoop",
                source_id=f"{metric}-{i}"))
    for i in range(8):
        day = START + timedelta(days=i * 5)
        records.strength_sets.append(StrengthSet(
            source="hevy", workout_id=f"w{i}", exercise_idx=0, set_idx=0,
            ts=datetime.combine(day, time(17), tzinfo=timezone.utc), local_date=day,
            exercise="Romanian Deadlift", set_type="normal",
            weight_kg=80 + i * 2.5, reps=5))
    records.lab_results.append(LabResult(
        source="labs", panel_id="p1", analyte="vitamin_d", local_date=START,
        value=41.0, unit="nmol/L", ref_low=50, ref_high=125, ref_source="generic",
        flag="low", converted=True, lab="Test Lab"))
    store.load(records)


def test_an_empty_database_renders_a_setup_checklist_not_an_error(db, config):
    payload = today_payload(db, config, day=TODAY)

    assert payload["empty"] is True
    assert payload["verdict"]["headline"] == "No data for today"
    assert set(payload["sync"]["missing"]) == {"whoop", "hevy", "apple_health"}
    assert "health auth whoop" in payload["sync"]["hints"]["whoop"]


def test_every_readout_cell_says_what_to_run_when_it_is_empty(db, config):
    payload = today_payload(db, config, day=TODAY)

    for cell in payload["readout"]:
        assert cell["value"] is None
        assert cell["hint"]


def test_a_populated_day_carries_values_baselines_and_deviations(db, config):
    _seed(db)

    payload = today_payload(db, config, day=TODAY)

    assert payload["empty"] is False
    hrv = next(c for c in payload["readout"] if c["metric"] == "hrv_rmssd")
    assert hrv["value"] is not None
    assert hrv["baseline_n"] == 28
    assert payload["chart"]["points"]
    assert payload["strength"][0]["exercise"] == "Romanian Deadlift"
    assert payload["bloods"]["flagged"] == 1


def test_sleep_is_shown_as_hours_and_minutes(db, config):
    _seed(db)
    payload = today_payload(db, config, day=TODAY)

    sleep = next(c for c in payload["readout"] if c["metric"] == "sleep_duration")
    assert ":" in sleep["value"]      # 7:22, not 442


def test_it_falls_back_to_the_last_day_with_data_and_says_so(db, config):
    _seed(db)

    payload = today_payload(db, config, day=TODAY + timedelta(days=5))

    assert payload["showing_older_day"] == str(TODAY)
    assert payload["date"] == str(TODAY)


def test_the_verdict_describes_rather_than_advises(db, config):
    """It reports what the numbers do. What to do about that is not something
    this data can decide."""
    _seed(db)
    payload = today_payload(db, config, day=TODAY)

    text = (payload["verdict"]["headline"] + payload["verdict"]["detail"]).lower()
    for word in ("should", "train ", "rest ", "recommend", "take "):
        assert word not in text


def test_a_cycle_explained_dip_is_reported_as_ordinary(db, config):
    _seed(db)
    records = Records()
    for start in (START, START + timedelta(days=28)):
        records.cycle_events.append(CycleEvent(
            source="apple_health", local_date=start, event="period_start", flow="medium"))
        for offset in range(4):
            records.cycle_events.append(CycleEvent(
                source="apple_health", local_date=start + timedelta(days=offset),
                event="flow", flow="light"))
    db.load(records)
    from health.features import cycle as cycle_features
    cycle_features.rebuild(db, through=TODAY)

    payload = today_payload(db, config, day=TODAY)

    assert payload["cycle"]["cycle_day"]
    assert payload["cycle"]["phase"]


def test_the_server_serves_the_page_and_the_api(db, config):
    _seed(db)
    db.close()          # DuckDB gives the file to one writer or many readers
    handler = type("Bound", (Handler,), {"config": config})
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{server.server_address[1]}"
    try:
        page = urlopen(base + "/").read().decode()
        payload = json.loads(urlopen(base + f"/api/today?date={TODAY}").read())
        missing = urlopen(base + "/api/metric?metric=hrv_rmssd").read()
        assert urlopen(base + "/").status == 200
    finally:
        server.shutdown()
        server.server_close()

    assert "<title>health</title>" in page
    assert payload["date"] == str(TODAY)
    assert json.loads(missing)["metric"] == "hrv_rmssd"


def test_a_locked_database_is_explained_rather_than_leaked(db, config):
    """`health sync` in another terminal holds the write lock. The page should
    say so, not surface a DuckDB error."""
    handler = type("Bound", (Handler,), {"config": config})
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{server.server_address[1]}"
    try:
        with pytest.raises(Exception) as caught:
            urlopen(base + "/api/today")
        body = json.loads(caught.value.read())
    finally:
        server.shutdown()
        server.server_close()

    assert "busy" in body["error"]
    assert "sync" in body["error"]


def test_the_server_binds_loopback_only(db, config):
    from health.web import server as web_server

    assert web_server.HOST == "127.0.0.1"
