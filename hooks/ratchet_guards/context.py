"""What a rule may consult: the working directory, the branch, the locator and git state."""

from dataclasses import dataclass, replace
from pathlib import Path
from typing import Protocol

from ratchet_guards.paths import Locator
from ratchet_guards.refs import MAIN_BRANCHES


class RepoState(Protocol):
    """Git facts the history rules need; ``None`` means git could not say."""

    def current_branch(self, cwd: Path) -> str | None:
        """The checked-out branch in ``cwd``."""
        ...

    def head_is_pushed(self, cwd: Path) -> bool | None:
        """Whether a remote-tracking branch already contains ``HEAD``."""
        ...


@dataclass(frozen=True, slots=True)
class Context:
    """Where the next command of a command line runs.

    ``branch`` is set once an earlier command on the same line switched branch, so that
    ``git switch main && git reset --hard x`` is judged on main.
    """

    cwd: Path
    branch: str | None = None

    def moved_to(self, cwd: Path) -> "Context":
        """This context after a ``cd``."""
        return replace(self, cwd=cwd)

    def switched_to(self, branch: str) -> "Context":
        """This context after a branch switch."""
        return replace(self, branch=branch)


@dataclass(frozen=True, slots=True)
class Guard:
    """The ports every rule shares."""

    locator: Locator
    repo: RepoState

    def branch(self, context: Context, cwd: Path) -> str | None:
        """The branch a command in ``cwd`` acts on."""
        return context.branch if context.branch is not None else self.repo.current_branch(cwd)

    def on_main(self, context: Context, cwd: Path) -> bool:
        """Whether that branch is main."""
        return self.branch(context, cwd) in MAIN_BRANCHES
