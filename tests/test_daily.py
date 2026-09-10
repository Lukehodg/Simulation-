from __future__ import annotations

import math
import random
from datetime import date, datetime, time, timedelta, timezone

import pytest

from health.features import daily
from health.models import Observation, Records, StrengthSet

START = date(2026, 5, 1)


def _load(store, metric: str, values: list[float], start: date = START,
          source: str = "whoop") -> None:
    records = Records(observations=[
        Observation(ts=datetime.combine(start + timedelta(days=i), time(6),
                                        tzinfo=timezone.utc),
                    local_date=start + timedelta(days=i), metric=metric,
                    value=value, unit="x", source=source,
                    source_id=f"{metric}-{i}")
        for i, value in enumerate(values)
    ])
    store.load(records)


def test_baseline_is_robust_to_a_bad_fortnight(store):
    """A holiday or an illness should not redefine normal."""
    steady = [70.0, 68.0, 72.0, 71.0, 69.0] * 4          # 20 ordinary days
    disrupted = [40.0, 38.0, 42.0, 39.0, 41.0, 37.0, 43.0, 40.0]  # 8 bad ones
    _load(store, "hrv_rmssd", steady + disrupted)
    as_of = START + timedelta(days=28)

    result = daily.baseline(store, "hrv_rmssd", as_of=as_of)

    assert result.n == 28
    assert result.median == pytest.approx(69.0, abs=2)   # a mean would say ~61
    assert result.usable


def test_z_score_is_expressed_in_the_metrics_own_spread(store):
    _load(store, "resting_hr", [52, 53, 51, 54, 52, 53, 52, 51, 53, 52,
                                52, 53, 51, 54, 52, 53, 52, 51, 53, 52])
    result = daily.baseline(store, "resting_hr", as_of=START + timedelta(days=20))

    assert result.z(52) == pytest.approx(0.0, abs=0.5)
    assert result.z(60) > 3          # eight beats up on a tight baseline is a lot
    assert result.z(None) is None


def test_a_baseline_is_withheld_until_there_is_enough_of_it(store):
    _load(store, "hrv_rmssd", [70.0] * 5)
    result = daily.baseline(store, "hrv_rmssd", as_of=START + timedelta(days=5))

    assert not result.usable
    assert result.z(40) is None
    assert "need 10" in result.note


def test_a_flat_metric_has_no_spread_to_judge_against(store):
    _load(store, "hrv_rmssd", [70.0] * 20)
    result = daily.baseline(store, "hrv_rmssd", as_of=START + timedelta(days=20))

    assert result.mad == 0
    assert not result.usable
    assert result.z(90) is None      # not "infinitely abnormal"


def test_deviations_shortlists_the_unusual_metrics(store):
    _load(store, "hrv_rmssd", [70.0, 68.0, 72.0, 71.0, 69.0] * 4 + [40.0])
    _load(store, "resting_hr", [52.0, 53.0, 51.0, 54.0, 52.0] * 4 + [52.0])
    day = START + timedelta(days=20)

    found = daily.deviations(store, day)

    assert [metric for metric, _, _ in found] == ["hrv_rmssd"]
    assert found[0][2] < -1.5


def test_autocorrelation_shrinks_the_effective_sample_size(store):
    """90 days of a slow-drifting signal is not 90 independent observations,
    and pretending otherwise is how noise becomes a finding."""
    random.seed(3)
    drift_a, drift_b, xs, ys = 70.0, 50.0, [], []
    for _ in range(90):
        drift_a += random.gauss(0, 1) - 0.02 * (drift_a - 70)
        drift_b += random.gauss(0, 1) - 0.02 * (drift_b - 50)
        xs.append(drift_a)
        ys.append(drift_b)
    _load(store, "hrv_rmssd", xs)
    _load(store, "resting_hr", ys)

    result = daily.correlate(store, "hrv_rmssd", "resting_hr")

    assert result.n == 90
    assert result.effective_n < 45          # heavily autocorrelated
    # The widened interval is what stops an accidental correlation reading as
    # established: two independent random walks should not "hold up".
    assert result.crosses_zero or abs(result.r) > 0.5
    assert "n=90 days" in result.describe()


def test_a_real_relationship_still_survives(store):
    """Correcting for autocorrelation must not sand away genuine signal."""
    random.seed(5)
    xs = [random.gauss(70, 8) for _ in range(60)]
    ys = [140 - x + random.gauss(0, 2) for x in xs]     # strong, unlagged
    _load(store, "hrv_rmssd", xs)
    _load(store, "resting_hr", ys)

    result = daily.correlate(store, "hrv_rmssd", "resting_hr")

    assert result.r < -0.9
    assert not result.crosses_zero
    assert "holds up" in result.describe()


def test_lag_shifts_the_first_series_backwards(store):
    """Lag 1 asks whether yesterday's value moves today's."""
    random.seed(7)
    driver = [random.gauss(10, 3) for _ in range(60)]
    response = [0.0] + [d * 2 + random.gauss(0, 0.5) for d in driver[:-1]]
    _load(store, "strain", driver)
    _load(store, "hrv_rmssd", response)

    same_day = daily.correlate(store, "strain", "hrv_rmssd", lag_days=0)
    next_day = daily.correlate(store, "strain", "hrv_rmssd", lag_days=1)

    assert abs(next_day.r) > abs(same_day.r)
    assert next_day.n == 59       # one day is lost to the shift


def test_correlation_is_withheld_on_too_few_days(store):
    _load(store, "hrv_rmssd", [70.0] * 5)
    _load(store, "resting_hr", [52.0] * 5)

    result = daily.correlate(store, "hrv_rmssd", "resting_hr")

    assert result.r is None
    assert "need 20" in result.note
    assert result.describe() == result.note


def test_a_scan_records_how_many_comparisons_it_made(store):
    """Testing many pairs guarantees some will look striking; say so."""
    random.seed(11)
    for metric in ("hrv_rmssd", "resting_hr", "strain", "steps"):
        _load(store, metric, [random.gauss(50, 10) for _ in range(40)])

    found = daily.scan(store, "hrv_rmssd")

    assert len(found) == 3
    assert all("of 3 comparisons" in c.note for c in found)
    assert abs(found[0].r) >= abs(found[-1].r)


def test_training_load_combines_strain_and_tonnage(store):
    _load(store, "strain", [10.0] * 28)
    sets = [
        StrengthSet(source="hevy", workout_id=f"w{i}", exercise_idx=0, set_idx=0,
                    ts=datetime.combine(START + timedelta(days=i), time(17),
                                        tzinfo=timezone.utc),
                    local_date=START + timedelta(days=i), exercise="Squat",
                    set_type="normal", weight_kg=100, reps=5)
        for i in range(28)
    ]
    store.load(Records(strength_sets=sets))

    # 28 days of data run START..START+27, so that is the last full window.
    load = daily.training_load(store, as_of=START + timedelta(days=27))

    assert load.acute == pytest.approx(10.5, abs=0.1)   # 10 strain + 500kg/1000
    assert load.ratio == 1.0
    assert "steady" in load.describe()


def test_ewma_is_the_default_and_flat_is_still_available(store):
    # 21 quiet days, then a week of hard training.
    _load(store, "strain", [5.0] * 21 + [18.0] * 7)
    as_of = START + timedelta(days=27)

    default = daily.training_load(store, as_of=as_of)
    flat = daily.training_load(store, as_of=as_of, method="flat")

    assert default.method == "ewma"
    assert flat.method == "flat"
    # both register the ramp as a real acute:chronic elevation
    assert default.ratio > 1.3 and flat.ratio > 1.3
    # the flat 7-day window sits exactly on the recent level; the EWMA blends
    # in the days just before it, so the two numbers are not identical
    assert flat.acute == pytest.approx(18.0, abs=0.1)
    assert default.acute != pytest.approx(flat.acute, abs=0.5)


def test_sleep_regularity_handles_onsets_either_side_of_midnight(store):
    from health.models import Sleep

    nights = []
    for i in range(10):
        # Alternating 23:30 and 00:30 starts: an hour apart, not 23 hours.
        onset = 23.5 if i % 2 == 0 else 24.5
        start = datetime.combine(START + timedelta(days=i), time(0),
                                 tzinfo=timezone.utc) + timedelta(hours=onset)
        nights.append(Sleep(source="whoop", source_id=f"s{i}", start_ts=start,
                            end_ts=start + timedelta(hours=8),
                            local_date=(start + timedelta(hours=8)).date(),
                            duration_min=480, is_nap=False))
    store.load(Records(sleeps=nights))

    result = daily.sleep_regularity(store, days=28, as_of=START + timedelta(days=12))

    assert result["nights"] == 10
    assert result["onset_sd_hours"] < 1.0
    assert result["mean_duration_hours"] == pytest.approx(8.0, abs=0.1)


def test_a_reading_is_not_part_of_its_own_baseline(store):
    """Including today in today's baseline drags the median toward the very
    reading being judged, and hides the deviation."""
    _load(store, "hrv_rmssd", [70.0] * 20 + [30.0])
    day = START + timedelta(days=20)

    result = daily.baseline(store, "hrv_rmssd", as_of=day)

    assert result.n == 20               # the 21st day is excluded
    assert result.median == 70.0        # undisturbed by the outlier
