"""Training observations, computed rather than generated.

Everything here is arithmetic on your own logged sets: which lifts have
stopped moving, which you have quietly stopped doing, where the weekly volume
actually goes, and how fast the load is ramping. A language model is good at
explaining these and bad at inventing them, so it gets them as findings and is
asked to interpret rather than to derive.

Training changes are the one kind of recommendation this system makes freely.
Adjusting rep ranges is not medicine, the feedback loop is short, and the cost
of being wrong is a mediocre eight weeks rather than harm.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta

from ..store import Store
from . import daily, strength

#: A lift needs this many sessions before "stalled" means anything.
MIN_SESSIONS_TO_JUDGE = 6
#: Below this weekly gain, with a poor fit, a lift is not progressing.
STALL_KG_PER_WEEK = 0.15
STALL_R2 = 0.3
#: Muscle groups below this share of working sets over the window are thin.
THIN_SHARE = 0.10


@dataclass
class Observation:
    kind: str            # progressing | stalled | regressing | dormant | imbalance | ramp
    subject: str
    detail: str
    basis: str
    suggestion: str | None = None

    def as_dict(self) -> dict:
        return {"kind": self.kind, "subject": self.subject, "detail": self.detail,
                "basis": self.basis, "suggestion": self.suggestion}


def lift_observations(store: Store, as_of: date | None = None) -> list[Observation]:
    as_of = as_of or date.today()
    out: list[Observation] = []
    stale = {row["exercise"]: row for row in strength.stale_lifts(store, as_of=as_of)}

    for row in strength.exercise_summary(store):
        exercise = row["exercise"]
        trend = strength.progression(store, exercise)

        if exercise in stale:
            days = stale[exercise]["days_since"]
            out.append(Observation(
                "dormant", exercise,
                f"not trained for {days} days, after {row['sessions']} sessions",
                f"last session {row['last_done']}",
                "either bring it back or drop it from the plan deliberately — "
                "an exercise you have stopped doing is still counted in your "
                "plan and not in your body",
            ))
            continue

        if trend.sessions < MIN_SESSIONS_TO_JUDGE or trend.trend_kg_per_week is None:
            continue

        basis = (f"{trend.sessions} sessions, {trend.trend_kg_per_week:+.2f} kg/wk, "
                 f"r²={trend.r_squared:.2f}")
        if trend.trend_kg_per_week <= -STALL_KG_PER_WEEK and (trend.r_squared or 0) >= STALL_R2:
            out.append(Observation(
                "regressing", exercise, "estimated 1RM is trending down", basis,
                "check whether this is fatigue, a change in how you are logging, "
                "or genuine loss — a deload week usually separates the first from "
                "the third",
            ))
        elif abs(trend.trend_kg_per_week) < STALL_KG_PER_WEEK or (trend.r_squared or 0) < STALL_R2:
            out.append(Observation(
                "stalled", exercise,
                f"flat for {trend.sessions} sessions "
                f"({trend.days_since_best} days since your best)", basis,
                "the usual levers are a rep-range change, an extra weekly "
                "session, or a planned lighter block before pushing again",
            ))
        else:
            out.append(Observation(
                "progressing", exercise,
                f"adding {trend.trend_kg_per_week:.2f} kg a week", basis,
                "leave this one alone",
            ))
    return out


def volume_balance(store: Store, weeks: int = 8) -> list[Observation]:
    """Where the working sets actually go, by muscle group."""
    rows = store.query(
        """
        SELECT COALESCE(primary_muscle, 'unmapped') AS muscle,
               SUM(volume_kg) AS volume, COUNT(*) AS sets
        FROM working_sets
        WHERE local_date > (SELECT MAX(local_date) FROM working_sets) - INTERVAL (?) WEEK
        GROUP BY 1 ORDER BY volume DESC
        """, [weeks])
    total = sum(row[2] for row in rows)
    if not total:
        return []

    out = []
    shares = [(muscle, sets / total, volume) for muscle, volume, sets in rows]
    for muscle, share, volume in shares:
        if muscle == "unmapped":
            out.append(Observation(
                "imbalance", "unmapped exercises",
                f"{share:.0%} of your sets have no muscle group attached",
                f"{weeks}-week window",
                "these are missing from every balance calculation — worth "
                "matching them to Hevy templates",
            ))
    thin = [m for m, share, _ in shares if share < THIN_SHARE and m != "unmapped"]
    top = shares[0]
    if len(shares) > 1:
        out.append(Observation(
            "imbalance", "volume distribution",
            f"{top[0]} takes {top[1]:.0%} of working sets"
            + (f"; thinnest: {', '.join(thin)}" if thin else ""),
            f"{weeks}-week window, {total} working sets",
            None if not thin else
            "worth checking whether the thin groups are deliberate or drift",
        ))
    return out


def ramp(store: Store, as_of: date | None = None) -> list[Observation]:
    load = daily.training_load(store, as_of=as_of or date.today())
    if load.ratio is None:
        return []
    if load.ratio > 1.5:
        return [Observation("ramp", "training load",
                            f"acute:chronic {load.ratio}", load.describe(),
                            "a sharp ramp; the conservative reading is to hold "
                            "volume steady for a week rather than add to it")]
    if load.ratio < 0.6:
        return [Observation("ramp", "training load",
                            f"acute:chronic {load.ratio}", load.describe(),
                            "you have backed off substantially — deliberate "
                            "deload, or drift?")]
    return []


def observations(store: Store, as_of: date | None = None) -> list[dict]:
    """Everything worth saying about the training, from the training alone."""
    found = lift_observations(store, as_of) + volume_balance(store) + ramp(store, as_of)
    order = {"regressing": 0, "stalled": 1, "dormant": 2, "ramp": 3,
             "imbalance": 4, "progressing": 5}
    found.sort(key=lambda o: order.get(o.kind, 9))
    return [o.as_dict() for o in found]
