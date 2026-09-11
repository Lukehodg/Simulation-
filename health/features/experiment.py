"""The n-of-1 trial: a pre-registered schedule and an exact test on it.

`recovery_drivers` and `divergence_history` produce hypotheses and stop there
— a correlation is a lead, not an answer. This is where a lead becomes one:
state the hypothesis, the metric, and the exact block schedule before any data
exists, then at the end compare block-level outcomes with a test built for
exactly this design.

The design is an alternating-block n-of-1: `blocks_planned` blocks of
`block_days` days each, alternating between control (**A**) and exposed
(**B**), the starting condition fixed at registration. The schedule is a pure
function of the pre-registration — `block_windows` below — so nothing about it
is stored or needs rebuilding; only the events that define it and, for a
`manual` exposure, the day-by-day adherence log are real tables.

The test is an **exact permutation test** over the blocks' outcome medians,
one-sided in the pre-registered direction. It is exact rather than asymptotic
because the sample is a handful of blocks, not enough for a normal
approximation to mean anything — and its own floor (the smallest p it could
ever report, set entirely by how many blocks there are) is always reported
alongside the result, the same discipline `trend.py` applies to autocorrelation
and `daily.correlate` applies to effective sample size: the honesty is in
naming the method's own limit, not just its output.
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass, field
from datetime import date, timedelta
from itertools import combinations

from .. import metrics as M
from ..store import Store
from . import daily

CONTROL, EXPOSED = "A", "B"
MIN_BLOCKS_PER_CONDITION = 2
#: A block needs at least half its days with an outcome reading before its
#: median is trusted rather than merely reported.
THIN_BLOCK_FRACTION = 0.5


@dataclass
class Experiment:
    id: str
    hypothesis: str
    exposure_type: str
    outcome_metric: str
    predicted_direction: str
    block_days: int
    blocks_planned: int
    start_date: date
    starting_condition: str
    exposure_metric: str | None = None
    exposure_threshold: float | None = None
    stopped: bool = False
    stop_date: date | None = None
    stop_reason: str | None = None


@dataclass
class Block:
    index: int
    condition: str
    start: date
    end: date
    days_with_data: int = 0
    days_total: int = 0
    adherent_days: int = 0
    outcome_median: float | None = None
    thin: bool = False


def _row_to_experiment(row: tuple) -> Experiment:
    (eid, hyp, etype, emetric, ethresh, outcome, direction, bdays, bplanned,
     start, starting) = row
    return Experiment(id=eid, hypothesis=hyp, exposure_type=etype,
                      exposure_metric=emetric, exposure_threshold=ethresh,
                      outcome_metric=outcome, predicted_direction=direction,
                      block_days=bdays, blocks_planned=bplanned,
                      start_date=start, starting_condition=starting)


_START_COLUMNS = ("experiment_id", "hypothesis", "exposure_type", "exposure_metric",
                  "exposure_threshold", "outcome_metric", "predicted_direction",
                  "block_days", "blocks_planned", "start_date", "starting_condition")


def load(store: Store, experiment_id: str) -> Experiment | None:
    rows = store.query(
        f"SELECT {', '.join(_START_COLUMNS)} FROM experiment_events "
        "WHERE experiment_id = ? AND event = 'start'", [experiment_id])
    if not rows:
        return None
    exp = _row_to_experiment(rows[0])
    stop = store.query(
        "SELECT local_date, reason FROM experiment_events "
        "WHERE experiment_id = ? AND event = 'stop'", [experiment_id])
    if stop:
        exp.stopped, exp.stop_date, exp.stop_reason = True, stop[0][0], stop[0][1]
    return exp


def all_experiments(store: Store) -> list[Experiment]:
    ids = [r[0] for r in store.query(
        "SELECT DISTINCT experiment_id FROM experiment_events WHERE event = 'start' "
        "ORDER BY 1")]
    return [e for i in ids if (e := load(store, i)) is not None]


def block_windows(exp: Experiment) -> list[Block]:
    """The whole pre-registered schedule — every block, past or future."""
    other = EXPOSED if exp.starting_condition == CONTROL else CONTROL
    conditions = [exp.starting_condition if i % 2 == 0 else other
                 for i in range(exp.blocks_planned)]
    out = []
    day = exp.start_date
    for i, condition in enumerate(conditions):
        end = day + timedelta(days=exp.block_days - 1)
        out.append(Block(index=i, condition=condition, start=day, end=end))
        day = end + timedelta(days=1)
    return out


def current_block(exp: Experiment, as_of: date | None = None) -> Block | None:
    as_of = as_of or date.today()
    return next((b for b in block_windows(exp) if b.start <= as_of <= b.end), None)


def status(exp: Experiment, as_of: date | None = None) -> str:
    if exp.stopped:
        return "stopped"
    as_of = as_of or date.today()
    return "complete" if as_of > block_windows(exp)[-1].end else "running"


def adherence(store: Store, exp: Experiment, day: date, condition: str) -> bool | None:
    """Did the day actually realise the block's assigned condition?

    `manual`: the logged exception, defaulting to True — a day nobody flagged
    is assumed adherent (intention-to-treat), which is the conservative
    assumption for a self-run trial.

    `metric_threshold`: read directly off the metric already being tracked —
    True when the day's value sits on the side of the threshold the condition
    calls for, None when there is no reading that day.
    """
    if exp.exposure_type == "manual":
        row = store.query(
            "SELECT adhered FROM experiment_adherence "
            "WHERE experiment_id = ? AND local_date = ?", [exp.id, day])
        return bool(row[0][0]) if row else True

    value = store.query(
        "SELECT value FROM daily_metrics WHERE metric = ? AND local_date = ?",
        [exp.exposure_metric, day])
    if not value or value[0][0] is None:
        return None
    exposed = value[0][0] >= exp.exposure_threshold
    return exposed == (condition == EXPOSED)


def block_results(store: Store, exp: Experiment, as_of: date | None = None) -> list[Block]:
    """Every block whose window has fully elapsed, with its outcome median.

    A stopped trial collected nothing past its stop date, whatever `as_of`
    is asked for — the cutoff is the earlier of the two.
    """
    as_of = as_of or date.today()
    if exp.stopped and exp.stop_date is not None:
        as_of = min(as_of, exp.stop_date)
    out = []
    for block in block_windows(exp):
        if block.end > as_of:
            break
        series = dict(daily.series(store, exp.outcome_metric, block.start, block.end))
        values = [v for d, v in series.items() if v is not None]
        block.days_total = (block.end - block.start).days + 1
        block.days_with_data = len(values)
        block.adherent_days = sum(
            1 for i in range(block.days_total)
            if adherence(store, exp, block.start + timedelta(days=i), block.condition) is True)
        block.thin = block.days_with_data < block.days_total * THIN_BLOCK_FRACTION
        if values:
            block.outcome_median = round(statistics.median(values), 4)
        out.append(block)
    return out


def _exact_permutation_p(by_condition: dict[str, list[float]],
                         predicted_direction: str) -> dict:
    """One-sided exact permutation test over the block medians.

    Every way to relabel the pooled block values into groups the size of the
    observed split is enumerated; `p` is the fraction at least as extreme, in
    the pre-registered direction, as what was actually observed. `p_floor` is
    the smallest p this design could ever report — set by the block count
    alone, before a single number is looked at.
    """
    control, exposed = by_condition[CONTROL], by_condition[EXPOSED]
    pooled = control + exposed
    n_exposed = len(exposed)
    sign = 1.0 if predicted_direction == "raises" else -1.0

    observed = (statistics.mean(exposed) - statistics.mean(control)) * sign
    n_total = 0
    n_at_least_as_extreme = 0
    for combo in combinations(range(len(pooled)), n_exposed):
        b = [pooled[i] for i in combo]
        a = [pooled[i] for i in set(range(len(pooled))) - set(combo)]
        stat = (statistics.mean(b) - statistics.mean(a)) * sign
        n_total += 1
        if stat >= observed - 1e-9:
            n_at_least_as_extreme += 1

    return {
        "observed_diff": round((statistics.mean(exposed) - statistics.mean(control)), 4),
        "p": round(n_at_least_as_extreme / n_total, 4),
        "n_permutations": n_total,
        "p_floor": round(1 / n_total, 4),
    }


def analyse(store: Store, exp: Experiment, as_of: date | None = None) -> dict:
    """The pre-registered comparison, run once enough blocks exist."""
    blocks = block_results(store, exp, as_of=as_of)
    usable = [b for b in blocks if b.outcome_median is not None and not b.thin]
    by_condition: dict[str, list[float]] = {CONTROL: [], EXPOSED: []}
    for b in usable:
        by_condition[b.condition].append(b.outcome_median)

    result: dict = {
        "id": exp.id, "hypothesis": exp.hypothesis,
        "exposure": exp.exposure_metric or "manual",
        "outcome_metric": exp.outcome_metric,
        "predicted_direction": exp.predicted_direction,
        "blocks_completed": len(blocks), "blocks_usable": len(usable),
        "blocks_planned": exp.blocks_planned,
        "blocks": [{"index": b.index, "condition": b.condition,
                    "start": str(b.start), "end": str(b.end),
                    "days_with_data": b.days_with_data, "days_total": b.days_total,
                    "adherent_days": b.adherent_days, "outcome_median": b.outcome_median,
                    "thin": b.thin} for b in blocks],
        "status": status(exp, as_of=as_of),
    }
    if exp.stopped:
        result["stopped_early"] = len(blocks) < exp.blocks_planned
        result["stop_reason"] = exp.stop_reason

    n_control, n_exposed = len(by_condition[CONTROL]), len(by_condition[EXPOSED])
    if n_control < MIN_BLOCKS_PER_CONDITION or n_exposed < MIN_BLOCKS_PER_CONDITION:
        result["verdict"] = "too early"
        result["note"] = (f"need at least {MIN_BLOCKS_PER_CONDITION} usable blocks of "
                          f"each condition; have {n_control} control, {n_exposed} exposed")
        return result

    test = _exact_permutation_p(by_condition, exp.predicted_direction)
    result["test"] = test

    direction_observed = ("raises" if test["observed_diff"] > 0 else
                          "lowers" if test["observed_diff"] < 0 else None)
    confirms = direction_observed == exp.predicted_direction
    label = M.unit_for(exp.outcome_metric) or ""
    if confirms and test["p"] <= test["p_floor"] + 1e-9:
        result["verdict"] = "an effect in the predicted direction"
    elif confirms:
        result["verdict"] = ("a trend in the predicted direction, not "
                             "distinguishable from chance with this many blocks")
    else:
        result["verdict"] = "no support for the predicted direction"
    result["note"] = (
        f"{exp.outcome_metric} moved {test['observed_diff']:+.3g} {label} "
        f"(exposed vs control); exact one-sided permutation test over "
        f"{test['n_permutations']} possible block splits — the smallest p this "
        f"design could ever report is {test['p_floor']}, and this result is "
        f"p={test['p']}"
    )
    return result
