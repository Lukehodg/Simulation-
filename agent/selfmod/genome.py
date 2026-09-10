"""The mutable part of the agent — and the code that rewrites it.

The block between the two markers below is real source that the agent edits
when it upgrades itself. A child generation is a copy of this package whose
``genome.py`` carries different literals, so successive generations differ in
code, not merely in a config file they happen to read.
"""

from __future__ import annotations

import re
from pathlib import Path

# --- genome:begin --- (rewritten by selfmod.genome.rewrite; edit by hand freely)
GENERATION = 1
ANCESTRY = "seed"
PARAMS = {
    "beam_width": 4,
    "heuristic_weight": 1.1,
    "step_budget": 200,
    "retry_limit": 0,
    "tie_breaker": 0.05,
    "llm_effort": 1,
    "llm_answer_tokens": 1024,
    "llm_reask_limit": 0,
}
PROMPT_CLAUSES = [0, 2]
# --- genome:end ---

#: Inclusive bounds every mutation is clamped to. Not itself mutable: this is
#: the envelope the lineage is allowed to explore.
BOUNDS: dict[str, tuple[float, float]] = {
    "beam_width": (1, 64),
    "heuristic_weight": (0.5, 4.0),
    "step_budget": (40, 4000),
    "retry_limit": (0, 4),
    "tie_breaker": (0.0, 1.0),
    "llm_effort": (0, 4),
    "llm_answer_tokens": (256, 8000),
    "llm_reask_limit": (0, 3),
}

#: Parameters that must stay whole numbers when mutated.
INTEGRAL = frozenset({"beam_width", "step_budget", "retry_limit", "llm_effort",
                      "llm_answer_tokens", "llm_reask_limit"})

#: How many clauses the prompt gene may draw on. Kept here rather than in the
#: task so the mutator never has to import a task to know the shape of a gene.
CLAUSE_POOL = 8

BEGIN = "# --- genome:begin ---"
END = "# --- genome:end ---"


def current() -> dict:
    """The genome of the package this call is executing from."""
    return {
        "generation": GENERATION,
        "ancestry": ANCESTRY,
        "params": dict(PARAMS),
        "clauses": list(PROMPT_CLAUSES),
    }


def clamp(params: dict) -> dict:
    out: dict[str, float] = {}
    for key, value in params.items():
        low, high = BOUNDS.get(key, (float("-inf"), float("inf")))
        value = max(low, min(high, value))
        out[key] = int(round(value)) if key in INTEGRAL else round(float(value), 4)
    return out


def clamp_clauses(clauses) -> list[int]:
    """Valid clause indices, de-duplicated, order preserved."""
    seen: list[int] = []
    for value in clauses:
        index = int(value)
        if 0 <= index < CLAUSE_POOL and index not in seen:
            seen.append(index)
    return seen


def render_block(generation: int, ancestry: str, params: dict,
                 clauses=()) -> str:
    lines = [
        BEGIN + " (rewritten by selfmod.genome.rewrite; edit by hand freely)",
        f"GENERATION = {int(generation)}",
        f"ANCESTRY = {ancestry!r}",
        "PARAMS = {",
    ]
    for key in sorted(params):
        lines.append(f"    {key!r}: {params[key]!r},")
    lines.append("}")
    lines.append(f"PROMPT_CLAUSES = {clamp_clauses(clauses)!r}")
    lines.append(END)
    return "\n".join(lines)


def rewrite(path: Path, *, generation: int, ancestry: str, params: dict,
            clauses=()) -> str:
    """Replace the genome block inside the ``genome.py`` file at ``path``."""
    source = Path(path).read_text(encoding="utf-8")
    pattern = re.compile(
        re.escape(BEGIN) + r".*?" + re.escape(END), re.DOTALL
    )
    if not pattern.search(source):
        raise ValueError(f"no genome block found in {path}")
    block = render_block(generation, ancestry, clamp(params), clauses)
    updated = pattern.sub(lambda _: block, source, count=1)
    Path(path).write_text(updated, encoding="utf-8")
    return block


def diff(before: dict, after: dict) -> str:
    """Human-readable summary of what a mutation changed."""
    changes = []
    for key in sorted(set(before) | set(after)):
        old, new = before.get(key), after.get(key)
        if old != new:
            changes.append(f"{key} {old}->{new}")
    return ", ".join(changes) if changes else "no change"
