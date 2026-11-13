"""What a rule may consult: the working directory, the branch, the locator and git state."""

from collections.abc import Callable, Iterable
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Protocol

from ratchet_guards.paths import Hit, Locator
from ratchet_guards.refs import MAIN_BRANCHES
from ratchet_guards.verdict import ACTING_RULES, Verdict

# Why: what git prints for a detached HEAD, and a name git refuses to give a branch.
DETACHED = "HEAD"


class RepoState(Protocol):
    """Git facts the history rules need; ``None`` means git could not say."""

    def current_branch(self, cwd: Path) -> str | None:
        """The checked-out branch in ``cwd``."""
        ...

    def checked_out(self, root: Path) -> str | None:
        """The branch the checkout at ``root`` has, ``DETACHED`` for a detached ``HEAD``."""
        ...

    def default_branches(self, root: Path) -> frozenset[str]:
        """The branch names a commit at ``root`` reaches the default branch under.

        ``main``, ``master`` and the branch the guard reads the committed manifest from.
        """
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
    now is not what the command will meet. ``lost`` marks a ``cwd`` that a ``cd`` the guard could
    not resolve may have replaced: the command then runs somewhere unknown, and a relative path
    it writes may be a guard path of whatever repository that is. ``hidden_switch`` is set once
    an earlier command may have moved ``HEAD`` in a way the guard does not read: a ``git switch``
    inside a shell's script, a substitution, ``eval``, ``xargs`` or an alias. Any checkout may
    then be on its default branch.
    """

    cwd: Path
    branch: str | None = None
    maybe: frozenset[str] = frozenset()
    exported: frozenset[str] = frozenset()
    unread: bool = False
    unsettled: bool = False
    lost: bool = False
    hidden_switch: bool = False

    def moved_to(self, cwd: Path, *, anchored: bool = True) -> "Context":
        """This context after a ``cd``: the branch known here may still be the one there.

        ``anchored`` says ``cwd`` is where the shell goes; a relative step from a lost place is
        still lost.
        """
        return replace(
            self, cwd=cwd, branch=None, maybe=self._claims(), lost=self.lost and not anchored
        )

    def adrift(self) -> "Context":
        """This context after a ``cd`` to a place the guard cannot resolve."""
        return replace(self, lost=True)

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

    def switching_unseen(self) -> "Context":
        """This context after a command that may have moved ``HEAD`` where the guard cannot see."""
        return replace(self, hidden_switch=True)

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

    def lands_now(self, context: Context, path: Path) -> bool:
        """Whether a change to ``path`` takes effect before any merge the judge reads.

        It does not when the checkout holding ``path`` has a branch other than its default one,
        or a detached ``HEAD``: a commit there reaches the default branch only through a merge.
        It does for a file in no checkout, in a checkout git cannot read, and in any checkout a
        switch earlier on the line may leave on a default branch, a switch the guard cannot read
        included (ADR 0033). A switch the line makes for certain counts for its own checkout.
        """
        filesystem = self.locator.filesystem
        root = filesystem.checkout(filesystem.canonical(path))
        if root is None or context.hidden_switch:
            return True
        names = self.repo.default_branches(root)
        own = root == filesystem.checkout(filesystem.canonical(context.cwd))
        head = context.branch if own and context.branch is not None else self.repo.checked_out(root)
        return head is None or bool((context.maybe | {head}) & names)

    def writes(
        self, context: Context, hits: Iterable[Hit], detail: Callable[[Hit], str]
    ) -> list[Verdict]:
        """The verdicts on changing ``hits`` from the shell.

        A path only the owner changes is refused anywhere; any other guard path only where the
        change takes effect before a merge.
        """
        return [
            Verdict(
                "guard-path-write" if hit.group.locked else "guard-path-default",
                detail(hit),
                hit.group.source,
            )
            for hit in hits
            if hit.group.locked or self.lands_now(context, hit.at)
        ]

    def acting(self, paths: Iterable[Path]) -> list[Verdict]:
        """The owner's question on each of ``paths`` that acts before the merge, on any branch."""
        found = (self.locator.acting(path) for path in paths)
        return [Verdict(ACTING_RULES[file.kind], file.path) for file in found if file is not None]
