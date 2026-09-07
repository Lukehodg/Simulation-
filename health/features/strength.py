"""Strength progression from Hevy's set-level data.

The question this exists to answer is "am I actually getting stronger, or just
adding reps?" — which needs estimated 1RM (which accounts for both) rather than
weight or volume alone, and needs a trend with its sample size attached rather
than a comparison of two cherry-picked sessions.

Three deliberate limits, applied here so nothing downstream has to remember
them:

  * Sets above 12 reps are excluded from strength estimates. Epley is fitted to
    low-rep work; past that it flatters endurance sets into fake PRs. They
    still count for volume.
  * A trend needs at least four sessions. Below that a slope is a line through
    noise, so `progression` returns one with `trend=None` and says why.
  * The slope is reported per week with its r-squared and n. A confident-looking
    "+2.1 kg/week" off three sessions at r²=0.2 is worse than no answer.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta

from ..store import Store

MIN_SESSIONS_FOR_TREND = 4
DEFAULT_WINDOW_SESSIONS = 8
STALE_AFTER_DAYS = 21


@dataclass
class Progression:
    exercise: str
    sessions: int
    first_date: date | None = None
    last_date: date | None = None
    first_e1rm: float | None = None
    last_e1rm: float | None = None
    best_e1rm: float | None = None
    best_date: date | None = None
    #: kg per week of estimated 1RM, over the requested window
    trend_kg_per_week: float | None = None
    r_squared: float | None = None
    note: str | None = None

    @property
    def days_since_best(self) -> int | None:
        if self.best_date is None or self.last_date is None:
            return None
        return (self.last_date - self.best_date).days

    def describe(self) -> str:
        if self.trend_kg_per_week is None:
            return self.note or "not enough data"
        direction = "up" if self.trend_kg_per_week > 0 else "down"
        return (f"{direction} {abs(self.trend_kg_per_week):.2f} kg/week "
                f"over {self.sessions} sessions (r²={self.r_squared:.2f})")


def session_history(store: Store, exercise: str, limit: int = 50) -> list[tuple]:
    """Every session for one exercise: date, best e1RM, top set, volume."""
    return store.query(
        """
        SELECT local_date, best_e1rm, top_weight_kg, sets, volume_kg
        FROM session_bests
        WHERE lower(exercise) = lower(?)
        ORDER BY local_date DESC
        LIMIT ?
        """,
        [exercise, limit],
    )


def progression(store: Store, exercise: str,
                window: int = DEFAULT_WINDOW_SESSIONS) -> Progression:
    """Trend in estimated 1RM over the last `window` sessions of one exercise."""
    rows = store.query(
        """
        SELECT local_date, best_e1rm
        FROM session_bests
        WHERE lower(exercise) = lower(?) AND best_e1rm IS NOT NULL
        ORDER BY local_date DESC
        LIMIT ?
        """,
        [exercise, window],
    )
    if not rows:
        return Progression(exercise=exercise, sessions=0,
                           note="no working sets recorded for this exercise")

    rows = sorted(rows)  # oldest first
    best_row = store.query(
        """
        SELECT local_date, best_e1rm
        FROM session_bests
        WHERE lower(exercise) = lower(?) AND best_e1rm IS NOT NULL
        ORDER BY best_e1rm DESC, local_date DESC
        LIMIT 1
        """,
        [exercise],
    )

    result = Progression(
        exercise=exercise,
        sessions=len(rows),
        first_date=rows[0][0], last_date=rows[-1][0],
        first_e1rm=round(rows[0][1], 1), last_e1rm=round(rows[-1][1], 1),
        best_e1rm=round(best_row[0][1], 1) if best_row else None,
        best_date=best_row[0][0] if best_row else None,
    )

    if len(rows) < MIN_SESSIONS_FOR_TREND:
        result.note = (f"only {len(rows)} session(s); a trend needs at least "
                       f"{MIN_SESSIONS_FOR_TREND}")
        return result

    # Regress e1RM on days elapsed, so unevenly spaced sessions weigh correctly.
    origin = rows[0][0]
    points = [((day - origin).days, value) for day, value in rows]
    stats = store.db.execute(
        "SELECT regr_slope(y, x), regr_r2(y, x) FROM (SELECT * FROM (VALUES "
        + ", ".join("(?, ?)" for _ in points)
        + ") AS t(x, y))",
        [value for point in points for value in point],
    ).fetchone()

    slope, r2 = stats if stats else (None, None)
    if slope is not None:
        result.trend_kg_per_week = round(slope * 7, 3)
        result.r_squared = round(r2, 3) if r2 is not None else None
    else:
        result.note = "every session fell on the same day; no trend to fit"
    return result


def exercise_summary(store: Store, since: date | None = None) -> list[dict]:
    """One row per exercise you have trained, most recent first."""
    rows = store.query(
        """
        SELECT exercise,
               COUNT(*)                       AS sessions,
               MAX(local_date)                AS last_done,
               MAX(best_e1rm)                 AS best_e1rm,
               SUM(volume_kg)                 AS volume_kg,
               ANY_VALUE(primary_muscle)      AS muscle
        FROM session_bests
        WHERE ? IS NULL OR local_date >= ?
        GROUP BY exercise
        ORDER BY last_done DESC, sessions DESC
        """,
        [since, since],
    )
    return [
        {"exercise": r[0], "sessions": r[1], "last_done": r[2],
         "best_e1rm": round(r[3], 1) if r[3] else None,
         "volume_kg": round(r[4], 1) if r[4] else None, "muscle": r[5]}
        for r in rows
    ]


def weekly_volume(store: Store, weeks: int = 12) -> list[tuple]:
    """Tonnage per muscle group per week, most recent first, `weeks` weeks of it.

    Includes an 'unmapped' row where an exercise has no Hevy template, so the
    total is always the true total.
    """
    return store.query(
        """
        SELECT week, muscle, round(volume_kg, 1), sets, sessions
        FROM weekly_volume
        WHERE week > (SELECT MAX(week) FROM weekly_volume) - INTERVAL (?) WEEK
        ORDER BY week DESC, volume_kg DESC
        """,
        [weeks],
    )


def stale_lifts(store: Store, days: int = STALE_AFTER_DAYS,
                as_of: date | None = None) -> list[dict]:
    """Exercises you used to train and have not done lately.

    Deliberately restricted to exercises with at least two sessions: one-off
    experiments are not abandoned programmes, and flagging them as such would
    make the whole list noise.
    """
    rows = store.query(
        """
        SELECT exercise, MAX(local_date) AS last_done, COUNT(*) AS sessions,
               MAX(best_e1rm) AS best_e1rm
        FROM session_bests
        GROUP BY exercise
        HAVING COUNT(*) >= 2
        ORDER BY last_done
        """
    )
    cutoff = (as_of or date.today()) - timedelta(days=days)
    return [
        {"exercise": r[0], "last_done": r[1], "sessions": r[2],
         "days_since": ((as_of or date.today()) - r[1]).days,
         "best_e1rm": round(r[3], 1) if r[3] else None}
        for r in rows if r[1] < cutoff
    ]
