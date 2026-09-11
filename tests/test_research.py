from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest

from health import research

FIXTURE = json.loads((Path(__file__).parent / "fixtures" / "europepmc.json").read_text())


def _client(capture: dict | None = None) -> httpx.Client:
    def handler(request: httpx.Request) -> httpx.Response:
        if capture is not None:
            capture.update(dict(request.url.params))
        return httpx.Response(200, json=FIXTURE)
    return httpx.Client(transport=httpx.MockTransport(handler))


def test_query_restricts_to_title_and_abstract_and_filters_design():
    query = research.build_query("low ferritin", since_year=2019,
                                 designs=["Meta-Analysis", "Review"])

    assert 'TITLE_ABS:"low ferritin"' in query   # not a full-text match
    assert 'PUB_TYPE:"Meta-Analysis"' in query
    assert "FIRST_PDATE:[2019-01-01" in query


def test_results_are_ordered_by_study_design_not_citations():
    """The meta-analysis outranks the more-cited review, because design is the
    better guide to weight and citation count is popularity."""
    papers = research.search("ferritin", client=_client())

    assert [p.design for p in papers] == ["meta-analysis", "review"]
    assert papers[0].cited_by < papers[1].cited_by


def test_papers_carry_what_is_needed_to_check_them():
    papers = research.search("ferritin", client=_client())
    meta = papers[0]

    assert meta.year == 2023
    assert meta.journal == "BJSM"
    assert meta.open_access is True
    assert meta.url == "https://doi.org/10.1000/example.2"
    assert meta.cite() == ("Iron supplementation and performance: a meta-analysis. "
                           "BJSM. 2023.")


def test_limit_is_respected_but_over_fetches_so_the_sort_is_meaningful():
    captured: dict = {}
    papers = research.search("ferritin", limit=1, client=_client(captured))

    assert len(papers) == 1
    assert int(captured["pageSize"]) >= 25
    assert papers[0].design == "meta-analysis"


def test_a_lab_result_becomes_a_direction_specific_query():
    """Low ferritin and high ferritin are different literatures."""
    assert research.evidence_query("ferritin", "low") == "low Ferritin"
    assert research.evidence_query("vitamin_d", "low", "endurance athletes") == (
        "low Vitamin D endurance athletes")
    assert research.evidence_query("ferritin", None) == "Ferritin"


# -- weekly_pattern_query --------------------------------------------------

from datetime import date, datetime, time, timedelta, timezone

from health.features import protocol as protocol_features
from health.models import (ExperimentEvent, Observation, ProtocolEvent,
                           Records)

WSTART = date(2026, 4, 1)
WEND = WSTART + timedelta(days=79)


def _obs(store, metric, values, start=WSTART, source="whoop"):
    store.load(Records(observations=[
        Observation(ts=datetime.combine(start + timedelta(days=i), time(6),
                                        tzinfo=timezone.utc),
                    local_date=start + timedelta(days=i), metric=metric,
                    value=v, unit="x", source=source,
                    source_id=f"{metric}-{(start + timedelta(days=i)).isoformat()}")
        for i, v in enumerate(values)]))


def test_a_quiet_week_returns_none(store):
    assert research.weekly_pattern_query(store, end=WEND) is None


def test_a_six_week_trend_is_picked_when_nothing_else_is_there(store):
    _obs(store, "respiratory_rate", [14.0 - 0.05 * i for i in range(80)])

    result = research.weekly_pattern_query(store, end=WEND)

    assert result["source"] == "trend"
    assert "respiratory" in result["query"]


def test_a_recovery_driver_outranks_a_bare_trend(store):
    import random
    random.seed(4)
    strain = [random.gauss(12, 4) for _ in range(80)]
    hrv = [70.0] + [90 - 1.5 * s + random.gauss(0, 2) for s in strain[:-1]]
    _obs(store, "strain", strain)
    _obs(store, "hrv_rmssd", hrv)
    _obs(store, "respiratory_rate", [14.0 - 0.05 * i for i in range(80)])  # also a real trend

    result = research.weekly_pattern_query(store, end=WEND)

    assert result["source"] == "recovery_driver"


def test_a_trending_monitoring_marker_outranks_a_driver(store):
    import random
    random.seed(4)
    strain = [random.gauss(12, 4) for _ in range(80)]
    hrv = [70.0] + [90 - 1.5 * s + random.gauss(0, 2) for s in strain[:-1]]
    _obs(store, "strain", strain)
    _obs(store, "hrv_rmssd", hrv)
    # blood pressure climbing steadily while on testosterone -> a monitored
    # marker (testosterone's "blood_pressure" marker resolves to bp_systolic)
    _obs(store, "bp_systolic", [118.0 + 0.2 * i for i in range(80)], source="checkin")
    store.load(Records(protocol_events=[ProtocolEvent(
        source="protocol", local_date=WSTART, event="start", compound="testosterone",
        dose=200, unit="mg", freq="weekly")]))
    protocol_features.rebuild(store)

    result = research.weekly_pattern_query(store, end=WEND)

    assert result["source"] == "protocol"
    assert "testosterone" in result["query"].lower()


def test_a_completed_experiment_outranks_everything(store):
    store.load(Records(experiment_events=[ExperimentEvent(
        source="experiment", local_date=WSTART, event="start", experiment_id="e1",
        hypothesis="magnesium and deep sleep", exposure_type="manual",
        outcome_metric="sleep_duration", predicted_direction="raises", block_days=7,
        blocks_planned=6, start_date=WSTART, starting_condition="A")]))
    from health.features import experiment as experiment_features
    exp = experiment_features.load(store, "e1")
    for block in experiment_features.block_windows(exp):
        val = 480.0 if block.condition == "B" else 400.0
        day = block.start
        while day <= block.end:
            _obs(store, "sleep_duration", [val], start=day)
            day += timedelta(days=1)
    # also seed a driver-worthy pair, to prove the experiment still wins
    import random
    random.seed(4)
    strain = [random.gauss(12, 4) for _ in range(80)]
    hrv = [70.0] + [90 - 1.5 * s + random.gauss(0, 2) for s in strain[:-1]]
    _obs(store, "strain", strain)
    _obs(store, "hrv_rmssd", hrv)

    end = experiment_features.block_windows(exp)[-1].end + timedelta(days=1)
    result = research.weekly_pattern_query(store, end=end)

    assert result["source"] == "experiment"
    assert "magnesium" in result["pattern"]


# -- weekly_research --------------------------------------------------------

def test_weekly_research_is_none_on_a_quiet_week(store):
    assert research.weekly_research(store, end=WEND) is None


def test_weekly_research_attaches_papers_to_the_pattern(store, monkeypatch):
    _obs(store, "respiratory_rate", [14.0 - 0.05 * i for i in range(80)])
    orig_search = research.search
    monkeypatch.setattr(research, "search",
                        lambda *a, **k: orig_search("respiratory rate", client=_client()))

    result = research.weekly_research(store, end=WEND)

    assert result["source"] == "trend"
    assert len(result["papers"]) == 2
    assert result["papers"][0]["design"] == "meta-analysis"


def test_weekly_research_reports_a_search_failure_without_losing_the_pattern(store, monkeypatch):
    _obs(store, "respiratory_rate", [14.0 - 0.05 * i for i in range(80)])

    def _boom(*a, **k):
        raise httpx.ConnectError("no network")
    monkeypatch.setattr(research, "search", _boom)

    result = research.weekly_research(store, end=WEND)

    assert result["source"] == "trend"       # the pattern itself still came through
    assert result["papers"] == []
    assert "ConnectError" in result["error"]
