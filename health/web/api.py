"""The payload behind the page.

One function assembles everything the interface shows, and it is built to
degrade honestly: every module reports whether it has data, and says what to
run when it does not. An empty database is the first thing you will see after
installing this, so it has to be as considered as the full one.
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any

from .. import metrics as M
from ..config import Config
from ..features import cycle as cycle_features
from ..features import daily, labs as lab_features
from ..features import strength as strength_features
from ..store import Store

#: What the readout row shows, in order.
HEADLINE_METRICS = [
    (M.HRV_RMSSD, "HRV", "ms", 0),
    (M.RESTING_HR, "Resting HR", "bpm", 0),
    (M.SLEEP_DURATION, "Sleep", "", 0),
    (M.RECOVERY_SCORE, "Recovery", "%", 0),
]
SETUP_HINTS = {
    "whoop": "health auth whoop, then health sync whoop",
    "hevy": "set HEVY_API_KEY, then health sync hevy",
    "apple_health": "set HEALTH_APPLE_EXPORT_DIR, then health sync",
    "labs": "health labs add report.pdf",
}


def _fmt_duration(minutes: float | None) -> str | None:
    if minutes is None:
        return None
    return f"{int(minutes // 60)}:{int(minutes % 60):02d}"


def _verdict(store: Store, day: date, metrics: dict[str, float],
             off: list[dict]) -> dict[str, str]:
    """A description of the day, never a recommendation.

    It reports what the numbers do — how far from baseline, and whether the
    cycle explains it. What to do about that is a judgement this data cannot
    make on its own.
    """
    if not metrics:
        return {"headline": "No data for today",
                "detail": "Nothing has synced for this date yet."}

    explained = [d for d in off if d.get("phase_explains")]
    unexplained = [d for d in off if not d.get("phase_explains")]

    if not off:
        return {"headline": "Nothing unusual.",
                "detail": "Every metric today sits inside its own recent baseline."}

    if not unexplained:
        names = ", ".join(d["label"] for d in explained)
        return {"headline": "Ordinary for the phase.",
                "detail": f"{names} would look low against your yearly baseline, "
                          f"but sits where it usually does at this point in your "
                          f"cycle."}

    names = ", ".join(d["label"] for d in unexplained)
    return {"headline": f"{len(unexplained)} off baseline.",
            "detail": f"{names} is outside its own recent range. One day is not "
                      f"a trend — worth watching rather than acting on."}


def today_payload(store: Store, config: Config, day: date | None = None) -> dict[str, Any]:
    day = day or date.today()

    rows = store.query(
        "SELECT metric, value, source FROM daily_metrics WHERE local_date = ?", [day])
    values = {metric: value for metric, value, _ in rows}
    sources = {metric: source for metric, _, source in rows}

    # If today is empty, fall back to the most recent day that is not, and say so.
    fell_back_to = None
    if not values:
        latest = store.query("SELECT MAX(local_date) FROM daily_metrics")
        if latest and latest[0][0]:
            fell_back_to = latest[0][0]
            day = fell_back_to
            rows = store.query(
                "SELECT metric, value, source FROM daily_metrics WHERE local_date = ?",
                [day])
            values = {m: v for m, v, _ in rows}
            sources = {m: s for m, _, s in rows}

    readout = []
    for metric, label, unit, places in HEADLINE_METRICS:
        value = values.get(metric)
        base = daily.baseline(store, metric, as_of=day)
        z = base.z(value)
        readout.append({
            "metric": metric, "label": label, "unit": unit,
            "value": _fmt_duration(value) if metric == M.SLEEP_DURATION
                     else (round(value, places) if value is not None else None),
            "raw": value, "z": z, "source": sources.get(metric),
            "baseline_n": base.n,
            "hint": None if value is not None else SETUP_HINTS.get("whoop"),
        })

    load = daily.training_load(store, as_of=day)

    off = []
    for metric, value, z in daily.deviations(store, day):
        reading = cycle_features.phase_adjusted(store, metric, day)
        off.append({
            "metric": metric, "label": metric.replace("_", " "), "value": value,
            "z": z, "phase": reading.phase,
            "phase_z": reading.phase_z,
            "phase_explains": bool(reading.verdicts_disagree),
        })

    series = daily.series(store, M.HRV_RMSSD, day - timedelta(days=27), day)
    base = daily.baseline(store, M.HRV_RMSSD, as_of=day)
    phases = dict(store.query(
        "SELECT local_date, phase FROM cycle_days WHERE local_date >= ? AND local_date <= ?",
        [day - timedelta(days=27), day]))

    cycle = cycle_features.summary(store, today=day)
    lifts = strength_features.exercise_summary(store)[:5]
    stale = {row["exercise"] for row in strength_features.stale_lifts(store)}
    progressions = []
    for lift in lifts:
        trend = strength_features.progression(store, lift["exercise"])
        progressions.append({
            "exercise": lift["exercise"], "sessions": lift["sessions"],
            "last_done": str(lift["last_done"]),
            "trend": trend.trend_kg_per_week, "r2": trend.r_squared,
            "best": trend.best_e1rm, "note": trend.note,
            "stale": lift["exercise"] in stale,
        })

    panels = lab_features.panels(store)
    bloods = {"panels": len(panels), "hint": SETUP_HINTS["labs"], "results": []}
    if panels:
        latest_panel = lab_features.latest_panel(store)
        flagged = lab_features.flagged(store)
        shown = flagged + [v for v in latest_panel if v.flag == "normal"][:3]
        bloods.update({
            "date": str(panels[0][0]), "lab": panels[0][1],
            "total": panels[0][2], "flagged": len(flagged),
            "results": [{"label": v.label, "value": v.value, "unit": v.unit,
                         "flag": v.flag, "range": v.range_text,
                         "range_source": v.ref_source, "note": v.note}
                        for v in shown],
        })

    coverage = [{"source": s, "table": t, "from": str(a), "to": str(b), "rows": n}
                for s, t, a, b, n in store.coverage()]
    synced = {s: (t.strftime("%d %b %H:%M") if t else None)
              for s, t in store.query("SELECT source, last_ok FROM sync_state")}
    connected = sorted({row["source"] for row in coverage})

    return {
        "date": str(day),
        "date_label": day.strftime("%A %-d %B"),
        "showing_older_day": str(fell_back_to) if fell_back_to else None,
        "verdict": _verdict(store, day, values, off),
        "readout": readout,
        "load": {"acute": load.acute, "chronic": load.chronic, "ratio": load.ratio,
                 "summary": load.describe()},
        "chart": {
            "points": [{"date": str(d), "value": v,
                        "phase": phases.get(d)} for d, v in series],
            "median": base.median, "mad": base.mad, "usable": base.usable,
            "metric": "HRV", "unit": "ms",
        },
        "cycle": {k: (str(v) if isinstance(v, date) else v) for k, v in cycle.items()},
        "strength": progressions,
        "bloods": bloods,
        "sync": {"connected": connected, "last_ok": synced,
                 "missing": [s for s in ("whoop", "hevy", "apple_health")
                             if s not in connected],
                 "hints": SETUP_HINTS},
        "empty": not coverage,
    }


def bloods_payload(store: Store, config: Config) -> dict[str, Any]:
    """Everything the bloods page shows, including exactly what an analysis
    would send — so the boundary can be inspected before it is crossed."""
    from ..analysis import redacted_payload
    from ..secrets import get_secret

    panels = lab_features.panels(store)
    if not panels:
        return {"panels": [], "results": [], "empty": True,
                "hint": SETUP_HINTS["labs"],
                "has_key": bool(get_secret("ANTHROPIC_API_KEY", config.config_dir,
                                           required=False))}

    values = lab_features.latest_panel(store)
    flagged = lab_features.flagged(store)
    trends = {}
    for value in values:
        trend = lab_features.trend(store, value.analyte)
        if len(trend.points) > 1:
            trends[value.analyte] = {
                "comparable": trend.comparable, "change": trend.change,
                "summary": trend.describe(), "note": trend.note,
                "points": [{"date": str(d), "value": v, "phase": p}
                           for d, v, p in trend.points],
            }

    return {
        "empty": False,
        "panels": [{"date": str(d), "lab": lab, "count": n} for d, lab, n in panels],
        "latest": {"date": str(panels[0][0]), "lab": panels[0][1],
                   "count": panels[0][2], "flagged": len(flagged)},
        "results": [{"analyte": v.analyte, "label": v.label, "value": v.value,
                     "unit": v.unit, "flag": v.flag, "range": v.range_text,
                     "range_source": v.ref_source, "phase": v.phase,
                     "note": v.note, "converted": v.converted}
                    for v in values],
        "trends": trends,
        "unconverted": [{"analyte": a, "unit": u} for a, u, _ in
                        lab_features.unconverted(store)],
        "would_send": redacted_payload(store),
        "has_key": bool(get_secret("ANTHROPIC_API_KEY", config.config_dir,
                                   required=False)),
    }


def metric_payload(store: Store, metric: str, days: int = 90,
                   as_of: date | None = None) -> dict[str, Any]:
    as_of = as_of or date.today()
    points = daily.series(store, metric, as_of - timedelta(days=days), as_of)
    base = daily.baseline(store, metric, as_of=as_of)
    return {
        "metric": metric, "unit": M.unit_for(metric),
        "points": [{"date": str(d), "value": v} for d, v in points],
        "median": base.median, "mad": base.mad, "usable": base.usable,
    }
