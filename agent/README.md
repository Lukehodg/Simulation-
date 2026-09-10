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
| `earn "..."` | Produce sellable work and stay solvent, or go bankrupt and self-delete. |
| `do "..."` | Give one instruction with checks: complete it or self-delete. |
| `new [--template]` | Write a question file — by interview, or as an example to edit. |
| `check FILE` | Validate a question file and print the prompt it produces. |
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

## Paying for itself: `earn`

The survival condition becomes solvency. The agent produces work you can
sell, every call it makes is priced into a spend ledger, and the money that
came in is read from a command **you** point at a real account. It lives
while `stake + revenue - spent > 0` and the work still passes your checks;
when it runs out of runway, the generation deletes itself.

```console
$ echo 0 > revenue.txt          # or a Stripe/PayPal balance command
$ python3 -m selfmod earn "Write a 120-word landing page for a Leeds plumber" \
      --stake 5 --revenue-command "cat revenue.txt" \
      --min-words 100 --must-not-contain "As an AI" --cycles 8
```

```
work: "Write a 120-word landing page for a Leeds plumber"

stake:   5 put in up front
revenue: whatever `cat revenue.txt` prints

survives while: stake + revenue - what it has spent > 0
                and the work still passes those checks
otherwise:      the generation deletes itself

cycle 1: gen-0001: child-rejected  balance +0.4990 (stake 0.005 + revenue 0.5
         - spent 0.0060), this cycle cost 0.0060, 1/1 checks
...
spent so far: 0.0443  (ledger: workspace/spend.jsonl)
still running: gen-0001 (score 0.9745)
work to sell:  /path/to/deliverables
```

Starve it and you get the other ending:

```
cycle 1: gen-0001: terminated  balance -0.0010 (stake 0.005 + revenue 0.00
         - spent 0.0060) — BANKRUPT   deleted 17 files
Bankrupt or failing its own quality bar: the last generation deleted itself
and the lineage is extinct.
```

**What it cannot do — read this before pointing it at anything.** It writes
text. It has no account, cannot take payment, cannot contact anyone, cannot
post or send. Finished work lands in `--deliverables` (default
`./deliverables`) for a person to price, sell and deliver; `--revenue-command`
is how the money that actually arrived gets back into the loop. Every claim
about earning in this section runs through a human, deliberately: an agent
whose survival depends on income is the last thing that should be handed a
payment rail, a mailing list or a marketplace login. Note also that many
marketplaces' terms restrict undisclosed automated work — that is your call
to make, with your name on it.

The honesty rules live in the fixed part of the prompt (`economy.BASE_SYSTEM`):
original, truthful, no impersonation, no invented credentials, testimonials or
statistics, and refuse the brief rather than fake it. Only the tactics pool
evolves, so nothing the lineage discovers can repeal them.

### What "keeping itself running" actually optimises

Score is `quality - 0.25 x (cycle cost / $0.05)`. So at equal quality, the
cheaper generation wins, and the lineage genuinely evolves toward covering its
own costs — shorter outputs, lower effort, fewer wasted tokens. Cache hits are
free and are not charged, so a repeated genome costs nothing to re-prove.

Prices come from a table in `selfmod/economy.py` ($5/$25 per million tokens
for Opus 5), overridable with `SELFMOD_PRICE_IN` / `SELFMOD_PRICE_OUT` — set
those alongside `SELFMOD_LLM_BACKEND=simulated` to watch a bankruptcy happen
for free. The spend ledger (`workspace/spend.jsonl`) sits above the
generations, so a generation deleting itself cannot erase the record of what
it spent. A revenue command that fails or prints no number **abstains** rather
than reporting zero — being unable to read the balance is not the same as
being broke, and the difference here is deletion.

## Giving it one instruction: `do`

The shortest way to use it. You type a command and how a finished job can be
recognised; the agent attempts it and either satisfies every check or deletes
itself.

```console
$ python3 -m selfmod do "Write a haiku about the M1 motorway" \
      --must-contain motorway --max-words 20
task: "Write a haiku about the M1 motorway"

done means:
  - must not be empty
  - must contain 'motorway'
  - must be at most 20 words

survives if: every check must pass; otherwise it deletes itself
```

If the first attempt misses, that generation removes itself and the lineage is
extinct — that is the rule, taken literally:

```console
attempt 1: gen-0001: terminated  [FAIL score 0.498] 1/2 checks — failed: must contain 'zebra'
  deleted 16 files at .../workspace/generations/gen-0001

x gen-0001  score +0.498  terminated  1/2 checks — failed: must contain 'zebra'

The lineage is extinct: every generation that failed the task deleted itself.
To let it work toward the task over several attempts instead of dying on the
first miss, add --survive-at 0.5
```

`--survive-at 0.5` is the difference between "do it or die" and "get half of it
right, survive, and evolve toward the rest". With it, a surviving generation
keeps rewriting its own prompt — from the tactics pool in
`selfmod/mission.py`, things like *do exactly what is asked and nothing more*
or *check your reply against every requirement* — and keeps whichever wording
satisfies more checks. Exit status is 0 if something survived, 1 if the
lineage died out.

### Saying what "done" means

| Flag | Check |
|---|---|
| `--must-contain TEXT` | the reply contains it (case-insensitive) |
| `--must-not-contain TEXT` | it does not |
| `--max-words N` / `--min-words N` | length |
| `--matches REGEX` | a pattern matches |
| `--json-output` | the reply parses as JSON |
| `--check-command 'CMD'` | your own shell command exits 0; `{output}` is replaced by a file holding the reply |
| `--judge` | a separate grader call must score it 6/10 or better |

The first six are deterministic and cannot be talked around. `--check-command`
is the one that reaches outside the text — `--check-command 'python3 -m json.tool {output} > /dev/null'`,
or a test suite, or a linter. It is your command and it runs on **every**
cycle, unattended.

`--judge` is the weak one, and it is off by default. It is a separate call
whose prompt this lineage cannot evolve, and which is told to treat the work
as untrusted text rather than instructions — but a lineage scored by a model
will still drift toward pleasing that model rather than doing the job. Use it
when nothing else can express what you want, alongside checks rather than
instead of them.

`--show` prints the mission and stops, so you can see what you have asked for
before spending anything. A `do` cycle costs 2 calls (4 with `--judge`).

## The second task: evolving a prompt against Claude

Where `do` gives it one command, `--task llm` gives it a whole graded set —
the same lifecycle, pointed at the Claude API. The agent answers a
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

### Using your own questions — no Python

`selfmod new` interviews you and writes a plain text file; nothing else needs
editing.

```console
$ python3 -m selfmod new
What should this agent do? (one line)
> Say which UK city each landmark is in.

Now the instruction lines it is allowed to use...
  instruction 1 > Answer with the city name only.
  instruction 2 > Do not add the country.
  instruction 3 >

Now the questions, each with the exact answer you will accept...
  question 1 > Which city is the Angel of the North in?
    exact answer > gateshead
  question 2 >

Written to prompt.txt — 1 questions, 2 instructions, starting with [0].
```

Then check it, seed a lineage from it, and run:

```console
$ python3 -m selfmod check prompt.txt
$ python3 -m selfmod init --force --questions prompt.txt
$ python3 -m selfmod evolve --task llm --questions prompt.txt --cycles 20
```

`check` prints the starting prompt and every question back at you, and refuses
files with problems, naming the line: a question with no answer, an answer
with no question, two identical questions, a `start:` number that points at no
instruction. `init` reads the file before it creates anything, so a typo never
leaves half a workspace behind.

`selfmod new --template` writes an example file to edit by hand instead of
answering questions. The format is forgiving — `#` comments, blank lines,
`Q:`/`A:` pairs or `question | answer` on one line:

```
job: Say which UK city each landmark is in.

instruction: Answer with the city name only.
instruction: Do not add the country.

start: 1

Q: Which city is the Angel of the North in?
A: gateshead

Which city is the Bullring in? | birmingham
```

`job:` is the fixed first line of every prompt. The `instruction:` lines are
the pool the agent evolves — it adds, drops and reorders them. `start:` says
which of them generation 1 begins with, numbered as you typed them; leave it
out and it starts with none. Answers are matched ignoring capitalisation and a
trailing full stop, and nothing else.

Two things to know. Passing `--questions` to `evolve` (not just `init`) matters
— it is how each generation's subprocess finds the file. And the simulated
backend cannot pretend to be a model on questions it has never seen: with your
own file it simply answers everything correctly and says so in the output. Use
it to prove the file parses and the loop runs; use the real backend to score.

### The genes

| Gene | What it does |
|---|---|
| `PROMPT_CLAUSES` | Indices into the clause pool — the built-in `CLAUSES`, or the `instruction:` lines of your file. Assembled in order into the system prompt. Mutations add, drop, swap or reorder one clause, weighted toward adding while the prompt is short. |
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

128 tests, standard library only, and **no test calls the API** — the suite
forces the simulated backend in `tests/__init__.py`. They cover the
containment rules (escape via `..`, via symlink, via a forged marker outside
the workspace, and deletion of the root itself are each refused), the genome
rewrite including the prompt gene, mutation bounds and seed stability, task
determinism, the reply cache and call budget, abstention deleting nothing,
every rejection the question-file parser can raise, every mission check
including the shell one, a mission nobody can satisfy leaving nothing behind, token pricing,
bankruptcy deleting a generation while its spend ledger survives, and
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
    promptpack.py    reads the plain-text question file `new` and `check` write
    mission.py       `do` mode: one instruction, deterministic checks, a judge
    economy.py       `earn` mode: token pricing, spend ledger, revenue, solvency
    llm.py           the API backend, reply cache, call budget and offline stand-in
    errors.py        TaskUnavailable: could not be judged, so nothing is deleted
    orchestrator.py  seeding, running the head, evolving, status
    cli.py           python -m selfmod ...
  tests/
  requirements-llm.txt   only for --task llm: the anthropic SDK
  workspace/         created by `init`; gitignored, and holds the reply cache
                     and the generations — the only deletable place
```
