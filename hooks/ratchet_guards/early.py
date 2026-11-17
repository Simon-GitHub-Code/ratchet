from enum import Enum
from pathlib import PurePosixPath

INSTRUCTION_NAMES = frozenset({"claude.md", "claude.local.md", "agents.md", "gemini.md"})
INSTRUCTION_DIRECTORY = ".claude"
WORKTREES = "worktrees"
WORKFLOWS = (".github", "workflows")


class Kind(Enum):
    WORKFLOW = "This changes what GitHub runs with this repository's secrets, before any merge"
    INSTRUCTIONS = "This changes the instructions agents follow in this project, starting now"


def is_instruction(relative: str) -> bool:
    parts = [part.lower() for part in PurePosixPath(relative).parts]
    if not parts:
        return False
    below = (
        part == INSTRUCTION_DIRECTORY and parts[index + 1] != WORKTREES
        for index, part in enumerate(parts[:-1])
    )
    return parts[-1] in {*INSTRUCTION_NAMES, INSTRUCTION_DIRECTORY} or any(below)


def kind_of(relative: str) -> Kind | None:
    parts = tuple(part.lower() for part in PurePosixPath(relative).parts)
    if (
        parts
        and parts[: len(WORKFLOWS)] == WORKFLOWS[: len(parts)]
        and len(parts) <= len(WORKFLOWS) + 1
    ):
        return Kind.WORKFLOW
    return Kind.INSTRUCTIONS if is_instruction(relative) else None
