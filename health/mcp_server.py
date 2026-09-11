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
from .features import checkin as checkin_features
from .features import cycle as cycle_features
from .features import daily, labs as lab_features
from .features import experiment as experiment_features
from .features import protocol as protocol_features
from .features import readiness as readiness_features
from .features import sleep as sleep_features
from .features import strength as strength_features
from .features import trend as trend_features
from .research import evidence_query, search
from .store import Store

INSTRUCTIONS = """
Tools over one person's own health data: WHOOP, Garmin, Hevy, MyFitnessPal,
their menstrual cycle where logged, and their blood tests.

How to use them well:

- Every number you report should come from a tool call, not from memory or
  arithmetic of your own. If a tool says a baseline is unusable or a sample is
  too small, say that rather than working around it.
- Deviations and correlations are hypotheses. `correlate`, `scan` and
  `recovery_drivers` return confidence intervals and an effective sample size
  already corrected for autocorrelation; an interval crossing zero means
  "consistent with nothing", and a scan means many comparisons were made at once.
- `readiness_call` gives a training recommendation with its reasons. Report the
  recommendation and the reasons; do not turn it into a number or a percentage.
  Training advice may be concrete — rep ranges, a load cap, train or skip —
  when it is tied to a specific figure it returned.
- If `protocol` returns compounds, use them as context: a metric that moved the
  way a compound is documented to move it is expected; one moving the opposite
  way is the informative case. Escalate a trending monitoring marker to "worth a
  doctor". Never comment on the dose, ancillary drugs, or PCT — reporting a
  documented effect is not advising on the protocol.
- `subjective_vs_objective` and `check_in` carry blood pressure and how the
  person rated themselves. Report both directions of disagreement — feeling
  worse than the data, and feeling better than it — not just the alarming one.
  `blood_pressure`'s verdict of "see a doctor" is exactly that; never turn it
  into dosing advice.
- `experiment_analysis` always returns its permutation test's p-value floor
  alongside the p-value — report both. Never say "significant" or "proven";
  use the `verdict` field's own wording. A result is this one person's twelve
  weeks, not a general finding.
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


@server.tool(description="Readiness to train on one day: a recommendation "
                         "(push / proceed / hold / pull_back) with the recovery, "
                         "load, sleep-debt and cycle-phase reasons behind it. "
                         "Deliberately not a score.")
@guarded
def readiness_call(day: str | None = None) -> dict[str, Any]:
    with _store() as store:
        return readiness_features.readiness(store, _day(day)).as_dict()


@server.tool(description="Rolling sleep debt over recent nights, against a need "
                         "estimated from the person's own better-recovered days "
                         "rather than a population figure.")
@guarded
def sleep_debt(as_of: str | None = None, days: int = 14) -> dict[str, Any]:
    with _store() as store:
        return readiness_features.sleep_debt(store, as_of=_day(as_of),
                                             days=days).as_dict()


@server.tool(description="Which of the person's own behaviours actually move "
                         "their recovery, over a window. Autocorrelation-"
                         "corrected intervals; only relationships whose interval "
                         "clears zero are returned, with the comparison count.")
@guarded
def recovery_drivers(days: int = 90) -> dict[str, Any]:
    with _store() as store:
        return readiness_features.recovery_drivers(store, days=days)


@server.tool(description="Whether the person's best strength sessions land on "
                         "their best-recovered days, and whether training "
                         "through a red day costs them. Per-exercise, with n on "
                         "every bucket.")
@guarded
def strength_recovery_link(days: int = 180) -> dict[str, Any]:
    with _store() as store:
        return readiness_features.strength_recovery_link(store, days=days)


@server.tool(description="Where heavy blocks and deloads should fall across the "
                         "next few weeks of the cycle, checked against the "
                         "person's own phase signature. Says so when there is no "
                         "cycle data.")
@guarded
def phase_training_plan(weeks: int = 4) -> dict[str, Any]:
    with _store() as store:
        return readiness_features.phase_training_plan(store, weeks=weeks)


@server.tool(description="What the person is on (compounds / peptides), what each "
                         "is documented to do to their metrics and bloods, the "
                         "monitoring markers a clinician should watch, and what a "
                         "next blood panel should include. Reports documented "
                         "effects only — never dosing, ancillary or PCT advice.")
@guarded
def protocol(day: str | None = None) -> dict[str, Any]:
    with _store() as store:
        return protocol_features.summary(store, today=_day(day))


@server.tool(description="Is a metric's baseline itself trending over the last "
                         "six weeks — the slow slide a day-vs-baseline check "
                         "cannot see. Robust slope plus a Mann-Kendall test with "
                         "the sample size corrected for autocorrelation.")
@guarded
def metric_trend(metric: str, days: int = 42) -> dict[str, Any]:
    with _store() as store:
        return trend_features.metric_trend(store, metric, days=days).as_dict()


@server.tool(description="Last night's sleep architecture — deep, REM, and time "
                         "awake after falling asleep — each against the person's "
                         "own recent baseline, plus a running deep-sleep debt.")
@guarded
def sleep_quality(days: int = 28, as_of: str | None = None) -> dict[str, Any]:
    with _store() as store:
        return sleep_features.sleep_quality(store, as_of=_day(as_of), days=days)


@server.tool(description="Whether resting HR, respiration and skin temperature "
                         "are rising together — a pattern that often precedes "
                         "illness by a day or two. Subtracts the resting-HR rise "
                         "a GLP-1 agonist is expected to cause. A flag to rest "
                         "and watch, never a diagnosis.")
@guarded
def illness_watch(as_of: str | None = None) -> dict[str, Any]:
    with _store() as store:
        return readiness_features.illness_watch(store, as_of=_day(as_of))


@server.tool(description="One model of a recovery metric on all the candidate "
                         "behaviours at once, so an effect can be told from its "
                         "confounder. Standardised coefficients, R-squared, and a "
                         "collinearity note. Observational and in-sample.")
@guarded
def drivers_model(target: str = "hrv_rmssd", days: int = 90) -> dict[str, Any]:
    with _store() as store:
        return readiness_features.drivers_model(store, target=target, days=days)


@server.tool(description="Home blood pressure — the 7-day average, per-day "
                         "history, a six-week trend, and a verdict (ok / watch "
                         "/ see a doctor). Compound-aware: notes when an active "
                         "androgen is documented to raise it. Never a dose.")
@guarded
def blood_pressure(days: int = 7, as_of: str | None = None) -> dict[str, Any]:
    with _store() as store:
        return checkin_features.blood_pressure(store, as_of=_day(as_of), days=days)


@server.tool(description="Today's check-in (blood pressure, how they rated "
                         "energy/mood/stress/sleep/libido/GI, and their note), "
                         "or recent history. Pass history=true for the last "
                         "`days` entries instead of just today.")
@guarded
def check_in(day: str | None = None, history: bool = False,
            days: int = 14) -> dict[str, Any]:
    with _store() as store:
        if history:
            return {"entries": [e.as_dict()
                                for e in checkin_features.history(store, days=days)]}
        entry = checkin_features.latest(store, _day(day))
        return entry.as_dict() if entry else {"logged": False}


@server.tool(description="Each check-in rating against the computed state for "
                         "the same day — where they agree, and where how the "
                         "person feels and what the numbers say pull apart. "
                         "Both directions are worth reporting.")
@guarded
def subjective_vs_objective(day: str | None = None) -> dict[str, Any]:
    with _store() as store:
        return checkin_features.subjective_vs_objective(store, _day(day))


@server.tool(description="Every pre-registered n-of-1 trial and where it stands "
                         "— which block, which condition, days remaining. Not "
                         "the analysis; call experiment_analysis for that.")
@guarded
def experiments() -> dict[str, Any]:
    with _store() as store:
        out = []
        for exp in experiment_features.all_experiments(store):
            block = experiment_features.current_block(exp)
            out.append({
                "id": exp.id, "hypothesis": exp.hypothesis,
                "status": experiment_features.status(exp),
                "current_block": {"index": block.index, "condition": block.condition,
                                  "start": str(block.start), "end": str(block.end)}
                                 if block else None,
                "outcome_metric": exp.outcome_metric,
                "predicted_direction": exp.predicted_direction,
            })
        return {"experiments": out}


@server.tool(description="The pre-registered comparison for one trial: block-"
                         "level outcomes, the exact permutation test, its own "
                         "p-value floor, and a verdict that is never "
                         "'significant' or 'proven'. Personal to this one "
                         "person's blocks, not a general finding.")
@guarded
def experiment_analysis(experiment_id: str) -> dict[str, Any]:
    with _store() as store:
        exp = experiment_features.load(store, experiment_id)
        if exp is None:
            return {"error": f"no experiment called {experiment_id!r}"}
        return experiment_features.analyse(store, exp)


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
            "readiness": readiness_features.readiness(store, target).as_dict(),
            "sleep_debt": readiness_features.sleep_debt(store, as_of=target).as_dict(),
            "sleep_architecture": sleep_features.sleep_quality(store, as_of=target),
            "training_load": daily.training_load(store, as_of=target).describe(),
            "sleep_regularity": daily.sleep_regularity(store, as_of=target),
            "six_week_trends": [t.as_dict()
                                for t in trend_features.trends(store, as_of=target)],
            "illness_watch": readiness_features.illness_watch(store, as_of=target),
        }
        on = protocol_features.summary(store, today=target)
        if on.get("on"):
            brief["protocol"] = on
        comparison = checkin_features.subjective_vs_objective(store, target)
        if comparison.get("rows"):
            brief["check_in"] = comparison
        bp = checkin_features.blood_pressure(store, as_of=target)
        if bp.get("verdict"):
            brief["blood_pressure"] = bp
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
