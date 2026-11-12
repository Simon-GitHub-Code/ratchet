"""What a rule may consult: the working directory, the branch, the locator and git state."""

from collections.abc import Iterable
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

    def full_name(self, cwd: Path, name: str) -> str | None:
        """The full name of the ref git reads ``name`` as in ``cwd``, when it is exactly one."""
        ...


@dataclass(frozen=True, slots=True)
class Context:
    """Where one command of a command line may run, and what the line has set for it.

    A command line is judged before any of it runs, so the guard cannot know that an earlier
    ``cd`` or ``git switch`` took effect. It is certain only when the command that follows is
    chained to it with ``&&``; in every other case the line is judged once in each state it may
    be in (``ratchet_guards.bash``, ADR 0024).

    ``branch`` is the branch a switch earlier on the line left ``cwd`` on, when that is certain.
    ``maybe`` holds the branches a switch may have left it on. ``exported`` holds the variables
    the line has set for this command. ``unread`` marks a ``git`` aimed at a repository other
    than the one in ``cwd`` (``--git-dir``, ``GIT_DIR``): the guard cannot ask which branch it
    is on, and judges it as a default branch. ``unsettled`` is set once an earlier command on the
    line may have changed what a ref name or an upstream resolves to, so what git says of them
    now is not what the command will meet.
    """

    cwd: Path
    branch: str | None = None
    maybe: frozenset[str] = frozenset()
    exported: frozenset[str] = frozenset()
    unread: bool = False
    unsettled: bool = False

    def moved_to(self, cwd: Path) -> "Context":
        """This context after a ``cd``: the branch known here may still be the one there."""
        return replace(self, cwd=cwd, branch=None, maybe=self._claims())

    def switched_to(self, branch: str) -> "Context":
        """This context after a branch switch that is certain to have happened."""
        return replace(self, branch=branch, maybe=frozenset())

    def claiming(self, branches: Iterable[str]) -> "Context":
        """This context after a switch that may not have happened, or whose branch is unknown."""
        return replace(self, maybe=self.maybe | frozenset(branches))

    def exporting(self, names: Iterable[str]) -> "Context":
        """This context with ``names`` set in the environment."""
        return replace(self, exported=self.exported | frozenset(names))

    def unsettling(self) -> "Context":
        """This context after a command that may have changed what a name resolves to."""
        return replace(self, unsettled=True)

    def elsewhere(self) -> "Context":
        """This context for a ``git`` aimed at a repository the guard does not read."""
        return replace(self, unread=True)

    def _claims(self) -> frozenset[str]:
        return self.maybe | (frozenset({self.branch}) if self.branch is not None else frozenset())


@dataclass(frozen=True, slots=True)
class Guard:
    """The ports every rule shares."""

    locator: Locator
    repo: RepoState

    def branches(self, context: Context, cwd: Path) -> frozenset[str]:
        """The branches a command in ``cwd`` may act on."""
        if context.unread:
            return context.maybe | MAIN_BRANCHES
        certain = context.branch is not None and cwd == context.cwd
        here = context.branch if certain else self.repo.current_branch(cwd)
        return context.maybe | (frozenset({here}) if here else frozenset())

    def on_main(self, context: Context, cwd: Path) -> bool:
        """Whether one of them is a default branch."""
        return bool(self.branches(context, cwd) & MAIN_BRANCHES)
