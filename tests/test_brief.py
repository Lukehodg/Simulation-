from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone

from health import brief
from health.models import Observation, Records, Sleep

START = date(2026, 5, 1)


def _seed(store, days: int = 40) -> date:
    records = Records()
    for i in range(days):
        day = START + timedelta(days=i)
        for metric, value in (("hrv_rmssd", 68 + (i % 5)),
                              ("resting_hr", 52 + (i % 3)),
                              ("recovery_score", 70 + (i % 6)),
                              ("sleep_duration", 450 + (i % 20)),
                              ("strain", 10 + (i % 4)),
                              ("steps", 9000 + (i % 800))):
            records.observations.append(Observation(
                ts=datetime.combine(day, time(6), tzinfo=timezone.utc),
                local_date=day, metric=metric, value=float(value), unit="x",
                source="whoop", source_id=f"{metric}-{i}"))
        onset = datetime.combine(day, time(23), tzinfo=timezone.utc)
        records.sleeps.append(Sleep(
            source="whoop", source_id=f"s{i}", start_ts=onset,
            end_ts=onset + timedelta(hours=7.5), local_date=day + timedelta(days=1),
            duration_min=450, is_nap=False))
    store.load(records)
    return START + timedelta(days=days - 1)


def test_daily_payload_is_derived_figures_not_a_raw_dump(store):
    day = _seed(store)

    payload = brief.daily_payload(store, day)

    assert set(payload) >= {"readiness", "sleep_debt", "training_load",
                            "deviations_from_baseline", "training_observations"}
    assert payload["readiness"]["no_score"]
    # No raw per-observation series is handed over.
    assert "observations" not in payload
    assert all(not isinstance(v, list) or len(v) < 40
               for v in payload.values() if isinstance(v, list))


def test_weekly_payload_carries_the_cross_domain_passes(store):
    day = _seed(store, days=60)

    payload = brief.weekly_payload(store, day)

    assert "recovery_drivers" in payload
    assert "strength_vs_recovery" in payload
    assert "phase_training_plan" in payload
    assert payload["recovery_drivers"]["comparisons_made"] >= 0
    # Seven days of series, not the whole history.
    assert len(payload["daily_series"]["hrv_rmssd"]) <= 7


def test_generate_returns_a_stub_when_there_is_no_data(store, config):
    result = brief.generate(store, config, span="today")

    assert "sync" in result.text
    assert result.usage == {}


def test_the_system_prompt_holds_the_house_rules():
    assert "NO SCORE" in brief.SYSTEM
    assert "ASSOCIATIONS ARE HYPOTHESES" in brief.SYSTEM
    assert "RED-S" in brief.SYSTEM
    assert "two short paragraphs" in brief.SYSTEM
    assert "figure you cite must come from this payload" in brief.SYSTEM
