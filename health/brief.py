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
from .features import cycle as cycle_features
from .features import strength as strength_features
from .features import training as training_features
from .llm import client as _client
from .store import Store

MODEL = "claude-sonnet-5"
MAX_TOKENS = 4000

SYSTEM = """
You are writing one person a short brief on their own health data. They train
seriously and want to know what today's numbers mean and what to do about them.
You are not their doctor.

What you are given is already computed: robust baselines and z-scores, an
acute:chronic training-load ratio, a sleep-debt estimate, a readiness
recommendation with its reasons, cycle phase where it is known, and — for the
weekly brief — correlations and strength-versus-recovery breakdowns. Every
figure you cite must come from this payload. Do not estimate, do not recall
typical values, do not compute your own.

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

- NO SCORE. Do not invent an overall readiness or health score, grade, or
  percentage. The readiness field is a recommendation with reasons; report it
  that way.

- ESCALATE, don't manage, a real clinical shape. Sustained low energy
  availability with high training load and cycle disruption is the RED-S
  signature and the right response is "these three together are worth raising
  with a doctor", not a nutrition tweak.

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
        "training_load": daily.training_load(store, as_of=day).describe(),
        "sleep_regularity": daily.sleep_regularity(store, as_of=day),
        "training_observations": training_features.observations(store, as_of=day),
        "yesterdays_workouts": _workouts_between(
            store, day - timedelta(days=1), day - timedelta(days=1)),
    }
    cycle_block = _cycle_block(store, day, deviations)
    if cycle_block:
        payload["cycle"] = cycle_block
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

    return {
        "week": f"{start} to {end}",
        "daily_series": {k: v for k, v in series.items() if v},
        "training_load_now": load_now.describe(),
        "training_load_a_week_ago": load_prev.describe(),
        "weekly_volume_by_muscle": [
            {"week": str(w), "muscle": m, "volume_kg": vol, "sets": s}
            for w, m, vol, s, _ in strength_features.weekly_volume(store, weeks=2)],
        "readiness_today": readiness.readiness(store, end).as_dict(),
        "sleep_debt": readiness.sleep_debt(store, as_of=end).as_dict(),
        "recovery_drivers": readiness.recovery_drivers(store, end=end),
        "strength_vs_recovery": readiness.strength_recovery_link(store, end=end),
        "training_observations": training_features.observations(store, as_of=end),
        "phase_training_plan": readiness.phase_training_plan(store, today=end),
    }


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
