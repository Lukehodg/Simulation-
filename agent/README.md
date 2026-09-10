# selfmod — an agent that deletes itself on failure and rewrites itself on success

A small, dependency-free Python agent with one rule:

> **Fail the task → the agent deletes the directory it is running from.
> Pass the task → the agent writes a mutated copy of its own source as the
> next generation, and keeps that copy only if the copy proves itself better.**

A lineage therefore climbs on its own: bad generations remove themselves, good
ones become the base for the next attempt, and every attempt is recorded in a
ledger that outlives the code it describes.

This directory is a standalone subproject. It shares nothing with the Unity
simulator in the rest of the repository — no Unity, Python 3.10+ and the
standard library only. The one exception is the optional `llm` task, which
talks to Claude and wants `pip install anthropic`; everything else, that task's
offline simulator included, runs with nothing installed.

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

## The second task: evolving a prompt against Claude

`--task llm` points the same lifecycle at the Claude API. The agent answers a
fixed set of eight questions with exact answers, and **what evolves is the
wording of its own system prompt**: the genome carries a list of indices into
a clause pool (`selfmod/llm_task.py`), and a child is a copy of the agent
whose `PROMPT_CLAUSES` differ.

```console
$ pip install anthropic          # only needed for this task
$ export ANTHROPIC_API_KEY=...   # or: ant auth login
$ python3 -m selfmod init && python3 -m selfmod evolve --task llm --cycles 40
```

Grading is programmatic — normalised exact match against a fixed answer key.
**The model never marks its own work.** In this lineage passing is what buys
the right to reproduce, so a self-graded task would just evolve a better
opinion of itself. The one self-assessment the agent is allowed at runtime is
whether a reply *looks* like a bare value; whether it is the *right* value is
settled by the key.

Watch it run offline first — the simulated backend is deterministic, free, and
models the same pressures (formatting clauses matter, prose clauses break
exact match, a low token ceiling truncates the long items):

```console
$ SELFMOD_LLM_BACKEND=simulated python3 -m selfmod evolve --task llm --cycles 80 --patience 40
cycle 9:  gen-0001: upgraded  4/8 exact  prompt +clause 5;                score 0.499 -> 0.624
cycle 10: gen-0010: upgraded  5/8 exact  prompt +clause 1;                score 0.624 -> 0.749
cycle 23: gen-0012: upgraded  6/8 exact  mutated llm_answer_tokens 1024->5994, llm_effort 0->2 (widened x3.0)
cycle 45: gen-0024: upgraded  7/8 exact  mutated llm_answer_tokens 5994->8000
cycle 61: gen-0046: upgraded  7/8 exact  prompt +clause 6;                score 0.874 -> 0.999

* gen-0001  score +0.499  survived  4/8 exact, 47 output tokens/item
  * gen-0010  score +0.624  survived  5/8 exact
    ...
      * gen-0062  score +0.999  survived  8/8 exact, 43 output tokens/item
```

Seven surviving generations out of 81 born: 74 children were rejected and
deleted along the way.
The seed prompt gets 4 of 8; the lineage ends on the clause set that gets all
8, with the token ceiling raised to carry the two long answers. The clauses
that invite prose get proposed too — each of those children scored 0/8 and
deleted itself.

### The genes

| Gene | What it does |
|---|---|
| `PROMPT_CLAUSES` | Indices into `CLAUSES`; assembled in order into the system prompt. Mutations add, drop, swap or reorder one clause — weighted toward adding while the prompt is short. |
| `llm_effort` | Index into `low … max`, passed as `output_config.effort`. Thinking is on by default on Opus 5; this is the depth dial. |
| `llm_answer_tokens` | `max_tokens` for the reply. |
| `llm_reask_limit` | How many times to re-ask when a reply comes back unusable, with the formatting demand moved to the end of the user turn. |

There is no `temperature` gene: sampling parameters are rejected on Opus 5, so
effort and prompt wording are the real dials. Each task declares the genes it
reads (`Task.genes`), so a `pathfind` lineage never wastes a cycle tuning
`llm_effort`, and vice versa.

### Spending, and not spending

Three guards, because this loop runs unattended:

- **A cache above the generations.** Replies are keyed by the exact request
  and stored in `workspace/llm-cache/`, so a parent re-proving an unchanged
  genome every cycle costs nothing. In the run above, most cycles show
  `0 calls (8 cached)`.
- **A call budget.** `SELFMOD_LLM_MAX_CALLS` (default 120 per process) stops a
  runaway lineage. Exceeding it abstains rather than fails.
- **Abstention instead of failure.** A missing key, an unreachable API, a
  rate limit that survived the SDK's retries, or a spent budget raises
  `TaskUnavailable`, and the cycle ends as `task-unavailable`: nothing is
  deleted, nothing is promoted, the head stays put. **An expired API key must
  never look like a task the agent failed** — failing a task here means
  deletion.

A cycle costs at most `2 × 8` calls (the parent's verdict and the child's
self-check), minus cache hits; `run`/`evolve` print the ceiling before
starting. Requests go to `claude-opus-5` with server-side refusal fallbacks
enabled, so a single declined item is retried on a fallback model inside the
same call instead of reading as a wrong answer. Override the model with
`SELFMOD_LLM_MODEL`.

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
`BOUNDS` in `genome.py`, and list them in `Task.genes` so mutation targets
them — `BOUNDS` is the envelope the lineage may explore and is not itself
mutable. Raise `TaskUnavailable` (not a failing verdict) for anything that
means "could not be judged": that is the difference between a bad genome and
a bad afternoon for your network.

Two caveats before pointing this at anything real: the task runs in-process
with no isolation beyond the sandbox, and a scored hill-climb will happily
exploit a badly designed score. Score what you actually want — and keep the
grader out of the model's reach, as `llm` does.

## Tests

```console
$ cd agent && python3 -m unittest discover -s tests -t .
```

66 tests, standard library only, and **no test calls the API** — the suite
forces the simulated backend in `tests/__init__.py`. They cover the
containment rules (escape via `..`, via symlink, via a forged marker outside
the workspace, and deletion of the root itself are each refused), the genome
rewrite including the prompt gene, mutation bounds and seed stability, task
determinism, the reply cache and call budget, abstention deleting nothing, and
the full lifecycle end to end with real subprocesses and real deletions —
upgrade, rejection, self-destruction, rollback to parent, extinction, and a
head too broken to run.

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
    llm_task.py      the Claude task whose system prompt is part of the genome
    llm.py           the API backend, reply cache, call budget and offline stand-in
    errors.py        TaskUnavailable: could not be judged, so nothing is deleted
    orchestrator.py  seeding, running the head, evolving, status
    cli.py           python -m selfmod ...
  tests/
  requirements-llm.txt   only for --task llm: the anthropic SDK
  workspace/         created by `init`; gitignored, and holds the reply cache
                     and the generations — the only deletable place
```
