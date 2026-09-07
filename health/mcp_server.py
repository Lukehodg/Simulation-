"""The tool surface Claude talks to.

Everything the model can see about your health comes through these functions,
and that is the design: the model never queries the database freely, never
receives a raw dump, and never does arithmetic. Python computes; the model
interprets. It is the difference between an assistant that reads your numbers
and one that invents them.

Each tool returns its own uncertainty — sample sizes, confidence intervals,
whether a baseline was usable, whether a comparison is even valid. That is what
lets the model say "not enough data yet" instead of guessing, which is the one
behaviour that decides whether any of this is worth trusting.

Run it with `health mcp`, and point Claude Code at it:

    claude mcp add health -- /path/to/.venv/bin/health mcp --root /path/to/project
"""

from __future__ import annotations

import functools
from datetime import date, timedelta
from typing import Any, Callable

from mcp.server.mcpserver import MCPServer

from .analytes import ANALYTES, canonical_analyte
from .config import Config, load_config
from .features import cycle as cycle_features
from .features import daily, labs as lab_features
from .features import strength as strength_features
from .research import evidence_query, search
from .store import Store

INSTRUCTIONS = """
Tools over one person's own health data: WHOOP, Garmin, Hevy, MyFitnessPal,
their menstrual cycle where logged, and their blood tests.

How to use them well:

- Every number you report should come from a tool call, not from memory or
  arithmetic of your own. If a tool says a baseline is unusable or a sample is
  too small, say that rather than working around it.
- Deviations and correlations are hypotheses. `correlate` and `scan` return
  confidence intervals and an effective sample size already corrected for
  autocorrelation; an interval crossing zero means "consistent with nothing",
  and a scan means many comparisons were made at once.
- Never compare HRV across devices. WHOOP reports RMSSD, Apple reports SDNN,
  and they are stored under different metric names for that reason.
- For anyone with a menstrual cycle, judge a reading with
  `phase_adjusted_reading` before calling it abnormal — a phase-blind baseline
  flags an ordinary luteal day every month.
- On blood tests: report what is flagged, what the range was, and whether the
  range was the lab's own or a generic one. Do not diagnose, do not suggest
  doses, and send anything outside range to their doctor.
- `search_literature` retrieves evidence; it does not settle anything. Say what
  a paper found, what design it was, and cite it so they can check.
""".strip()

server = MCPServer(name="health", instructions=INSTRUCTIONS)
_config: Config | None = None


def configure(config: Config) -> None:
    global _config
    _config = config


def _store() -> Store:
    config = _config or load_config()
    if not config.db_path.exists():
        raise RuntimeError("No database yet — run `health init` and `health sync`.")
    return Store(config.db_path, read_only=True)


def _day(value: str | None) -> date:
    return date.fromisoformat(value) if value else date.today()


def guarded(fn: Callable[..., dict]) -> Callable[..., dict]:
    """Return failures as data the model can read and act on.

    An exception raised out of a tool reaches the model as "Error executing
    tool X" with the message stripped, so "run `health sync` first" becomes
    invisible exactly when it matters. Returning the message keeps the model
    able to tell you what to do instead of guessing around a silent failure.
    """
    @functools.wraps(fn)
    def wrapper(*args: Any, **kwargs: Any) -> dict:
        try:
            return fn(*args, **kwargs)
        except Exception as exc:  # noqa: BLE001 - surfaced, not swallowed
            return {"error": f"{type(exc).__name__}: {exc}"}
    return wrapper


@server.tool(description="What the database holds: sources, date ranges, row counts.")
@guarded
def coverage() -> dict[str, Any]:
    with _store() as store:
        rows = store.coverage()
        state = store.query("SELECT source, last_ok FROM sync_state ORDER BY source")
    return {
        "tables": [{"source": s, "table": t, "from": str(a), "to": str(b), "rows": n}
                   for s, t, a, b, n in rows],
        "last_successful_sync": {s: str(t) if t else None for s, t in state},
    }


@server.tool(description="A daily metric over a date range, oldest first.")
@guarded
def metric_series(metric: str, start: str | None = None,
                  end: str | None = None) -> dict[str, Any]:
    with _store() as store:
        rows = daily.series(store, metric,
                            date.fromisoformat(start) if start else None,
                            date.fromisoformat(end) if end else None)
    return {"metric": metric, "n": len(rows),
            "points": [{"date": str(d), "value": v} for d, v in rows]}


@server.tool(description="One metric's robust baseline (median and MAD) over the "
                         "days before a date, and whether it is usable yet.")
@guarded
def metric_baseline(metric: str, as_of: str | None = None,
                    window: int = 28) -> dict[str, Any]:
    with _store() as store:
        result = daily.baseline(store, metric, as_of=_day(as_of), window=window)
    return {"metric": metric, "window": window, "n": result.n,
            "median": result.median, "mad": result.mad,
            "usable": result.usable, "note": result.note}


@server.tool(description="Metrics sitting unusually far from their own recent "
                         "baseline on a given day. A shortlist to look at, not "
                         "a list of problems.")
@guarded
def deviations(day: str | None = None, threshold: float = 1.5) -> dict[str, Any]:
    target = _day(day)
    with _store() as store:
        found = daily.deviations(store, target, threshold=threshold)
    return {"date": str(target), "threshold_sd": threshold,
            "deviations": [{"metric": m, "value": v, "z": z} for m, v, z in found]}


@server.tool(description="Relationship between two daily metrics, with a "
                         "confidence interval and a sample size corrected for "
                         "autocorrelation. lag_days=1 asks whether yesterday's "
                         "first metric moves today's second.")
@guarded
def correlate_metrics(metric_a: str, metric_b: str, lag_days: int = 0,
                      start: str | None = None, end: str | None = None) -> dict[str, Any]:
    with _store() as store:
        result = daily.correlate(store, metric_a, metric_b, lag_days=lag_days,
                                 start=date.fromisoformat(start) if start else None,
                                 end=date.fromisoformat(end) if end else None)
    return {"a": result.a, "b": result.b, "lag_days": result.lag_days,
            "r": result.r, "ci": [result.ci_low, result.ci_high],
            "n_days": result.n, "effective_n": result.effective_n,
            "consistent_with_no_effect": result.crosses_zero,
            "summary": result.describe(), "note": result.note}


@server.tool(description="Every metric correlated against one target, strongest "
                         "first. A hypothesis generator: many comparisons at "
                         "once, so expect some to look striking by chance.")
@guarded
def scan_correlations(target: str, lag_days: int = 1,
                      limit: int = 10) -> dict[str, Any]:
    with _store() as store:
        found = daily.scan(store, target, lag_days=lag_days)
    return {"target": target, "comparisons": len(found),
            "results": [{"metric": c.a, "r": c.r, "ci": [c.ci_low, c.ci_high],
                         "effective_n": c.effective_n,
                         "consistent_with_no_effect": c.crosses_zero}
                        for c in found[:limit]]}


@server.tool(description="Acute (7d) against chronic (28d) training load, from "
                         "WHOOP strain and Hevy tonnage.")
@guarded
def training_load(as_of: str | None = None) -> dict[str, Any]:
    with _store() as store:
        load = daily.training_load(store, as_of=_day(as_of))
    return {"acute": load.acute, "chronic": load.chronic, "ratio": load.ratio,
            "summary": load.describe()}


@server.tool(description="Sleep timing and duration variability over recent nights.")
@guarded
def sleep_regularity(days: int = 28, as_of: str | None = None) -> dict[str, Any]:
    with _store() as store:
        return daily.sleep_regularity(store, days=days, as_of=_day(as_of))


@server.tool(description="Strength progression for one exercise: estimated 1RM "
                         "trend with its fit, best ever, and session history.")
@guarded
def lift_progression(exercise: str, window: int = 8) -> dict[str, Any]:
    with _store() as store:
        result = strength_features.progression(store, exercise, window=window)
        history = strength_features.session_history(store, exercise, limit=window)
    return {"exercise": result.exercise, "sessions": result.sessions,
            "trend_kg_per_week": result.trend_kg_per_week,
            "r_squared": result.r_squared, "best_e1rm": result.best_e1rm,
            "days_since_best": result.days_since_best,
            "summary": result.describe(), "note": result.note,
            "recent": [{"date": str(d), "e1rm": e, "top_weight_kg": w,
                        "sets": s, "volume_kg": v} for d, e, w, s, v in history]}


@server.tool(description="Every exercise trained, with best estimated 1RM, last "
                         "session and muscle group. Also lists stale lifts.")
@guarded
def strength_overview() -> dict[str, Any]:
    with _store() as store:
        rows = strength_features.exercise_summary(store)
        stale = strength_features.stale_lifts(store)
    return {
        "exercises": [{**r, "last_done": str(r["last_done"])} for r in rows],
        "stale": [{**r, "last_done": str(r["last_done"])} for r in stale],
    }


@server.tool(description="Weekly training tonnage by muscle group.")
@guarded
def weekly_volume(weeks: int = 8) -> dict[str, Any]:
    with _store() as store:
        rows = strength_features.weekly_volume(store, weeks=weeks)
    return {"weeks": [{"week": str(w), "muscle": m, "volume_kg": v,
                       "sets": s, "sessions": n} for w, m, v, s, n in rows]}


@server.tool(description="Current cycle phase, cycle regularity, and the next "
                         "period estimate. Says so when a day cannot be placed.")
@guarded
def cycle_summary(today: str | None = None) -> dict[str, Any]:
    with _store() as store:
        result = cycle_features.summary(store, today=_day(today))
    return {k: (str(v) if isinstance(v, date) else v) for k, v in result.items()}


@server.tool(description="One day's reading judged twice: against the overall "
                         "baseline, and against the same cycle phase of their "
                         "own previous cycles. Use before calling a reading "
                         "abnormal for anyone with a menstrual cycle.")
@guarded
def phase_adjusted_reading(metric: str, day: str | None = None) -> dict[str, Any]:
    target = _day(day)
    with _store() as store:
        reading = cycle_features.phase_adjusted(store, metric, target)
    return {"metric": metric, "date": str(target), "value": reading.value,
            "phase": reading.phase, "cycle_day": reading.cycle_day,
            "z_overall": reading.overall_z, "n_overall": reading.overall_n,
            "z_within_phase": reading.phase_z, "n_within_phase": reading.phase_n,
            "phase_blind_baseline_would_mislead": reading.verdicts_disagree,
            "summary": reading.describe(), "note": reading.note}


@server.tool(description="How a metric differs between cycle phases, and whether "
                         "that matches the expected physiology.")
@guarded
def cycle_phase_effect(metric: str) -> dict[str, Any]:
    with _store() as store:
        signature = cycle_features.phase_signature(store, metric)
        baselines = cycle_features.phase_baselines(store, metric)
    return {
        "metric": metric,
        "phases": {phase: {"n": s.n, "mean": s.mean, "sd": s.sd, "usable": s.usable}
                   for phase, s in baselines.items()},
        "luteal_minus_follicular": signature["delta"],
        "expected_direction": signature["expected"],
        "matches_expectation": signature["agrees"],
        "note": signature.get("note"),
    }


@server.tool(description="Most recent blood panel: every result with its flag, "
                         "reference range, whether the range was the lab's own "
                         "or generic, and the cycle phase it was drawn in.")
@guarded
def latest_bloods() -> dict[str, Any]:
    with _store() as store:
        values = lab_features.latest_panel(store)
        out_of_range = lab_features.flagged(store)
    return {
        "results": [{"analyte": v.analyte, "label": v.label, "date": str(v.local_date),
                     "value": v.value, "unit": v.unit, "flag": v.flag,
                     "range": v.range_text, "range_source": v.ref_source,
                     "cycle_phase": v.phase, "note": v.note} for v in values],
        "outside_range": [v.analyte for v in out_of_range],
        "caveat": "Report what is flagged and send it to their doctor. Do not "
                  "diagnose or suggest treatment.",
    }


@server.tool(description="One blood analyte over time. Refuses to trend "
                         "cycle-sensitive analytes across different phases, "
                         "because those draws are not comparable.")
@guarded
def blood_trend(analyte: str) -> dict[str, Any]:
    key = canonical_analyte(analyte) or analyte
    with _store() as store:
        result = lab_features.trend(store, key)
        grouped = lab_features.by_phase(store, key)
    spec = ANALYTES.get(key)
    return {
        "analyte": key, "label": result.label,
        "points": [{"date": str(d), "value": v, "cycle_phase": p}
                   for d, v, p in result.points],
        "comparable": result.comparable, "change": result.change,
        "summary": result.describe(), "note": result.note,
        "analyte_note": spec.note if spec else None,
        "by_phase": {phase: [{"date": str(d), "value": v} for d, v in points]
                     for phase, points in grouped.items()} if not result.comparable else None,
    }


@server.tool(description="Search Europe PMC. Returns study design, journal, "
                         "year, citations and DOI. Retrieval only — say what "
                         "each paper found and cite it.")
@guarded
def search_literature(query: str, limit: int = 6,
                      since_year: int = 2015) -> dict[str, Any]:
    papers = search(query, limit=limit, since_year=since_year,
                    designs=["Meta-Analysis", "Systematic Review",
                             "Randomized Controlled Trial", "Review"])
    return {"query": query, "papers": [
        {"title": p.title, "design": p.design, "journal": p.journal,
         "year": p.year, "cited_by": p.cited_by, "open_access": p.open_access,
         "url": p.url, "abstract": (p.abstract or "")[:1200]} for p in papers]}


@server.tool(description="Literature around one of their own blood results, "
                         "using its direction — low and high are different "
                         "literatures.")
@guarded
def literature_for_blood_result(analyte: str, context: str | None = None,
                                limit: int = 6) -> dict[str, Any]:
    key = canonical_analyte(analyte) or analyte
    with _store() as store:
        history = lab_features.history(store, key)
    latest = history[-1] if history else None
    direction = latest.flag if latest and latest.flag in ("low", "high") else None
    query = evidence_query(key, direction, context)
    result = search_literature(query, limit=limit)
    result["their_result"] = None if latest is None else {
        "value": latest.value, "unit": latest.unit, "flag": latest.flag,
        "range": latest.range_text, "date": str(latest.local_date),
    }
    return result


@server.tool(description="A briefing for one day: what the body is saying, "
                         "against baselines and cycle phase where available.")
@guarded
def daily_brief(day: str | None = None) -> dict[str, Any]:
    target = _day(day)
    with _store() as store:
        metrics = store.query(
            "SELECT metric, value, source FROM daily_metrics WHERE local_date = ?",
            [target])
        brief: dict[str, Any] = {
            "date": str(target),
            "metrics": [{"metric": m, "value": v, "source": s} for m, v, s in metrics],
            "deviations": [{"metric": m, "value": v, "z": z}
                           for m, v, z in daily.deviations(store, target)],
            "training_load": daily.training_load(store, as_of=target).describe(),
            "sleep_regularity": daily.sleep_regularity(store, as_of=target),
        }
        cycle = cycle_features.summary(store, today=target)
        if cycle.get("cycles"):
            brief["cycle"] = {k: (str(v) if isinstance(v, date) else v)
                              for k, v in cycle.items()}
            brief["phase_adjusted"] = [
                cycle_features.phase_adjusted(store, metric, target).describe()
                for metric, _, _ in brief["deviations"][:3]
            ]
    return brief


def run(config: Config | None = None) -> None:
    if config:
        configure(config)
    server.run(transport="stdio")
