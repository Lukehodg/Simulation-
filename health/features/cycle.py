"""The menstrual cycle as an axis of analysis.

None of Garmin, WHOOP or MyFitnessPal condition their scores on where you are
in your cycle, which is why a recovery score that is entirely normal for day 23
still reads as alarming. Everything here exists to fix that: reconstruct the
cycles from your logs, place each day in a phase, and compare today against the
same phase of your own previous cycles rather than against your yearly average.

Two pieces of physiology do the work. Ovulation is estimated by counting
*backwards* from the next period rather than forwards from the last, because
the luteal phase is far more stable in length than the follicular phase — a
"day 14" assumption is wrong for most cycles and wrong in a way that shifts
with cycle length. And the luteal phase raises resting heart rate and skin
temperature while lowering HRV, all of which you already measure on two
devices, so the calendar's estimate can be checked against the body rather
than trusted blindly.

Nothing here diagnoses anything. It reports what your data says, with how much
data it says it from.
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass
from datetime import date, timedelta

from ..store import Store

#: Days between ovulation and the next period. Stable across cycle lengths in
#: a way the follicular phase is not, which is why we anchor to it.
LUTEAL_LENGTH = 14
#: Ovulation is a day, but no estimate of it is; treat a 3-day window as one.
OVULATION_WINDOW = 1
#: A flow day this long after the previous one starts a new cycle rather than
#: continuing it. Shorter than any plausible cycle, longer than any gap within
#: a period.
NEW_CYCLE_GAP_DAYS = 10
#: Below this many same-phase observations, a phase baseline is not a baseline.
MIN_PHASE_SAMPLES = 5
#: How far from a baseline a reading has to sit before it is worth remarking on.
#: One standard deviation, deliberately: the interesting case is not the freak
#: day but the ordinary luteal one that a phase-blind baseline calls a problem.
NOTABLE_Z = 1.0
#: Cycle lengths outside this range are reported as unusual rather than used
#: to predict the next period.
PLAUSIBLE_LENGTH = range(21, 41)

MENSES, FOLLICULAR, OVULATION, LUTEAL = "menses", "follicular", "ovulation", "luteal"
#: Days we genuinely cannot place — the tail of a first cycle, with no
#: completed cycle to estimate its length from. They get their own label rather
#: than being filed under follicular, because a guess quietly mixed into a
#: baseline is worse than an admission.
UNKNOWN = "unknown"
PHASES = (MENSES, FOLLICULAR, OVULATION, LUTEAL)

#: What the luteal phase is expected to do to each metric, relative to the
#: follicular phase. Used only to report agreement, never to overrule your logs.
LUTEAL_EXPECTATION = {
    "resting_hr": "higher",
    "hrv_rmssd": "lower",
    "hrv_sdnn": "lower",
    "skin_temp_deviation": "higher",
    "wrist_temp": "higher",
    "respiratory_rate": "higher",
}


@dataclass
class Cycle:
    index: int
    start: date
    end: date | None            # last day before the next period started
    menses_end: date            # last consecutive day of bleeding
    length: int | None          # None while it is still running
    is_current: bool = False

    @property
    def ovulation(self) -> date | None:
        """Estimated ovulation day, counted back from the next period."""
        if self.end is None:
            return None
        return self.end + timedelta(days=1) - timedelta(days=LUTEAL_LENGTH)


def period_starts(store: Store) -> list[date]:
    """First day of each period, from explicit logs or from gaps in flow."""
    explicit = [r[0] for r in store.query(
        "SELECT DISTINCT local_date FROM cycle_events "
        "WHERE event = 'period_start' ORDER BY local_date"
    )]
    flow_days = [r[0] for r in store.query(
        "SELECT DISTINCT local_date FROM cycle_events "
        "WHERE event IN ('flow', 'period_start') ORDER BY local_date"
    )]
    if not flow_days:
        return []

    inferred: list[date] = []
    previous: date | None = None
    for day in flow_days:
        if previous is None or (day - previous).days >= NEW_CYCLE_GAP_DAYS:
            inferred.append(day)
        previous = day

    # Explicit logs win where they exist; inferred starts fill the gaps for
    # cycles logged as bare flow days with no cycle_start flag.
    starts = sorted(set(explicit) | {
        day for day in inferred
        if not any(abs((day - e).days) < NEW_CYCLE_GAP_DAYS for e in explicit)
    })
    return starts


def _menses_end(store: Store, start: date, limit: date | None) -> date:
    days = [r[0] for r in store.query(
        "SELECT DISTINCT local_date FROM cycle_events "
        "WHERE event IN ('flow', 'period_start') AND local_date >= ? "
        "ORDER BY local_date", [start]
    )]
    end = start
    for day in days:
        if limit and day >= limit:
            break
        if (day - end).days <= 1:
            end = day
        else:
            break
    return end


def cycles(store: Store) -> list[Cycle]:
    """Every cycle we can reconstruct, oldest first."""
    starts = period_starts(store)
    out: list[Cycle] = []
    for index, start in enumerate(starts):
        next_start = starts[index + 1] if index + 1 < len(starts) else None
        end = next_start - timedelta(days=1) if next_start else None
        out.append(Cycle(
            index=index, start=start, end=end,
            menses_end=_menses_end(store, start, next_start),
            length=(end - start).days + 1 if end else None,
            is_current=next_start is None,
        ))
    return out


def median_length(cycle_list: list[Cycle]) -> int | None:
    """Median of plausible completed cycles. A 60-day gap is a missed log, not
    a 60-day cycle, and letting it into the median would move every prediction."""
    lengths = [c.length for c in cycle_list
               if c.length and c.length in PLAUSIBLE_LENGTH]
    return round(statistics.median(lengths)) if lengths else None


def phase_for(cycle: Cycle, day: date, expected_length: int | None) -> tuple[str, bool]:
    """Which phase a day falls in, and whether that rests on a prediction."""
    predicted = False
    if day <= cycle.menses_end:
        return MENSES, predicted

    end = cycle.end
    if end is None:                      # the cycle is still running
        predicted = True
        if not expected_length:
            return UNKNOWN, predicted  # no completed cycle to anchor an estimate to
        end = cycle.start + timedelta(days=expected_length - 1)

    ovulation = end + timedelta(days=1) - timedelta(days=LUTEAL_LENGTH)
    if day < ovulation - timedelta(days=OVULATION_WINDOW):
        return FOLLICULAR, predicted
    if day <= ovulation + timedelta(days=OVULATION_WINDOW):
        return OVULATION, predicted
    return LUTEAL, predicted


def rebuild(store: Store, through: date | None = None) -> int:
    """Recompute `cycle_days` from the logs. Cheap, and always correct."""
    cycle_list = cycles(store)
    if not cycle_list:
        store.replace_table("cycle_days", ["local_date"], [])
        return 0

    expected = median_length(cycle_list)
    last_observed = next(
        (r[0] for r in store.query("SELECT MAX(local_date) FROM observations") if r[0]),
        None,
    )
    # Project to today even when the last sync is a few days stale — knowing
    # today's phase is the point — but no further than the period we expect,
    # since a day past that is either a late period or an unlogged one, and
    # calling it "luteal day 40" would be an invention.
    through = through or max(filter(None, [last_observed, date.today()]))

    rows = []
    for cycle in cycle_list:
        horizon = through
        if cycle.end is None and expected:
            horizon = min(through, cycle.start + timedelta(days=expected - 1))
        last = cycle.end or max(horizon, cycle.menses_end)
        day = cycle.start
        while day <= last:
            phase, predicted = phase_for(cycle, day, expected)
            rows.append([day, cycle.index, (day - cycle.start).days + 1,
                         phase, cycle.length, predicted])
            day += timedelta(days=1)

    return store.replace_table(
        "cycle_days",
        ["local_date", "cycle_index", "cycle_day", "phase", "cycle_length", "is_predicted"],
        rows,
    )


@dataclass
class PhaseStats:
    phase: str
    n: int
    mean: float | None = None
    sd: float | None = None

    @property
    def usable(self) -> bool:
        # A zero standard deviation is not a tight baseline, it is a metric
        # that has not varied yet — dividing by it would report every reading
        # as infinitely abnormal.
        return self.n >= MIN_PHASE_SAMPLES and bool(self.sd)


def phase_baselines(store: Store, metric: str,
                    exclude_date: date | None = None) -> dict[str, PhaseStats]:
    """Distribution of one metric within each phase of your own history."""
    rows = store.query(
        """
        SELECT c.phase, COUNT(*), AVG(d.value), stddev_samp(d.value)
        FROM daily_metrics d
        JOIN cycle_days c ON c.local_date = d.local_date
        WHERE d.metric = ? AND (? IS NULL OR d.local_date != ?)
        GROUP BY c.phase
        """,
        [metric, exclude_date, exclude_date],
    )
    stats = {phase: PhaseStats(phase=phase, n=0) for phase in PHASES}
    for phase, n, mean, sd in rows:
        stats[phase] = PhaseStats(phase=phase, n=n,
                                  mean=round(mean, 2) if mean is not None else None,
                                  sd=round(sd, 3) if sd is not None else None)
    return stats


@dataclass
class Reading:
    metric: str
    day: date
    value: float | None = None
    phase: str | None = None
    cycle_day: int | None = None
    overall_z: float | None = None
    overall_n: int = 0
    phase_z: float | None = None
    phase_n: int = 0
    note: str | None = None

    @property
    def verdicts_disagree(self) -> bool:
        """The case this whole module exists for: a day a phase-blind baseline
        would flag, and the same phase of your own history calls unremarkable."""
        if self.overall_z is None or self.phase_z is None:
            return False
        return abs(self.overall_z) >= NOTABLE_Z and abs(self.phase_z) < NOTABLE_Z

    def describe(self) -> str:
        if self.value is None:
            return f"no {self.metric} recorded for {self.day}"
        parts = [f"{self.metric} {self.value:g} on {self.day}"]
        if self.phase:
            parts.append(f"{self.phase} (cycle day {self.cycle_day})")
        if self.overall_z is not None:
            parts.append(f"{self.overall_z:+.1f} SD against your usual")
        if self.phase_z is not None:
            parts.append(f"{self.phase_z:+.1f} SD against the same phase (n={self.phase_n})")
        elif self.note:
            parts.append(self.note)
        return " — ".join(parts)


def phase_adjusted(store: Store, metric: str, day: date) -> Reading:
    """One day's reading, judged both ways: against everything, and against the
    same phase of your own previous cycles."""
    row = store.query(
        "SELECT value FROM daily_metrics WHERE metric = ? AND local_date = ?",
        [metric, day],
    )
    reading = Reading(metric=metric, day=day)
    if not row:
        return reading
    reading.value = row[0][0]

    placement = store.query(
        "SELECT phase, cycle_day FROM cycle_days WHERE local_date = ?", [day]
    )
    if placement:
        reading.phase, reading.cycle_day = placement[0]

    overall = store.query(
        "SELECT COUNT(*), AVG(value), stddev_samp(value) FROM daily_metrics "
        "WHERE metric = ? AND local_date != ?",
        [metric, day],
    )
    count, mean, sd = overall[0]
    reading.overall_n = count
    if sd:
        reading.overall_z = round((reading.value - mean) / sd, 2)

    if not reading.phase:
        reading.note = "this day is not inside a reconstructed cycle"
        return reading
    if reading.phase == UNKNOWN:
        reading.note = ("no completed cycle yet, so this day cannot be placed "
                        "in a phase")
        return reading

    stats = phase_baselines(store, metric, exclude_date=day)[reading.phase]
    reading.phase_n = stats.n
    if stats.usable:
        reading.phase_z = round((reading.value - stats.mean) / stats.sd, 2)
    elif stats.n >= MIN_PHASE_SAMPLES:
        reading.note = (f"{metric} has not varied across your {reading.phase} "
                        f"days yet; no spread to judge against")
    else:
        reading.note = (f"only {stats.n} previous {reading.phase} day(s) with "
                        f"{metric}; need {MIN_PHASE_SAMPLES} for a phase baseline")
    return reading


def phase_signature(store: Store, metric: str) -> dict:
    """Does this metric actually shift between follicular and luteal for you?

    Reported as your own measured difference, with the direction the literature
    expects alongside it — so a disagreement shows up as a disagreement rather
    than being quietly resolved in either direction.
    """
    stats = phase_baselines(store, metric)
    follicular, luteal = stats[FOLLICULAR], stats[LUTEAL]
    result = {
        "metric": metric,
        "follicular": follicular,
        "luteal": luteal,
        "delta": None,
        "expected": LUTEAL_EXPECTATION.get(metric),
        "agrees": None,
    }
    if not (follicular.usable and luteal.usable):
        result["note"] = "not enough data in both phases yet"
        return result

    delta = round(luteal.mean - follicular.mean, 2)
    result["delta"] = delta
    if result["expected"]:
        observed = "higher" if delta > 0 else "lower"
        result["agrees"] = observed == result["expected"]
    return result


def summary(store: Store, today: date | None = None) -> dict:
    """Where you are, how regular your cycles have been, and on what evidence."""
    today = today or date.today()
    cycle_list = cycles(store)
    if not cycle_list:
        return {"cycles": 0, "note": "no period logs found — see the README on "
                                     "getting cycle data in from Apple Health"}

    lengths = [c.length for c in cycle_list if c.length]
    plausible = [n for n in lengths if n in PLAUSIBLE_LENGTH]
    expected = median_length(cycle_list)
    current = cycle_list[-1]
    placement = store.query(
        "SELECT phase, cycle_day, is_predicted FROM cycle_days WHERE local_date = ?",
        [today],
    )

    expected_end = (current.start + timedelta(days=expected - 1)) if expected else None
    overdue = (today - expected_end).days if expected_end and today > expected_end else 0

    result = {
        "cycles": len(cycle_list),
        "completed": len(lengths),
        "median_length": expected,
        "length_range": (min(plausible), max(plausible)) if plausible else None,
        "irregular": bool(plausible) and (max(plausible) - min(plausible)) > 8,
        "implausible_lengths": sorted(n for n in lengths if n not in PLAUSIBLE_LENGTH),
        "current_start": current.start,
        "phase": placement[0][0] if placement else None,
        "cycle_day": placement[0][1] if placement else None,
        "predicted": placement[0][2] if placement else None,
        "overdue_days": overdue,
        "days_since_period": (today - current.start).days,
    }
    if overdue:
        result["note"] = (
            f"your last logged period started {result['days_since_period']} days "
            f"ago, {overdue} past the {expected}-day median — most often that "
            f"means a period went unlogged rather than a cycle running long"
        )
    if expected and current.is_current:
        result["next_period_estimate"] = current.start + timedelta(days=expected)
    return result
