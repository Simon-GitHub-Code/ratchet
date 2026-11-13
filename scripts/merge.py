#!/usr/bin/env python3
"""The merge with no forge: judge a branch, run ``check`` on the merge result, land it. ADR 0021.

``merge.py <branch>`` is what the binding's ``merge`` entry point runs in a repository with no
remote. It makes a temporary work tree of the default branch, merges the branch there with
``--no-ff``, asks the merge judge (``merge_ready.py``) about the branch, and runs the binding's
``check`` in that work tree: on the merge result, never on the branch. Only when the judge says
``merge: agent`` and ``check`` is green does the default branch move to the merge commit, forward
and nowhere else. Otherwise it prints ``merge: owner`` with every reason and changes nothing; where
the reasons are the judge's, it prints the owner's merge command and the tier 2 entry point to run
after it.
The work tree is removed either way.

``check`` runs in a fresh work tree, as it does in CI, so its recipes install what they need.

The adoption, the change that brings the manifest to a default branch with none, is judged by
its own gates: ``check-deep`` runs on its merge result in place of ``check`` (ADR 0031).

``merge.py --records`` lands what the owner's conversation wrote and left in the work tree of the
default branch: a spec, a ticket, a conjecture, a change description, an added term or ADR. Every
uncommitted path must be a record by the manifest the default branch holds. It commits them on a
branch of their own, ``records/<time>``, with the repository's commit hooks, switches back to the
default branch, and merges that branch as above: the judge asks no verdict record of records alone
(ADR 0031). A merged records branch is deleted; one the owner merges stays, and the work tree is
clean either way, so nothing is ever committed on the default branch itself.

With a remote named ``origin`` the script refuses: the pull request's merge job is the one that
merges there, and a local merge could not be pushed.

Usage: ``merge.py <branch> [--root DIR]`` or ``merge.py --records [--root DIR]``. Exit 0 merged,
1 the owner merges, 2 nothing to judge.
"""

from __future__ import annotations

import argparse
import shlex
import shutil
import subprocess
import sys
import tempfile
from datetime import UTC, datetime
from pathlib import Path

import gatelib
import merge_ready
from merge_ready import Reason

HEADS = "refs/heads/"
CHECK = "check"
TIER2 = "check-deep"
NEXT = "next: "
RECORDS = "records/"
SUBJECT = "docs(records): land what the owner's conversation wrote"
HOOK_LINES = 5
# Why: these say the merge itself failed, which the owner's merge would meet too; every other
# reason is the judge's, and the owner merging is what it asks for.
NOT_THE_JUDGES = frozenset({"gates", "conflict", "moved", "refs"})


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


def run_check(tree: Path, entry: str = CHECK) -> Reason | None:
    """Run the binding's ``entry`` on the merge result; why it is not green, if it is not."""
    command = gatelib.entry_point(tree, entry)
    if command is None:
        return Reason("gates", f"the binding names no `{entry}` entry point, so no gate ran")
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
    # Why: the adoption binds the gates, and nothing before it says they bite; tier 2 does.
    gate = TIER2 if merge_ready.adopts(root, f"{HEADS}{default}", head) else CHECK
    red = run_check(tree, gate) or land(root, default, old=old, new=merged)
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
    if reasons and not any(reason.code in NOT_THE_JUDGES for reason in reasons):
        command = gatelib.entry_point(root, TIER2)
        deep = f"`{command}`" if command else f"the binding's `{TIER2}` entry point"
        print(
            f"{NEXT}the owner types `git switch {default} && git merge --no-ff {branch}`, then"
            f" {deep} on `{default}`: no gate ran on this merge, and tier 2 runs every gate"
        )
    if reasons:
        return 1
    deep = gatelib.entry_point(root, TIER2) or f"the binding's `{TIER2}` entry point"
    print(f"merged: `{branch}` into `{default}` at {merged[:12]}")
    print(f"{NEXT}{deep} on `{default}`: tier 2 runs after every merge, and a red one comes first")
    return 0


def on_branch(root: Path) -> str | None:
    return gatelib.git(root, "symbolic-ref", "--quiet", "--short", "HEAD")


def porcelain(root: Path, *args: str, given: str = "") -> subprocess.CompletedProcess[str]:
    """``git args`` with the commit hooks running as they do on any commit; ``given`` on stdin."""
    return subprocess.run(
        ["git", "-C", str(root), *args], input=given, capture_output=True, text=True, check=False
    )


def commit_records(root: Path, default: str, paths: list[str]) -> str | Reason:
    """Commit ``paths`` on a new records branch and switch back to ``default``; the branch.

    Anything short of a clean commit puts the index back as the owner left it, switches back
    and deletes the branch, so the records stay uncommitted on the default branch. Why HEAD is
    read rather than the exit code: a failing ``post-checkout`` hook fails a switch that happened.
    """
    branch = f"{RECORDS}{datetime.now(UTC):%Y%m%d-%H%M%S}"
    index = gatelib.git(root, "write-tree")
    if index is None or tip(root, branch) is not None:
        return Reason("refs", f"`{branch}` exists already, or the index holds a merge conflict")
    porcelain(root, "switch", "--quiet", "-c", branch)
    if on_branch(root) != branch:
        return Reason("refs", f"`{branch}` could not be made and checked out")
    message = (
        f"{SUBJECT}\n\nRecords alone, landed by `merge.py --records` (ADR 0031):\n\n"
        + "".join(f"- {path}\n" for path in paths)
    )
    added = porcelain(
        root,
        "add",
        "--all",
        "--pathspec-from-file=-",
        "--pathspec-file-nul",
        given="\0".join(paths),
    )
    done = (
        porcelain(root, "commit", "-q", "--file=-", given=message)
        if not added.returncode
        else added
    )
    committed = done.returncode == 0
    left = merge_ready.uncommitted(root) if committed else []
    if committed and not left:
        porcelain(root, "switch", "--quiet", default)
        return branch
    if committed:
        porcelain(root, "reset", "--quiet", "--soft", "HEAD~1")
    porcelain(root, "switch", "--quiet", default)
    gatelib.git(root, "read-tree", index)
    gatelib.git(root, "branch", "--quiet", "-D", branch)
    if left:
        said = "the commit hooks changed " + ", ".join(left) + " as they ran"
    else:
        said = "the commit hooks refused them: " + " / ".join(
            (done.stdout + done.stderr).strip().splitlines()[-HOOK_LINES:]
        )
    return Reason("gates", f"the records stay uncommitted on the default branch, since {said}")


def land_records(root: Path) -> int:
    """Commit the records the default branch's work tree holds on a branch, then merge it."""
    default = local_default(root)
    if gatelib.git(root, "remote", "get-url", "origin") is not None:
        print(
            "merge: this repository has a forge (`origin`): commit the records on a branch of"
            " their own and open the pull request; its merge job merges records alone"
        )
        return 2
    if default is None or gatelib.git(root, "symbolic-ref", "--short", "HEAD") != default:
        print("merge: records land from the default branch; on a working branch, commit them there")
        return 2
    paths = merge_ready.uncommitted(root)
    strangers = merge_ready.not_records(root, f"{HEADS}{default}", paths)
    if not paths or strangers:
        what = ", ".join(strangers) if strangers else "nothing uncommitted"
        print(
            f"merge: --records lands records alone, and the work tree holds {what}; a change goes"
            " on a branch of its own and through `merge <branch>`"
        )
        return 2
    branch = commit_records(root, default, paths)
    if isinstance(branch, Reason):
        print(f"records: {branch}")
        return 1
    print(f"records: {len(paths)} path(s) committed on `{branch}`", flush=True)
    code = merge(root, branch)
    if code == 0:
        gatelib.git(root, "branch", "--quiet", "-d", branch)
    return code


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("branch", nargs="?")
    parser.add_argument("--records", action="store_true")
    parser.add_argument("--root", default=".")
    options = parser.parse_args(argv[1:])
    if options.records == (options.branch is not None):
        parser.error("name a branch, or pass --records")
    top = gatelib.git(Path(options.root), "rev-parse", "--show-toplevel")
    if top is None:
        print(f"merge: {options.root} is not a git repository")
        return 2
    return land_records(Path(top)) if options.records else merge(Path(top), options.branch)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
