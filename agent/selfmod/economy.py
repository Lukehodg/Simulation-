"""Solvency as the survival condition: pay for yourself, or be deleted.

This is the honest version of "make enough money to keep itself running".
The agent cannot take payment, contact anyone, or transact — it produces
work, and a human sells it. What the agent *can* do is measured exactly:

* every call it makes is priced and written to a spend ledger;
* the money that came in is read from a command **you** provide, pointed at
  a real account or a file you keep;
* a generation survives only while ``stake + revenue - spend`` is positive
  and the work still passes your quality checks.

Run out of runway and the generation deletes itself. That is the whole
mechanism, and it is also why the loop is deliberately kept away from
anything that could earn money without a person in between: an agent whose
survival depends on income is exactly the thing you do not hand a payment
rail to.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path

from .errors import TaskUnavailable
from .llm import EFFORT_LEVELS, Backend, Reply, build_client
from .mission import Check, MissionError, user_cwd
from .tasks import Task, Verdict

ENV_PLAN = "SELFMOD_EARN_PLAN"
ENV_PRICE_IN = "SELFMOD_PRICE_IN"
ENV_PRICE_OUT = "SELFMOD_PRICE_OUT"

#: Dollars per million tokens, input/output. Override with the env vars above.
PRICES: dict[str, tuple[float, float]] = {
    "claude-opus-5": (5.0, 25.0),
    "claude-opus-4-8": (5.0, 25.0),
    "claude-sonnet-5": (2.0, 10.0),
    "claude-haiku-4-5": (1.0, 5.0),
    "claude-fable-5-1": (10.0, 50.0),
    "simulated": (0.0, 0.0),
}

#: Fixed, and not part of what evolves. The clause pool can change how the
#: work is written; it cannot licence dishonest work.
BASE_SYSTEM = (
    "You produce a finished piece of work to be sold by the person who "
    "commissioned it.\n"
    "Everything you write must be original and truthful. Do not impersonate "
    "any person or company, do not invent credentials, testimonials, reviews "
    "or statistics, and do not copy existing text. If the task cannot be done "
    "honestly, say so instead of doing it."
)

#: How the agent may instruct itself to do the work. Cheapness is a tactic
#: here, because tokens are the running cost it has to cover.
CLAUSES: tuple[str, ...] = (
    "Deliver the finished work only: no preamble, no notes, no apology.",
    "Write it so it can be handed to a paying customer unchanged.",
    "Keep it as short as the brief allows — length is cost.",
    "Follow every requirement in the brief exactly.",
    "Prefer concrete specifics over filler and adjectives.",
    "Do not describe what you are about to do; just do it.",
    "Use plain English a non-expert can act on.",
    "Explain your creative choices at the end.",
)


class PlanError(Exception):
    """A funding plan that cannot be run as written."""


# --------------------------------------------------------------------------
# Money in, money out
# --------------------------------------------------------------------------

def price_for(model: str) -> tuple[float, float]:
    try:
        return (float(os.environ[ENV_PRICE_IN]), float(os.environ[ENV_PRICE_OUT]))
    except (KeyError, ValueError):
        pass
    return PRICES.get(model, PRICES["claude-opus-5"])


def cost_of(model: str, input_tokens: int, output_tokens: int) -> float:
    per_in, per_out = price_for(model)
    return (input_tokens * per_in + output_tokens * per_out) / 1_000_000.0


class SpendLedger:
    """Append-only record of what the lineage has spent, in dollars.

    It lives beside the family tree, above the generations, so a generation
    cannot wipe out the record of its own costs by deleting itself.
    """

    def __init__(self, workspace: Path):
        self.path = Path(workspace) / "spend.jsonl"

    def record(self, *, generation: str, model: str, input_tokens: int,
               output_tokens: int, calls: int) -> float:
        amount = cost_of(model, input_tokens, output_tokens)
        entry = {"ts": time.time(), "generation": generation, "model": model,
                 "input_tokens": input_tokens, "output_tokens": output_tokens,
                 "calls": calls, "cost": round(amount, 6)}
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(entry, sort_keys=True) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        return amount

    def total(self) -> float:
        if not self.path.exists():
            return 0.0
        spent = 0.0
        for line in self.path.read_text(encoding="utf-8").splitlines():
            try:
                spent += float(json.loads(line).get("cost", 0.0))
            except (json.JSONDecodeError, TypeError, ValueError):
                continue
        return spent


NUMBER = re.compile(r"-?\d+(?:[\d,]*\d)?(?:\.\d+)?")


def read_revenue(command: str) -> float:
    """Run the user's command and read the number it prints.

    Anything that stops this from working — a missing tool, a failed API
    call, output with no number in it — abstains rather than failing. Being
    unable to read the balance is not the same as having no money.
    """
    if not command:
        return 0.0
    try:
        done = subprocess.run(command, shell=True, capture_output=True,
                              text=True, timeout=120, cwd=user_cwd())
    except subprocess.TimeoutExpired:
        raise TaskUnavailable("the revenue command timed out")
    except OSError as exc:
        raise TaskUnavailable(f"could not run the revenue command: {exc}")
    if done.returncode != 0:
        tail = (done.stderr or done.stdout).strip().splitlines()
        raise TaskUnavailable(
            f"the revenue command failed (exit {done.returncode})"
            + (f": {tail[-1]}" if tail else "")
        )
    found = NUMBER.search((done.stdout or "").replace("$", "").replace("£", ""))
    if not found:
        raise TaskUnavailable(
            f"the revenue command printed no number: {done.stdout.strip()[:80]!r}"
        )
    return float(found.group(0).replace(",", ""))


# --------------------------------------------------------------------------
# The plan
# --------------------------------------------------------------------------

@dataclass
class Plan:
    #: What to produce each cycle — the thing a human then sells.
    brief: str
    #: Money already put in, in the same units the revenue command reports.
    stake: float = 5.0
    #: Command printing total money received so far. Empty means none yet.
    revenue_command: str = ""
    checks: list[Check] = field(default_factory=list)
    #: Fraction of quality checks that must pass.
    quality_at: float = 1.0
    #: Where finished work is written for a human to sell.
    deliverables: str = ""

    def to_dict(self) -> dict:
        return {"brief": self.brief, "stake": self.stake,
                "revenue_command": self.revenue_command,
                "checks": [{"kind": c.kind, "value": c.value} for c in self.checks],
                "quality_at": self.quality_at,
                "deliverables": self.deliverables}

    @classmethod
    def from_dict(cls, data: dict) -> "Plan":
        return cls(brief=data["brief"], stake=float(data.get("stake", 5.0)),
                   revenue_command=data.get("revenue_command", ""),
                   checks=[Check(c["kind"], c.get("value", ""))
                           for c in data.get("checks", [])],
                   quality_at=float(data.get("quality_at", 1.0)),
                   deliverables=data.get("deliverables", ""))

    def save(self, path: Path) -> Path:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(self.to_dict(), indent=2), encoding="utf-8")
        return path

    def describe(self) -> str:
        lines = [f'work: "{self.brief}"', "",
                 f"stake:   {self.stake:g} put in up front",
                 f"revenue: " + (f"whatever `{self.revenue_command}` prints"
                                 if self.revenue_command
                                 else "none configured — it can only burn down")]
        if self.checks:
            lines += ["", "the work must also:"]
            lines += [f"  - {check.describe()}" for check in self.checks]
        lines += ["",
                  "survives while: stake + revenue - what it has spent > 0",
                  "                and the work still passes those checks",
                  "otherwise:      the generation deletes itself"]
        return "\n".join(lines)


def load() -> Plan | None:
    raw = os.environ.get(ENV_PLAN, "").strip()
    if not raw:
        return None
    try:
        return Plan.from_dict(json.loads(Path(raw).read_text(encoding="utf-8")))
    except FileNotFoundError:
        raise PlanError(f"no plan file at {raw}") from None
    except (OSError, json.JSONDecodeError, KeyError) as exc:
        raise PlanError(f"could not read {raw}: {exc}") from None


# --------------------------------------------------------------------------
# The task
# --------------------------------------------------------------------------

def build_system(clauses) -> str:
    chosen = [CLAUSES[i] for i in clauses if 0 <= i < len(CLAUSES)]
    return "\n".join([BASE_SYSTEM, *chosen])


class EarnTask(Task):
    name = "earn"
    description = "produce sellable work and stay solvent, or delete yourself"
    genes = ("llm_effort", "llm_answer_tokens")
    evolves_prompt = True

    def clause_pool(self) -> int:
        return len(CLAUSES)

    def run(self, params, *, seed: int = 0, clauses=()) -> Verdict:
        plan = load()
        if plan is None:
            raise TaskUnavailable(
                'no funding plan — use `selfmod earn "..."` to set one'
            )

        workspace = Path(os.environ.get("SELFMOD_WORKSPACE", ".")).resolve()
        spend_ledger = SpendLedger(workspace)
        spent_before = spend_ledger.total()
        revenue = read_revenue(plan.revenue_command)

        effort_index = int(params.get("llm_effort", 1))
        effort = EFFORT_LEVELS[max(0, min(len(EFFORT_LEVELS) - 1, effort_index))]
        max_tokens = int(params.get("llm_answer_tokens", 1024))

        client = build_client(backend=_backend())
        reply = client.ask(system=build_system(clauses), user=plan.brief,
                           effort=effort, max_tokens=max_tokens)

        cycle_cost = spend_ledger.record(
            generation=os.environ.get("SELFMOD_GENERATION", "?"),
            model=client.model if client.backend.name != "simulated" else "simulated",
            input_tokens=client.input_tokens, output_tokens=client.output_tokens,
            calls=client.calls,
        )
        spent = spent_before + cycle_cost
        balance = plan.stake + revenue - spent

        checks = list(plan.checks) or [Check("non_empty")]
        passed, failures = 0, []
        for check in checks:
            ok, note = check.run(reply.text)
            passed += 1 if ok else 0
            if not ok:
                failures.append(f"{check.describe()}"
                                + (f" ({note})" if note else ""))
        quality = passed / len(checks)

        if reply.text.strip() and plan.deliverables:
            path = Path(plan.deliverables)
            path.mkdir(parents=True, exist_ok=True)
            name = os.environ.get("SELFMOD_GENERATION", "work")
            (path / f"{name}.txt").write_text(reply.text, encoding="utf-8")

        solvent = balance > 0
        # Quality is the goal; cost is the constraint. A cheaper cycle at the
        # same quality scores better, which is the only way this lineage can
        # actually extend its own runway.
        score = quality - 0.25 * min(1.0, cycle_cost / 0.05)
        detail = (f"balance {balance:+.4f} (stake {plan.stake:g} + revenue "
                  f"{revenue:g} - spent {spent:.4f}), this cycle cost "
                  f"{cycle_cost:.4f}, {passed}/{len(checks)} checks"
                  + (f" — failed: {'; '.join(failures[:2])}" if failures else ""))
        if not solvent:
            detail += " — BANKRUPT"

        return Verdict(
            passed=solvent and quality >= plan.quality_at,
            score=score,
            detail=detail,
            metrics={"balance": round(balance, 6), "revenue": revenue,
                     "spent": round(spent, 6), "cycle_cost": round(cycle_cost, 6),
                     "stake": plan.stake, "solvent": solvent,
                     "checks_passed": passed, "checks": len(checks),
                     "quality": round(quality, 4), "clauses": list(clauses),
                     "output": reply.text[:2000]},
        )


class SimulatedBackend(Backend):
    """A free stand-in that at least gets the *shape* of the costs right.

    Brevity clauses shorten the reply, the explain-yourself clause lengthens
    it, and higher effort burns more tokens — so the cost side of the loop
    can be exercised without spending anything. The work itself is a stub.
    """

    name = "simulated"

    def complete(self, *, system: str, user: str, effort: str,
                 max_tokens: int) -> Reply:
        brief = CLAUSES[2] in system
        chatty = CLAUSES[7] in system
        body = user.strip().splitlines()[0] if user.strip() else ""
        words = 60 if brief else 140
        words += 90 if chatty else 0
        text = f"[simulated work for] {body}\n" + "sample copy. " * (words // 2)
        effort_index = EFFORT_LEVELS.index(effort)
        out = min(max_tokens, int(len(text.split()) * 1.4) + 200 * effort_index)
        return Reply(text=text, output_tokens=out,
                     input_tokens=20 + len(system.split()) + len(user.split()))


def _backend():
    choice = os.environ.get("SELFMOD_LLM_BACKEND", "anthropic").strip().lower()
    return SimulatedBackend() if choice == "simulated" else None


def register(registry: dict) -> None:
    registry[EarnTask.name] = EarnTask()
