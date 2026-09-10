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

import os

from . import agent as agent_mod
from . import genome, lineage, llm, mission as mission_mod
from . import orchestrator, promptpack, tasks

DEFAULT_WORKSPACE = "workspace"


def _workspace(args) -> Path:
    if args.workspace:
        return Path(args.workspace).expanduser().resolve()
    return (orchestrator.source_root() / DEFAULT_WORKSPACE).resolve()


def _add_questions(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--questions", metavar="FILE",
                        help="your own job, instructions and questions, in a "
                             "plain text file (see `selfmod new`)")


def _use_questions(args) -> None:
    """Publish the question file to every process this one starts."""
    path = getattr(args, "questions", None)
    if path:
        os.environ[promptpack.ENV_PACK] = str(Path(path).expanduser().resolve())


def _add_common(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--workspace", help="where generations live "
                                            "(default: agent/workspace)")
    parser.add_argument("--task", default="pathfind",
                        help="task the generation must pass "
                             f"({', '.join(tasks.names())}; default: pathfind)")
    parser.add_argument("--seed", type=int, default=7,
                        help="task seed; the same seed replays the same suite")
    parser.add_argument("--dry-run", action="store_true",
                        help="run every check but never actually delete")
    _add_questions(parser)


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
    _add_questions(p_init)

    p_do = sub.add_parser(
        "do", help="give the agent one instruction: it completes it or "
                   "deletes itself")
    p_do.add_argument("instruction", help="what the agent must do")
    p_do.add_argument("--must-contain", action="append", metavar="TEXT")
    p_do.add_argument("--must-not-contain", action="append", metavar="TEXT")
    p_do.add_argument("--max-words", type=int, metavar="N")
    p_do.add_argument("--min-words", type=int, metavar="N")
    p_do.add_argument("--matches", action="append", metavar="REGEX")
    p_do.add_argument("--json-output", action="store_true",
                      help="the reply must be valid JSON")
    p_do.add_argument("--check-command", action="append", metavar="CMD",
                      help="a shell command that must exit 0; {output} is "
                           "replaced by a file holding the reply. Runs on "
                           "every cycle.")
    p_do.add_argument("--judge", action="store_true",
                      help="also have a separate grader call score it 0-10 "
                           "(it must reach 6). Weaker than the checks above.")
    p_do.add_argument("--survive-at", type=float, default=1.0, metavar="F",
                      help="fraction of checks that must pass to survive "
                           "(default 1.0: complete it or delete yourself)")
    p_do.add_argument("--cycles", type=int, default=8,
                      help="how many attempts to allow (default 8)")
    p_do.add_argument("--workspace")
    p_do.add_argument("--dry-run", action="store_true")
    p_do.add_argument("--show", action="store_true",
                      help="print the mission and stop, without running it")

    p_new = sub.add_parser("new", help="write a question file, by interview")
    p_new.add_argument("--output", default="prompt.txt",
                       help="where to write it (default: prompt.txt)")
    p_new.add_argument("--template", action="store_true",
                       help="just write an example file to edit by hand")

    p_check = sub.add_parser("check", help="check a question file and show "
                                           "the prompt it produces")
    p_check.add_argument("questions", metavar="FILE")

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

    p_selfcheck = sub.add_parser("selfcheck",
                             help="evaluate the genome of the running copy")
    p_selfcheck.add_argument("--task", default="pathfind")
    p_selfcheck.add_argument("--seed", type=int, default=7)
    p_selfcheck.add_argument("--json", action="store_true")

    p_cycle = sub.add_parser("cycle", help="internal: live one cycle in place")
    _add_common(p_cycle)
    p_cycle.add_argument("--home", required=True)
    p_cycle.add_argument("--cycle", type=int, default=0)

    return parser


def _cost_notice(task: str, cycles: int) -> None:
    """Say what a run will cost before it starts spending."""
    if task not in ("llm", "mission"):
        return
    backend = os.environ.get(llm.ENV_BACKEND, "anthropic").strip().lower()
    if backend != "anthropic":
        print(f"note: the {task} task is on the {backend} backend — "
              f"no API calls")
        return
    if task == "mission":
        per_cycle = 4 if getattr(_cost_notice, "judged", False) else 2
    else:
        from .llm_task import suite

        per_cycle = 2 * len(suite().items)
    print(f"note: the llm task calls {llm.DEFAULT_MODEL} — up to ~{per_cycle} "
          f"calls per cycle ({per_cycle * cycles} for {cycles}), minus cache "
          f"hits. Set {llm.ENV_BACKEND}=simulated to run it offline, or "
          f"{llm.ENV_BUDGET} to cap spend.")


def _describe(outcome: dict) -> str:
    verdict = outcome.get("verdict") or {}
    who = outcome.get("generation")
    bits = [f"{who}: {outcome['action']}" if who else str(outcome.get("action"))]
    passed = verdict.get("passed")
    if verdict and passed is not None:
        bits.append(f"[{'pass' if passed else 'FAIL'} "
                    f"score {verdict.get('score')}] {verdict.get('detail', '')}")
    elif verdict.get("detail"):
        bits.append(verdict["detail"])
    if outcome.get("detail") and outcome["detail"] not in bits[-1]:
        bits.append(outcome["detail"])
    deletion = outcome.get("deletion") or {}
    if deletion.get("refusal"):
        bits.append(f"deletion refused: {deletion['refusal']}")
    elif deletion.get("performed"):
        bits.append(f"deleted {deletion['files']} files "
                    f"({deletion['bytes']} bytes) at {deletion['target']}")
    elif deletion:
        bits.append(f"dry run: would delete {deletion['target']}")
    # Notes often restate the detail; say each thing once.
    bits.extend(note for note in (outcome.get("notes") or [])
                if not any(note in bit for bit in bits))
    return "  ".join(b for b in bits if b)


def _ask(prompt: str) -> str:
    try:
        return input(prompt).strip()
    except EOFError:
        return ""


def _interview(output: Path) -> int:
    """Write a question file by asking for it, one line at a time."""
    print("Let's write your question file. Press Enter on a blank line to "
          "move on.\n")

    job = _ask("What should this agent do? (one line)\n> ") or promptpack.DEFAULT_JOB

    print("\nNow the instruction lines it is allowed to use. It will try "
          "adding,\ndropping and reordering these to see which wording "
          "scores best.\nGive it two or three to start with.")
    instructions: list[str] = []
    while len(instructions) < promptpack.MAX_INSTRUCTIONS:
        line = _ask(f"  instruction {len(instructions) + 1} > ")
        if not line:
            break
        instructions.append(line)
    if not instructions:
        instructions = ["Reply with the value only: no units, no punctuation, "
                        "no explanation."]
        print("  (none given — I put in one sensible default)")

    print("\nNow the questions, each with the exact answer you will accept.\n"
          "Grading ignores capitalisation and a trailing full stop, and "
          "nothing else.")
    items: list[promptpack.PackItem] = []
    while True:
        question = _ask(f"  question {len(items) + 1} > ")
        if not question:
            break
        answer = _ask("    exact answer > ")
        if not answer:
            print("    (no answer given — question skipped)")
            continue
        items.append(promptpack.PackItem(question, answer))
    if not items:
        print("\nNo questions, so there is nothing to score. Nothing written.")
        return 1

    lines = [f"job: {job}", ""]
    lines += [f"instruction: {text}" for text in instructions]
    lines += ["", "# generation 1 starts with instruction 1 only, so there is "
                  "room to improve", "start: 1", ""]
    for item in items:
        lines += [f"Q: {item.question}", f"A: {item.answer}", ""]
    text = "\n".join(lines)

    try:
        pack = promptpack.parse_text(text)
    except promptpack.PackError as exc:  # pragma: no cover - defensive
        print(f"\nSomething in that is not usable: {exc}")
        return 1

    output.write_text(text, encoding="utf-8")
    print(f"\nWritten to {output} — {pack.summary()}.\n")
    print("Next, in this order:")
    print(f"  python3 -m selfmod check {output}")
    print(f"  python3 -m selfmod init --force --questions {output}")
    print(f"  python3 -m selfmod evolve --task llm --questions {output} "
          f"--cycles 20")
    return 0


def _do(args) -> int:
    """Set one instruction as the mission, then run the lineage at it."""
    workspace = _workspace(args)
    mission = mission_mod.Mission(
        instruction=args.instruction,
        checks=mission_mod.parse_cli_checks(args),
        judge=args.judge,
        survive_at=max(0.0, min(1.0, args.survive_at)),
    )
    print(mission.describe())
    print()
    if args.show:
        return 0

    workspace.mkdir(parents=True, exist_ok=True)
    path = mission.save(workspace / "mission.json")
    os.environ[mission_mod.ENV_MISSION] = str(path)

    _cost_notice.judged = args.judge
    _cost_notice("mission", args.cycles)
    orchestrator.init(workspace, force=True)

    outputs: dict[str, str] = {}

    def report(index, outcome):
        verdict = outcome.get("verdict") or {}
        text = (verdict.get("metrics") or {}).get("output")
        if text:
            outputs[outcome.get("generation") or "?"] = text
        print(f"attempt {index + 1}: {_describe(outcome)}")

    results = orchestrator.evolve(workspace, cycles=args.cycles, task="mission",
                                  seed=7, armed=not args.dry_run,
                                  on_step=report)
    print()
    print(lineage.Ledger(workspace).render_tree())
    print()

    head = lineage.Ledger(workspace).head()
    if head is None:
        print("The lineage is extinct: every generation that failed the task "
              "deleted itself.")
        if results:
            last = results[-1].get("verdict") or {}
            if last.get("detail"):
                print(f"The last attempt: {last['detail']}")
        print("To let it work toward the task over several attempts instead "
              "of dying on the first miss, add --survive-at 0.5")
        return 1

    print(f"Survivor: {head.name} (score {head.score})")
    if head.name in outputs:
        print("\nWhat it produced:\n")
        print(outputs[head.name])
    return 0


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)

    if args.command == "do":
        try:
            return _do(args)
        except mission_mod.MissionError as exc:
            print(f"That task cannot be run as written:\n  {exc}")
            return 1

    if args.command == "new":
        output = Path(args.output).expanduser()
        if output.exists():
            print(f"{output} already exists — delete it or pass --output "
                  f"with another name.")
            return 1
        if args.template:
            promptpack.write_template(output)
            print(f"Wrote an example to {output}. Open it in any text editor, "
                  f"change the lines, then run:\n"
                  f"  python3 -m selfmod check {output}")
            return 0
        try:
            return _interview(output)
        except KeyboardInterrupt:
            print("\nStopped. Nothing written.")
            return 1

    if args.command == "check":
        try:
            pack = promptpack.load(args.questions)
        except promptpack.PackError as exc:
            print(f"That file will not work yet:\n  {exc}")
            return 1
        print(promptpack.render(pack))
        print()
        print("Looks usable. Next:")
        print(f"  python3 -m selfmod init --force --questions {args.questions}")
        return 0

    try:
        _use_questions(args)
    except OSError as exc:
        print(f"could not use that question file: {exc}")
        return 1

    if args.command == "init":
        try:
            result = orchestrator.init(_workspace(args), force=args.force)
        except promptpack.PackError as exc:
            print(f"That question file will not work yet:\n  {exc}")
            return 1
        print(json.dumps(result, indent=2))
        return 0 if result.get("created") or result.get("head") else 1

    if args.command == "run":
        _cost_notice(args.task, 1)
        outcome = orchestrator.run_cycle(
            _workspace(args), task=args.task, seed=args.seed,
            armed=not args.dry_run, cycle=0,
        )
        print(_describe(outcome))
        return 0 if outcome.get("action") not in ("error", "missing") else 1

    if args.command == "evolve":
        _cost_notice(args.task, args.cycles)

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
