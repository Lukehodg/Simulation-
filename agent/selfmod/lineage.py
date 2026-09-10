"""Append-only ledger of every birth, upgrade, rejection and termination.

The ledger lives in the workspace root, never inside a generation directory,
so the record of a generation survives that generation deleting itself.
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass
from pathlib import Path

LEDGER_NAME = "lineage.jsonl"

BORN = "born"
SURVIVED = "survived"
UPGRADED = "upgraded"
REJECTED = "rejected"
TERMINATED = "terminated"
SPARED = "spared"


@dataclass
class Generation:
    name: str
    parent: str | None
    born_at: float
    score: float | None = None
    alive: bool = True
    outcome: str = "unproven"
    detail: str = ""
    params: dict | None = None


class Ledger:
    def __init__(self, workspace: Path):
        self.workspace = Path(workspace)
        self.path = self.workspace / LEDGER_NAME

    def record(self, event: str, generation: str, **fields) -> dict:
        entry = {"ts": time.time(), "event": event, "generation": generation}
        entry.update(fields)
        self.workspace.mkdir(parents=True, exist_ok=True)
        line = json.dumps(entry, sort_keys=True)
        # Append-only, flushed and fsynced: a generation about to delete itself
        # must not lose its own tombstone to a buffer.
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(line + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        return entry

    def entries(self) -> list[dict]:
        if not self.path.exists():
            return []
        out = []
        for line in self.path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line:
                try:
                    out.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
        return out

    def generations(self) -> dict[str, Generation]:
        gens: dict[str, Generation] = {}
        for entry in self.entries():
            name = entry.get("generation")
            if not name:
                continue
            event = entry.get("event")
            if event == BORN:
                gens[name] = Generation(
                    name=name,
                    parent=entry.get("parent"),
                    born_at=entry.get("ts", 0.0),
                    params=entry.get("params"),
                )
                continue
            gen = gens.get(name)
            if gen is None:
                gen = Generation(name=name, parent=entry.get("parent"),
                                 born_at=entry.get("ts", 0.0))
                gens[name] = gen
            if "score" in entry and entry["score"] is not None:
                gen.score = entry["score"]
            if entry.get("detail"):
                gen.detail = entry["detail"]
            if event in (TERMINATED, REJECTED):
                gen.alive = False
                gen.outcome = event
            elif event in (SURVIVED, UPGRADED, SPARED):
                gen.outcome = event
        return gens

    def living(self) -> list[Generation]:
        alive = [g for g in self.generations().values() if g.alive]
        return sorted(alive, key=lambda g: (g.born_at, g.name))

    def head(self) -> Generation | None:
        """The newest generation still standing — the one that runs next."""
        alive = self.living()
        return alive[-1] if alive else None

    def next_name(self) -> str:
        highest = 0
        for name in self.generations():
            try:
                highest = max(highest, int(name.split("-")[-1]))
            except ValueError:
                continue
        return f"gen-{highest + 1:04d}"

    def render_tree(self) -> str:
        gens = self.generations()
        if not gens:
            return "(no generations yet)"
        children: dict[str | None, list[Generation]] = {}
        for gen in sorted(gens.values(), key=lambda g: g.born_at):
            children.setdefault(gen.parent, []).append(gen)

        lines: list[str] = []

        def walk(parent: str | None, depth: int) -> None:
            for gen in children.get(parent, []):
                mark = "*" if gen.alive else "x"
                score = "  --  " if gen.score is None else f"{gen.score:+.3f}"
                detail = f"  {gen.detail}" if gen.detail else ""
                lines.append(
                    f"{'  ' * depth}{mark} {gen.name}  score {score}  "
                    f"{gen.outcome}{detail}"
                )
                walk(gen.name, depth + 1)

        walk(None, 0)
        # Anything whose parent was pruned from the ledger still gets printed.
        printed = {line.split()[1] for line in lines}
        for gen in sorted(gens.values(), key=lambda g: g.born_at):
            if gen.name not in printed:
                lines.append(f"? {gen.name}  (orphaned)  {gen.outcome}")
        return "\n".join(lines)
