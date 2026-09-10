"""Day-to-day baselines, load, and relationships between metrics.

This is the substrate the agent reasons over, so every function here returns
its uncertainty alongside its answer. A number handed to a language model
without its sample size will be repeated back to you with total confidence.

Two statistical decisions are made once, here:

*Baselines are robust.* Median and MAD rather than mean and standard
deviation, because a fortnight of illness or a fortnight of holiday should not
redefine normal, and a single bad night should not widen the band that judges
the next one.

*Correlations are corrected for autocorrelation.* Health series are strongly
serially correlated — today looks like yesterday — so 90 days of data does not
contain 90 independent observations. Treating it as though it does turns
ordinary noise into confident findings, which is the single easiest way for a
system like this to mislead you. The effective sample size is estimated and
the confidence interval widened accordingly.
"""

from __future__ import annotations

import math
import statistics
from dataclasses import dataclass
from datetime import date, timedelta

from ..store import Store

BASELINE_WINDOW = 28
MIN_BASELINE_POINTS = 10
MIN_CORRELATION_POINTS = 20
#: MAD to standard-deviation equivalent for normally distributed data.
MAD_TO_SD = 1.4826


@dataclass
class Baseline:
    metric: str
    window: int
    n: int
    median: float | None = None
    mad: float | None = None
    note: str | None = None

    @property
    def usable(self) -> bool:
        return self.n >= MIN_BASELINE_POINTS and bool(self.mad)

    def z(self, value: float | None) -> float | None:
        """Robust z-score: how far from normal, in normal's own units."""
        if value is None or not self.usable:
            return None
        return round((value - self.median) / (self.mad * MAD_TO_SD), 2)


@dataclass
class Correlation:
    a: str
    b: str
    lag_days: int
    n: int
    effective_n: int
    r: float | None = None
    ci_low: float | None = None
    ci_high: float | None = None
    note: str | None = None

    @property
    def crosses_zero(self) -> bool:
        return (self.ci_low is None or self.ci_high is None
                or self.ci_low <= 0 <= self.ci_high)

    def describe(self) -> str:
        if self.r is None:
            return self.note or "not enough overlapping days"
        lag = f" (lagged {self.lag_days}d)" if self.lag_days else ""
        verdict = "consistent with nothing" if self.crosses_zero else "holds up"
        return (f"r={self.r:+.2f} [{self.ci_low:+.2f}, {self.ci_high:+.2f}]{lag}, "
                f"n={self.n} days (~{self.effective_n} independent) — {verdict}")


def series(store: Store, metric: str, start: date | None = None,
           end: date | None = None) -> list[tuple[date, float]]:
    return store.query(
        """
        SELECT local_date, value FROM daily_metrics
        WHERE metric = ?
          AND (? IS NULL OR local_date >= ?)
          AND (? IS NULL OR local_date <= ?)
        ORDER BY local_date
        """,
        [metric, start, start, end, end],
    )


def baseline(store: Store, metric: str, as_of: date | None = None,
             window: int = BASELINE_WINDOW) -> Baseline:
    """Robust baseline over the `window` days before `as_of` (exclusive)."""
    as_of = as_of or date.today()
    # The day being judged is deliberately outside its own baseline: including
    # it pulls the median toward the very reading we are asking about, and
    # shrinks the deviation it is supposed to reveal.
    values = [v for _, v in series(store, metric,
                                   as_of - timedelta(days=window),
                                   as_of - timedelta(days=1))
              if v is not None]
    result = Baseline(metric=metric, window=window, n=len(values))
    if len(values) < MIN_BASELINE_POINTS:
        result.note = (f"{len(values)} day(s) in the last {window}; "
                       f"need {MIN_BASELINE_POINTS} for a baseline")
        return result

    result.median = round(statistics.median(values), 3)
    deviations = [abs(v - result.median) for v in values]
    result.mad = round(statistics.median(deviations), 4)
    if not result.mad:
        result.note = f"{metric} has not varied in this window"
    return result


def deviations(store: Store, day: date, window: int = BASELINE_WINDOW,
               threshold: float = 1.5) -> list[tuple[str, float, float]]:
    """Metrics sitting unusually far from their own recent baseline.

    Returns (metric, value, z) for anything past `threshold`, most extreme
    first. This is a shortlist to look at, not a list of problems.
    """
    today = store.query(
        "SELECT metric, value FROM daily_metrics WHERE local_date = ?", [day])
    out = []
    for metric, value in today:
        z = baseline(store, metric, as_of=day, window=window).z(value)
        if z is not None and abs(z) >= threshold:
            out.append((metric, value, z))
    return sorted(out, key=lambda row: -abs(row[2]))


@dataclass
class TrainingLoad:
    acute: float
    chronic: float
    days: int

    @property
    def ratio(self) -> float | None:
        """Acute:chronic workload. Widely used, weakly evidenced — a ramp
        indicator, not an injury predictor, whatever its reputation."""
        return round(self.acute / self.chronic, 2) if self.chronic else None

    def describe(self) -> str:
        if self.ratio is None:
            return "no chronic load to compare against yet"
        if self.ratio > 1.3:
            shape = "ramping up"
        elif self.ratio < 0.8:
            shape = "backing off"
        else:
            shape = "steady"
        return (f"{shape}: last 7 days averaging {self.acute:.0f} against a "
                f"28-day average of {self.chronic:.0f} (ratio {self.ratio})")


def training_load(store: Store, as_of: date | None = None,
                  acute: int = 7, chronic: int = 28) -> TrainingLoad:
    """Load from WHOOP strain and Hevy tonnage, on one scale.

    Tonnage is divided by 1000 so that a heavy lifting session and a hard
    conditioning session contribute at comparable magnitudes; the number is an
    index for tracking your own ramp, not a physiological quantity.
    """
    as_of = as_of or date.today()

    def window_mean(days: int) -> float:
        rows = store.query(
            """
            SELECT COALESCE(SUM(strain), 0) + COALESCE(SUM(tonnage), 0) / 1000.0
            FROM (
                SELECT local_date, value AS strain, NULL AS tonnage
                FROM daily_metrics WHERE metric = 'strain'
                UNION ALL
                SELECT local_date, NULL, volume_kg FROM working_sets
            )
            WHERE local_date > ? AND local_date <= ?
            """,
            [as_of - timedelta(days=days), as_of],
        )
        total = rows[0][0] if rows and rows[0][0] is not None else 0.0
        return total / days

    return TrainingLoad(acute=round(window_mean(acute), 2),
                        chronic=round(window_mean(chronic), 2), days=chronic)


def sleep_regularity(store: Store, days: int = 28,
                     as_of: date | None = None) -> dict:
    """Variability in when you sleep, which predicts more than how long."""
    as_of = as_of or date.today()
    rows = store.query(
        """
        SELECT local_date,
               epoch(end_ts - start_ts) / 3600.0 AS hours,
               hour(start_ts) + minute(start_ts) / 60.0 AS onset_hour
        FROM sleeps
        WHERE COALESCE(is_nap, false) = false
          AND local_date > ? AND local_date <= ?
        ORDER BY local_date
        """,
        [as_of - timedelta(days=days), as_of],
    )
    if len(rows) < 5:
        return {"nights": len(rows), "note": "not enough nights yet"}

    # Onsets straddle midnight, so centre them before taking a spread.
    onsets = [(h + 12) % 24 for _, _, h in rows]
    return {
        "nights": len(rows),
        "onset_sd_hours": round(statistics.pstdev(onsets), 2),
        "duration_sd_hours": round(statistics.pstdev([h for _, h, _ in rows]), 2),
        "mean_duration_hours": round(statistics.mean([h for _, h, _ in rows]), 2),
    }


def _pearson(xs: list[float], ys: list[float]) -> float | None:
    n = len(xs)
    if n < 3:
        return None
    mx, my = statistics.mean(xs), statistics.mean(ys)
    cov = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    vx = sum((x - mx) ** 2 for x in xs)
    vy = sum((y - my) ** 2 for y in ys)
    if vx <= 0 or vy <= 0:
        return None
    return cov / math.sqrt(vx * vy)


def _lag1(values: list[float]) -> float:
    """How much each day resembles the one before it."""
    if len(values) < 3:
        return 0.0
    return _pearson(values[:-1], values[1:]) or 0.0


def _align(left: dict[date, float], right: dict[date, float],
           lag_days: int) -> list[tuple[float, float]]:
    """Pair `left` with `right` `lag_days` later, on the days both have."""
    pairs = []
    for day, value in sorted(left.items()):
        target = day + timedelta(days=lag_days)
        if target in right and value is not None and right[target] is not None:
            pairs.append((value, right[target]))
    return pairs


def correlate_series(a: str, left: dict[date, float], b: str,
                     right: dict[date, float], lag_days: int = 0) -> Correlation:
    """`correlate` over series you have already built, rather than metric names.

    For inputs that are not columns in `daily_metrics` — strength tonnage, the
    hour of the last workout — so the same autocorrelation-corrected interval
    covers them too.
    """
    return _correlate(a, b, lag_days, _align(left, right, lag_days))


def correlate(store: Store, a: str, b: str, lag_days: int = 0,
              start: date | None = None, end: date | None = None) -> Correlation:
    """Relationship between two daily metrics, with honest uncertainty.

    `lag_days` shifts `a` earlier: lag 1 asks whether yesterday's `a` moves
    today's `b`. A correlation here is a hypothesis worth testing deliberately,
    never a mechanism — with forty metrics there are hundreds of pairs, and
    some will look convincing by chance alone.
    """
    left = dict(series(store, a, start, end))
    right = dict(series(store, b, start, end))
    return _correlate(a, b, lag_days, _align(left, right, lag_days))


def _correlate(a: str, b: str, lag_days: int,
               pairs: list[tuple[float, float]]) -> Correlation:
    result = Correlation(a=a, b=b, lag_days=lag_days, n=len(pairs),
                         effective_n=len(pairs))
    if len(pairs) < MIN_CORRELATION_POINTS:
        result.note = (f"{len(pairs)} overlapping day(s); need "
                       f"{MIN_CORRELATION_POINTS} before this means anything")
        return result

    xs = [p[0] for p in pairs]
    ys = [p[1] for p in pairs]
    r = _pearson(xs, ys)
    if r is None:
        result.note = "one of these metrics does not vary over this period"
        return result
    result.r = round(r, 3)

    # Both series are autocorrelated, so the pair contains fewer independent
    # observations than it has days. Quenouille's adjustment.
    ra, rb = _lag1(xs), _lag1(ys)
    factor = (1 - ra * rb) / (1 + ra * rb) if (1 + ra * rb) else 1.0
    effective = max(3, min(len(pairs), int(round(len(pairs) * factor))))
    result.effective_n = effective

    if abs(r) < 0.999 and effective > 3:
        # Fisher z, using the effective sample size rather than the raw one.
        z = 0.5 * math.log((1 + r) / (1 - r))
        se = 1 / math.sqrt(effective - 3)
        lo, hi = z - 1.96 * se, z + 1.96 * se
        result.ci_low = round(math.tanh(lo), 3)
        result.ci_high = round(math.tanh(hi), 3)
    return result


def scan(store: Store, target: str, candidates: list[str] | None = None,
         lag_days: int = 1, start: date | None = None,
         end: date | None = None) -> list[Correlation]:
    """Every metric against one target, ordered by strength.

    Explicitly a hypothesis generator. Testing many pairs at once guarantees
    some will clear any threshold by chance, so results are returned with their
    intervals and no p-values, and the count of comparisons is on the record.
    """
    if candidates is None:
        candidates = [r[0] for r in store.query(
            "SELECT DISTINCT metric FROM daily_metrics WHERE metric != ?", [target])]

    out = [correlate(store, metric, target, lag_days=lag_days, start=start, end=end)
           for metric in sorted(candidates)]
    scored = [c for c in out if c.r is not None]
    scored.sort(key=lambda c: -abs(c.r))
    for correlation in scored:
        correlation.note = (f"one of {len(scored)} comparisons — expect a few "
                            f"to look striking by chance")
    return scored
