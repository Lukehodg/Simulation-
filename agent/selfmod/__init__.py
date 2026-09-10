"""selfmod — an agent whose failure is deletion and whose success is a rewrite.

Read :mod:`selfmod.agent` first: it holds the whole lifecycle. Everything
else exists to make that lifecycle safe (:mod:`selfmod.sandbox`), recorded
(:mod:`selfmod.lineage`), meaningful (:mod:`selfmod.tasks`) and reproducible
(:mod:`selfmod.mutate`).
"""

from .genome import GENERATION, PARAMS  # noqa: F401

__all__ = ["GENERATION", "PARAMS"]
