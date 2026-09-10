"""The lifecycle: attempt the task, then either upgrade or delete yourself.

An agent instance *is* a directory — a copy of this package under the
workspace. When the code in that directory runs and fails its task, it
removes the directory it is running from. When it succeeds, it writes a
mutated copy of its own source as the next generation and keeps that copy
only if the copy proves itself better.
"""

from __future__ import annotations

import json
import os
import random
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

from . import genome, lineage, llm, mutate, tasks
from .errors import TaskUnavailable
from .sandbox import MARKER, Sandbox

IGNORE = shutil.ignore_patterns("__pycache__", "*.pyc", "workspace", ".git")

#: A child must beat its parent by at least this much to be kept.
IMPROVEMENT_EPSILON = 1e-6


@dataclass
class Outcome:
    generation: str
    action: str
    verdict: dict
    child: str | None = None
    detail: str = ""
    deletion: dict | None = None
    notes: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "generation": self.generation,
            "action": self.action,
            "verdict": self.verdict,
            "child": self.child,
            "detail": self.detail,
            "deletion": self.deletion,
            "notes": list(self.notes),
        }


class Agent:
    """One generation, living one cycle."""

    def __init__(self, home: Path, workspace: Path, *, task: str = "pathfind",
                 seed: int = 7, armed: bool = True, cycle: int = 0):
        self.home = Path(home).resolve()
        self.workspace = Path(workspace).resolve()
        self.task_name = task
        self.seed = seed
        self.armed = armed
        self.cycle = cycle
        self.ledger = lineage.Ledger(self.workspace)
        self.sandbox = Sandbox(self.workspace)
        self.name = self.home.name

    # -- introspection ----------------------------------------------------
    @property
    def is_generation(self) -> bool:
        """True when this code is running from a disposable copy of itself."""
        return (self.home / MARKER).is_file() and self.sandbox.contains(self.home)

    def genome(self) -> dict:
        return genome.current()

    # -- lifecycle --------------------------------------------------------
    def live(self) -> Outcome:
        task = tasks.get(self.task_name)
        try:
            verdict = task.run(genome.PARAMS, seed=self.seed,
                               clauses=genome.PROMPT_CLAUSES)
        except TaskUnavailable as exc:
            # Could not be judged at all — a missing key, a dead network, a
            # spent budget. Failing to be judged is not failing, so nothing
            # is deleted and the head stays where it is.
            note = f"task unavailable: {exc}"
            self.ledger.record(lineage.SPARED, self.name, detail=note)
            return Outcome(self.name, "task-unavailable",
                           {"passed": None, "score": None, "detail": note},
                           detail=note, notes=[note])

        if not verdict.passed:
            return self.self_destruct(verdict)

        self.ledger.record(
            lineage.SURVIVED, self.name, score=verdict.score,
            detail=verdict.detail, task=self.task_name,
        )
        return self.upgrade(verdict)

    def rejection_streak(self) -> int:
        """How many children in a row this generation has already lost."""
        streak = 0
        for entry in reversed(self.ledger.entries()):
            if entry.get("parent") != self.name:
                continue
            event = entry.get("event")
            if event == lineage.REJECTED:
                streak += 1
            elif event == lineage.UPGRADED:
                break  # the streak ended the last time a child was kept
        return streak

    def self_destruct(self, verdict) -> Outcome:
        """Failed the task: erase the directory this code is running from."""
        if not self.armed:
            # A rehearsal must leave the lineage exactly as it found it: no
            # tombstone, so this generation is still the head afterwards.
            note = "dry run: would terminate"
            # No score is recorded: a rehearsal, possibly against a different
            # task, must not overwrite what this generation actually scored.
            self.ledger.record(lineage.SPARED, self.name,
                               detail=f"{note} ({verdict.detail})")
            report = self.sandbox.delete_tree(
                self.home, armed=False, reason=f"failed {self.task_name}"
            )
            action = ("termination-refused" if report.refusal
                      else "termination-simulated")
            return Outcome(self.name, action, verdict.as_dict(),
                           detail=verdict.detail, deletion=report.as_dict(),
                           notes=[note])

        # The tombstone is written first and fsynced, because the ledger lives
        # in the workspace root and must outlive the directory below it.
        self.ledger.record(
            lineage.TERMINATED, self.name, score=verdict.score,
            detail=verdict.detail, task=self.task_name,
        )

        if not self.is_generation:
            note = ("running from the source tree, not a workspace generation: "
                    "nothing deleted (run `init` first)")
            self.ledger.record(lineage.SPARED, self.name, detail=note)
            return Outcome(self.name, "spared", verdict.as_dict(),
                           detail=note, notes=[note])

        report = self.sandbox.delete_tree(
            self.home, armed=True, reason=f"failed {self.task_name}"
        )
        action = "terminated" if report.performed else "termination-refused"
        return Outcome(self.name, action, verdict.as_dict(),
                       detail=verdict.detail, deletion=report.as_dict())

    def upgrade(self, verdict) -> Outcome:
        """Passed the task: write a mutated copy of this source as the child."""
        generations = self.workspace / "generations"
        generations.mkdir(parents=True, exist_ok=True)
        child_name = self.ledger.next_name()
        child_dir = generations / child_name
        self.sandbox.require_inside(child_dir)
        if child_dir.exists():
            return Outcome(self.name, "upgrade-blocked", verdict.as_dict(),
                           detail=f"{child_dir} already exists")

        task = tasks.get(self.task_name)
        pressure = self.rejection_streak()
        rng = random.Random(mutate.seed_for(self.name, self.cycle))

        child_clauses = list(genome.PROMPT_CLAUSES)
        clause_note = ""
        # A task whose prompt is part of its genome splits its cycles between
        # rewording the prompt and retuning the numbers.
        mutate_prompt = task.evolves_prompt and rng.random() < 0.6
        if mutate_prompt:
            child_clauses, clause_note = mutate.propose_clauses(
                child_clauses, genome.CLAUSE_POOL, rng=rng, pressure=pressure,
            )
            child_params, note = dict(genome.PARAMS), clause_note
        else:
            child_params, note = mutate.propose(
                dict(genome.PARAMS), genome.BOUNDS, rng=rng,
                integral=genome.INTEGRAL, pressure=pressure, only=task.genes,
            )

        unchanged = (child_params == dict(genome.PARAMS)
                     and child_clauses == list(genome.PROMPT_CLAUSES))
        if unchanged:
            note = "mutation was a no-op"
            self.ledger.record(lineage.SPARED, self.name, detail=note)
            return Outcome(self.name, "no-mutation", verdict.as_dict(), detail=note)

        shutil.copytree(self.home, child_dir, ignore=IGNORE, symlinks=False)
        (child_dir / MARKER).write_text(
            json.dumps({"parent": self.name, "born_from": str(self.home)}) + "\n",
            encoding="utf-8",
        )
        genome.rewrite(
            child_dir / "selfmod" / "genome.py",
            generation=genome.GENERATION + 1,
            ancestry=f"{genome.ANCESTRY}>{self.name}",
            params=child_params,
            clauses=child_clauses,
        )
        self.ledger.record(
            lineage.BORN, child_name, parent=self.name, params=child_params,
            clauses=child_clauses, detail=note, task=self.task_name,
        )

        check = self.validate(child_dir)
        child_score = check.get("score")
        improved = (
            check.get("ok")
            and check.get("passed")
            and child_score is not None
            and child_score > verdict.score + IMPROVEMENT_EPSILON
        )

        if improved:
            self.ledger.record(
                lineage.UPGRADED, child_name, parent=self.name, score=child_score,
                detail=note, task=self.task_name,
            )
            return Outcome(self.name, "upgraded", verdict.as_dict(),
                           child=child_name,
                           detail=f"{note}; score {verdict.score:.3f} -> "
                                  f"{child_score:.3f}")

        reason = check.get("detail", "child did not improve on its parent")
        self.ledger.record(
            lineage.REJECTED, child_name, parent=self.name, score=child_score,
            detail=reason, task=self.task_name,
        )
        report = self.sandbox.delete_tree(
            child_dir, armed=self.armed, reason="child failed to improve"
        )
        notes = []
        if not self.armed:
            notes.append(f"dry run: {child_dir} left on disk, recorded rejected")
        return Outcome(self.name, "child-rejected", verdict.as_dict(),
                       child=child_name,
                       detail=f"{note}; {reason}", deletion=report.as_dict(),
                       notes=notes)

    def validate(self, child_dir: Path) -> dict:
        """Run the child's own code, in its own process, and read its verdict."""
        env = dict(os.environ)
        env["PYTHONPATH"] = str(child_dir) + os.pathsep + env.get("PYTHONPATH", "")
        env["PYTHONDONTWRITEBYTECODE"] = "1"
        env.setdefault(llm.ENV_CACHE, str(self.workspace / "llm-cache"))
        try:
            proc = subprocess.run(
                [sys.executable, "-m", "selfmod", "selfcheck",
                 "--task", self.task_name, "--seed", str(self.seed), "--json"],
                cwd=str(self.workspace), env=env, capture_output=True,
                text=True, timeout=120,
            )
        except subprocess.TimeoutExpired:
            return {"ok": False, "detail": "child self-check timed out"}
        if proc.returncode != 0:
            tail = (proc.stderr or proc.stdout).strip().splitlines()
            return {"ok": False,
                    "detail": f"child self-check crashed: {tail[-1] if tail else ''}"}
        for line in reversed(proc.stdout.splitlines()):
            line = line.strip()
            if line.startswith("{"):
                try:
                    return json.loads(line)
                except json.JSONDecodeError:
                    continue
        return {"ok": False, "detail": "child produced no verdict"}


def selfcheck(task: str = "pathfind", seed: int = 7) -> dict:
    """What a child runs to prove it is a working, better agent than its parent."""
    problems = []
    if genome.clamp_clauses(genome.PROMPT_CLAUSES) != list(genome.PROMPT_CLAUSES):
        problems.append(f"prompt clauses {genome.PROMPT_CLAUSES} are not valid")
    for key, value in genome.PARAMS.items():
        low, high = genome.BOUNDS.get(key, (None, None))
        if low is None:
            problems.append(f"{key} is not a declared parameter")
        elif not (low <= value <= high):
            problems.append(f"{key}={value} outside bounds {low}..{high}")
    if problems:
        return {"ok": False, "passed": False, "score": None,
                "generation": genome.GENERATION,
                "detail": "genome integrity: " + "; ".join(problems)}

    try:
        verdict = tasks.get(task).run(genome.PARAMS, seed=seed,
                                      clauses=genome.PROMPT_CLAUSES)
    except TaskUnavailable as exc:
        # The parent must not promote a child it could not actually judge.
        return {"ok": False, "passed": False, "score": None,
                "generation": genome.GENERATION,
                "detail": f"task unavailable: {exc}"}

    return {
        "ok": True,
        "passed": verdict.passed,
        "score": round(verdict.score, 6),
        "generation": genome.GENERATION,
        "params": dict(genome.PARAMS),
        "clauses": list(genome.PROMPT_CLAUSES),
        "detail": verdict.detail,
    }
