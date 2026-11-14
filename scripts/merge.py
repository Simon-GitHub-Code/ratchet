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
The work tree is removed either way, and one a killed run left is removed by the next run: it
holds a lock the kernel frees when the run dies, so a work tree nobody holds is a dead run's.
The fast-forward of a checked-out default branch writes its intent first, under a lock of the
same kind; a run killed in it leaves files of the merge in the work tree and a lock on its
index, and the next run puts back only those files, as the default branch had them, and removes
that lock. A path the fast-forward was writing which matches neither blob is git's part-written
file, and goes back too; one the owner had changed or left untracked before it began (the
intent lists them) is left, and said.

A branch that does not hold the tip of the default branch is the owner's with the ``behind``
reason, as on a forge: its verdict was given on another tree, so it is brought up to date and
judged again first (``drain.py update``, then the pipeline's ``rejudge``).

``check`` runs in a fresh work tree, as it does in CI, so its recipes install what they need.

The adoption, the change that brings the manifest to a default branch with none, is judged by
its own gates: ``check-deep`` runs on its merge result in place of ``check`` (ADR 0031).

``merge.py --records`` lands what the owner's conversation wrote and left in the work tree of the
default branch: a spec, a ticket, a conjecture, a change description, an added term, or an ADR,
which the judge leaves to the owner to agree. Every uncommitted path must be a record by the
manifest the default branch holds. It commits them on a branch of their own, ``records/<time>``,
with the repository's commit hooks, switches back to the default branch, and merges that branch
as above: the judge asks no verdict record of records alone (ADR 0031). A records branch this
script merges is deleted, by ``--records`` or by name; one the owner merges stays, and the work
tree is clean either way, so nothing is ever committed on the default branch itself. A run that
stopped partway, killed in the commit hooks or after its commit, leaves HEAD on its records
branch; the next run switches back, carrying what is uncommitted, and lands the records or merges
their commit. When git cannot switch back, it prints git's line and what to type before running
again.

With a remote named ``origin`` the script refuses: the pull request's merge job is the one that
merges there, and a local merge could not be pushed.

Usage: ``merge.py <branch> [--root DIR]`` or ``merge.py --records [--root DIR]``. Exit 0 merged,
1 the owner merges, 2 nothing to judge.
"""

from __future__ import annotations

import argparse
import contextlib
import fcntl
import json
import os
import shlex
import shutil
import subprocess
import sys
import tempfile
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING

import gatelib
import merge_ready
from merge_ready import Reason

if TYPE_CHECKING:
    from collections.abc import Iterator

HEADS = "refs/heads/"
CHECK = "check"
TIER2 = "check-deep"
NEXT = "next: "
RECORDS = "records/"
SUBJECT = "docs(records): land what the owner's conversation wrote"
HOOK_LINES = 5
INTENT_BYTES = 1 << 20
SCRATCH = "ratchet-merge-"
# Why: a lock the kernel holds for an open file is freed when its holder dies, however it dies.
HELD = ".lock"
STALE_SCRATCH = 60  # seconds an empty scratch directory stands before a sweep removes it
LANDING = "ratchet-landing"
OWNER_FILE = 0o600
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


def state_dir(root: Path) -> Path:
    """The repository's own directory of git state, shared by every work tree."""
    shown = gatelib.git(root, "rev-parse", "--git-common-dir")
    return (root / shown).resolve() if shown else root / "git-state"


@contextlib.contextmanager
def locked(path: Path, *, create: bool = True) -> Iterator[int | None]:
    """The open file ``path`` with its lock held, or ``None`` when another process holds it."""
    try:
        descriptor = os.open(path, os.O_RDWR | (os.O_CREAT if create else 0), OWNER_FILE)
    except OSError:
        yield None
        return
    try:
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            yield None
        else:
            yield descriptor
    finally:
        os.close(descriptor)


def land(root: Path, default: str, *, old: str, new: str) -> Reason | None:
    """Move ``default`` from ``old`` forward to ``new``; why it could not, if it could not.

    A checked-out branch moves by ``git merge --ff-only``, which writes the files before the ref:
    killed in between it leaves files of ``new`` in the work tree and a lock on the index. The
    intent is written first, under a lock that dies with this process, so ``recover`` can put the
    work tree back from the intent alone.
    """
    holder = checked_out(root, default)
    if holder is None:
        moved = gatelib.git(root, "update-ref", "-m", "merge.py", f"{HEADS}{default}", new, old)
    else:
        with locked(state_dir(root) / LANDING) as descriptor:
            if descriptor is None:
                return Reason("moved", f"another merge is moving `{default}` now")
            owned = sorted({path for _, path, _, _ in blobs(root, old, new)} & dirty_paths(holder))
            intent = {
                "default": default,
                "old": old,
                "new": new,
                "holder": str(holder),
                "owned": owned,
            }
            os.ftruncate(descriptor, 0)
            os.write(descriptor, json.dumps(intent).encode())
            os.fsync(descriptor)
            moved = gatelib.git(holder, "merge", "--ff-only", "--quiet", new)
            os.ftruncate(descriptor, 0)
    if moved is None:
        return Reason(
            "moved",
            f"`{default}` could not be moved forward to the merge: it moved meanwhile, or its work"
            " tree holds changes the merge would overwrite",
        )
    return None


def git_running_in(tree: Path, proc: Path = Path("/proc")) -> bool:
    """Whether a ``git`` process runs in ``tree``; a system without ``/proc`` says no."""
    for entry in proc.glob("[0-9]*"):
        try:
            if (entry / "comm").read_text().strip() == "git" and (
                (entry / "cwd").readlink().is_relative_to(tree)
            ):
                return True
        except OSError:
            continue
    return False


def blobs(root: Path, old: str, new: str) -> list[tuple[str, str, str, str]]:
    """Each path ``new`` changes from ``old``: its status, the path, the blob it had and has."""
    raw = gatelib.git(root, "diff", "--raw", "-z", "--no-abbrev", "--no-renames", old, new) or ""
    fields = raw.split("\0")
    rows = []
    for at in range(0, len(fields) - 1, 2):
        head = fields[at].split()
        rows.append((head[4][0], fields[at + 1], head[2], head[3]))
    return rows


def hashed(tree: Path, paths: list[str]) -> dict[str, str]:
    """The blob id of each of ``paths`` as ``tree`` holds it; a path that is no file is left out."""
    plain = [p for p in paths if (tree / p).is_file() and not (tree / p).is_symlink()]
    done = subprocess.run(
        ["git", "-C", str(tree), "hash-object", "--stdin-paths"],
        input="".join(f"{p}\n" for p in plain),
        capture_output=True,
        text=True,
        check=False,
    )
    return dict(zip(plain, done.stdout.split(), strict=False)) if done.returncode == 0 else {}


def dirty_paths(tree: Path) -> set[str]:
    """The paths ``tree`` holds changed, staged, deleted or untracked."""
    listed = (
        gatelib.git(
            tree, "ls-files", "-z", "--modified", "--deleted", "--others", "--exclude-standard"
        ),
        gatelib.git(tree, "diff", "-z", "--name-only", "--cached"),
    )
    return {path for text in listed for path in (text or "").split("\0") if path}


def roll_back(root: Path, holder: Path, *, old: str, new: str, owned: frozenset[str]) -> list[str]:
    """Put the work tree of ``holder`` back at ``old``, touching only what ``new`` changed.

    A path is put back when the work tree holds the blob ``old`` or ``new`` has there, or
    nothing. One that holds neither was written part-way by the killed fast-forward, and is put
    back too, unless it was in ``owned``: changed or untracked before the fast-forward began,
    so the owner's. Those stay, and the lines say so.
    """
    changes = blobs(root, old, new)
    held = hashed(holder, [path for _, path, _, _ in changes])
    stays = [
        p for _, p, was, now in changes if p in held and held[p] not in {was, now} and p in owned
    ]
    restore = [p for status, p, _, _ in changes if status != "A" and p not in stays]
    added = [p for status, p, _, _ in changes if status == "A" and p not in stays]
    pathspecs = ("--pathspec-from-file=-", "--pathspec-file-nul")
    if restore:
        args = ("restore", f"--source={old}", "--staged", "--worktree", *pathspecs)
        porcelain(holder, *args, given="\0".join(restore))
    if added:
        args = ("rm", "--quiet", "--cached", "--ignore-unmatch", *pathspecs)
        porcelain(holder, *args, given="\0".join(added))
    for path in added:
        (holder / path).unlink(missing_ok=True)
        with contextlib.suppress(OSError):
            os.removedirs((holder / path).parent)
    return [f"merge: {path} was changed since, and stays as it is" for path in stays]


def texts(value: object) -> list[str]:
    return [item for item in value if isinstance(item, str)] if isinstance(value, list) else []


def intent_of(data: bytes) -> dict[str, object]:
    """The landing intent in ``data``, or an empty one."""
    try:
        loaded: object = json.loads(data or b"")
    except ValueError:
        return {}
    return loaded if isinstance(loaded, dict) else {}


def recover_landing(root: Path) -> tuple[list[str], bool]:
    """Put right a ``git merge --ff-only`` that was killed: the work tree and the lock on its index.

    Returns what it did and whether it finished: it did not while a git command still runs in the
    tree, which is left as it is.

    Only while no merge is landing, which the lock on the intent shows. When the branch moved,
    the work tree was finished; when it did not, the files the fast-forward wrote are put back
    and the next merge starts from the branch as it was.
    """
    lines: list[str] = []
    with locked(state_dir(root) / LANDING, create=False) as descriptor:
        if descriptor is None:
            return lines, True
        intent = intent_of(os.pread(descriptor, INTENT_BYTES, 0))
        if not intent:
            return lines, True
        default, old, new = (str(intent.get(name)) for name in ("default", "old", "new"))
        holder = Path(str(intent.get("holder")))
        if tip(root, default) == old and holder.is_dir():
            if git_running_in(holder):
                waiting = (
                    f"merge: a git command still runs in {holder}; run this again once it ends"
                )
                return [waiting], False
            lock = gatelib.git(holder, "rev-parse", "--git-path", "index.lock") or "index.lock"
            (holder / lock).unlink(missing_ok=True)
            lines.append(
                f"merge: a run that stopped was moving `{default}`; its work tree is put back"
            )
            owned = frozenset(texts(intent.get("owned")))
            lines += roll_back(root, holder, old=old, new=new, owned=owned)
        os.ftruncate(descriptor, 0)
    return lines, True


def sweep(root: Path) -> list[str]:
    """Remove the scratch work trees of merge runs that died: registered, and no run holds them."""
    listing = gatelib.git(root, "worktree", "list", "--porcelain") or ""
    home = Path(tempfile.gettempdir()).resolve()
    lines = []
    for line in listing.splitlines():
        tree = Path(line.removeprefix("worktree "))
        scratch = tree.parent
        if (
            not line.startswith("worktree ")
            or not scratch.name.startswith(SCRATCH)
            or scratch.parent != home
        ):
            continue
        with locked(scratch / HELD, create=False) as descriptor:
            if descriptor is None:
                continue
            gatelib.git(root, "worktree", "remove", "--force", str(tree))
            shutil.rmtree(scratch, ignore_errors=True)
            lines.append(f"merge: removed the work tree {tree} that a run which stopped left")
    if lines:
        gatelib.git(root, "worktree", "prune")
    return lines + empty_scratch(home)


def empty_scratch(home: Path) -> list[str]:
    """Remove the empty scratch directories of runs killed between making one and its work tree.

    Why: such a run registered no work tree, so the sweep above never finds its directory. An
    empty one is a live run's too in the moment before it takes its lock, so only one that has
    stood a while is removed, and only one of this user's.
    """
    lines = []
    for scratch in home.glob(f"{SCRATCH}*"):
        # Why: another run's directory may be made or removed between the listing and the look.
        with contextlib.suppress(OSError):
            stat = scratch.lstat()
            if (
                scratch.is_dir()
                and not scratch.is_symlink()
                and stat.st_uid == os.getuid()
                and time.time() - stat.st_mtime > STALE_SCRATCH
                and not any(scratch.iterdir())
            ):
                scratch.rmdir()
                lines.append(
                    f"merge: removed the empty directory {scratch} that a run which stopped left"
                )
    return lines


def recovery(root: Path) -> tuple[list[str], bool]:
    """What a merge that was killed left, put right, and whether all of it was."""
    landed, finished = recover_landing(root)
    return [*sweep(root), *landed], finished


def recover(root: Path) -> list[str]:
    """What a merge that was killed left: its work tree, and a fast-forward cut off half-way."""
    return recovery(root)[0]


def attempt(root: Path, tree: Path, *, default: str, branch: str) -> tuple[list[Reason], str]:
    """Merge ``branch`` in ``tree``, judge it, check it, land it; the reasons and the merge."""
    old, head = tip(root, default) or "", tip(root, branch) or ""
    if gatelib.git(root, "worktree", "add", "--quiet", "--detach", str(tree), old) is None:
        return [Reason("refs", f"no work tree of `{default}` could be made at {tree}")], ""
    message = f"Merge branch '{branch}'"
    if gatelib.git(tree, "merge", "--no-ff", "--quiet", "-m", message, head) is None:
        return [Reason("conflict", f"`{branch}` does not merge into `{default}` cleanly")], ""
    # Why: the verdict was given on the tree the branch was judged on; one that does not hold the
    # tip of the default branch lands a tree no review saw (ADR 0023), so it is brought up to date
    # and judged again, as the forge's judge asks.
    asked = merge_ready.Asked(current=True)
    reasons = merge_ready.judge(root, f"{HEADS}{default}", head, asked=asked)
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
    for line in recover(root):
        print(line)
    scratch = Path(tempfile.mkdtemp(prefix=SCRATCH))
    tree = scratch / "tree"
    with locked(scratch / HELD):
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


def again(root: Path) -> str:
    """The binding's ``merge`` entry point with ``--records``, as its table names the command."""
    command = gatelib.entry_point(root, "merge")
    return f"`{command} --records`" if command else "the binding's `merge` entry point --records"


def come_back(root: Path, default: str, branch: str) -> int | None:
    """Leave the records branch a stopped run left checked out; ``None`` once back on ``default``.

    Why: a run killed in the commit hooks leaves HEAD on its branch, the records staged and not
    committed; one killed after its commit leaves them committed there. Either way the next run
    goes back to ``default``, carrying what is uncommitted, and lands the records from there.
    """
    committed = gatelib.git(root, "merge-base", "--is-ancestor", branch, default) is None
    switched = porcelain(root, "switch", "--quiet", default)
    if on_branch(root) != default:
        said = (switched.stdout + switched.stderr).strip().splitlines()[:1]
        print(
            f"merge: a run that stopped left the records on `{branch}`, and git cannot switch back"
            f" to `{default}`: {''.join(said)}"
        )
        lock = root / (gatelib.git(root, "rev-parse", "--git-path", "index.lock") or "")
        if lock.is_file():
            print(
                f"{NEXT}once no git command runs in this repository, type `rm {lock}`, then"
                f" {again(root)}"
            )
        else:
            print(f"{NEXT}commit or set aside what git names on `{branch}`, then {again(root)}")
        return 2
    print(f"records: a run that stopped left them on `{branch}`; back on `{default}`", flush=True)
    if committed:
        return merge_records(root, branch)
    gatelib.git(root, "branch", "--quiet", "-D", branch)
    return None


def merge_records(root: Path, branch: str) -> int:
    """Merge a records branch as any branch is merged; delete it once it is merged."""
    code = merge(root, branch)
    if code == 0:
        gatelib.git(root, "branch", "--quiet", "-d", branch)
    return code


def land_records(root: Path) -> int:
    """Commit the records the default branch's work tree holds on a branch, then merge it."""
    default = local_default(root)
    if gatelib.git(root, "remote", "get-url", "origin") is not None:
        print(
            "merge: this repository has a forge (`origin`): commit the records on a branch of"
            " their own and open the pull request; its merge job merges records alone"
        )
        return 2
    current = on_branch(root) or ""
    if default is not None and current.startswith(RECORDS):
        code = come_back(root, default, current)
        if code is not None:
            return code
    if default is None or on_branch(root) != default:
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
    return merge_records(root, branch)


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
    if options.records:
        return land_records(Path(top))
    # Why: the route names `merge <branch>` for a records branch a stopped run left unmerged.
    land = merge_records if options.branch.startswith(RECORDS) else merge
    return land(Path(top), options.branch)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
