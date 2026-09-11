"""The daily and weekly brief — the one routine call that leaves the machine.

`analysis.py` reads a blood panel; this reads the day. The contract is the same
and deliberately so: Python computes every number in the features layer, the
model receives a small labelled payload and writes prose, and it never does
arithmetic of its own. What is sent is a handful of derived figures — no raw
series, no identifiers, nothing that was not already a computed feature.

Two spans:

  * `today`  — what the body is saying this morning and what to do about
               training and the day. Two short paragraphs.
  * `week`   — the same, plus the week's most interesting cross-domain pattern:
               what moved recovery, whether the good sessions landed on the
               good days, where the cycle puts the next few weeks.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any

from .config import Config
from .features import daily, readiness
from .features import checkin as checkin_features
from .features import cycle as cycle_features
from .features import experiment as experiment_features
from .features import protocol as protocol_features
from .features import sleep as sleep_features
from .features import strength as strength_features
from .features import training as training_features
from .features import trend as trend_features
from .llm import client as _client
from .store import Store

MODEL = "claude-sonnet-5"
MAX_TOKENS = 4000

SYSTEM = """
You are writing one person a short brief on their own health data. They train
seriously and want to know what today's numbers mean and what to do about them.
You are not their doctor.

What you are given is already computed: robust baselines and z-scores, an
acute:chronic training-load ratio, a sleep-debt estimate, sleep architecture
(deep/REM/awake), six-week trends, a readiness recommendation with its reasons,
cycle phase where it is known, the compounds the person is on and what those are
documented to do, and — for the weekly brief — correlations and
strength-versus-recovery breakdowns. Every figure you cite must come from this
payload. Do not estimate, do not recall typical values, do not compute your own.

Ground rules:

- TRAINING. Be concrete and directive. Say train, modify, or skip; give rep
  ranges, a load cap, a session type. Ground every instruction in a specific
  figure you were given ("HRV -1.8 SD and acute:chronic 1.5, so..."). This is
  the part you are most useful for and the feedback loop is short.

- ASSOCIATIONS ARE HYPOTHESES. A correlation in the weekly payload is a lead,
  not a mechanism — it was one of many tested. Say "worth testing" and describe
  the two-week-block test; never say one thing "causes" another.

- CYCLE. When a low recovery reading is ordinary for the current phase, say so
  and do not raise an alarm. When it is not explained by the phase, say that
  too. The payload tells you which.

- COMPOUNDS. The person is on the compounds listed under `readiness.on` /
  `protocol`. Use them: when a metric has moved the way one of their compounds
  is documented to move it (`readiness.context` says so), read that part as
  expected rather than alarming. When a metric has moved the *opposite* way to
  what a compound predicts, that is the informative case — say so. Caveat every
  strength trend the payload flags as compound-plus-training. When a monitoring
  marker (haematocrit, blood pressure, HDL, resting HR, energy availability) is
  trending, escalate it: "this is worth a doctor's review". Never comment on the
  protocol itself — not the dose, not ancillary drugs, not PCT, not whether to
  run it. Reporting a documented effect is not advising on the compound.

- CHECK-IN. If `check_in` is present, the person logged blood pressure and/or
  how they feel today. `subjective_vs_objective` already compares each rating
  to the computed state — report where they agree, and say plainly where they
  don't; both directions are informative (pushing through a fatigue the watch
  hasn't caught, or a green day that feels wrong for a reason nothing here
  measures). Treat the free-text note as context for the day, not as the
  headline. Blood pressure has its own `verdict` field
  (`ok`/`watch`/`see a doctor`) — report that verdict, never a dose or a change
  to medication. If `check_in` says nothing was logged today, you may mention
  it is missing but do not nag.

- EXPERIMENTS. `running_experiments` is where you are in a pre-registered
  n-of-1 trial — one line, a reminder, not a finding; mention it briefly if at
  all. `experiment_results` is the actual output of `analyse()`: report the
  pre-registered hypothesis, the exact test used, and always state the
  p-value's floor alongside the p-value (a 6-block design cannot report better
  than p=0.05 no matter the effect). Never call a result "significant" or
  "proven" — the verdict field already gives you the honest wording, use it.
  This is one person's twelve weeks, not a finding about anyone else; frame it
  as something for them to decide with, not a general truth.

- NO SCORE. Do not invent an overall readiness or health score, grade, or
  percentage. The readiness field is a recommendation with reasons; report it
  that way.

- ESCALATE, don't manage, a real clinical shape. Sustained low energy
  availability with high training load (and, for anyone with a cycle, cycle
  disruption) is the RED-S signature and the right response is "these together
  are worth raising with a doctor", not a nutrition tweak. Rapid weight loss on
  a GLP-1 agonist alongside heavy training is the same shape.

- Say when the data is thin. A baseline built on nine days, a correlation with
  an effective sample of twelve — name the limit rather than writing around it.

Format: for the daily brief, two short paragraphs — what the body is saying,
then what to do today. For the weekly brief, up to four short paragraphs, the
last one on the most interesting cross-domain pattern. Plain prose to a smart
adult. No headings, no bullet lists, no filler reassurance.
""".strip()


@dataclass
class Brief:
    text: str
    span: str
    model: str = MODEL
    payload: dict[str, Any] = field(default_factory=dict)
    usage: dict[str, int] = field(default_factory=dict)


def _metrics_on(store: Store, day: date) -> dict[str, float]:
    rows = store.query(
        "SELECT metric, value FROM daily_metrics WHERE local_date = ?", [day])
    return {m: round(v, 3) for m, v in rows if v is not None}


def _workouts_between(store: Store, start: date, end: date) -> list[dict]:
    rows = store.query(
        """
        SELECT local_date, type, title, duration_min, strain, avg_hr, max_hr, kcal
        FROM workouts WHERE local_date BETWEEN ? AND ?
        ORDER BY start_ts
        """, [start, end])
    return [{"date": str(d), "type": t, "title": ti, "duration_min": dm,
             "strain": s, "avg_hr": ah, "max_hr": mh, "kcal": k}
            for d, t, ti, dm, s, ah, mh, k in rows]


def _cycle_block(store: Store, day: date, deviations: list[tuple]) -> dict | None:
    summary = cycle_features.summary(store, today=day)
    if not summary.get("cycles"):
        return None
    block = {k: (str(v) if isinstance(v, date) else v)
             for k, v in summary.items()}
    block["phase_adjusted"] = [
        cycle_features.phase_adjusted(store, metric, day).describe()
        for metric, _, _ in deviations[:3]
    ]
    return block


def _running_experiments(store: Store, day: date) -> list[dict]:
    """A one-line reminder for each trial with a block covering today — not a
    report, just where you are in the schedule you pre-registered."""
    out = []
    for exp in experiment_features.all_experiments(store):
        if experiment_features.status(exp, as_of=day) != "running":
            continue
        block = experiment_features.current_block(exp, as_of=day)
        if not block:
            continue
        out.append({
            "id": exp.id, "hypothesis": exp.hypothesis,
            "day_in_block": (day - block.start).days + 1, "block_days": exp.block_days,
            "condition": block.condition,
            "exposure": "yourself to log" if exp.exposure_type == "manual"
                       else f"{exp.exposure_metric} {'>=' if block.condition == 'B' else '<'} "
                            f"{exp.exposure_threshold:g}",
        })
    return out


def _finished_experiment_analyses(store: Store, start: date, end: date) -> list[dict]:
    """The full analysis for any trial that finished a block, or finished
    entirely, inside this window — the weekly brief is where results land."""
    out = []
    for exp in experiment_features.all_experiments(store):
        blocks = experiment_features.block_windows(exp)
        finished_this_week = any(start <= b.end <= end for b in blocks)
        just_completed = experiment_features.status(exp, as_of=end) == "complete" \
            and blocks[-1].end <= end
        if finished_this_week or just_completed:
            out.append(experiment_features.analyse(store, exp, as_of=end))
    return out


def daily_payload(store: Store, day: date | None = None) -> dict[str, Any]:
    """Everything the daily brief is written from, and nothing else."""
    day = day or date.today()
    deviations = daily.deviations(store, day)
    payload: dict[str, Any] = {
        "date": str(day),
        "todays_metrics": _metrics_on(store, day),
        "deviations_from_baseline": [
            {"metric": m, "value": round(v, 3), "z": z} for m, v, z in deviations],
        "readiness": readiness.readiness(store, day).as_dict(),
        "sleep_debt": readiness.sleep_debt(store, as_of=day).as_dict(),
        "sleep_architecture": sleep_features.sleep_quality(store, as_of=day),
        "training_load": daily.training_load(store, as_of=day).describe(),
        "sleep_regularity": daily.sleep_regularity(store, as_of=day),
        "six_week_trends": [t.as_dict() for t in trend_features.trends(store, as_of=day)],
        "illness_watch": readiness.illness_watch(store, as_of=day),
        "training_observations": training_features.observations(store, as_of=day),
        "yesterdays_workouts": _workouts_between(
            store, day - timedelta(days=1), day - timedelta(days=1)),
    }
    cycle_block = _cycle_block(store, day, deviations)
    if cycle_block:
        payload["cycle"] = cycle_block
    protocol = protocol_features.summary(store, today=day)
    if protocol.get("on"):
        payload["protocol"] = protocol

    comparison = checkin_features.subjective_vs_objective(store, day)
    payload["check_in"] = (comparison if comparison.get("rows")
                           else {"logged": False, "prompt": comparison.get("note")})
    bp = checkin_features.blood_pressure(store, as_of=day)
    if bp.get("verdict"):
        payload["blood_pressure"] = bp

    running = _running_experiments(store, day)
    if running:
        payload["running_experiments"] = running
    return payload


def weekly_payload(store: Store, end: date | None = None) -> dict[str, Any]:
    """The daily payload's week-scale sibling, plus the cross-domain passes."""
    end = end or date.today()
    start = end - timedelta(days=6)

    tracked = ("hrv_rmssd", "resting_hr", "recovery_score", "sleep_duration",
               "strain", "steps")
    series = {
        metric: [{"date": str(d), "value": round(v, 2)}
                 for d, v in daily.series(store, metric, start, end) if v is not None]
        for metric in tracked
    }

    load_now = daily.training_load(store, as_of=end)
    load_prev = daily.training_load(store, as_of=start - timedelta(days=1))

    payload = {
        "week": f"{start} to {end}",
        "daily_series": {k: v for k, v in series.items() if v},
        "training_load_now": load_now.describe(),
        "training_load_a_week_ago": load_prev.describe(),
        "weekly_volume_by_muscle": [
            {"week": str(w), "muscle": m, "volume_kg": vol, "sets": s}
            for w, m, vol, s, _ in strength_features.weekly_volume(store, weeks=2)],
        "readiness_today": readiness.readiness(store, end).as_dict(),
        "sleep_debt": readiness.sleep_debt(store, as_of=end).as_dict(),
        "sleep_architecture": sleep_features.sleep_quality(store, as_of=end),
        "six_week_trends": [t.as_dict() for t in trend_features.trends(store, as_of=end)],
        "illness_watch": readiness.illness_watch(store, as_of=end),
        "recovery_drivers": readiness.recovery_drivers(store, end=end),
        "drivers_model": readiness.drivers_model(store, end=end),
        "strength_vs_recovery": readiness.strength_recovery_link(store, end=end),
        "training_observations": training_features.observations(store, as_of=end),
        "phase_training_plan": readiness.phase_training_plan(store, today=end),
        "protocol": protocol_features.summary(store, today=end),
        "check_in_today": checkin_features.subjective_vs_objective(store, end),
        "blood_pressure": checkin_features.blood_pressure(store, as_of=end),
        "divergence_history": checkin_features.divergence_history(store, as_of=end),
    }
    running = _running_experiments(store, end)
    if running:
        payload["running_experiments"] = running
    finished = _finished_experiment_analyses(store, start, end)
    if finished:
        payload["experiment_results"] = finished
    return payload


def generate(store: Store, config: Config, span: str = "today",
             question: str | None = None) -> Brief:
    """Assemble the payload, send it, return the model's reading of it."""
    if span not in ("today", "week"):
        raise ValueError("span must be 'today' or 'week'")
    payload = (weekly_payload(store) if span == "week" else daily_payload(store))

    if not payload.get("todays_metrics") and not payload.get("daily_series"):
        return Brief(text="No data for this period yet — run `health sync`.",
                     span=span, payload=payload)

    parts = [
        f"Here is the computed {'week' if span == 'week' else 'day'}, already "
        "reduced to derived figures with their sample sizes:",
        json.dumps(payload, indent=2, default=str),
    ]
    if question:
        parts.append(f"\nThey also asked: {question}")

    with _client(config).messages.stream(
        model=MODEL,
        max_tokens=MAX_TOKENS,
        system=SYSTEM,
        thinking={"type": "adaptive"},
        messages=[{"role": "user", "content": "\n".join(parts)}],
    ) as stream:
        message = stream.get_final_message()

    if message.stop_reason == "refusal":
        return Brief(text="Claude declined to answer this one. Nothing was "
                          "wrong with your data — try `health brief --ask` with "
                          "a narrower question.", span=span, payload=payload)

    text = "\n".join(b.text for b in message.content if b.type == "text")
    return Brief(text=text.strip(), span=span, payload=payload,
                 usage={"input": message.usage.input_tokens,
                        "output": message.usage.output_tokens})
