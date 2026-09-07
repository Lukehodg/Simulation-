from __future__ import annotations

from datetime import date

import pytest

from health.sources.apple_health import AppleHealthSource

FIXTURE = "apple_export.json"


@pytest.fixture
def apple(config):
    return AppleHealthSource(config)


@pytest.fixture
def records(apple, land):
    return apple.parse(land("apple_health", "export", FIXTURE))


def _value(records, metric: str, day: date) -> float:
    matches = [o.value for o in records.observations
               if o.metric == metric and o.local_date == day]
    assert len(matches) == 1, f"expected one {metric} on {day}, got {matches}"
    return matches[0]


def test_cumulative_metrics_are_summed_per_day(records):
    assert _value(records, "steps", date(2026, 9, 1)) == 8300      # 3200 + 5100
    assert _value(records, "steps", date(2026, 9, 2)) == 2000


def test_instantaneous_metrics_are_averaged(records):
    assert _value(records, "resting_hr", date(2026, 9, 1)) == 55   # (54 + 56) / 2


def test_last_reading_wins_for_body_mass(records):
    assert _value(records, "body_mass", date(2026, 9, 1)) == 64.1


def test_apple_hrv_keeps_its_own_metric_name(records):
    """Apple's SDNN must never be mixed into WHOOP's RMSSD series."""
    assert _value(records, "hrv_sdnn", date(2026, 9, 1)) == 61.2
    assert not [o for o in records.observations if o.metric == "hrv_rmssd"]


def test_fractional_percentages_are_scaled(records):
    assert _value(records, "spo2", date(2026, 9, 1)) == pytest.approx(97.2)


def test_myfitnesspal_macros_land_as_a_nutrition_day(records):
    day = next(n for n in records.nutrition_days if n.local_date == date(2026, 9, 1))
    assert day.kcal == 2110       # 480 + 720 + 910
    assert day.protein_g == 120   # 62 + 58


def test_sleep_hours_become_minutes_and_credit_the_waking_day(records):
    sleep = records.sleeps[0]
    assert sleep.local_date == date(2026, 9, 1)
    assert sleep.duration_min == pytest.approx(426.0)   # 7.1 h
    assert sleep.deep_min == pytest.approx(90.0)        # 1.5 h
    assert sleep.efficiency == pytest.approx(91.6, abs=0.1)


def test_period_logs_become_cycle_events(records):
    events = {(e.local_date, e.event): e.flow for e in records.cycle_events}
    assert events[(date(2026, 9, 1), "period_start")] == "medium"
    assert events[(date(2026, 9, 1), "flow")] == "medium"
    assert events[(date(2026, 9, 2), "flow")] == "light"
    # A logged "none" day is not bleeding and must not become an event.
    assert not any(day == date(2026, 9, 5) for day, _ in events)
