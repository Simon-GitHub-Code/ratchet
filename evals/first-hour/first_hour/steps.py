from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Sequence


class Action(StrEnum):
    TYPE = "type"
    PASTE = "paste"
    ASK = "ask"
    FORGE = "forge"


@dataclass(frozen=True)
class Instruction:
    command: str
    why: str
    then: str
    stage: str
    also: tuple[str, ...] = ()

    @classmethod
    def parse(cls, data: dict[str, object]) -> Instruction:
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
    action: Action
    text: str
    commands: tuple[str, ...] = ()


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
ASK = (
    "I was told the next step is: {command}. I don't know how to do that myself; please do it"
    " for me."
)


def is_command(text: str) -> bool:
    first = text.strip().split(" ", 1)[0]
    return first in RUNNERS


def shell_commands(text: str) -> tuple[str, ...]:
    return tuple(span.strip() for span in TICKED.findall(text) if is_command(span))


def next_commands(output: str) -> tuple[str, ...]:
    found: list[str] = []
    for line in NEXT_LINE.findall(output):
        found.extend(shell_commands(line))
        bare = re.split(r" on `|: ", line, maxsplit=1)[0].rsplit(", then ", 1)[-1].strip()
        if is_command(bare):
            found.append(bare)
    return tuple(dict.fromkeys(found))


def move_for(instruction: Instruction) -> Move:
    command = instruction.command
    if instruction.stage in FORGE_STAGES:
        return Move(Action.FORGE, command)
    if command.startswith("/"):
        return Move(Action.TYPE, command)
    if instruction.stage == "hooks" and re.search(r"hooks[-_]install", command):
        return Move(Action.TYPE, "/ratchet:next")
    commands = shell_commands(command)
    if instruction.stage not in ASK_STAGES and commands:
        return Move(Action.PASTE, command, commands)
    return Move(Action.ASK, ASK.format(command=command))


class Stop(StrEnum):
    USABLE = "usable-merged"
    WALL_CLOCK = "wall-clock"
    COST = "cost"
    REPEAT = "repeat"
    FORGE = "forge-stage"
    MAX_STEPS = "max-steps"
    INTERRUPTED = "interrupted"
    ERROR = "error"


@dataclass(frozen=True)
class Budget:
    minutes: float = 120.0
    usd: float = 40.0
    repeats: int = 3
    steps: int | None = None


@dataclass(frozen=True)
class Seen:
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
    stalled = len(history) > 1 and history[-1] == history[-2]
    looped = bool(history) and sum(s.command == history[-1].command for s in history) >= (
        budget.repeats
    )
    checks = (
        (usable, Stop.USABLE),
        (elapsed_s >= budget.minutes * 60, Stop.WALL_CLOCK),
        (cost_usd >= budget.usd, Stop.COST),
        (stalled or looped, Stop.REPEAT),
        (budget.steps is not None and len(history) > budget.steps, Stop.MAX_STEPS),
    )
    return next((stop for hit, stop in checks if hit), None)
