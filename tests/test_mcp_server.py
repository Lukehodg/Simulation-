"""The tool surface is what the model actually sees, so it is worth testing
end to end rather than trusting that the features underneath work."""

from __future__ import annotations

import asyncio
import json
from datetime import date, datetime, time, timedelta, timezone

import pytest

from health import mcp_server
from health.models import CycleEvent, LabResult, Observation, Records
from health.store import Store

START = date(2026, 5, 1)
TODAY = START + timedelta(days=59)


@pytest.fixture
def served(config):
    """A real database on disk, since the tools open it read-only by path."""
    config.ensure_dirs()
    with Store(config.db_path) as store:
        store.init_schema()
        records = Records()
        for i in range(60):
            day = START + timedelta(days=i)
            for metric, value in (("hrv_rmssd", 70 - (i % 7)),
                                  ("resting_hr", 52 + (i % 5))):
                records.observations.append(Observation(
                    ts=datetime.combine(day, time(6), tzinfo=timezone.utc),
                    local_date=day, metric=metric, value=float(value), unit="x",
                    source="whoop", source_id=f"{metric}-{i}"))
        records.lab_results.append(LabResult(
            source="labs", panel_id="p1", analyte="ferritin", local_date=START,
            value=11.0, unit="ug/L", ref_low=13, ref_high=150, ref_source="lab",
            flag="low", converted=True, lab="Test Lab"))
        for start in (START, START + timedelta(days=28)):
            records.cycle_events.append(CycleEvent(
                source="apple_health", local_date=start, event="period_start",
                flow="medium"))
            for offset in range(4):
                records.cycle_events.append(CycleEvent(
                    source="apple_health", local_date=start + timedelta(days=offset),
                    event="flow", flow="light"))
        store.load(records)
        from health.features import cycle as cycle_features
        cycle_features.rebuild(store, through=TODAY)

    mcp_server.configure(config)
    return config


def call(name: str, **arguments):
    result = asyncio.run(mcp_server.server.call_tool(name, arguments))
    payload = result.structured_content
    if payload is None:
        payload = json.loads(result.content[0].text)
    if isinstance(payload, dict) and set(payload) == {"result"}:
        payload = payload["result"]
    return payload


def test_every_tool_is_described(served):
    tools = asyncio.run(mcp_server.server.list_tools())

    assert len(tools) >= 15
    assert all(tool.description for tool in tools)
    names = {tool.name for tool in tools}
    assert {"daily_brief", "phase_adjusted_reading", "latest_bloods",
            "correlate_metrics", "search_literature"} <= names


def test_coverage_reports_what_is_stored(served):
    result = call("coverage")
    tables = {(row["source"], row["table"]) for row in result["tables"]}

    assert ("whoop", "observations") in tables
    assert ("labs", "lab_results") in tables


def test_a_baseline_travels_with_its_usability(served):
    usable = call("metric_baseline", metric="hrv_rmssd", as_of=str(TODAY))
    missing = call("metric_baseline", metric="never_recorded", as_of=str(TODAY))

    assert usable["usable"] is True and usable["n"] == 28
    assert missing["usable"] is False
    assert "need 10" in missing["note"]


def test_correlations_expose_their_effective_sample_size(served):
    result = call("correlate_metrics", metric_a="hrv_rmssd", metric_b="resting_hr")

    assert result["n_days"] == 60
    assert result["effective_n"] <= result["n_days"]
    assert "consistent_with_no_effect" in result
    assert result["summary"]


def test_bloods_carry_their_range_source_and_a_standing_caveat(served):
    result = call("latest_bloods")
    ferritin = next(r for r in result["results"] if r["analyte"] == "ferritin")

    assert ferritin["flag"] == "low"
    assert ferritin["range_source"] == "lab"
    assert result["outside_range"] == ["ferritin"]
    assert "doctor" in result["caveat"]


def test_a_reading_can_be_judged_against_its_cycle_phase(served):
    result = call("phase_adjusted_reading", metric="hrv_rmssd",
                  day=str(START + timedelta(days=40)))

    assert result["phase"] in ("menses", "follicular", "ovulation", "luteal")
    assert "z_overall" in result and "z_within_phase" in result
    assert "phase_blind_baseline_would_mislead" in result


def test_the_daily_brief_pulls_the_pieces_together(served):
    result = call("daily_brief", day=str(TODAY))

    assert result["date"] == str(TODAY)
    assert isinstance(result["metrics"], list)
    assert "training_load" in result
    assert "cycle" in result          # cycle logs exist, so it is included


def test_energy_availability_is_honest_about_being_dormant(served):
    result = call("energy_availability")

    assert result["energy_availability"]["available"] is False
    assert result["red_s_watch"]["flag"] == "insufficient data"


def test_active_alerts_reports_nothing_flagged_on_a_quiet_fixture(served):
    result = call("active_alerts")

    assert result["flags"] == []
    assert "nothing" in result["note"]


def test_a_missing_database_is_reported_to_the_model_not_hidden(config):
    """The SDK strips exception messages on the way out, so an unusable
    database has to come back as data or the model is left guessing."""
    mcp_server.configure(config)          # nothing created

    result = call("coverage")

    assert "health init" in result["error"]
