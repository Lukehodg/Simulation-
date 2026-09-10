"""Command line for the lineage.

    python -m selfmod init                 seed generation 1 in the workspace
    python -m selfmod run                  live one cycle as the current head
    python -m selfmod evolve --cycles 12   run cycles until they stop paying off
    python -m selfmod lineage              print the family tree
    python -m selfmod status               where the lineage stands
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from . import agent as agent_mod
from . import genome, lineage, orchestrator

DEFAULT_WORKSPACE = "workspace"


def _workspace(args) -> Path:
    if args.workspace:
        return Path(args.workspace).expanduser().resolve()
    return (orchestrator.source_root() / DEFAULT_WORKSPACE).resolve()


def _add_common(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--workspace", help="where generations live "
                                            "(default: agent/workspace)")
    parser.add_argument("--task", default="pathfind",
                        help="task the generation must pass (default: pathfind)")
    parser.add_argument("--seed", type=int, default=7,
                        help="task seed; the same seed replays the same suite")
    parser.add_argument("--dry-run", action="store_true",
                        help="run every check but never actually delete")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="selfmod",
        description="An agent that deletes itself when it fails its task and "
                    "rewrites itself when it succeeds.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_init = sub.add_parser("init", help="seed a workspace with generation 1")
    p_init.add_argument("--workspace")
    p_init.add_argument("--force", action="store_true",
                        help="delete an existing lineage and start over")

    p_run = sub.add_parser("run", help="run one cycle as the living head")
    _add_common(p_run)

    p_ev = sub.add_parser("evolve", help="run cycles until extinction or exhaustion")
    _add_common(p_ev)
    p_ev.add_argument("--cycles", type=int, default=8)
    p_ev.add_argument("--patience", type=int, default=0,
                      help="stop after this many cycles without an upgrade")

    p_tree = sub.add_parser("lineage", help="print the family tree")
    p_tree.add_argument("--workspace")

    p_status = sub.add_parser("status", help="summarise the lineage")
    p_status.add_argument("--workspace")
    p_status.add_argument("--json", action="store_true")

    p_check = sub.add_parser("selfcheck",
                             help="evaluate the genome of the running copy")
    p_check.add_argument("--task", default="pathfind")
    p_check.add_argument("--seed", type=int, default=7)
    p_check.add_argument("--json", action="store_true")

    p_cycle = sub.add_parser("cycle", help="internal: live one cycle in place")
    _add_common(p_cycle)
    p_cycle.add_argument("--home", required=True)
    p_cycle.add_argument("--cycle", type=int, default=0)

    return parser


def _describe(outcome: dict) -> str:
    verdict = outcome.get("verdict") or {}
    who = outcome.get("generation")
    bits = [f"{who}: {outcome['action']}" if who else str(outcome.get("action"))]
    if verdict:
        bits.append(f"[{'pass' if verdict.get('passed') else 'FAIL'} "
                    f"score {verdict.get('score')}] {verdict.get('detail', '')}")
    if outcome.get("detail"):
        bits.append(outcome["detail"])
    deletion = outcome.get("deletion") or {}
    if deletion.get("refusal"):
        bits.append(f"deletion refused: {deletion['refusal']}")
    elif deletion.get("performed"):
        bits.append(f"deleted {deletion['files']} files "
                    f"({deletion['bytes']} bytes) at {deletion['target']}")
    elif deletion:
        bits.append(f"dry run: would delete {deletion['target']}")
    bits.extend(outcome.get("notes") or [])
    return "  ".join(b for b in bits if b)


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)

    if args.command == "init":
        result = orchestrator.init(_workspace(args), force=args.force)
        print(json.dumps(result, indent=2))
        return 0 if result.get("created") or result.get("head") else 1

    if args.command == "run":
        outcome = orchestrator.run_cycle(
            _workspace(args), task=args.task, seed=args.seed,
            armed=not args.dry_run, cycle=0,
        )
        print(_describe(outcome))
        return 0 if outcome.get("action") not in ("error", "missing") else 1

    if args.command == "evolve":
        def report(index, outcome):
            print(f"cycle {index + 1}: {_describe(outcome)}")

        results = orchestrator.evolve(
            _workspace(args), cycles=args.cycles, task=args.task,
            seed=args.seed, armed=not args.dry_run, patience=args.patience,
            on_step=report,
        )
        print()
        print(lineage.Ledger(_workspace(args)).render_tree())
        return 0 if results and results[-1].get("action") != "error" else 1

    if args.command == "lineage":
        print(lineage.Ledger(_workspace(args)).render_tree())
        return 0

    if args.command == "status":
        info = orchestrator.status(_workspace(args))
        if args.json:
            print(json.dumps(info, indent=2))
        else:
            for key, value in info.items():
                print(f"{key}: {value}")
        return 0

    if args.command == "selfcheck":
        result = agent_mod.selfcheck(task=args.task, seed=args.seed)
        print(json.dumps(result, sort_keys=True))
        return 0 if result.get("ok") else 1

    if args.command == "cycle":
        agent = agent_mod.Agent(
            Path(args.home), _workspace(args), task=args.task, seed=args.seed,
            armed=not args.dry_run, cycle=args.cycle,
        )
        outcome = agent.live()
        print(_describe(outcome.as_dict()))
        print(orchestrator.RESULT_PREFIX + json.dumps(outcome.as_dict()))
        return 0

    raise AssertionError(f"unhandled command {args.command!r}")
