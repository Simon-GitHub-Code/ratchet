"""Files that act the moment they are written, before any merge (ADR 0033).

The merge judge reads a change once it is committed and decides at the merge, so it cannot
protect against a file that has already acted. Two kinds of file do:

- A CI workflow under ``.github/workflows/``. GitHub runs a branch's own workflows on a push or a
  pull request, with the repository's token and secrets.
- An agent-instruction file at any depth: ``CLAUDE.md``, ``CLAUDE.local.md``, ``AGENTS.md``,
  ``GEMINI.md``, or anything under a ``.claude/`` directory but its ``worktrees/``. Claude Code
  loads a nested ``CLAUDE.md`` when it reads a file beside it, and follows it. An agent could
  write one, let other agents work under it, and delete it before the commit, so the judge never
  sees it.

In a checkout whose default branch holds the manifest, the hook asks the owner before a write of
either kind, on every branch. Names are compared without letter case, as a case-insensitive
filesystem opens them. This is the one definition of an instruction file: the merge judge imports
it from here.
"""

from enum import Enum
from pathlib import PurePosixPath

INSTRUCTION_NAMES = frozenset({"claude.md", "claude.local.md", "agents.md", "gemini.md"})
INSTRUCTION_DIRECTORY = ".claude"
WORKTREES = "worktrees"
WORKFLOWS = (".github", "workflows")


class Kind(Enum):
    """What a file that acts before the merge does, as the owner reads it at the prompt."""

    WORKFLOW = "This changes what GitHub runs with this repository's secrets, before any merge"
    INSTRUCTIONS = "This changes the instructions agents follow in this project, starting now"


def is_instruction(relative: str) -> bool:
    """Whether ``relative``, a path from the checkout's root, is an agent-instruction file.

    ``.claude/worktrees/`` holds agent checkouts, each judged from its own root, so a path below
    it is under no ``.claude/`` directory of this one.
    """
    parts = [part.lower() for part in PurePosixPath(relative).parts]
    if not parts:
        return False
    below = (
        part == INSTRUCTION_DIRECTORY and parts[index + 1] != WORKTREES
        for index, part in enumerate(parts[:-1])
    )
    return parts[-1] in INSTRUCTION_NAMES or any(below)


def kind_of(relative: str) -> Kind | None:
    """The kind of ``relative``, a path from the checkout's root, or ``None`` when it waits."""
    parts = tuple(part.lower() for part in PurePosixPath(relative).parts)
    if len(parts) > len(WORKFLOWS) and parts[: len(WORKFLOWS)] == WORKFLOWS:
        return Kind.WORKFLOW
    return Kind.INSTRUCTIONS if is_instruction(relative) else None
