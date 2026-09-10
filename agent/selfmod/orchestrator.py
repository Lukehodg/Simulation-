"""Starts lineages and hands control to whichever generation is still alive.

The orchestrator never evaluates a genome itself. It looks up the head of the
lineage, then runs *that generation's own copy of the package* in a separate
process, so the code deciding to upgrade or delete itself is always the code
under test.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

from . import genome, lineage, llm, promptpack
from .agent import IGNORE
from .sandbox import MARKER, Sandbox

RESULT_PREFIX = "##OUTCOME##"


def source_root() -> Path:
    """The pristine tree in the repository: read-only as far as the agent knows."""
    return Path(__file__).resolve().parent.parent


def generations_dir(workspace: Path) -> Path:
    return Path(workspace) / "generations"


def init(workspace: Path, *, force: bool = False) -> dict:
    # Read the question file before anything is created, so a typo in it
    # leaves no half-seeded workspace behind.
    pack = promptpack.active()

    workspace = Path(workspace).resolve()
    workspace.mkdir(parents=True, exist_ok=True)
    ledger = lineage.Ledger(workspace)

    if ledger.entries() and not force:
        head = ledger.head()
        return {"created": False,
                "detail": "workspace already has a lineage; use --force to reseed",
                "head": head.name if head else None}

    if force and generations_dir(workspace).exists():
        sandbox = Sandbox(workspace)
        for child in sorted(generations_dir(workspace).iterdir()):
            if child.is_dir():
                sandbox.delete_tree(child, armed=True, reason="reseed")
        if ledger.path.exists():
            ledger.path.unlink()
        ledger = lineage.Ledger(workspace)

    generations_dir(workspace).mkdir(parents=True, exist_ok=True)
    name = ledger.next_name()
    home = generations_dir(workspace) / name
    shutil.copytree(source_root() / "selfmod", home / "selfmod", ignore=IGNORE)
    (home / MARKER).write_text(
        json.dumps({"parent": None, "born_from": str(source_root())}) + "\n",
        encoding="utf-8",
    )
    # A question file may say which instructions generation 1 starts with;
    # that seeds the prompt gene of the copy, not the source tree.
    clauses = list(genome.PROMPT_CLAUSES)
    detail = "seeded from source tree"
    if pack is not None:
        clauses = list(pack.start)
        genome.rewrite(home / "selfmod" / "genome.py", generation=1,
                       ancestry="seed", params=dict(genome.PARAMS),
                       clauses=clauses, pool=len(pack.instructions))
        detail = f"seeded from source tree with questions from {pack.path}"

    ledger.record(lineage.BORN, name, parent=None, params=dict(genome.PARAMS),
                  clauses=clauses, detail=detail)
    return {"created": True, "head": name, "home": str(home),
            "questions": str(pack.path) if pack else None}


def run_cycle(workspace: Path, *, task: str = "pathfind", seed: int = 7,
              armed: bool = True, cycle: int = 0) -> dict:
    workspace = Path(workspace).resolve()
    ledger = lineage.Ledger(workspace)
    head = ledger.head()
    if head is None:
        if not ledger.entries():
            return {"action": "uninitialised",
                    "detail": "no lineage here yet; run `python -m selfmod init`"}
        return {"action": "extinct",
                "detail": "no living generation; the lineage has died out"}

    home = generations_dir(workspace) / head.name
    if not home.is_dir():
        return {"action": "missing",
                "detail": f"head {head.name} is recorded alive but {home} is gone"}

    env = dict(os.environ)
    env["PYTHONPATH"] = str(home) + os.pathsep + env.get("PYTHONPATH", "")
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    # One cache for the whole lineage, kept above the generations so it
    # survives them: an unchanged genome re-proves itself for free.
    env.setdefault(llm.ENV_CACHE, str(workspace / "llm-cache"))
    env["SELFMOD_WORKSPACE"] = str(workspace)
    argv = [sys.executable, "-m", "selfmod", "cycle",
            "--home", str(home), "--workspace", str(workspace),
            "--task", task, "--seed", str(seed), "--cycle", str(cycle)]
    if not armed:
        argv.append("--dry-run")

    proc = subprocess.run(argv, cwd=str(workspace), env=env,
                          capture_output=True, text=True, timeout=600)
    for line in proc.stdout.splitlines():
        if line.startswith(RESULT_PREFIX):
            return json.loads(line[len(RESULT_PREFIX):])

    # The head could not even reach a verdict — a mutation that produced
    # unrunnable source, say. Failing to run is failing the task, so the
    # supervisor applies the same rule the generation would have applied to
    # itself, and the lineage rolls back to the parent.
    detail = ((proc.stderr or proc.stdout).strip().splitlines() or
              ["generation produced no outcome"])[-1][-300:]
    ledger.record(lineage.TERMINATED, head.name, score=None,
                  detail=f"failed to run: {detail}", task=task)
    if not armed:
        return {"action": "unrunnable", "generation": head.name, "detail": detail}
    report = Sandbox(workspace).delete_tree(
        home, armed=True, reason="generation could not run"
    )
    return {"action": "terminated-by-supervisor", "generation": head.name,
            "detail": detail, "deletion": report.as_dict()}


STOP_ACTIONS = ("extinct", "uninitialised", "missing", "error", "terminated",
                "unrunnable", "terminated-by-supervisor", "task-unavailable")


def evolve(workspace: Path, *, cycles: int = 5, task: str = "pathfind",
           seed: int = 7, armed: bool = True, patience: int = 0,
           on_step=None) -> list[dict]:
    """Run cycles until the lineage stops, or stops paying for itself.

    ``patience`` ends the run after that many consecutive cycles without a
    kept upgrade; 0 means run every requested cycle.
    """
    results = []
    barren = 0
    for index in range(cycles):
        outcome = run_cycle(workspace, task=task, seed=seed, armed=armed,
                            cycle=index)
        results.append(outcome)
        if on_step is not None:
            on_step(index, outcome)
        if outcome.get("action") in STOP_ACTIONS:
            break
        barren = 0 if outcome.get("action") == "upgraded" else barren + 1
        if patience and barren >= patience:
            if on_step is not None:
                on_step(index, {"action": "plateau", "generation": None,
                                "detail": f"no upgrade in {patience} cycles"})
            break
    return results


def status(workspace: Path) -> dict:
    workspace = Path(workspace).resolve()
    ledger = lineage.Ledger(workspace)
    gens = ledger.generations()
    head = ledger.head()
    on_disk = []
    if generations_dir(workspace).is_dir():
        on_disk = sorted(p.name for p in generations_dir(workspace).iterdir()
                         if p.is_dir())
    living = {g.name for g in ledger.living()}
    scored = [g for g in gens.values() if g.score is not None]
    best = max(scored, key=lambda g: g.score) if scored else None
    return {
        "workspace": str(workspace),
        "generations_recorded": len(gens),
        "living": [g.name for g in ledger.living()],
        "on_disk": on_disk,
        "orphaned_on_disk": sorted(set(on_disk) - living),
        "missing_from_disk": sorted(living - set(on_disk)),
        "head": head.name if head else None,
        "head_score": head.score if head else None,
        "best": {"generation": best.name, "score": best.score} if best else None,
    }
