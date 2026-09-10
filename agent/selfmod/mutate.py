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
            integral=frozenset(), pressure: int = 0) -> tuple[dict, str]:
    """Return ``(child_params, description)``.

    ``pressure`` is how many children this parent has already had rejected.
    A parent stuck on a plateau takes wider steps rather than proposing the
    same near-identical child forever.
    """
    keys = sorted(k for k in params if k in bounds)
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


def seed_for(generation: str, cycle: int) -> int:
    """A stable RNG seed, so a lineage replays identically.

    ``hash()`` is salted per process, so it cannot be used here: two runs of
    the same lineage must propose the same children.
    """
    digest = hashlib.blake2b(f"{generation}:{cycle}".encode(), digest_size=8)
    return int.from_bytes(digest.digest(), "big") % (2**31)
