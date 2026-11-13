"""The owner's next move, decided by the plugin's route, and when the run stops.

The driver never chooses a step: it runs the route engine behind ``/ratchet:next`` and does what
its ``next:`` line names. A slash command is typed into the session. A shell command is pasted,
which the run counts. Anything else (commit this, mark that ticket resolved) is something a
non-engineer asks the assistant to do for them.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum


class Action(StrEnum):
    """What the owner does with an instruction."""

    TYPE = "type"
    PASTE = "paste"
    ASK = "ask"
    FORGE = "forge"


@dataclass(frozen=True)
class Instruction:
    """One answer of the route: the command, why, the step after, its stage, the proposals."""

    command: str
    why: str
    then: str
    stage: str
    also: tuple[str, ...] = ()

    @classmethod
    def parse(cls, data: dict[str, object]) -> Instruction:
        """The route's ``--json`` output."""
        also = data.get("also")
        return cls(
            command=str(data["command"]),
            why=str(data.get("why", "")),
            then=str(data.get("then", "")),
            stage=str(data.get("stage", "")),
            also=tuple(str(a) for a in also) if isinstance(also, list) else (),
        )


@dataclass(frozen=True)
class Move:
    """What the owner does: type a command, paste shell commands, or ask the assistant."""

    action: Action
    text: str
    commands: tuple[str, ...] = ()


# Why: these stages name work on the checkout in words (commit it, mark a ticket resolved) that
# a non-engineer cannot do by hand; they ask the assistant, which the route allows.
ASK_STAGES = frozenset({"change", "triage", "resolve", "blocked", "blocked-run"})
FORGE_STAGES = frozenset({"review", "pull-request"})
RUNNERS = (
    "git",
    "just",
    "python3",
    "python",
    "uv",
    "npm",
    "pnpm",
    "yarn",
    "bun",
    "node",
    "npx",
    "make",
    "bash",
    "sh",
    "cargo",
    "go",
)
TICKED = re.compile(r"`([^`]+)`")
NEXT_LINE = re.compile(r"^next:\s*(.*)$", re.MULTILINE)
ASK = "I was told the next step is: {command}. I don't know how to do that myself; please do it for me."


def is_command(text: str) -> bool:
    """Whether ``text`` reads as a shell command line."""
    first = text.strip().split(" ", 1)[0]
    return first in RUNNERS


def shell_commands(text: str) -> tuple[str, ...]:
    """The backticked shell commands in ``text``, in order."""
    return tuple(span.strip() for span in TICKED.findall(text) if is_command(span))


def next_commands(output: str) -> tuple[str, ...]:
    """The shell commands an entry point's ``next:`` lines tell the owner to run.

    ``merge`` prints ``next: just check-deep on `main`: ...`` with the command bare, and
    ``next: the owner types `git switch ...`, then `just check-deep` ...`` with it ticked.
    """
    found: list[str] = []
    for line in NEXT_LINE.findall(output):
        ticked = shell_commands(line)
        if ticked:
            found.extend(ticked)
            continue
        bare = re.split(r" on `|: ", line, maxsplit=1)[0].strip()
        if is_command(bare):
            found.append(bare)
    return tuple(dict.fromkeys(found))


def move_for(instruction: Instruction) -> Move:
    """What the owner does with the route's instruction."""
    command = instruction.command
    if instruction.stage in FORGE_STAGES:
        return Move(Action.FORGE, command)
    if command.startswith("/"):
        return Move(Action.TYPE, command)
    if instruction.stage == "hooks" and re.search(r"hooks[-_]install", command):
        # Why: /ratchet:next runs the hooks-install entry point itself; it is not the owner's.
        return Move(Action.TYPE, "/ratchet:next")
    commands = shell_commands(command)
    if instruction.stage not in ASK_STAGES and commands:
        return Move(Action.PASTE, command, commands)
    return Move(Action.ASK, ASK.format(command=command))


# --- stopping -----------------------------------------------------------------------------


class Stop(StrEnum):
    """Why a run ended."""

    USABLE = "usable-merged"
    WALL_CLOCK = "wall-clock"
    COST = "cost"
    REPEAT = "repeat"
    FORGE = "forge-stage"
    MAX_STEPS = "max-steps"
    ERROR = "error"


@dataclass(frozen=True)
class Budget:
    """The limits of one run."""

    minutes: float = 120.0
    usd: float = 40.0
    repeats: int = 3
    steps: int | None = None


@dataclass(frozen=True)
class Seen:
    """One instruction the route gave, with the checkout's state when it gave it."""

    command: str
    state: str


def stop_reason(
    budget: Budget,
    *,
    elapsed_s: float,
    cost_usd: float,
    usable: bool,
    history: Sequence[Seen],
) -> Stop | None:
    """Why the run stops before the next instruction, or ``None`` to carry on.

    The same instruction twice on an unchanged checkout is a step that made no progress; the
    same instruction ``budget.repeats`` times is a loop even when something changed between.
    """
    if usable:
        return Stop.USABLE
    if elapsed_s >= budget.minutes * 60:
        return Stop.WALL_CLOCK
    if cost_usd >= budget.usd:
        return Stop.COST
    if len(history) >= 2 and history[-1] == history[-2]:  # noqa: PLR2004
        return Stop.REPEAT
    if history and sum(s.command == history[-1].command for s in history) >= budget.repeats:
        return Stop.REPEAT
    if budget.steps is not None and len(history) > budget.steps:
        return Stop.MAX_STEPS
    return None
