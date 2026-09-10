"""Sleep architecture, not just how long.

`daily.sleep_regularity` covers timing. This covers what the night was made of:
deep sleep, REM, and time awake after falling asleep (WASO) each carry more
than total duration does — deep sleep in particular moves with alcohol, a late
meal, a late hard session and illness, and it is the first thing to go.

Every figure is judged against the person's own recent nights with a robust
baseline (median and MAD), the same choice `daily.baseline` makes and for the
same reason: a fortnight of bad sleep should not quietly become the new normal
that the next night is measured against.
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass
from datetime import date, timedelta

from ..store import Store
from .daily import MAD_TO_SD, MIN_BASELINE_POINTS

WINDOW = 28
#: Stages to judge, and which direction is the bad one (-1: less is worse).
STAGES = (("deep_min", "deep sleep", -1), ("rem_min", "REM", -1),
          ("awake_min", "time awake (WASO)", 1), ("efficiency", "efficiency", -1),
          ("duration_min", "time asleep", -1))


@dataclass
class Component:
    column: str
    label: str
    last: float | None = None
    median: float | None = None
    mad: float | None = None
    n: int = 0
    bad_direction: int = -1

    @property
    def z(self) -> float | None:
        if self.last is None or self.median is None or not self.mad:
            return None
        return round((self.last - self.median) / (self.mad * MAD_TO_SD), 2)

    @property
    def verdict(self) -> str:
        z = self.z
        if z is None:
            return "no baseline yet"
        adverse = z * self.bad_direction
        return "low" if adverse >= 1.0 and self.bad_direction == -1 else \
               "high" if adverse >= 1.0 else "ordinary"

    def as_dict(self) -> dict:
        return {"metric": self.column, "label": self.label, "last_night": self.last,
                "median": self.median, "z": self.z, "n": self.n,
                "verdict": self.verdict}


def sleep_series(store: Store, column: str, start: date,
                 end: date) -> list[tuple[date, float]]:
    return store.query(
        f"""
        SELECT local_date, {column} FROM sleeps
        WHERE COALESCE(is_nap, false) = false
          AND {column} IS NOT NULL
          AND local_date > ? AND local_date <= ?
        ORDER BY local_date
        """,
        [start, end],
    )


def _component(store: Store, column: str, label: str, bad: int,
               as_of: date, window: int) -> Component:
    rows = sleep_series(store, column, as_of - timedelta(days=window), as_of)
    comp = Component(column=column, label=label, n=len(rows), bad_direction=bad)
    if not rows:
        return comp
    # Judge the most recent night against the ones before it, not including it.
    comp.last = rows[-1][1]
    latest_day = rows[-1][0]
    values = [v for d, v in rows if d != latest_day]
    if len(values) >= MIN_BASELINE_POINTS:
        comp.median = round(statistics.median(values), 2)
        comp.mad = round(statistics.median([abs(v - comp.median) for v in values]), 3)
    return comp


def sleep_quality(store: Store, as_of: date | None = None,
                  days: int = WINDOW) -> dict:
    """Last night's architecture against your own recent baseline, plus a
    running deep-sleep debt."""
    as_of = as_of or date.today()
    components = [_component(store, col, label, bad, as_of, days)
                  for col, label, bad in STAGES]
    by_col = {c.column: c for c in components}

    nights = by_col["duration_min"].n
    if nights < 5:
        return {"nights": nights, "note": "not enough nights yet"}

    deep = by_col["deep_min"]
    awake = by_col["awake_min"]
    duration = by_col["duration_min"]

    fragmentation = None
    if awake.last is not None and duration.last:
        fragmentation = round(awake.last / (duration.last + awake.last), 3)

    # Deep-sleep debt: shortfall against the deep-sleep baseline over the window,
    # floored per night so one big night cannot bank a surplus.
    deep_debt = None
    if deep.median:
        rows = sleep_series(store, "deep_min",
                            as_of - timedelta(days=days), as_of)
        deep_debt = round(sum(min(deep.median, deep.median - v)
                              for _, v in rows), 1)

    return {
        "nights": nights,
        "components": [c.as_dict() for c in components],
        "fragmentation_last_night": fragmentation,
        "deep_sleep_debt_min": deep_debt,
        "flags": [c.label for c in components if c.verdict in ("low", "high")],
    }
