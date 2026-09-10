"""Derived features.

Everything here is computed from the canonical tables and nothing here talks to
the network. These are the functions the agent's tools will call, so they
return numbers *with* their sample size and uncertainty rather than bare
point estimates — a slope over three sessions and a slope over thirty should
not look identical by the time they reach a language model.
"""

from . import cycle, daily, labs, readiness
from .cycle import (
    Cycle,
    Reading,
    cycles,
    phase_adjusted,
    phase_baselines,
    phase_signature,
)
from .strength import (
    Progression,
    exercise_summary,
    progression,
    session_history,
    stale_lifts,
    weekly_volume,
)

__all__ = [
    "daily", "labs", "readiness",
    "Cycle", "Reading", "cycle", "cycles", "phase_adjusted", "phase_baselines",
    "phase_signature",
    "Progression", "exercise_summary", "progression", "session_history",
    "stale_lifts", "weekly_volume",
]
