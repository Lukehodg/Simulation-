"""What you are on, as an axis of interpretation.

The menstrual-cycle model reconstructs a timeline from logged events and then
reads every day against the phase it fell in. This does the same for
compounds: `protocol_events` (start / change / stop, entered by hand) become
`protocol_days`, and the rest of the layer asks this module "is this reading
something the person's compounds would predict?"

Three kinds of answer, and a hard line between them:

  * `context()` — a resting HR that is up because of a GLP-1 agonist is read as
    expected; a marker moving *against* what a compound predicts is flagged as
    the informative case it is.
  * `strength_note()` — a strength trend on exogenous androgens is compound plus
    training, and a stall means more than it would otherwise.
  * `monitoring()` — the markers a clinician should watch. The output is always
    "a doctor should see this", never a change to the protocol. Nothing here
    suggests a dose, an ancillary, or a PCT.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta

from .. import compounds as C
from ..store import Store
from . import trend as trend_features

#: dose × this reaches a weekly dose
_FREQ_TO_WEEKLY = {
    "weekly": 1.0, "1x/week": 1.0, "e7d": 1.0,
    "twice weekly": 2.0, "2x/week": 2.0, "e3d": 7 / 3, "e3.5d": 2.0,
    "eod": 3.5, "e2d": 3.5, "daily": 7.0, "ed": 7.0,
    "e5d": 1.4, "e10d": 0.7, "biweekly": 0.5, "e14d": 0.5,
}


@dataclass
class Active:
    compound: str
    label: str
    weekly_dose: float | None
    unit: str | None
    weeks_on: float

    def as_dict(self) -> dict:
        spec = C.COMPOUNDS.get(self.compound)
        return {
            "compound": self.compound, "label": self.label,
            "weekly_dose": self.weekly_dose, "unit": self.unit,
            "weeks_on": round(self.weeks_on, 1),
            "class": spec.klass if spec else None,
            "settled": bool(spec and self.weeks_on >= spec.onset_weeks),
        }


def _events(store: Store) -> list[tuple]:
    return store.query(
        "SELECT local_date, event, compound, dose, unit, freq FROM protocol_events "
        "ORDER BY compound, local_date, "
        "CASE event WHEN 'start' THEN 0 WHEN 'change' THEN 1 ELSE 2 END")


def _weekly(dose: float | None, freq: str | None) -> float | None:
    if dose is None:
        return None
    return round(dose * _FREQ_TO_WEEKLY.get((freq or "weekly").lower().strip(), 1.0), 3)


def rebuild(store: Store, through: date | None = None) -> int:
    """Recompute protocol_days from the logged events."""
    rows = _events(store)
    if not rows:
        store.replace_table("protocol_days",
                            ["local_date", "compound", "weekly_dose", "unit",
                             "weeks_on"], [])
        return 0

    last_obs = next((r[0] for r in store.query(
        "SELECT MAX(local_date) FROM observations") if r[0]), None)
    through = through or max(filter(None, [last_obs, date.today()]))

    out: list[list] = []
    by_compound: dict[str, list[tuple]] = {}
    for row in rows:
        by_compound.setdefault(row[2], []).append(row)

    for compound, events in by_compound.items():
        start_day = events[0][0]
        weekly, unit = None, None
        segments: list[tuple[date, date | None, float | None, str | None]] = []
        for day, event, _c, dose, u, freq in events:
            if event == "stop":
                if segments:
                    segments[-1] = (*segments[-1][:1], day - timedelta(days=1),
                                    *segments[-1][2:])
                continue
            weekly, unit = _weekly(dose, freq), u
            if segments:
                segments[-1] = (*segments[-1][:1], day - timedelta(days=1),
                                *segments[-1][2:])
            segments.append((day, None, weekly, unit))

        for seg_start, seg_end, seg_weekly, seg_unit in segments:
            stop = seg_end or through
            day = seg_start
            while day <= stop:
                out.append([day, compound, seg_weekly, seg_unit,
                            round((day - start_day).days / 7, 3)])
                day += timedelta(days=1)

    return store.replace_table(
        "protocol_days",
        ["local_date", "compound", "weekly_dose", "unit", "weeks_on"], out)


def active(store: Store, on: date | None = None) -> list[Active]:
    on = on or date.today()
    rows = store.query(
        "SELECT compound, weekly_dose, unit, weeks_on FROM protocol_days "
        "WHERE local_date = ? ORDER BY compound", [on])
    out = []
    for compound, weekly_dose, unit, weeks_on in rows:
        spec = C.COMPOUNDS.get(compound)
        out.append(Active(compound=compound,
                          label=spec.label if spec else compound,
                          weekly_dose=weekly_dose, unit=unit,
                          weeks_on=weeks_on or 0.0))
    return out


def _keys(store: Store, on: date) -> list[str]:
    return [a.compound for a in active(store, on)]


def context(store: Store, metric: str, day: date, z: float | None) -> list[str]:
    """How the active compounds bear on one metric reading."""
    if z is None:
        return []
    keys = _keys(store, day)
    if not keys:
        return []
    out: list[str] = []
    moved = "raises" if z > 0 else "lowers"

    for key in keys:
        spec = C.COMPOUNDS.get(key)
        if not spec:
            continue
        effect = spec.effect_on(metric)
        wk = next((a.weeks_on for a in active(store, day) if a.compound == key), 0)
        if effect == moved and abs(z) >= 0.5:
            extra = ""
            if metric == "resting_hr" and spec.resting_hr_offset_bpm:
                lo, hi = spec.resting_hr_offset_bpm
                extra = f" ~{lo:g}–{hi:g} bpm"
            when = f"week {wk:.0f}" if wk >= 1 else "just started"
            settling = "" if wk >= spec.onset_weeks else \
                " — and the effect is still building at this point" if wk >= 1 else ""
            out.append(f"on {spec.label.lower()} ({when}), which {effect} "
                       f"{_pretty(metric)}{extra}, so some of this is expected{settling}")
        elif effect and effect != moved and abs(z) >= 1.0:
            out.append(f"{_pretty(metric)} is moving the opposite way to what "
                       f"{spec.label.lower()} predicts ({effect}) — that makes "
                       f"this reading more informative, not less")
    return out


def strength_note(store: Store, day: date | None = None) -> str | None:
    day = day or date.today()
    for a in active(store, day):
        spec = C.COMPOUNDS.get(a.compound)
        if spec and spec.strength_confound:
            since = _start_date(store, a.compound)
            wk = f"week {a.weeks_on:.0f}" if a.weeks_on >= 1 else "just started"
            return (f"on {spec.label.lower()} since {since} ({wk}) — read "
                    f"strength trends as the compound plus training, and a "
                    f"stall as more meaningful than it would be otherwise")
    return None


def _start_date(store: Store, compound: str) -> str:
    rows = store.query(
        "SELECT MIN(local_date) FROM protocol_events WHERE compound = ?",
        [compound])
    return str(rows[0][0]) if rows and rows[0][0] else "an unknown date"


#: a monitor marker's name, where it isn't itself a stored metric
_MARKER_METRIC = {"blood_pressure": "bp_systolic"}


def monitoring(store: Store, day: date | None = None) -> list[dict]:
    """Markers a clinician should be watching, with their current direction."""
    day = day or date.today()
    tracked = {"resting_hr", "hrv_rmssd", "body_mass", "respiratory_rate",
               "hdl", "haematocrit", "bp_systolic", "bp_diastolic"}
    out = []
    for a in active(store, day):
        spec = C.COMPOUNDS.get(a.compound)
        if not spec:
            continue
        for marker, why in spec.monitor:
            drift = None
            metric = _MARKER_METRIC.get(marker, marker)
            if metric in tracked:
                t = trend_features.metric_trend(store, metric, as_of=day)
                if t.verdict in ("rising", "falling"):
                    drift = t.describe()
            out.append({"compound": a.compound, "marker": marker, "why": why,
                        "current_trend": drift, "weeks_on": round(a.weeks_on, 1)})
    return out


def pre_panel_checklist(store: Store, day: date | None = None) -> list[str]:
    day = day or date.today()
    wanted: dict[str, list[str]] = {}
    for a in active(store, day):
        spec = C.COMPOUNDS.get(a.compound)
        if not spec:
            continue
        for analyte in spec.pre_panel:
            wanted.setdefault(analyte, []).append(spec.label)
    return [f"{analyte} (for {', '.join(sorted(set(labels)))})"
            for analyte, labels in sorted(wanted.items())]


def summary(store: Store, today: date | None = None) -> dict:
    today = today or date.today()
    now = active(store, today)
    if not now:
        return {"on": [], "note": "nothing logged — `health protocol add ...`"}
    return {
        "as_of": str(today),
        "on": [a.as_dict() for a in now],
        "strength_note": strength_note(store, today),
        "monitoring": monitoring(store, today),
        "pre_panel": pre_panel_checklist(store, today),
        "boundary": "documented effects and monitoring markers only — no dosing, "
                    "ancillary or PCT guidance is given or implied",
    }


def _pretty(metric: str) -> str:
    return {"resting_hr": "resting heart rate", "hrv_rmssd": "HRV",
            "body_mass": "weight", "recovery_score": "recovery",
            "respiratory_rate": "respiration rate",
            "skin_temp_deviation": "skin temperature"}.get(metric, metric)
