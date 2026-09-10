"""How a surviving generation proposes the genome of its successor.

Plain seeded hill-climbing: perturb one or two parameters, clamp to the
declared bounds, and let the caller keep the child only if it actually scores
better. The strategy is deliberately boring — the interesting part of this
package is the lifecycle around it, and a deterministic mutator keeps runs
reproducible and testable.
"""

from __future__ import annotations

import hashlib
import random

#: Fraction of a parameter's range a single mutation may move it.
STEP = 0.35


def propose(params: dict, bounds: dict, *, rng: random.Random,
            integral=frozenset(), pressure: int = 0,
            only=()) -> tuple[dict, str]:
    """Return ``(child_params, description)``.

    ``pressure`` is how many children this parent has already had rejected.
    A parent stuck on a plateau takes wider steps rather than proposing the
    same near-identical child forever. ``only`` restricts mutation to the
    parameters the current task actually reads.
    """
    allowed = set(only) if only else None
    keys = sorted(k for k in params
                  if k in bounds and (allowed is None or k in allowed))
    if not keys:
        return dict(params), "no mutable parameters"

    reach = STEP * min(3.0, 1.0 + 0.5 * max(0, pressure))

    # A clamped mutation can land back on the parent's value; try again rather
    # than spending a cycle on a child identical to its parent.
    for attempt in range(6):
        count = 1 if rng.random() < 0.65 else 2
        chosen = rng.sample(keys, min(count, len(keys)))
        child = dict(params)
        notes = []
        for key in chosen:
            low, high = bounds[key]
            span = high - low
            delta = rng.gauss(0.0, span * reach / 2.0)
            if key in integral:
                delta = round(delta) or rng.choice((-1, 1))
            value = max(low, min(high, child[key] + delta))
            child[key] = (int(round(value)) if key in integral
                          else round(float(value), 4))
            notes.append(f"{key} {params[key]!r}->{child[key]!r}")
        if child != params:
            suffix = f" (widened x{reach / STEP:.1f})" if pressure else ""
            return child, "mutated " + ", ".join(notes) + suffix

    return dict(params), "mutation was a no-op"


def propose_clauses(clauses, pool_size: int, *, rng: random.Random,
                    pressure: int = 0) -> tuple[list[int], str]:
    """Add, drop, swap or reorder one clause of an evolving prompt.

    Prompt wording is a gene like any other here: the lineage keeps a change
    only if the change scores better on the same graded question set.
    """
    original = [int(c) for c in clauses]
    if pool_size <= 0:
        return list(original), "no clause move available"

    # As with the numeric genes, a move that lands back on the parent is not
    # a child: try again rather than spend a cycle proving that.
    for _ in range(6):
        current = list(original)
        edits = 2 if pressure >= 3 and rng.random() < 0.5 else 1
        notes = []
        for _ in range(edits):
            available = [i for i in range(pool_size) if i not in current]
            # A short prompt has more to gain from another clause than from
            # losing one, so the move is weighted by how full the prompt is.
            moves, weights = [], []
            if available:
                moves.append("add")
                weights.append(len(available))
            if current:
                moves.append("drop")
                weights.append(len(current))
            if current and available:
                moves.append("swap")
                weights.append(2)
            if len(current) > 1:
                moves.append("move")
                weights.append(1)
            if not moves:
                break
            move = rng.choices(moves, weights=weights, k=1)[0]
            if move == "add":
                clause = rng.choice(available)
                current.insert(rng.randint(0, len(current)), clause)
                notes.append(f"+clause {clause}")
            elif move == "drop":
                clause = rng.choice(current)
                current.remove(clause)
                notes.append(f"-clause {clause}")
            elif move == "swap":
                old = rng.choice(current)
                new = rng.choice(available)
                current[current.index(old)] = new
                notes.append(f"clause {old}->{new}")
            else:
                clause = rng.choice(current)
                rest = [c for c in current if c != clause]
                position = rng.choice(
                    [i for i in range(len(rest) + 1)
                     if rest[:i] + [clause] + rest[i:] != current]
                )
                current = rest[:position] + [clause] + rest[position:]
                notes.append(f"moved clause {clause} to position {position}")
        if notes and current != original:
            return current, "prompt " + ", ".join(notes)

    return list(original), "no clause move available"


def seed_for(generation: str, cycle: int) -> int:
    """A stable RNG seed, so a lineage replays identically.

    ``hash()`` is salted per process, so it cannot be used here: two runs of
    the same lineage must propose the same children.
    """
    digest = hashlib.blake2b(f"{generation}:{cycle}".encode(), digest_size=8)
    return int.from_bytes(digest.digest(), "big") % (2**31)
