"""A task whose genome is a prompt.

The agent is given eight questions with exact answers and must return each
answer as a bare value. Grading is programmatic — normalised exact match
against a fixed key — because the agent must never be the judge of whether it
passed. A model asked to grade its own work will learn to declare victory,
and in this lineage declaring victory is what buys the right to reproduce.

What evolves is the wording: the genome carries a list of indices into
:data:`CLAUSES`, and the system prompt is those clauses in order. Alongside
them are three numeric genes — reasoning effort, the answer token ceiling,
and how many times to re-ask when a reply comes back unusable.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from . import promptpack
from .errors import TaskUnavailable
from .llm import EFFORT_LEVELS, Backend, Reply, build_client
from .tasks import Task, Verdict

PASS_MARK = 0.5

BASE_SYSTEM = "You answer short factual questions about the text you are given."

#: The clause pool. Some of these help, some are noise, and two of them are
#: actively counterproductive under exact-match grading — the lineage has to
#: find that out by scoring worse.
CLAUSES: tuple[str, ...] = (
    "Reply with the value only: no units, no punctuation, no explanation.",
    "Write any date in ISO 8601 format (YYYY-MM-DD).",
    "Write any number in digits, with no thousands separators.",
    "Take your time and work carefully.",
    "Restate the question in your answer.",
    "Round to the nearest whole number unless asked otherwise.",
    "Never include a currency symbol.",
    "Show your reasoning in the reply.",
)

#: Clauses that break exact-match grading no matter what the question is.
COUNTERPRODUCTIVE = frozenset({4, 7})


@dataclass(frozen=True)
class Item:
    question: str
    answer: str
    #: Clauses without which a capable model still formats the answer wrongly.
    needs: frozenset = field(default_factory=frozenset)
    #: Minimum effort index the question needs to be got right at all.
    min_effort: int = 0
    #: Answer tokens below which the reply is cut off.
    min_tokens: int = 0


ITEMS: tuple[Item, ...] = (
    Item("Invoice 4471 was raised on the 3rd of February 2024. On what date "
         "was it raised?", "2024-02-03", frozenset({0, 1})),
    Item("How many centimetres are there in 5 feet 4 inches?", "163",
         frozenset({0, 5})),
    Item("A basket holds items at £4.25, £11.00 and £1,284.25. What is the "
         "total?", "1299.50", frozenset({0, 2, 6}), min_tokens=1500),
    Item("Contact us at Sales.Team+eu@example.co.uk or by post. What is the "
         "email address?", "sales.team+eu@example.co.uk", frozenset({0})),
    Item("Write 'twelve thousand and five' in digits.", "12005",
         frozenset({0, 2})),
    Item("What is the third word of the sentence 'the quick brown fox jumps'?",
         "brown", frozenset({0})),
    Item("Of 14 crates, 9 are sealed and the rest are open. How many are "
         "open?", "5", frozenset({0, 2})),
    Item("A job ran from 09:47 to 13:12. How many minutes is that, to the "
         "nearest minute?", "205", frozenset({0, 5}), min_effort=1,
         min_tokens=1500),
)


# --------------------------------------------------------------------------
# The live question set: the built-in one, or whatever the user's file says.
# --------------------------------------------------------------------------

@dataclass(frozen=True)
class Suite:
    job: str
    clauses: tuple[str, ...]
    items: tuple[Item, ...]
    custom: bool = False
    source: str = "built in"


BUILTIN = Suite(BASE_SYSTEM, CLAUSES, ITEMS)

_cached: tuple[str, Suite] | None = None


def suite() -> Suite:
    """The question set in force, reloaded when the file behind it changes."""
    global _cached
    import os

    marker = os.environ.get(promptpack.ENV_PACK, "")
    if _cached is not None and _cached[0] == marker:
        return _cached[1]

    if not marker:
        loaded = BUILTIN
    else:
        pack = promptpack.load(marker)
        loaded = Suite(
            job=pack.job,
            clauses=tuple(pack.instructions),
            items=tuple(Item(i.question, i.answer) for i in pack.items),
            custom=True,
            source=str(pack.path),
        )
    _cached = (marker, loaded)
    return loaded


def build_system(clauses, active: Suite | None = None) -> str:
    """The evolved system prompt: the job line plus the chosen clauses."""
    active = active or suite()
    chosen = [active.clauses[i] for i in clauses if 0 <= i < len(active.clauses)]
    return "\n".join([active.job, *chosen])


def normalise(text: str) -> str:
    """Trim the shapes a bare value legitimately arrives in — nothing more."""
    cleaned = text.strip().strip("`").strip()
    cleaned = cleaned.strip('"').strip("'").strip()
    cleaned = re.sub(r"[.\s]+$", "", cleaned)
    return cleaned.lower()


def looks_unusable(text: str) -> bool:
    """Whether a reply is visibly not a bare value.

    This is the only self-assessment the agent is allowed: it can see that a
    paragraph is not an answer, but it cannot see whether the answer is right.
    """
    stripped = text.strip()
    return not stripped or len(stripped.split()) > 12


# --------------------------------------------------------------------------
# Offline stand-in
# --------------------------------------------------------------------------

class SimulatedBackend(Backend):
    """A deterministic model that behaves the way a real one roughly does.

    It answers cleanly when the prompt actually asks for a bare value, dresses
    the answer up when the prompt invites prose, and gets cut off when the
    token ceiling is too low. Used by the tests and by anyone who wants to
    watch the lineage run without spending anything.
    """

    name = "simulated"

    def complete(self, *, system: str, user: str, effort: str,
                 max_tokens: int) -> Reply:
        active = suite()
        if active.custom:
            # Nothing is known about how a hand-written question responds to
            # wording, so the stand-in just answers it. Useful for proving the
            # file parses and the loop runs; useless as a score.
            item = next((i for i in active.items if i.question in user), None)
            return Reply(text=item.answer if item else "", output_tokens=24)

        present = {i for i, clause in enumerate(CLAUSES) if clause in system}
        # A firm instruction in the user turn is the last thing the model
        # reads, so it overrides the standing style clauses.
        nudged = CLAUSES[0] in user
        if nudged:
            present = (present - COUNTERPRODUCTIVE) | {0}
        item = next((i for i in ITEMS if i.question in user), None)
        if item is None:
            return Reply(text="", output_tokens=8)

        effort_index = EFFORT_LEVELS.index(effort)
        cost = lambda words: 20 + 15 * effort_index + 8 * words  # noqa: E731

        if item.min_tokens and max_tokens < item.min_tokens:
            return Reply(text="", output_tokens=cost(0))  # truncated
        if effort_index < item.min_effort:
            return Reply(text="i am not sure", output_tokens=cost(4))
        if present & COUNTERPRODUCTIVE:
            return Reply(text=f"The answer to your question is {item.answer}, "
                              f"which follows from the details you gave.",
                         output_tokens=cost(14))
        if not item.needs <= present:
            return Reply(text=f"About {item.answer} (approximately).",
                         output_tokens=cost(4))
        return Reply(text=item.answer, output_tokens=cost(1))


# --------------------------------------------------------------------------
# The task
# --------------------------------------------------------------------------

class LLMPromptTask(Task):
    name = "llm"
    description = "answer a fixed question set through Claude; the prompt evolves"
    genes = ("llm_effort", "llm_answer_tokens", "llm_reask_limit")
    evolves_prompt = True

    def clause_pool(self) -> int:
        return len(suite().clauses)

    def run(self, params, *, seed: int = 0, clauses=()) -> Verdict:
        effort_index = int(params.get("llm_effort", 1))
        effort = EFFORT_LEVELS[max(0, min(len(EFFORT_LEVELS) - 1, effort_index))]
        max_tokens = int(params.get("llm_answer_tokens", 1024))
        reasks = int(params.get("llm_reask_limit", 0))

        active = suite()
        client = build_client()
        system = build_system(clauses, active)
        plainest = active.clauses[0] if active.clauses else CLAUSES[0]

        correct = 0
        unusable = 0
        refusals = 0
        for item in active.items:
            reply = client.ask(system=system, user=item.question, effort=effort,
                               max_tokens=max_tokens)
            attempt = 0
            while attempt < reasks and (reply.refused or looks_unusable(reply.text)):
                attempt += 1
                # The only lever available mid-run: ask again, and put the
                # formatting demand last, where it carries the most weight.
                reply = client.ask(
                    system=system,
                    user=f"{item.question}\n\n{plainest}",
                    effort=effort, max_tokens=max_tokens,
                )
            refusals += 1 if reply.refused else 0
            unusable += 1 if looks_unusable(reply.text) else 0
            correct += 1 if normalise(reply.text) == normalise(item.answer) else 0

        total = len(active.items)
        accuracy = correct / total
        avg_tokens = client.output_tokens / total
        # Accuracy is what counts; the token term is a light nudge away from
        # buying marginal accuracy with unbounded verbosity.
        score = accuracy - 0.05 * min(1.0, avg_tokens / 2000.0)
        detail = (f"{correct}/{total} exact, {unusable} unusable, "
                  f"{client.calls} calls ({client.cache_hits} cached), "
                  f"{avg_tokens:.0f} output tokens/item")
        if active.custom and client.backend.name == "simulated":
            detail += " [simulated backend on a custom file: scores mean nothing]"
        return Verdict(
            passed=accuracy >= PASS_MARK,
            score=score,
            detail=detail,
            metrics={"correct": correct, "items": total,
                     "accuracy": round(accuracy, 4), "unusable": unusable,
                     "refusals": refusals, "calls": client.calls,
                     "cache_hits": client.cache_hits,
                     "avg_output_tokens": round(avg_tokens, 1),
                     "effort": effort, "clauses": list(clauses),
                     "suite": active.source},
        )


def register(registry: dict) -> None:
    registry[LLMPromptTask.name] = LLMPromptTask()


__all__ = ["CLAUSES", "ITEMS", "LLMPromptTask", "SimulatedBackend", "Suite",
           "TaskUnavailable", "build_system", "normalise", "register", "suite"]
