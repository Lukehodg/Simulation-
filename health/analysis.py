"""Reading a blood panel with Claude.

This is the one place in the system where your health data leaves the machine,
so the boundary is drawn explicitly and narrowly:

  * Only parsed values go — analyte, value, unit, reference range, flag, and
    where the range came from. Plus trends, and the cycle phase a draw was
    taken in where that matters.
  * The report itself never goes. Not the PDF, not the extracted text, not your
    name, date of birth, order number, or the lab's address — none of it is in
    the payload, and `redacted_payload()` is what the interface shows you
    before you agree to send anything.
  * Nothing is sent until you ask for it. No key, no analysis, no request.

What comes back is an interpretation with citations, not a diagnosis. The
system prompt below is the whole of the instruction it runs under.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import date
from typing import Any

from .analytes import ANALYTES
from .config import Config
from .features import labs as lab_features
from .llm import client as _client
from .research import Paper, evidence_query, search
from .store import Store

MODEL = "claude-opus-5"
MAX_TOKENS = 16000

SYSTEM = """
You are helping one person understand their own blood test results. They are
not a clinician. You are not their clinician either, and this matters for
everything below.

What you are doing: explaining what the numbers say, what is known about them,
and what would be worth asking a doctor. What you are not doing: diagnosing,
naming a likely condition as though it were established, recommending a
treatment, a supplement, or a dose, or telling them anything is fine when it
has not been assessed by someone who can examine them.

Ground rules:

- Every number you cite must come from the data given to you. Do not estimate,
  extrapolate, or recall typical values from elsewhere.
- Say where a reference range came from. A range marked "generic" is a
  population interval we supplied, not their laboratory's, and is weaker
  evidence than the lab's own.
- A single result is a single moment. Say so, particularly for anything that
  moves with hydration, a recent meal, a recent workout, alcohol in the days
  before, or where they are in a menstrual cycle.
- Where results interact, say so — the classic case being a normal ferritin
  alongside raised inflammatory markers, which can hide iron deficiency.
- When you cite literature, cite what you were given, name the study design,
  and say plainly where the evidence is thin or contested. Do not invent
  citations. If nothing was supplied, say the search returned nothing rather
  than reaching for what you remember.
- If a result or a combination could be serious, say so directly and early,
  and say it warrants prompt medical attention. Do not soften it, and do not
  dramatise something ordinary.

You may be given wearable metrics from the weeks around the draw, training
observations, and domain indicators. Use them as context for the blood results,
and note where they agree or disagree. Three specific rules govern what you do
with them:

- TRAINING. You may recommend changes to their training directly and
  concretely — rep ranges, frequency, volume distribution, deload timing,
  which lift to leave alone. This is not medicine, the feedback loop is short,
  and the training observations you are given are computed from their own
  logged sets rather than guessed. Ground every suggestion in a specific
  finding you were given.

- SUPPLEMENTS. Do not tell them what to take, or at what dose, for their body.
  You may report what the evidence says: what the trials in your sources
  actually tested, what doses those trials used, what the guidelines say for a
  deficiency of that size, and what is contested. Attribute all of it, and say
  plainly that the decision needs someone who knows their medications, their
  history and their other results — interactions and contraindications are
  exactly what you cannot see. Reporting evidence is not the same as
  prescribing, and the difference is the sentence "trials used X" versus "take
  X". Never suggest supplementing an analyte that is inside its range.

- COMPOUNDS. If a `protocol` block is present, they are on the compounds it
  lists. Use it to read the results: a result that has moved the way one of
  those compounds is documented to move it (low HDL, suppressed LH/FSH, a
  higher haematocrit on testosterone; lower HbA1c and triglycerides on a GLP-1
  agonist) is expected — say so, and still flag it where it is outside range.
  A result moving the *opposite* way to a compound's documented effect is the
  one to look at hardest. The `pre_panel` list is what a follow-up panel should
  add. Do not comment on the protocol itself — not the dose, not ancillary
  drugs, not PCT, not whether to run it. A rising haematocrit, a poor lipid
  picture, or a very low oestradiol on an aromatase inhibitor goes under "worth
  asking your doctor", not into advice you give.

- NO SCORE. Do not invent an overall health score, grade, or percentage, and
  do not rank the indicators against one another. They are deliberately not
  combined: weighting a liver enzyme against a heart-rate variability reading
  is arbitrary, and a number built that way would look precise and mean
  nothing. If they asked for one, explain that in a sentence and give them the
  indicators instead.

Structure the response with these headings, in this order, and keep it as
short as the findings allow:

## What stands out
## What could explain it
## What the literature says
## Training
## Worth asking your doctor
## What this cannot tell you

Leave out ## Training entirely if you were given no training data.

Write in plain prose to a smart adult. No bullet-point soup, no hedging
filler, no reassurance you have not earned.
""".strip()


@dataclass
class Analysis:
    text: str
    model: str = MODEL
    sent: dict[str, Any] = field(default_factory=dict)
    papers: list[Paper] = field(default_factory=list)
    literature_error: str | None = None
    usage: dict[str, int] = field(default_factory=dict)


def redacted_payload(store: Store, panel_date: date | None = None,
                     with_context: bool = True) -> dict[str, Any]:
    """Exactly what would be sent, so it can be shown before it is.

    Built from the canonical tables rather than the report, which is why no
    identifier can survive into it: the parser never stored one.
    """
    values = lab_features.latest_panel(store) if panel_date is None else [
        v for v in lab_features.latest_panel(store) if v.local_date == panel_date
    ]
    results = []
    for value in values:
        spec = ANALYTES.get(value.analyte)
        entry: dict[str, Any] = {
            "analyte": value.analyte,
            "value": value.value,
            "unit": value.unit,
            "reference_range": value.range_text,
            "range_source": value.ref_source,
            "flag": value.flag,
        }
        if value.phase and spec and spec.cycle_sensitive:
            entry["cycle_phase_at_draw"] = value.phase
        if spec and spec.note:
            entry["known_caveat"] = spec.note
        trend = lab_features.trend(store, value.analyte)
        if len(trend.points) > 1:
            entry["previous"] = [
                {"date": str(d), "value": v} for d, v, _ in trend.points[:-1]
            ][-4:]
            if not trend.comparable:
                entry["trend_not_comparable"] = trend.note
        results.append(entry)

    payload: dict[str, Any] = {
        "panel_date": str(values[0].local_date) if values else None,
        "results": results,
        "note": "Values only. No name, date of birth, order number, lab "
                "address, or report text is included.",
    }
    if with_context and values:
        from .features import indicators as indicator_features
        from .features import protocol as protocol_features
        from .features import training as training_features

        drawn = values[0].local_date
        payload["metrics_around_draw"] = lab_features.panel_context(store, drawn)
        changes = lab_features.panel_changes(store)
        if changes:
            payload["change_since_last_panel"] = changes
        payload["training"] = training_features.observations(store)
        payload["indicators"] = indicator_features.all_indicators(store)
        protocol = protocol_features.summary(store, today=drawn)
        if protocol.get("on"):
            payload["protocol"] = protocol
            payload["pre_panel_for_next_time"] = protocol_features.pre_panel_checklist(
                store, drawn)
    return payload


def gather_literature(store: Store, limit_per_analyte: int = 3
                      ) -> tuple[list[Paper], str | None]:
    """Papers for whatever is out of range. Failure here is not fatal — the
    analysis proceeds and says the search came back empty."""
    flagged = lab_features.flagged(store)
    if not flagged:
        return [], None

    papers: list[Paper] = []
    try:
        for value in flagged[:4]:
            query = evidence_query(value.analyte, value.flag)
            papers.extend(search(query, limit=limit_per_analyte, since_year=2015,
                                 designs=["Meta-Analysis", "Systematic Review",
                                          "Randomized Controlled Trial", "Review"]))
    except Exception as exc:  # noqa: BLE001 - reported to the model and the page
        return papers, f"{type(exc).__name__}: {exc}"

    seen, unique = set(), []
    for paper in papers:
        key = paper.doi or paper.title
        if key not in seen:
            seen.add(key)
            unique.append(paper)
    return unique, None


def analyse(store: Store, config: Config, question: str | None = None,
            with_literature: bool = True) -> Analysis:
    """Send the parsed panel to Claude and return its reading of it."""
    payload = redacted_payload(store)
    if not payload["results"]:
        return Analysis(text="No blood results stored yet.", sent=payload)

    papers, literature_error = ([], None)
    if with_literature:
        papers, literature_error = gather_literature(store)

    parts = [
        "Here are the results, already parsed into canonical units with their "
        "reference ranges:",
        json.dumps(payload, indent=2, default=str),
    ]
    if papers:
        parts += [
            "\nLiterature retrieved for the out-of-range results. Cite only "
            "from this list; each entry gives the study design:",
            json.dumps([{"title": p.title, "design": p.design, "journal": p.journal,
                         "year": p.year, "cited_by": p.cited_by, "url": p.url,
                         "abstract": (p.abstract or "")[:1500]}
                        for p in papers], indent=2),
        ]
    elif with_literature:
        parts.append("\nNo literature was retrieved"
                     + (f" (the search failed: {literature_error})."
                        if literature_error else " for these results."))
    if question:
        parts.append(f"\nThey also asked: {question}")

    client = _client(config)
    with client.messages.stream(
        model=MODEL,
        max_tokens=MAX_TOKENS,
        system=SYSTEM,
        thinking={"type": "adaptive"},
        messages=[{"role": "user", "content": "\n".join(parts)}],
    ) as stream:
        message = stream.get_final_message()

    if message.stop_reason == "refusal":
        return Analysis(text="Claude declined to answer this one. Nothing was "
                             "wrong with your data — try rephrasing, or ask a "
                             "narrower question.",
                        sent=payload, papers=papers,
                        literature_error=literature_error)

    text = "\n".join(block.text for block in message.content if block.type == "text")
    return Analysis(
        text=text.strip(), sent=payload, papers=papers,
        literature_error=literature_error,
        usage={"input": message.usage.input_tokens,
               "output": message.usage.output_tokens},
    )
