"""The committed manifest: ``guards/manifest.toml`` as the default branch has it.

Path guards are on only once the manifest is committed on the default branch. The hook reads
that state from git and from nothing else: not the work tree, where an agent adopting the
method is still writing the manifest and the files it names, and not a flag, which an agent
could set. Until the adoption merges, only the command rules apply; after it, the manifest
the owner merged is the one every path is judged against, whatever the work tree says.

A default branch whose tip lacks the file but whose history held it has not gone back to before
the adoption: the hook reads the last manifest it held and locks every path that one named
(``ratchet_guards.manifest.gone``). Finding that costs one path-limited ``git log``, run only when
the tip lacks the file.

The default branch is the remote's ``HEAD`` when there is one and it names a commit, else the first
of ``refs/remotes/origin/main``, ``refs/remotes/origin/master``, ``refs/heads/main`` and
``refs/heads/master`` that exists. Every name is spelled in full and checked with ``show-ref
--verify``, which takes no other spelling: git looks a short name such as ``main`` up in
``.git/main``, ``refs/main`` and ``refs/tags/main`` before ``refs/heads/main``, so a tag or a ref an
agent made under the short name would be read in place of the branch. ``ratchet_guards.refmoves``
refuses the commands that move these refs (ADR 0016).

A repository with no remote whose default branch has another name (``trunk``, ``develop``) has none
of those refs. Then the default branch is the first one the repository's main work tree (the first
entry of ``git worktree list``) was on, read from that work tree's ``HEAD`` reflog: the oldest
``checkout: moving from <branch>``, else the branch it is on. Git keeps that outside ``refs/``, and
a ref an agent creates, renames or deletes does not change it. Moving the main work tree's ``HEAD``
to another branch, or detaching it, adds a later entry and cannot change the first: were the anchor
the current branch, an agent could commit a weakened manifest on a branch of its own and switch the
main work tree to it. A branch that no longer exists is skipped for the next. A reflog is a file
git expires, so this anchor covers only the window before the conformance gate is first run: it
is red while the default branch is found this way, until the owner adds the remote or names the
branch ``main`` or ``master`` (ADR 0041, ``anchored_on_reflog``).
"""

import subprocess
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path

from ratchet_guards.manifest import MANIFEST_PATH
from ratchet_guards.refs import LOCAL, REMOTE_HEAD, TRACKING

Git = Callable[[Path, Sequence[str]], str | None]
GIT_TIMEOUT_SECONDS = 5
FALLBACK_BRANCHES = (*TRACKING, *LOCAL)
# Why: `log.follow` in the user's config would follow the file back through a rename, to a commit
# that holds it under another name.
_LAST_HELD = ("-c", "log.follow=false", "log", "-1", "--format=%H", "--diff-filter=AM")


@dataclass(frozen=True, slots=True)
class Committed:
    """The manifest's text, and whether the default branch's tip lost the file that held it."""

    text: str
    gone: bool = False


def run_git(cwd: Path, args: Sequence[str]) -> str | None:
    """``git -C cwd args`` stdout stripped, or ``None`` when git fails or is missing."""
    try:
        completed = subprocess.run(  # noqa: S603 # -- a fixed argv, no shell
            ["git", "-C", str(cwd), *args],  # noqa: S607 # -- git is resolved from PATH on purpose
            capture_output=True,
            text=True,
            timeout=GIT_TIMEOUT_SECONDS,  # pragma: no mutate # -- only bounds a hung git
            check=False,  # pragma: no mutate # -- a failure returns None either way
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return completed.stdout.strip() if completed.returncode == 0 else None


_MOVING = "checkout: moving from "


def _main_worktree_branches(checkout: Path, git: Git) -> list[str]:
    """The branches the main work tree has been on, the first it was on first, then the current.

    The first is read from the main work tree's ``HEAD`` reflog, the oldest ``checkout: moving
    from <branch> to <other>``. Moving ``HEAD`` there adds an entry and changes the current branch
    but never the first one.
    """
    listed = git(checkout, ["worktree", "list", "--porcelain"])
    first = (listed or "").split("\n\n", 1)[0].splitlines()
    path = next((ln.removeprefix("worktree ") for ln in first if ln.startswith("worktree ")), "")
    current = next((ln.removeprefix("branch ") for ln in first if ln.startswith("branch ")), "")
    log = git(Path(path), ["log", "-g", "--format=%gs", "HEAD"]) if path else None
    moves = [line for line in reversed((log or "").splitlines()) if line.startswith(_MOVING)]
    previous = [line.removeprefix(_MOVING).rpartition(" to ")[0] for line in moves]
    return [*(f"refs/heads/{name}" for name in previous), *([current] if current else [])]


def _first_existing(checkout: Path, refs: Sequence[str], git: Git) -> str | None:
    return next((r for r in refs if git(checkout, ["show-ref", "--verify", "--hash", r])), None)


def _fixed(checkout: Path, git: Git) -> str | None:
    remote_head = git(checkout, ["symbolic-ref", REMOTE_HEAD])
    return _first_existing(
        checkout, [*([remote_head] if remote_head else []), *FALLBACK_BRANCHES], git
    )


def default_branch(checkout: Path, git: Git = run_git) -> str | None:
    """The full name of the ref the default branch's tip is read from, else ``None``."""
    return _fixed(checkout, git) or _first_existing(
        checkout, _main_worktree_branches(checkout, git), git
    )


def anchored_on_reflog(checkout: Path, git: Git = run_git) -> bool:
    """Whether the default branch is found only through the main work tree's reflog."""
    return _fixed(checkout, git) is None and default_branch(checkout, git) is not None


def committed_manifest(checkout: Path, git: Git = run_git) -> Committed | None:
    """The manifest at the default branch's tip, else the last one it held, else ``None``."""
    ref = default_branch(checkout, git)
    if ref is None:
        return None
    text = git(checkout, ["show", f"{ref}:{MANIFEST_PATH}"])
    if text is not None:
        return Committed(text)
    last = git(checkout, [*_LAST_HELD, ref, "--", MANIFEST_PATH])
    held = git(checkout, ["show", f"{last}:{MANIFEST_PATH}"]) if last else None
    return None if held is None else Committed(held, gone=True)
