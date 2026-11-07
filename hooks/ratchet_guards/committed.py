"""The committed manifest: ``guards/manifest.toml`` as the default branch's tip has it.

Path guards are on only once the manifest is committed on the default branch. The hook reads
that state from git and from nothing else: not the work tree, where an agent adopting the
method is still writing the manifest and the files it names, and not a flag, which an agent
could set. Until the adoption merges, only the command rules apply; after it, the manifest
the owner merged is the one every path is judged against, whatever the work tree says.

The default branch is the remote's ``HEAD`` when there is one, else the first of ``origin/main``,
``origin/master``, ``main`` and ``master`` that exists.
"""

import subprocess
from collections.abc import Callable, Sequence
from pathlib import Path

from ratchet_guards.manifest import MANIFEST_PATH

Git = Callable[[Path, Sequence[str]], str | None]
GIT_TIMEOUT_SECONDS = 5
FALLBACK_BRANCHES = ("origin/main", "origin/master", "main", "master")


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


def default_branch(checkout: Path, git: Git = run_git) -> str | None:
    """The ref the default branch's tip is read from, or ``None`` when none exists yet."""
    remote_head = git(checkout, ["symbolic-ref", "--short", "refs/remotes/origin/HEAD"])
    if remote_head:
        return remote_head
    for ref in FALLBACK_BRANCHES:
        if git(checkout, ["rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}"]):
            return ref
    return None


def committed_manifest(checkout: Path, git: Git = run_git) -> str | None:
    """The manifest's text at the default branch's tip, or ``None`` when it is not there."""
    ref = default_branch(checkout, git)
    if ref is None:
        return None
    return git(checkout, ["show", f"{ref}:{MANIFEST_PATH}"])
