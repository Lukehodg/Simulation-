from __future__ import annotations

import random
from datetime import date, datetime, time, timedelta, timezone

from health.features import readiness
from health.models import Observation, Records

START = date(2026, 4, 1)


def _load(store, metric: str, values: list[float]) -> None:
    store.load(Records(observations=[
        Observation(ts=datetime.combine(START + timedelta(days=i), time(6),
                                        tzinfo=timezone.utc),
                    local_date=START + timedelta(days=i), metric=metric,
                    value=v, unit="x", source="whoop", source_id=f"{metric}-{i}")
        for i, v in enumerate(values)]))


def test_partial_correlation_dissolves_a_planted_confounder(store):
    random.seed(1)
    n = 80
    # strain drives BOTH caffeine and HRV; caffeine has no direct effect.
    strain = [random.gauss(12, 4) for _ in range(n)]
    caffeine = [40 * s + random.gauss(0, 20) for s in strain]
    hrv = [90 - 1.5 * s + random.gauss(0, 2) for s in strain]
    _load(store, "strain", strain)
    _load(store, "caffeine", caffeine)
    _load(store, "hrv_rmssd", hrv)
    end = START + timedelta(days=n - 1)

    raw = readiness.partial_correlate(store, "caffeine", "hrv_rmssd", [],
                                      days=200, end=end)
    adjusted = readiness.partial_correlate(store, "caffeine", "hrv_rmssd",
                                           ["strain"], days=200, end=end)

    assert raw.r is not None and abs(raw.r) > 0.4      # a strong raw correlation
    assert adjusted.crosses_zero                       # ...that is really strain


def test_drivers_model_ranks_the_real_driver_and_flags_collinearity(store):
    random.seed(2)
    n = 90
    strain = [random.gauss(12, 4) for _ in range(n)]
    steps = [800 * s + random.gauss(0, 500) for s in strain]   # collinear with strain
    sleep_eff = [random.gauss(88, 4) for _ in range(n)]
    hrv = [90 - 1.6 * s + random.gauss(0, 2) for s in strain]
    _load(store, "strain", strain)
    _load(store, "steps", steps)
    _load(store, "sleep_efficiency", sleep_eff)
    _load(store, "hrv_rmssd", hrv)

    model = readiness.drivers_model(store, "hrv_rmssd", days=200,
                                    end=START + timedelta(days=n - 1))

    assert model["r_squared"] > 0.4
    top = model["standardised_coefficients"][0]["input"]
    assert top in ("strain", "steps")
    assert model["collinear_inputs"]
