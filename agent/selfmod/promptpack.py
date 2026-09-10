"""A plain-text file describing the job, so nobody has to edit Python.

The format is deliberately forgiving — blank lines and ``#`` comments are
ignored, keys are case-insensitive, and questions may be written either as a
``Q:``/``A:`` pair or on one line separated by ``|``::

    job: You answer short factual questions.

    instruction: Reply with the value only, no explanation.
    instruction: Write dates as YYYY-MM-DD.

    start: 1

    Q: What is the capital of France?
    A: paris

    How many days are in a leap year? | 366

Errors carry the line number and say what to do about them: this file is
written by hand, usually by someone who did not write the rest of this.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

ENV_PACK = "SELFMOD_PROMPT_FILE"

DEFAULT_JOB = "You answer short factual questions about the text you are given."
MAX_INSTRUCTIONS = 32

TEMPLATE = '''# What this agent is for. One line.
job: You answer short factual questions.

# The instruction lines the agent may use. It evolves its own prompt by
# adding, dropping and reordering these, keeping whatever scores best.
# Two or more, up to 32.
instruction: Reply with the value only: no units, no punctuation, no explanation.
instruction: Write any date as YYYY-MM-DD.
instruction: Write numbers in digits, with no commas.

# Which instructions the first generation starts with, by their number above.
# Start it weak and let it find the rest. Optional.
start: 1

# The questions, and the exact answers you will accept. Grading is exact
# match, ignoring capitalisation and a trailing full stop — nothing else.
Q: What is the capital of France?
A: Paris

Q: How many days are in a leap year?
A: 366
'''


class PackError(Exception):
    """A problem in the file, phrased for the person who wrote it."""


@dataclass
class PackItem:
    question: str
    answer: str
    line: int = 0


@dataclass
class Pack:
    job: str = DEFAULT_JOB
    instructions: list[str] = field(default_factory=list)
    start: list[int] = field(default_factory=list)
    items: list[PackItem] = field(default_factory=list)
    path: Path | None = None
    warnings: list[str] = field(default_factory=list)

    def summary(self) -> str:
        return (f"{len(self.items)} questions, {len(self.instructions)} "
                f"instructions, starting with {self.start or '[]'}")


def parse_text(text: str, *, path: Path | None = None) -> Pack:
    pack = Pack(path=path)
    pending_question: tuple[str, int] | None = None

    for number, raw in enumerate(text.splitlines(), start=1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue

        key, _, value = line.partition(":")
        key = key.strip().lower()
        value = value.strip()

        if key == "job" and value:
            pack.job = value
        elif key in ("instruction", "clause", "rule") and value:
            pack.instructions.append(value)
        elif key == "start":
            for token in value.replace(",", " ").split():
                try:
                    pack.start.append(int(token))
                except ValueError:
                    raise PackError(
                        f"line {number}: 'start' takes instruction numbers "
                        f"like '1 3', not {token!r}"
                    ) from None
        elif key in ("q", "question") and value:
            if pending_question is not None:
                raise PackError(
                    f"line {number}: the question on line {pending_question[1]} "
                    f"has no 'A:' answer under it"
                )
            pending_question = (value, number)
        elif key in ("a", "answer") and value:
            if pending_question is None:
                raise PackError(
                    f"line {number}: an answer with no question above it"
                )
            pack.items.append(PackItem(pending_question[0], value,
                                       pending_question[1]))
            pending_question = None
        elif "|" in line:
            question, _, answer = line.partition("|")
            if not question.strip() or not answer.strip():
                raise PackError(
                    f"line {number}: write it as 'question | answer', with "
                    f"text on both sides of the |"
                )
            pack.items.append(PackItem(question.strip(), answer.strip(), number))
        else:
            raise PackError(
                f"line {number}: don't know what to do with {line!r}. Lines "
                f"look like 'job: ...', 'instruction: ...', 'Q: ...', "
                f"'A: ...', or 'question | answer'."
            )

    if pending_question is not None:
        raise PackError(
            f"line {pending_question[1]}: the last question has no 'A:' answer"
        )
    return validate(pack)


def validate(pack: Pack) -> Pack:
    if not pack.items:
        raise PackError("no questions found — add at least one 'Q:'/'A:' pair")
    if not pack.instructions:
        raise PackError(
            "no instruction lines found — add at least two 'instruction:' "
            "lines, or the agent has no prompt to evolve"
        )
    if len(pack.instructions) > MAX_INSTRUCTIONS:
        raise PackError(
            f"{len(pack.instructions)} instructions is too many "
            f"(limit {MAX_INSTRUCTIONS}) — trim the list"
        )
    if len(pack.instructions) == 1:
        pack.warnings.append(
            "only one instruction line: the agent can add or drop it and "
            "little else. Two or three give it room to work."
        )

    seen: dict[str, int] = {}
    for item in pack.items:
        key = item.question.strip().lower()
        if key in seen:
            raise PackError(
                f"line {item.line}: this question also appears on line "
                f"{seen[key]}. Each question must be different."
            )
        seen[key] = item.line
        if not item.answer.strip():
            raise PackError(f"line {item.line}: the answer is empty")

    cleaned: list[int] = []
    for number in pack.start:
        if not 1 <= number <= len(pack.instructions):
            raise PackError(
                f"'start: {number}' has no instruction {number} — there are "
                f"{len(pack.instructions)}, numbered 1 to {len(pack.instructions)}"
            )
        if number - 1 not in cleaned:
            cleaned.append(number - 1)  # stored zero-based, as the genome wants
    pack.start = cleaned
    return pack


def load(path) -> Pack:
    path = Path(path).expanduser()
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        raise PackError(f"no such file: {path}") from None
    except OSError as exc:
        raise PackError(f"could not read {path}: {exc}") from None
    return parse_text(text, path=path.resolve())


def active() -> Pack | None:
    """The pack named by the environment, if any."""
    raw = os.environ.get(ENV_PACK, "").strip()
    return load(raw) if raw else None


def write_template(path) -> Path:
    path = Path(path).expanduser()
    path.write_text(TEMPLATE, encoding="utf-8")
    return path


def render(pack: Pack) -> str:
    """What `check` prints back: the file as the agent will read it."""
    lines = [f"file: {pack.path}" if pack.path else "file: (unsaved)",
             f"job:  {pack.job}", "",
             "instructions it may use (it picks and orders these itself):"]
    for index, instruction in enumerate(pack.instructions, start=1):
        mark = "*" if index - 1 in pack.start else " "
        lines.append(f"  {mark} {index}. {instruction}")
    lines.append("")
    lines.append("* = in the starting prompt, which looks like this:")
    lines.append(f"    {pack.job}")
    for index in pack.start:
        lines.append(f"    {pack.instructions[index]}")
    lines.append("")
    lines.append(f"questions ({len(pack.items)}):")
    for item in pack.items:
        lines.append(f"    {item.question}")
        lines.append(f"      -> must answer exactly: {item.answer}")
    for warning in pack.warnings:
        lines.append("")
        lines.append(f"warning: {warning}")
    return "\n".join(lines)
