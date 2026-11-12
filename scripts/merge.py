#!/usr/bin/env python3
"""The merge with no forge: judge a branch, run ``check`` on the merge result, land it. ADR 0021.

``merge.py <branch>`` is what the binding's ``merge`` entry point runs in a repository with no
remote. It makes a temporary work tree of the default branch, merges the branch there with
``--no-ff``, asks the merge judge (``merge_ready.py``) about the branch, and runs the binding's
``check`` in that work tree: on the merge result, never on the branch. Only when the judge says
``merge: agent`` and ``check`` is green does the default branch move to the merge commit, forward
and nowhere else. Otherwise it prints ``merge: owner`` with every reason and changes nothing.
The work tree is removed either way.

``check`` runs in a fresh work tree, as it does in CI, so its recipes install what they need.

With a remote named ``origin`` the script refuses: the pull request's merge job is the one that
merges there, and a local merge could not be pushed.

Usage: ``merge.py <branch> [--root DIR]``. Exit 0 merged, 1 the owner merges, 2 nothing to judge.
"""

from __future__ import annotations

import argparse
import shlex
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import gatelib
import merge_ready
from merge_ready import Reason

HEADS = "refs/heads/"
CHECK = "check"
TIER2 = "check-deep"


def local_default(root: Path) -> str | None:
    """The default branch as a local branch name, or ``None`` when there is none."""
    names = [name for name in gatelib.default_branches(root) if not name.startswith("origin/")]
    return next((name for name in names if tip(root, name)), None)


def tip(root: Path, branch: str) -> str | None:
    """The commit the local branch ``branch`` points at."""
    return gatelib.git(root, "rev-parse", "--verify", "--quiet", f"{HEADS}{branch}^{{commit}}")


def checked_out(root: Path, branch: str) -> Path | None:
    """The work tree that has ``branch`` checked out, if one does."""
    listing = gatelib.git(root, "worktree", "list", "--porcelain") or ""
    for entry in listing.split("\n\n"):
        lines = entry.splitlines()
        if f"branch {HEADS}{branch}" in lines:
            return Path(lines[0].removeprefix("worktree "))
    return None


def run_check(tree: Path) -> Reason | None:
    """Run the binding's ``check`` on the merge result; why it is not green, if it is not."""
    command = gatelib.entry_point(tree, CHECK)
    if command is None:
        return Reason("gates", "the binding names no `check` entry point, so no gate ran")
    try:
        code = subprocess.run(shlex.split(command), cwd=tree, check=False).returncode
    except OSError as error:
        return Reason("gates", f"`{command}` could not run on the merge result: {error}")
    if code != 0:
        return Reason("gates", f"`{command}` is red on the merge result (exit {code})")
    return None


def land(root: Path, default: str, *, old: str, new: str) -> Reason | None:
    """Move ``default`` from ``old`` forward to ``new``; why it could not, if it could not."""
    holder = checked_out(root, default)
    if holder is not None:
        moved = gatelib.git(holder, "merge", "--ff-only", "--quiet", new)
    else:
        moved = gatelib.git(root, "update-ref", "-m", "merge.py", f"{HEADS}{default}", new, old)
    if moved is None:
        return Reason(
            "moved",
            f"`{default}` could not be moved forward to the merge: it moved meanwhile, or its work"
            " tree holds changes the merge would overwrite",
        )
    return None


def attempt(root: Path, tree: Path, *, default: str, branch: str) -> tuple[list[Reason], str]:
    """Merge ``branch`` in ``tree``, judge it, check it, land it; the reasons and the merge."""
    old, head = tip(root, default) or "", tip(root, branch) or ""
    if gatelib.git(root, "worktree", "add", "--quiet", "--detach", str(tree), old) is None:
        return [Reason("refs", f"no work tree of `{default}` could be made at {tree}")], ""
    message = f"Merge branch '{branch}'"
    if gatelib.git(tree, "merge", "--no-ff", "--quiet", "-m", message, head) is None:
        return [Reason("conflict", f"`{branch}` does not merge into `{default}` cleanly")], ""
    reasons = merge_ready.judge(root, f"{HEADS}{default}", head)
    if reasons:
        return reasons, ""
    merged = gatelib.git(tree, "rev-parse", "HEAD") or ""
    red = run_check(tree) or land(root, default, old=old, new=merged)
    return ([red] if red else []), merged


def merge(root: Path, branch: str) -> int:
    """Judge, check and land ``branch``; print the answer; the exit code."""
    default = local_default(root)
    if gatelib.git(root, "remote", "get-url", "origin") is not None:
        print("merge: this repository has a forge (`origin`): the pull request's merge job merges")
        return 2
    if default is None or tip(root, branch) is None or branch == default:
        print(f"merge: `{branch}` is not a local branch to merge into a local default branch")
        return 2
    scratch = Path(tempfile.mkdtemp(prefix="ratchet-merge-"))
    tree = scratch / "tree"
    try:
        reasons, merged = attempt(root, tree, default=default, branch=branch)
    finally:
        gatelib.git(root, "worktree", "remove", "--force", str(tree))
        shutil.rmtree(scratch, ignore_errors=True)
        gatelib.git(root, "worktree", "prune")
    print(merge_ready.render(reasons), end="")
    if reasons:
        return 1
    deep = gatelib.entry_point(root, TIER2) or f"the binding's `{TIER2}` entry point"
    print(f"merged: `{branch}` into `{default}` at {merged[:12]}")
    print(f"next: {deep} on `{default}`: tier 2 runs after every merge, and a red one comes first")
    return 0


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("branch")
    parser.add_argument("--root", default=".")
    options = parser.parse_args(argv[1:])
    top = gatelib.git(Path(options.root), "rev-parse", "--show-toplevel")
    if top is None:
        print(f"merge: {options.root} is not a git repository")
        return 2
    return merge(Path(top), options.branch)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
