"""Reading blood tests over time.

A single panel is a snapshot with a reference range around it; the useful
questions are about direction and comparability. Two of those are handled here
because getting them wrong is easy and quiet:

*Cycle phase.* Oestradiol, progesterone, LH and FSH vary several-fold across a
cycle, and ferritin and haemoglobin move with menstrual blood loss. Comparing
two draws taken in different phases of the cycle is comparing two different
questions, so results for those analytes carry the phase they were taken in and
a trend across phases is reported as not comparable rather than plotted.

*Reference ranges.* Whether a flag came from your lab's own range or from a
generic one travels with the flag, everywhere.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

from .. import analytes as A
from ..store import Store


@dataclass
class LabValue:
    analyte: str
    label: str
    local_date: date
    value: float | None
    unit: str | None
    ref_low: float | None
    ref_high: float | None
    ref_source: str | None
    flag: str
    converted: bool
    phase: str | None = None
    cycle_day: int | None = None
    note: str | None = None

    @property
    def range_text(self) -> str:
        if self.ref_low is not None and self.ref_high is not None:
            span = f"{self.ref_low:g}-{self.ref_high:g}"
        elif self.ref_high is not None:
            span = f"<{self.ref_high:g}"
        elif self.ref_low is not None:
            span = f">{self.ref_low:g}"
        else:
            return "no range"
        return span + ("" if self.ref_source == "lab" else " (generic)")


@dataclass
class Trend:
    analyte: str
    label: str
    points: list[tuple[date, float, str | None]] = field(default_factory=list)
    comparable: bool = True
    note: str | None = None

    @property
    def change(self) -> float | None:
        if not self.comparable or len(self.points) < 2:
            return None
        return round(self.points[-1][1] - self.points[0][1], 3)

    def describe(self) -> str:
        if len(self.points) < 2:
            return "only one measurement so far"
        if not self.comparable:
            return self.note or "not comparable across draws"
        first, last = self.points[0], self.points[-1]
        direction = "up" if self.change > 0 else "down" if self.change < 0 else "flat"
        return (f"{direction} {abs(self.change):g} since {first[0]} "
                f"({first[1]:g} → {last[1]:g}, {len(self.points)} draws)")


def _rows_to_values(rows: list[tuple]) -> list[LabValue]:
    out = []
    for row in rows:
        (analyte, day, value, unit, low, high, ref_source, flag, converted,
         note, phase, cycle_day) = row
        spec = A.ANALYTES.get(analyte)
        out.append(LabValue(
            analyte=analyte, label=spec.label if spec else analyte, local_date=day,
            value=value, unit=unit, ref_low=low, ref_high=high,
            ref_source=ref_source, flag=flag or "unknown", converted=bool(converted),
            phase=phase, cycle_day=cycle_day, note=note,
        ))
    return out


_SELECT = """
    SELECT l.analyte, l.local_date, l.value, l.unit, l.ref_low, l.ref_high,
           l.ref_source, l.flag, l.converted, l.note, c.phase, c.cycle_day
    FROM lab_results l
    LEFT JOIN cycle_days c ON c.local_date = l.local_date
"""


def panels(store: Store) -> list[tuple[date, str | None, int]]:
    return store.query(
        "SELECT local_date, ANY_VALUE(lab), COUNT(*) FROM lab_results "
        "GROUP BY local_date ORDER BY local_date DESC"
    )


def latest_panel(store: Store) -> list[LabValue]:
    rows = store.query(
        _SELECT + """
        WHERE l.local_date = (SELECT MAX(local_date) FROM lab_results)
        ORDER BY l.flag != 'normal' DESC, l.analyte
        """
    )
    return _rows_to_values(rows)


def flagged(store: Store, latest_only: bool = True) -> list[LabValue]:
    """Results outside their range — the ones worth a conversation."""
    sql = _SELECT + " WHERE l.flag IN ('low', 'high')"
    if latest_only:
        sql += " AND l.local_date = (SELECT MAX(local_date) FROM lab_results)"
    return _rows_to_values(store.query(sql + " ORDER BY l.analyte"))


def history(store: Store, analyte: str) -> list[LabValue]:
    return _rows_to_values(store.query(
        _SELECT + " WHERE l.analyte = ? ORDER BY l.local_date", [analyte]
    ))


def trend(store: Store, analyte: str) -> Trend:
    values = history(store, analyte)
    spec = A.ANALYTES.get(analyte)
    result = Trend(analyte=analyte, label=spec.label if spec else analyte)
    result.points = [(v.local_date, v.value, v.phase) for v in values
                     if v.value is not None]

    if spec and spec.cycle_sensitive and len(result.points) > 1:
        phases = {phase for _, _, phase in result.points}
        if len(phases - {None}) > 1 or None in phases and len(phases) > 1:
            result.comparable = False
            named = ", ".join(sorted(p or "unplaced" for p in phases))
            result.note = (
                f"{result.label} moves with the cycle and these draws span "
                f"{named}; compare draws from the same phase, not this series"
            )
    return result


def by_phase(store: Store, analyte: str) -> dict[str, list[tuple[date, float]]]:
    """Cycle-sensitive results grouped by the phase they were drawn in."""
    grouped: dict[str, list[tuple[date, float]]] = {}
    for value in history(store, analyte):
        if value.value is None:
            continue
        grouped.setdefault(value.phase or "unplaced", []).append(
            (value.local_date, value.value))
    return grouped


def unconverted(store: Store) -> list[tuple[str, str, str]]:
    """Results whose unit we did not recognise, so their flag is withheld."""
    return store.query(
        "SELECT DISTINCT analyte, raw_unit, raw_value FROM lab_results "
        "WHERE converted = false ORDER BY analyte"
    )
