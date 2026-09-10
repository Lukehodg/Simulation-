"""Is the baseline itself moving?

Everything else in this layer asks "is today unusual against the last 28 days".
That question is blind to a slow slide: if HRV falls a little every week, the
28-day baseline falls with it and each single day looks normal. This module
asks the other question — over the last six weeks, is the level trending, and
is that trend more than noise.

Two robust tools, chosen to match the rest of the layer:

*Theil–Sen* for the slope — the median of all pairwise slopes, which a handful
of bad nights cannot drag around the way least-squares can.

*Mann–Kendall* for whether the trend is real — a rank test, so it does not
assume the series is normal or the trend straight. Its variance is inflated for
autocorrelation the same way `daily.correlate` widens its interval, because a
serially correlated series contains fewer independent observations than it has
days and pretending otherwise turns drift-shaped noise into a finding.
"""

from __future__ import annotations

import math
import statistics
from dataclasses import dataclass
from datetime import date, timedelta

from ..store import Store
from . import daily

MIN_TREND_DAYS = 21
#: |z| past this is "a trend"; below it the direction is not distinguishable
#: from noise. ~1.64 is the one-sided 5% point.
NOTABLE_Z = 1.64

DEFAULT_METRICS = ("hrv_rmssd", "resting_hr", "recovery_score", "sleep_duration",
                   "respiratory_rate", "skin_temp_deviation", "body_mass")


@dataclass
class Trend:
    metric: str
    days: int
    n: int
    effective_n: int = 0
    slope_per_week: float | None = None
    tau: float | None = None
    z: float | None = None
    pct_change: float | None = None
    verdict: str = "too few days"

    def describe(self) -> str:
        if self.slope_per_week is None:
            return f"{self.n} day(s) in {self.days}; need {MIN_TREND_DAYS} for a trend"
        arrow = {"rising": "up", "falling": "down", "flat": "flat"}[self.verdict]
        body = (f"{self.metric} {arrow} {abs(self.slope_per_week):.3g}/week over "
                f"{self.n} days (Kendall tau {self.tau:+.2f}, ~{self.effective_n} "
                f"independent)")
        if self.verdict == "flat":
            return body + " — within noise"
        way = "upward" if self.verdict == "rising" else "downward"
        return body + f" — a real {way} drift"

    def as_dict(self) -> dict:
        return {"metric": self.metric, "days": self.days, "n": self.n,
                "effective_n": self.effective_n, "slope_per_week": self.slope_per_week,
                "tau": self.tau, "z": self.z, "pct_change": self.pct_change,
                "verdict": self.verdict, "summary": self.describe()}


def _theil_sen(points: list[tuple[int, float]]) -> float | None:
    slopes = [(yj - yi) / (xj - xi)
              for i, (xi, yi) in enumerate(points)
              for xj, yj in points[i + 1:] if xj != xi]
    return statistics.median(slopes) if slopes else None


def _mann_kendall(values: list[float]) -> tuple[float, float]:
    """Returns (S, Var(S)) with the standard tie correction."""
    n = len(values)
    s = sum(_sign(values[j] - values[i])
            for i in range(n - 1) for j in range(i + 1, n))
    counts: dict[float, int] = {}
    for v in values:
        counts[v] = counts.get(v, 0) + 1
    ties = sum(t * (t - 1) * (2 * t + 5) for t in counts.values() if t > 1)
    var = (n * (n - 1) * (2 * n + 5) - ties) / 18
    return s, var


def _sign(x: float) -> int:
    return (x > 0) - (x < 0)


def _lag1(values: list[float]) -> float:
    if len(values) < 3:
        return 0.0
    mean = statistics.mean(values)
    num = sum((values[i] - mean) * (values[i + 1] - mean)
              for i in range(len(values) - 1))
    den = sum((v - mean) ** 2 for v in values)
    return num / den if den else 0.0


def _ewma(values: list[float], span: int) -> float:
    alpha = 2 / (span + 1)
    acc = values[0]
    for v in values[1:]:
        acc = alpha * v + (1 - alpha) * acc
    return acc


def metric_trend(store: Store, metric: str, days: int = 42,
                 as_of: date | None = None, min_days: int = MIN_TREND_DAYS) -> Trend:
    as_of = as_of or date.today()
    series = [(d, v) for d, v in daily.series(
        store, metric, as_of - timedelta(days=days - 1), as_of) if v is not None]

    result = Trend(metric=metric, days=days, n=len(series), effective_n=len(series))
    if len(series) < min_days:
        return result

    origin = series[0][0]
    points = [((d - origin).days, v) for d, v in series]
    values = [v for _, v in points]

    slope = _theil_sen(points)
    if slope is None:
        return result
    result.slope_per_week = round(slope * 7, 4)

    s, var = _mann_kendall(values)
    # A deterministic trend is itself perfectly autocorrelated, so the
    # autocorrelation has to be measured on the *residuals* after removing the
    # trend — otherwise a real trend penalises itself out of existence. This is
    # the Hamed–Rao / Yue–Pilon pre-whitening idea.
    intercept = statistics.median(v - slope * x for x, v in points)
    residuals = [v - (intercept + slope * x) for x, v in points]
    r1 = _lag1(residuals)
    factor = (1 + r1) / (1 - r1) if abs(r1) < 0.999 else 5.0
    factor = max(factor, 1.0)
    result.effective_n = max(3, int(round(len(values) / factor)))
    var_corrected = var * factor

    if var_corrected > 0:
        z = (s - _sign(s)) / math.sqrt(var_corrected)
        result.z = round(z, 2)
        result.tau = round(s / (len(values) * (len(values) - 1) / 2), 3)

    first, last = _ewma(values[:max(3, len(values) // 3)], 5), _ewma(values, 5)
    if first:
        result.pct_change = round(100 * (last - first) / abs(first), 1)

    if result.z is None or abs(result.z) < NOTABLE_Z:
        result.verdict = "flat"
    else:
        result.verdict = "rising" if result.z > 0 else "falling"
    return result


def trends(store: Store, metrics: tuple[str, ...] = DEFAULT_METRICS,
           days: int = 42, as_of: date | None = None) -> list[Trend]:
    """Every tracked metric's six-week trend, the moving ones first."""
    found = [metric_trend(store, m, days=days, as_of=as_of) for m in metrics]
    moving = [t for t in found if t.verdict in ("rising", "falling")]
    moving.sort(key=lambda t: -abs(t.z or 0))
    return moving
