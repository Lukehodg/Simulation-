"""Cross-domain signals — the part that is not on the watch.

Each of the four apps answers a question inside its own lane: WHOOP scores your
recovery, Hevy tracks your lifts, neither looks at the other. The functions
here read across the lanes:

  * `sleep_debt`      — how much sleep you actually owe, against a need
                        estimated from your own good days rather than a
                        population number.
  * `readiness`       — one call on today's training, from recovery, load,
                        sleep debt and cycle phase together. A recommendation
                        with its reasons attached, never a 0-100 score.
  * `recovery_drivers`— which of your own behaviours actually move your HRV and
                        resting heart rate, with the same autocorrelation-
                        corrected intervals `daily.correlate` uses.
  * `strength_recovery_link` — whether your best sessions land on your best-
                        recovered days, and whether training through a red day
                        costs you.
  * `phase_training_plan` — where the heavy blocks and the deloads should fall
                        across the cycle, checked against your own phase
                        signature rather than the textbook.

Nothing here talks to the network, and nothing here is combined into a single
number: the reasons are the output.
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass, field
from datetime import date, timedelta

from .. import metrics as M
from ..store import Store
from . import cycle as cycle_features
from . import daily
from . import strength as strength_features

#: A night this far short of need still only counts as this much debt — one
#: long lie-in should not wipe out a week of deficit, and cannot bank credit.
MAX_NIGHTLY_DEBT_MIN = 90.0
MIN_NIGHTLY_DEBT_MIN = -30.0
#: Fallback sleep need when there is not enough of the user's own data.
DEFAULT_NEED_MIN = 450.0
#: Paired sleep/recovery nights needed before need is estimated from them.
MIN_NIGHTS_FOR_NEED = 15
#: A robust z this far in the bad direction is "adverse" for readiness.
ADVERSE_Z = 1.5
#: ...and this close to baseline (either side) is "clean".
CLEAN_Z = 0.5

NO_SCORE = (
    "This is a recommendation and the reasons behind it, not a number. "
    "Readiness scores collapse independent signals — an autonomic reading, a "
    "training ratio, a sleep deficit — into one figure whose movements cannot "
    "be traced back. The parts are more useful kept separate."
)

#: Recovery metrics and the direction that counts as adverse (-1: low is bad).
_RECOVERY_SIGNALS = (
    (M.HRV_RMSSD, "HRV", -1),
    (M.RESTING_HR, "resting HR", +1),
    (M.RECOVERY_SCORE, "recovery score", -1),
)


# --------------------------------------------------------------------------
# sleep debt
# --------------------------------------------------------------------------

@dataclass
class SleepDebt:
    need_min: float
    nights: int
    debt_min: float = 0.0
    last_night_min: float | None = None
    basis: str = ""
    note: str | None = None

    @property
    def need_hours(self) -> float:
        return round(self.need_min / 60, 2)

    @property
    def debt_hours(self) -> float:
        return round(self.debt_min / 60, 2)

    @property
    def last_night_hours(self) -> float | None:
        return round(self.last_night_min / 60, 2) if self.last_night_min else None

    def as_dict(self) -> dict:
        return {"need_hours": self.need_hours, "debt_hours": self.debt_hours,
                "last_night_hours": self.last_night_hours, "nights": self.nights,
                "basis": self.basis, "note": self.note}


def _sleep_need(store: Store, as_of: date) -> tuple[float, str, str | None]:
    """Sleep need in minutes, from the nights that preceded the user's own
    better-recovered days — falling back to a fixed default with a note."""
    rows = store.query(
        """
        SELECT s.value AS sleep_min, r.value AS recovery
        FROM daily_metrics s
        JOIN daily_metrics r ON r.local_date = s.local_date
        WHERE s.metric = ? AND r.metric = ? AND s.local_date <= ?
        """,
        [M.SLEEP_DURATION, M.RECOVERY_SCORE, as_of],
    )
    if len(rows) < MIN_NIGHTS_FOR_NEED:
        # Try HRV as the "good day" marker where recovery score is absent.
        rows = store.query(
            """
            SELECT s.value AS sleep_min, h.value AS marker
            FROM daily_metrics s
            JOIN daily_metrics h ON h.local_date = s.local_date
            WHERE s.metric = ? AND h.metric = ? AND s.local_date <= ?
            """,
            [M.SLEEP_DURATION, M.HRV_RMSSD, as_of],
        )
        marker = "HRV"
    else:
        marker = "recovery score"

    usable = [(sleep, mark) for sleep, mark in rows
              if sleep is not None and mark is not None]
    if len(usable) < MIN_NIGHTS_FOR_NEED:
        return (DEFAULT_NEED_MIN, f"default {DEFAULT_NEED_MIN / 60:g} h",
                f"only {len(usable)} night(s) with both sleep and a recovery "
                f"marker; need {MIN_NIGHTS_FOR_NEED} to estimate your own")

    cutoff = statistics.quantiles([m for _, m in usable], n=3)[1]  # top tertile
    good_nights = [sleep for sleep, mark in usable if mark >= cutoff]
    if len(good_nights) < 5:
        return (DEFAULT_NEED_MIN, f"default {DEFAULT_NEED_MIN / 60:g} h",
                "not enough well-recovered days to read a need off yet")
    return (round(statistics.median(good_nights), 1),
            f"median sleep on your top-third {marker} days (n={len(good_nights)})",
            None)


def sleep_debt(store: Store, as_of: date | None = None,
               days: int = 14) -> SleepDebt:
    """Rolling sleep debt over the last `days` nights, against personal need."""
    as_of = as_of or date.today()
    need, basis, note = _sleep_need(store, as_of)

    nights = [(d, v) for d, v in daily.series(
        store, M.SLEEP_DURATION, as_of - timedelta(days=days - 1), as_of)
        if v is not None]

    result = SleepDebt(need_min=need, nights=len(nights), basis=basis, note=note)
    if not nights:
        result.note = "no sleep recorded in this window"
        return result

    result.last_night_min = nights[-1][1]
    result.debt_min = round(sum(
        min(MAX_NIGHTLY_DEBT_MIN, max(MIN_NIGHTLY_DEBT_MIN, need - v))
        for _, v in nights
    ), 1)
    return result


# --------------------------------------------------------------------------
# readiness to train
# --------------------------------------------------------------------------

PUSH, PROCEED, HOLD, PULL_BACK = "push", "proceed", "hold", "pull_back"

_ADVICE = {
    PUSH: "green light — a hard session or a PR attempt is well supported today",
    PROCEED: "train as planned; nothing in the data argues against it",
    HOLD: "keep it moderate — hold volume and intensity where they are rather "
          "than adding, and reassess tomorrow",
    PULL_BACK: "back off today — technique work, a walk, or a rest day; the "
               "signals that argue for pushing are not there",
}


@dataclass
class Readiness:
    day: date
    recommendation: str
    reasons: list[str] = field(default_factory=list)
    caveats: list[str] = field(default_factory=list)
    phase_context: str | None = None

    def as_dict(self) -> dict:
        return {
            "date": str(self.day),
            "recommendation": self.recommendation,
            "advice": _ADVICE[self.recommendation],
            "reasons": self.reasons,
            "caveats": self.caveats,
            "phase_context": self.phase_context,
            "no_score": NO_SCORE,
        }


def _recovery_signal(store: Store, metric: str, label: str, bad: int,
                     day: date, cycle_known: bool):
    """(adverse_magnitude, reason, caveat) for one recovery metric on `day`.

    adverse_magnitude is in robust-SD units, positive when the reading sits in
    the direction that argues against training hard.
    """
    row = store.query(
        "SELECT value FROM daily_metrics WHERE metric = ? AND local_date = ?",
        [metric, day])
    if not row or row[0][0] is None:
        return None, None, f"no {label} for {day}"
    value = row[0][0]

    z = None
    same_phase = False
    if cycle_known:
        reading = cycle_features.phase_adjusted(store, metric, day)
        if reading.phase_z is not None:
            z, same_phase = reading.phase_z, True
        elif reading.overall_z is not None:
            z = reading.overall_z
    if z is None:
        base = daily.baseline(store, metric, as_of=day)
        if not base.usable:
            return None, None, (f"{label} {value:.4g} — {base.note or 'no baseline yet'}")
        z = base.z(value)
    if z is None:
        return None, None, f"{label} {value:.4g} — no baseline yet"

    adverse = z * bad
    against = "same-phase" if same_phase else "28-day"
    reason = f"{label} {value:.4g}, {z:+.1f} SD vs your {against} baseline"
    return adverse, reason, None


def readiness(store: Store, day: date | None = None) -> Readiness:
    """One call on today's training, from recovery, load, sleep and cycle."""
    day = day or date.today()
    cycle_known = bool(cycle_features.summary(store, today=day).get("cycles"))

    adverse_mags: list[float] = []
    reasons: list[str] = []
    caveats: list[str] = []
    hrv_adverse = None

    for metric, label, bad in _RECOVERY_SIGNALS:
        mag, reason, caveat = _recovery_signal(store, metric, label, bad, day,
                                               cycle_known)
        if caveat:
            caveats.append(caveat)
        if mag is None:
            continue
        adverse_mags.append(mag)
        if metric == M.HRV_RMSSD:
            hrv_adverse = mag
        if mag >= CLEAN_Z or mag <= -CLEAN_Z:
            reasons.append(reason + (
                " — adverse" if mag >= ADVERSE_Z else
                " — mildly adverse" if mag >= CLEAN_Z else " — favourable"))

    adverse_signals = sum(1 for m in adverse_mags if m >= ADVERSE_Z)

    load = daily.training_load(store, as_of=day)
    if load.ratio is None:
        caveats.append("no training load history to compare against yet")
    else:
        reasons.append(f"acute:chronic load {load.ratio} ({load.describe()})")

    debt = sleep_debt(store, as_of=day)
    if debt.note and "default" in debt.basis:
        caveats.append(f"sleep need is a {debt.basis} — {debt.note}")
    reasons.append(f"sleep debt {debt.debt_hours:+.1f} h over {debt.nights} "
                   f"nights (need {debt.need_hours} h, {debt.basis})")

    ratio = load.ratio or 1.0
    if (adverse_signals >= 2 or ratio > 1.5
            or (adverse_signals >= 1 and debt.debt_hours > 3)):
        rec = PULL_BACK
    elif adverse_signals >= 1 or ratio > 1.3 or debt.debt_hours > 4:
        rec = HOLD
    elif (len(adverse_mags) == len(_RECOVERY_SIGNALS)
          and all(m < CLEAN_Z for m in adverse_mags)
          and 0.8 <= ratio <= 1.3 and debt.debt_hours < 1):
        rec = PUSH
    else:
        rec = PROCEED

    result = Readiness(day=day, recommendation=rec, reasons=reasons,
                       caveats=caveats)

    if cycle_known and hrv_adverse is not None and hrv_adverse >= CLEAN_Z:
        reading = cycle_features.phase_adjusted(store, M.HRV_RMSSD, day)
        if reading.phase == cycle_features.LUTEAL:
            result.phase_context = (
                "you are luteal, where HRV usually runs lower — some of this is "
                "the cycle rather than fatigue"
                + (", and against the same phase of your own cycles it is "
                   "ordinary" if reading.verdicts_disagree else ""))
        elif reading.phase in (cycle_features.FOLLICULAR, cycle_features.MENSES):
            result.phase_context = (
                f"you are {reading.phase}, where HRV usually runs higher, so "
                "this reading is not cycle-driven")
    return result


# --------------------------------------------------------------------------
# what actually moves your recovery
# --------------------------------------------------------------------------

#: Behaviours worth testing against recovery. Anything absent from the data is
#: skipped rather than reported as "no effect".
_DRIVER_METRICS = (
    M.STRAIN, M.STEPS, M.SLEEP_EFFICIENCY, M.RESPIRATORY_RATE,
    M.SKIN_TEMP_DEV, M.ENERGY_INTAKE, M.PROTEIN, M.ALCOHOL, M.CAFFEINE,
)
_SAME_NIGHT = {M.ALCOHOL, M.CAFFEINE, M.ENERGY_INTAKE}


def _tonnage_series(store: Store, start: date, end: date) -> dict[date, float]:
    rows = store.query(
        "SELECT local_date, SUM(volume_kg) FROM working_sets "
        "WHERE local_date BETWEEN ? AND ? GROUP BY local_date",
        [start, end])
    return {d: v for d, v in rows if v is not None}


def _last_workout_hour_series(store: Store, start: date,
                              end: date) -> dict[date, float]:
    rows = store.query(
        "SELECT local_date, MAX(hour(start_ts) + minute(start_ts) / 60.0) "
        "FROM workouts WHERE local_date BETWEEN ? AND ? GROUP BY local_date",
        [start, end])
    return {d: v for d, v in rows if v is not None}


def recovery_drivers(store: Store, targets: tuple[str, ...] = (M.HRV_RMSSD,
                     M.RESTING_HR), days: int = 90, end: date | None = None) -> dict:
    """Which behaviours actually move the targets, over the last `days`.

    A hypothesis generator, deliberately: many pairs are tested at once, the
    intervals are corrected for autocorrelation, and only relationships whose
    interval clears zero are returned — with the comparison count on the record
    and a pointer to testing one properly rather than acting on it.
    """
    end = end or date.today()
    start = end - timedelta(days=days)

    inputs: dict[str, tuple[dict[date, float], int]] = {}
    for metric in _DRIVER_METRICS:
        pts = dict(daily.series(store, metric, start, end))
        if len(pts) >= daily.MIN_CORRELATION_POINTS:
            inputs[metric] = (pts, 0 if metric in _SAME_NIGHT else 1)
    tonnage = _tonnage_series(store, start, end)
    if len(tonnage) >= daily.MIN_CORRELATION_POINTS:
        inputs["strength_tonnage"] = (tonnage, 1)
    workout_hour = _last_workout_hour_series(store, start, end)
    if len(workout_hour) >= daily.MIN_CORRELATION_POINTS:
        inputs["last_workout_hour"] = (workout_hour, 1)

    findings: list[dict] = []
    comparisons = 0
    for target in targets:
        right = dict(daily.series(store, target, start, end))
        if len(right) < daily.MIN_CORRELATION_POINTS:
            continue
        for name, (left, lag) in inputs.items():
            comparisons += 1
            corr = daily.correlate_series(name, left, target, right, lag_days=lag)
            if corr.r is None or corr.crosses_zero:
                continue
            findings.append({
                "input": name, "recovery_metric": target,
                "lag_days": lag, "r": corr.r,
                "ci": [corr.ci_low, corr.ci_high],
                "n_days": corr.n, "effective_n": corr.effective_n,
                "reading": corr.describe(),
            })

    findings.sort(key=lambda f: -abs(f["r"]))
    return {
        "window_days": days,
        "comparisons_made": comparisons,
        "survivors": findings,
        "caveat": (f"{comparisons} pairs were tested; with that many, one or two "
                   "will clear an interval by chance. Treat each as a "
                   "hypothesis."),
        "next_step": ("to test one: pick the input, alternate two-week blocks "
                      "of high and low, and compare the target between blocks — "
                      "state the metric first so the result is not chosen after "
                      "the fact"),
    }


# --------------------------------------------------------------------------
# strength against recovery
# --------------------------------------------------------------------------

#: An exercise needs this many dated sessions before the buckets mean anything
#: — the same floor the rest of the training layer uses for a verdict.
MIN_SESSIONS_FOR_LINK = 6
#: WHOOP-style recovery bands.
_GREEN, _RED = 67.0, 34.0


def _recovery_bucket(store: Store, day: date) -> str | None:
    row = store.query(
        "SELECT value FROM daily_metrics WHERE metric = ? AND local_date = ?",
        [M.RECOVERY_SCORE, day])
    if row and row[0][0] is not None:
        score = row[0][0]
        return "green" if score >= _GREEN else "red" if score < _RED else "amber"
    base = daily.baseline(store, M.HRV_RMSSD, as_of=day)
    hrv = store.query(
        "SELECT value FROM daily_metrics WHERE metric = ? AND local_date = ?",
        [M.HRV_RMSSD, day])
    if base.usable and hrv and hrv[0][0] is not None:
        z = base.z(hrv[0][0])
        return "green" if z >= CLEAN_Z else "red" if z <= -CLEAN_Z else "amber"
    return None


def _linear_fit(points: list[tuple[int, float]]) -> tuple[float, float] | None:
    """(intercept, slope_per_day) least-squares, or None if degenerate."""
    n = len(points)
    if n < 3:
        return None
    mx = sum(x for x, _ in points) / n
    my = sum(y for _, y in points) / n
    sxx = sum((x - mx) ** 2 for x, _ in points)
    if sxx == 0:
        return None
    slope = sum((x - mx) * (y - my) for x, y in points) / sxx
    return my - slope * mx, slope


def strength_recovery_link(store: Store, days: int = 180,
                           end: date | None = None) -> dict:
    """Do the good sessions land on the good days, and does a red day cost you?"""
    end = end or date.today()
    start = end - timedelta(days=days)
    need, _, _ = _sleep_need(store, end)

    summary = strength_features.exercise_summary(store, since=start)
    findings: list[dict] = []
    lows_on_red = lows_total = 0

    for row in summary:
        if row["sessions"] < MIN_SESSIONS_FOR_LINK:
            continue
        history = sorted(strength_features.session_history(
            store, row["exercise"], limit=200))
        history = [(d, e) for d, e, *_ in history
                   if e is not None and start <= d <= end]
        if len(history) < MIN_SESSIONS_FOR_LINK:
            continue

        origin = history[0][0]
        fit = _linear_fit([((d - origin).days, e) for d, e in history])
        if not fit:
            continue
        intercept, slope = fit
        running_best = 0.0

        buckets: dict[str, list[float]] = {"green": [], "amber": [], "red": []}
        prs = {"green": 0, "amber": 0, "red": 0}
        residuals = []
        for d, e in history:
            predicted = intercept + slope * (d - origin).days
            resid = e - predicted
            residuals.append(resid)
            is_pr = e > running_best
            running_best = max(running_best, e)
            bucket = _recovery_bucket(store, d)
            if bucket:
                buckets[bucket].append(resid)
                if is_pr:
                    prs[bucket] += 1

        if not residuals:
            continue
        spread = statistics.pstdev(residuals) if len(residuals) > 1 else 0.0
        for d, e in history:
            predicted = intercept + slope * (d - origin).days
            if e - predicted < -max(spread, 1.0):
                lows_total += 1
                sleep = store.query(
                    "SELECT value FROM daily_metrics WHERE metric = ? AND local_date = ?",
                    [M.SLEEP_DURATION, d])
                under_slept = bool(sleep and sleep[0][0] is not None
                                   and sleep[0][0] < need)
                if _recovery_bucket(store, d) == "red" or under_slept:
                    lows_on_red += 1

        graded = {b: v for b, v in buckets.items() if v}
        if len(graded) < 2:
            continue
        findings.append({
            "exercise": row["exercise"],
            "sessions_graded": sum(len(v) for v in graded.values()),
            "by_recovery": {
                b: {"n": len(v), "mean_e1rm_vs_trend_kg": round(statistics.mean(v), 2)}
                for b, v in graded.items()},
            "prs_by_recovery": {b: prs[b] for b in graded},
        })

    return {
        "window_days": days,
        "exercises": findings,
        "session_lows": {
            "total": lows_total,
            "on_a_red_or_under_slept_day": lows_on_red,
            "note": ("a session low is an estimated 1RM more than one standard "
                     "deviation below that lift's own trend line"),
        },
        "note": ("e1RM-vs-trend is the residual from each lift's own fitted "
                 "progression, so a positive green number means you beat your "
                 "trend on well-recovered days. Read the n on every bucket."),
    }


# --------------------------------------------------------------------------
# phase-aware training plan
# --------------------------------------------------------------------------

_PHASE_EMPHASIS = {
    cycle_features.MENSES: "technique and easy volume; treat it as a light week",
    cycle_features.FOLLICULAR: "the window for heavy blocks and PR attempts — "
                               "rising oestrogen favours strength and recovery",
    cycle_features.OVULATION: "strength holds up; watch joint laxity on maximal "
                              "singles",
    cycle_features.LUTEAL: "productive for moderate volume early, then taper — "
                           "expect higher resting HR and perceived effort late",
    cycle_features.UNKNOWN: "not enough cycle history to place this window",
}


def _projected_phase(day_in_cycle: int, length: int, menses_days: int) -> str:
    """Phase of a day `day_in_cycle` (1-based) into a cycle of `length` days,
    projecting forward from the anchors `cycle.phase_for` uses."""
    if day_in_cycle <= menses_days:
        return cycle_features.MENSES
    # Ovulation is anchored to the next period: luteal length is stable, the
    # follicular phase is not. Matches `cycle.phase_for`'s 1-based day.
    ovulation = length - cycle_features.LUTEAL_LENGTH + 1
    if day_in_cycle < ovulation - cycle_features.OVULATION_WINDOW:
        return cycle_features.FOLLICULAR
    if day_in_cycle <= ovulation + cycle_features.OVULATION_WINDOW:
        return cycle_features.OVULATION
    return cycle_features.LUTEAL


def phase_training_plan(store: Store, weeks: int = 4,
                        today: date | None = None) -> dict:
    """Where the heavy blocks and deloads should fall across the next `weeks`."""
    today = today or date.today()
    summary = cycle_features.summary(store, today=today)
    if not summary.get("cycles"):
        return {
            "available": False,
            "note": ("no period logs found, so training cannot be planned "
                     "against cycle phase. This activates once roughly two "
                     "cycles are logged — see the README on getting cycle data "
                     "in from Apple Health."),
        }

    length = summary.get("median_length")
    cycle_list = cycle_features.cycles(store)
    current = cycle_list[-1]
    if not length:
        return {
            "available": False,
            "note": ("no completed cycle yet, so the next period cannot be "
                     "estimated and the phases past today cannot be placed"),
        }
    menses_days = max(1, (current.menses_end - current.start).days + 1)

    # Check the plan against the user's own physiology before giving it.
    signature_notes = []
    for metric in (M.HRV_RMSSD, M.RESTING_HR):
        sig = cycle_features.phase_signature(store, metric)
        if sig.get("expected") and sig.get("agrees") is False:
            signature_notes.append(
                f"your {metric} does not shift the textbook way between phases "
                f"(measured {sig['delta']:+.1f}, expected {sig['expected']}), so "
                f"treat the emphasis below as generic rather than yours")

    windows = []
    day = today
    for _ in range(weeks):
        end = day + timedelta(days=6)
        # Day of the week's midpoint within its (possibly projected) cycle.
        elapsed = (day + timedelta(days=3) - current.start).days
        day_in_cycle = elapsed % length + 1
        phase = _projected_phase(day_in_cycle, length, menses_days)
        windows.append({
            "from": str(day), "to": str(end),
            "phase": phase,
            "cycle_day_midweek": day_in_cycle,
            "projected_into_next_cycle": elapsed >= length,
            "emphasis": _PHASE_EMPHASIS.get(phase, ""),
        })
        day = end + timedelta(days=1)

    return {
        "available": True,
        "median_cycle_length": length,
        "current_cycle_start": str(current.start),
        "next_period_estimate": (str(summary["next_period_estimate"])
                                 if summary.get("next_period_estimate") else None),
        "windows": windows,
        "checked_against_your_data": signature_notes or [
            "your HRV and resting-HR phase shifts match the expected direction"],
        "caveat": ("phase windows are estimated from your median cycle length "
                   "and move if this cycle runs long or short"),
    }
