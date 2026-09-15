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


# -- the native export (Settings > Health > Export All Health Data) ------------

NATIVE_XML = """<?xml version="1.0" encoding="UTF-8"?>
<HealthData locale="en_GB">
 <Record type="HKQuantityTypeIdentifierBodyMass" sourceName="Connect" unit="lb"
         startDate="2026-09-01 07:10:00 +0100" endDate="2026-09-01 07:10:00 +0100" value="204"/>
 <Record type="HKQuantityTypeIdentifierBodyMass" sourceName="MacroFactor" unit="kg"
         startDate="2026-09-01 07:40:00 +0100" endDate="2026-09-01 07:40:00 +0100" value="92.3"/>
 <Record type="HKQuantityTypeIdentifierStepCount" sourceName="iPhone" unit="count"
         startDate="2026-09-01 09:00:00 +0100" endDate="2026-09-01 10:00:00 +0100" value="3000"/>
 <Record type="HKQuantityTypeIdentifierStepCount" sourceName="iPhone" unit="count"
         startDate="2026-09-01 12:00:00 +0100" endDate="2026-09-01 13:00:00 +0100" value="4000"/>
 <Record type="HKQuantityTypeIdentifierStepCount" sourceName="Watch" unit="count"
         startDate="2026-09-01 09:00:00 +0100" endDate="2026-09-01 13:00:00 +0100" value="7500"/>
 <Record type="HKQuantityTypeIdentifierRestingHeartRate" sourceName="Watch" unit="count/min"
         startDate="2026-09-01 00:00:00 +0100" endDate="2026-09-01 23:00:00 +0100" value="56"/>
 <Record type="HKQuantityTypeIdentifierDietaryProtein" sourceName="MacroFactor" unit="g"
         startDate="2026-09-01 08:00:00 +0100" endDate="2026-09-01 08:00:00 +0100" value="40"/>
 <Record type="HKQuantityTypeIdentifierDietaryProtein" sourceName="MacroFactor" unit="g"
         startDate="2026-09-01 19:00:00 +0100" endDate="2026-09-01 19:00:00 +0100" value="55"/>
 <Record type="HKQuantityTypeIdentifierDietaryEnergyConsumed" sourceName="MacroFactor" unit="Cal"
         startDate="2026-09-01 19:00:00 +0100" endDate="2026-09-01 19:00:00 +0100" value="2100"/>
 <Record type="HKCategoryTypeIdentifierSleepAnalysis" sourceName="Watch"
         value="HKCategoryValueSleepAnalysisAsleepCore"
         startDate="2026-08-31 23:30:00 +0100" endDate="2026-09-01 03:30:00 +0100"/>
 <Record type="HKCategoryTypeIdentifierSleepAnalysis" sourceName="Watch"
         value="HKCategoryValueSleepAnalysisAsleepDeep"
         startDate="2026-09-01 03:30:00 +0100" endDate="2026-09-01 04:30:00 +0100"/>
 <Record type="HKCategoryTypeIdentifierSleepAnalysis" sourceName="Watch"
         value="HKCategoryValueSleepAnalysisAsleepREM"
         startDate="2026-09-01 04:30:00 +0100" endDate="2026-09-01 06:30:00 +0100"/>
 <Record type="HKCategoryTypeIdentifierSleepAnalysis" sourceName="iPhone"
         value="HKCategoryValueSleepAnalysisInBed"
         startDate="2026-08-31 23:00:00 +0100" endDate="2026-09-01 07:00:00 +0100"/>
 <Record type="HKQuantityTypeIdentifierHeight" sourceName="Health" unit="cm"
         startDate="2026-09-01 07:10:00 +0100" endDate="2026-09-01 07:10:00 +0100" value="180"/>
</HealthData>
"""


@pytest.fixture
def native_zip(tmp_path):
    import zipfile

    path = tmp_path / "export.zip"
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("apple_health_export/export.xml", NATIVE_XML)
    return path


@pytest.fixture
def native(apple, native_zip):
    landed = apple.add(native_zip)
    return apple.parse(landed)


def test_the_native_export_lands_as_daily_json_not_the_xml(apple, native_zip):
    landed = apple.add(native_zip)
    assert landed.suffix == ".json"
    assert landed.stat().st_size < 4000                  # reduced, not copied


def test_native_body_mass_takes_the_last_reading_in_kg(native):
    assert _value(native, "body_mass", date(2026, 9, 1)) == 92.3   # not 204 lb


def test_native_steps_keep_one_device_rather_than_adding_both(native):
    assert _value(native, "steps", date(2026, 9, 1)) == 7500       # not 14500


def test_native_nutrition_sums_the_day(native):
    assert _value(native, "protein", date(2026, 9, 1)) == 95
    day = next(n for n in native.nutrition_days if n.local_date == date(2026, 9, 1))
    assert day.kcal == 2100
    assert day.protein_g == 95


def test_native_sleep_prefers_the_night_with_stages(native):
    nights = [s for s in native.sleeps if s.local_date == date(2026, 9, 1)]
    assert len(nights) == 1
    night = nights[0]
    assert night.deep_min == 60
    assert night.rem_min == 120
    assert night.duration_min == 420                     # core 240 + deep 60 + rem 120


def test_native_ignores_types_it_does_not_know(native):
    assert not any(o.metric == "height" for o in native.observations)
