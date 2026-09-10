from __future__ import annotations

import random
from datetime import date, datetime, time, timedelta, timezone

from health.features import trend
from health.models import Observation, Records

START = date(2026, 4, 1)


def _load(store, metric: str, values: list[float]) -> None:
    store.load(Records(observations=[
        Observation(ts=datetime.combine(START + timedelta(days=i), time(6),
                                        tzinfo=timezone.utc),
                    local_date=START + timedelta(days=i), metric=metric,
                    value=v, unit="x", source="whoop", source_id=f"{metric}-{i}")
        for i, v in enumerate(values)]))


def test_a_steady_decline_is_caught_as_a_trend(store):
    random.seed(1)
    values = [60 - 0.25 * i + random.gauss(0, 1.5) for i in range(42)]
    _load(store, "hrv_rmssd", values)

    t = trend.metric_trend(store, "hrv_rmssd", as_of=START + timedelta(days=41))

    assert t.verdict == "falling"
    assert t.slope_per_week < 0
    assert "downward drift" in t.describe()


def test_noise_is_not_a_trend(store):
    random.seed(2)
    _load(store, "hrv_rmssd", [55 + random.gauss(0, 6) for _ in range(42)])

    t = trend.metric_trend(store, "hrv_rmssd", as_of=START + timedelta(days=41))

    assert t.verdict == "flat"
    assert "within noise" in t.describe()


def test_autocorrelation_shrinks_the_effective_sample(store):
    random.seed(3)
    drift, values = 50.0, []
    for _ in range(42):
        drift += random.gauss(0, 1)          # a random walk: heavily autocorrelated
        values.append(drift)
    _load(store, "resting_hr", values)

    t = trend.metric_trend(store, "resting_hr", as_of=START + timedelta(days=41))

    assert t.effective_n < t.n
    assert t.effective_n < 30


def test_a_trend_is_withheld_until_there_is_enough_of_it(store):
    _load(store, "hrv_rmssd", [50.0 - i for i in range(15)])

    t = trend.metric_trend(store, "hrv_rmssd", as_of=START + timedelta(days=14))

    assert t.slope_per_week is None
    assert "need 21" in t.describe()


def test_trends_returns_only_the_moving_metrics(store):
    _load(store, "hrv_rmssd", [60 - 0.3 * i for i in range(42)])   # clearly falling
    _load(store, "resting_hr", [55.0] * 42)                         # flat

    found = {t.metric for t in trend.trends(store, as_of=START + timedelta(days=41))}
    assert "hrv_rmssd" in found
    assert "resting_hr" not in found
