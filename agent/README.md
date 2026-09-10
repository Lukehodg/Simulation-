# selfmod — an agent that deletes itself on failure and rewrites itself on success

A small, dependency-free Python agent with one rule:

> **Fail the task → the agent deletes the directory it is running from.
> Pass the task → the agent writes a mutated copy of its own source as the
> next generation, and keeps that copy only if the copy proves itself better.**

A lineage therefore climbs on its own: bad generations remove themselves, good
ones become the base for the next attempt, and every attempt is recorded in a
ledger that outlives the code it describes.

This directory is a standalone subproject. It shares nothing with the Unity
simulator in the rest of the repository — no Unity, no packages, no network,
Python 3.10+ and the standard library only.

## Quick start

```console
$ cd agent
$ python3 -m selfmod init            # seed generation 1 into agent/workspace
$ python3 -m selfmod evolve --cycles 20 --patience 6
```

Real output, trimmed:

```
cycle 1: gen-0001: child-rejected  [pass score 0.617594] solved 10/16  mutated retry_limit 0->1; solved 10/16 ...
cycle 4: gen-0001: child-rejected  [pass score 0.617594] solved 10/16  mutated tie_breaker 0.05->0.0 (widened x2.5) ...
cycle 5: gen-0001: upgraded        [pass score 0.617594] solved 10/16  mutated beam_width 4->22, step_budget 200->4000 (widened x3.0); score 0.618 -> 0.988
cycle 6: gen-0006: child-rejected  [pass score 0.987566] solved 16/16 ...
cycle 11: plateau  no upgrade in 6 cycles

* gen-0001  score +0.618  survived  solved 10/16, 118 expansions/instance
  x gen-0002  score +0.613  rejected  solved 10/16, 194 expansions/instance
  ...
  * gen-0006  score +0.988  survived  solved 16/16, 199 expansions/instance
    x gen-0007  score +0.979  rejected  solved 16/16, 336 expansions/instance
```

`*` is a generation still on disk, `x` one that has been deleted.

Watch the other half of the rule — failure — with the built-in unpassable task:

```console
$ python3 -m selfmod run --task impossible
gen-0001: terminated  [FAIL score 0.0] task is unpassable by construction
  deleted 11 files (47428 bytes) at .../workspace/generations/gen-0001
```

Add `--dry-run` to any command to run every check and every decision while
deleting nothing.

## Commands

| Command | What it does |
|---|---|
| `init [--force]` | Copy the source package into `workspace/generations/gen-0001` and record its birth. `--force` wipes an existing lineage. |
| `run [--task T] [--seed N] [--dry-run]` | Hand control to the living head for one cycle: it passes and upgrades, or fails and deletes itself. |
| `evolve --cycles N [--patience K]` | Repeat `run` until extinction, exhaustion, or `K` cycles without a kept upgrade. |
| `lineage` | Print the family tree with scores and outcomes. |
| `status [--json]` | Head, living generations, best score, and any divergence between the ledger and the disk. |
| `selfcheck [--json]` | Evaluate the genome of the copy you are running. This is what a child runs to prove itself. |
| `cycle --home DIR` | Internal: the entry point a generation is launched through. |

## How a cycle actually runs

The orchestrator never evaluates a genome itself. It looks up the head of the
lineage and runs **that generation's own copy of the package** in a separate
process, so the code deciding to upgrade or delete itself is always the code
under test.

```
orchestrator.run_cycle
  └─ subprocess: PYTHONPATH=<gen-000N>  python -m selfmod cycle --home <gen-000N>
       └─ Agent.live()
            ├─ task fails →  ledger tombstone (fsynced), then rmtree of its own directory
            └─ task passes →  copy own source to gen-000N+1
                              rewrite the child's genome.py (real source, new literals)
                              subprocess: the child runs its own selfcheck
                              child scored better?  keep it — it is the new head
                              child scored worse?   the child is deleted, parent stays
```

Because a parent stays alive when its child is promoted, a generation that
later fails and deletes itself **rolls the lineage back** to its parent, which
is a genome already known to work. If a head is so broken it cannot even reach
a verdict — a mutation that produced unrunnable source — the supervisor applies
the same rule on its behalf and the lineage rolls back one step.

## What "upgrade itself" means here

The block between the markers in `selfmod/genome.py` is real source that gets
rewritten in the child:

```python
# --- genome:begin ---
GENERATION = 1
ANCESTRY = "seed"
PARAMS = {
    "beam_width": 4,
    "heuristic_weight": 1.1,
    "step_budget": 200,
    "retry_limit": 0,
    "tie_breaker": 0.05,
}
# --- genome:end ---
```

Generations differ in code, not in a config file they happen to read. The
mutation strategy itself is deliberately boring — seeded hill-climbing that
perturbs one or two parameters and widens its steps when a parent's children
keep getting rejected (`selfmod/mutate.py`). The interesting part is the
lifecycle around it, and a deterministic mutator keeps runs reproducible.

The default task (`selfmod/tasks.py`) is pathfinding under a budget: sixteen
seeded instances of rising difficulty, half scattered-obstacle grids where the
beam width bites, half perfect mazes where the step budget bites. The seed
genome solves 10 of 16; a tuned one solves all 16. So tuning is worth something
measurable, which is what keeps the upgrade loop from being decoration.

## Safety model

Self-deleting code deserves to be fenced in. Every destructive call goes
through `Sandbox` (`selfmod/sandbox.py`), which refuses unless **all** of these
hold:

- the target resolves to a real directory strictly inside the workspace root;
- it is not the workspace root, and not a symlink;
- it carries the `.selfmod-generation` marker this package writes, so only
  directories the agent created can ever be removed;
- it has no `.protected` marker.

Consequences worth stating plainly:

- **The source tree in this repository is never deleted.** It sits outside the
  workspace, so an agent run from it records its failure and reports `spared`.
  Only the disposable copies under `agent/workspace/` are at risk.
- **The ledger outlives its subject.** `workspace/lineage.jsonl` is append-only
  and lives above the generation directories; a tombstone is written and
  fsynced *before* a generation removes itself.
- **Nothing here escapes the machine.** No network, no shell-outs beyond
  running Python on its own copies, no writes outside the workspace.
- A generation directory is a copy, not the original, so a deletion is never
  unrecoverable: `init --force` reseeds from source.

`--dry-run` rehearses everything, and is careful to leave the lineage exactly
as it found it — no tombstone, so the head is still the head afterwards.

## Plugging in your own task

Subclass `Task`, return a `Verdict`, and register it:

```python
from selfmod.tasks import REGISTRY, Task, Verdict

class DeployTask(Task):
    name = "deploy"
    description = "smoke-test the build the genome's settings produce"

    def run(self, params, *, seed=0):
        passed, score, detail = my_check(params)
        return Verdict(passed=passed, score=score, detail=detail)

REGISTRY[DeployTask.name] = DeployTask()
```

`passed` decides deletion versus upgrade; `score` decides whether a proposed
child is kept. If your task has params of its own, add them to `PARAMS` and
`BOUNDS` in `genome.py` — `BOUNDS` is the envelope the lineage may explore and
is not itself mutable.

Two caveats before pointing this at anything real: the task runs in-process
with no isolation beyond the sandbox, and a scored hill-climb will happily
exploit a badly designed score. Score what you actually want.

## Tests

```console
$ cd agent && python3 -m unittest discover -s tests -t .
```

43 tests, standard library only. They cover the containment rules (escape via
`..`, via symlink, via a forged marker outside the workspace, and deletion of
the root itself are each refused), the genome rewrite, mutation bounds and
seed stability, task determinism, and the full lifecycle end to end with real
subprocesses and real deletions — upgrade, rejection, self-destruction,
rollback to parent, extinction, and a head too broken to run.

## Layout

```
agent/
  selfmod/
    agent.py         the lifecycle: live, self_destruct, upgrade, validate
    sandbox.py       containment rules; the only code that deletes anything
    lineage.py       append-only ledger, family tree, head resolution
    genome.py        the mutable parameters, and the rewriter that edits them
    mutate.py        seeded hill-climbing with plateau pressure
    tasks.py         the pathfinding suite, plus tasks that always fail or pass
    orchestrator.py  seeding, running the head, evolving, status
    cli.py           python -m selfmod ...
  tests/
  workspace/         created by `init`; gitignored, and the only deletable place
```
