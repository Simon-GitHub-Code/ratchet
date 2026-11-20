#!/usr/bin/env python3
from __future__ import annotations

import argparse
import contextlib
import fcntl
import hashlib
import json
import os
import shlex
import shutil
import stat
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
    from collections.abc import Callable, Iterator

HEADS = "refs/heads/"
CHECK = "check"
TIER2 = "check-deep"
NEXT = "next: "
RECORDS = "records/"
SUBJECT = "docs(records): land what the owner's conversation wrote"
HOOK_LINES = 5
INTENT_BYTES = 1 << 20
SCRATCH = "ratchet-merge-"
HELD = ".lock"
STALE_SCRATCH = 60
LANDING = "ratchet-landing"
OWNER_FILE = 0o600
LANDING_VAR = "RATCHET_LANDING"
PRIVATE = "ratchet-landed-by-"
LOCK_TRIES, LOCK_POLL = 20, 0.05
NOT_THE_JUDGES = frozenset({"gates", "conflict", "moved", "refs"})


def local_default(root: Path) -> str | None:
    names = [name for name in gatelib.default_branches(root) if not name.startswith("origin/")]
    return next((name for name in names if tip(root, name)), None)


def tip(root: Path, branch: str) -> str | None:
    return gatelib.git(root, "rev-parse", "--verify", "--quiet", f"{HEADS}{branch}^{{commit}}")


def checked_out(root: Path, branch: str) -> Path | None:
    listing = gatelib.git(root, "worktree", "list", "--porcelain") or ""
    for entry in listing.split("\n\n"):
        lines = entry.splitlines()
        if f"branch {HEADS}{branch}" in lines:
            return Path(lines[0].removeprefix("worktree "))
    return None


def run_check(tree: Path, entry: str = CHECK) -> Reason | None:
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
    shown = gatelib.git(root, "rev-parse", "--git-common-dir")
    return (root / shown).resolve() if shown else root / "git-state"


@contextlib.contextmanager
def locked(path: Path, *, create: bool = True) -> Iterator[int | None]:
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


def branch_digest(branch: str) -> str:
    return hashlib.sha256(branch.encode()).hexdigest()[:16]


def merge_lock_path(root: Path, branch: str) -> Path:
    return state_dir(root) / f"ratchet-merge-lock-{branch_digest(branch)}"


@contextlib.contextmanager
def locked_retrying(path: Path, tries: int, poll: float) -> Iterator[int | None]:
    for _ in range(tries - 1):
        with locked(path) as held:
            if held is not None:
                yield held
                return
        time.sleep(poll)
    with locked(path) as held:
        yield held


def lock_held(path: Path) -> bool:
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except OSError:
        return False
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        return True
    finally:
        os.close(descriptor)
    return False


def private_lock_path(root: Path, name: str) -> Path:
    return state_dir(root) / f"{PRIVATE}{name}"


def landing_state(root: Path, named: str) -> str:
    given = Path(named)
    path = given.parent.resolve() / given.name
    if path.parent != state_dir(root) or not path.name.startswith(PRIVATE):
        return "invalid"
    try:
        shown = os.lstat(path)
    except OSError:
        return "gone"
    if not stat.S_ISREG(shown.st_mode):
        return "invalid"
    return "alive" if lock_held(path) else "gone"


def land(root: Path, default: str, *, old: str, new: str) -> Reason | None:
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
    raw = gatelib.git(root, "diff", "--raw", "-z", "--no-abbrev", "--no-renames", old, new) or ""
    fields = raw.split("\0")
    rows = []
    for at in range(0, len(fields) - 1, 2):
        head = fields[at].split()
        rows.append((head[4][0], fields[at + 1], head[2], head[3]))
    return rows


def hashed(tree: Path, paths: list[str]) -> dict[str, str]:
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
    listed = (
        gatelib.git(
            tree, "ls-files", "-z", "--modified", "--deleted", "--others", "--exclude-standard"
        ),
        gatelib.git(tree, "diff", "-z", "--name-only", "--cached"),
    )
    return {path for text in listed for path in (text or "").split("\0") if path}


def roll_back(root: Path, holder: Path, *, old: str, new: str, owned: frozenset[str]) -> list[str]:
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
    try:
        loaded: object = json.loads(data or b"")
    except ValueError:
        return {}
    return loaded if isinstance(loaded, dict) else {}


def recover_landing(root: Path) -> tuple[list[str], bool]:
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
    lines = []
    for scratch in home.glob(f"{SCRATCH}*"):
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
    landed, finished = recover_landing(root)
    return [*sweep(root), *landed], finished


def recover(root: Path) -> list[str]:
    return recovery(root)[0]


def attempt(root: Path, tree: Path, *, default: str, branch: str) -> tuple[list[Reason], str]:
    old, head = tip(root, default) or "", tip(root, branch) or ""
    if gatelib.git(root, "worktree", "add", "--quiet", "--detach", str(tree), old) is None:
        return [Reason("refs", f"no work tree of `{default}` could be made at {tree}")], ""
    message = f"Merge branch '{branch}'"
    if gatelib.git(tree, "merge", "--no-ff", "--quiet", "-m", message, head) is None:
        return [Reason("conflict", f"`{branch}` does not merge into `{default}` cleanly")], ""
    asked = merge_ready.Asked(current=True)
    reasons = merge_ready.judge(root, f"{HEADS}{default}", head, asked=asked)
    if reasons:
        return reasons, ""
    merged = gatelib.git(tree, "rev-parse", "HEAD") or ""
    gate = TIER2 if merge_ready.adopts(root, f"{HEADS}{default}", head) else CHECK
    red = run_check(tree, gate) or land(root, default, old=old, new=merged)
    return ([red] if red else []), merged


def merge(root: Path, branch: str) -> int:
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
    switch = ""
    if on_branch(root) == branch and not left(root, default):
        switch = f"`git switch {default}`, then "
    print(
        f"{NEXT}{switch}{deep} on `{default}`: tier 2 runs after every merge, and a red one"
        " comes first"
    )
    return 0


def left(root: Path, default: str) -> bool:
    if dirty_paths(root) or checked_out(root, default) is not None:
        return False
    return gatelib.git(root, "switch", "--quiet", default) is not None


def on_branch(root: Path) -> str | None:
    return gatelib.git(root, "symbolic-ref", "--quiet", "--short", "HEAD")


def porcelain(root: Path, *args: str, given: str = "") -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", str(root), *args], input=given, capture_output=True, text=True, check=False
    )


def commit_records(
    root: Path, default: str, paths: list[str], *, branch: str | None = None
) -> str | Reason:
    branch = branch or f"{RECORDS}{datetime.now(UTC):%Y%m%d-%H%M%S}"
    others = set(merge_ready.uncommitted(root)) - set(paths)
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
    left = [p for p in merge_ready.uncommitted(root) if p not in others] if committed else []
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
    command = gatelib.entry_point(root, "merge")
    return f"`{command} --records`" if command else "the binding's `merge` entry point --records"


def come_back(root: Path, default: str, branch: str) -> int | None:
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


def holds_only_records(root: Path, default: str, name: str) -> bool:
    base = f"{HEADS}{default}"
    change = merge_ready.read(root, base, f"{HEADS}{name}")
    return not isinstance(change, merge_ready.Reason) and not merge_ready.not_records(
        root, base, change.paths, change=change
    )


def merge_records(root: Path, branch: str) -> int:
    code = merge(root, branch)
    if code == 0:
        gatelib.git(root, "branch", "--quiet", "-d", branch)
    return code


def land_records(root: Path, *, only: list[str] | None = None, branch: str | None = None) -> int:
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
    paths = [p for p in merge_ready.uncommitted(root) if only is None or p in only]
    strangers = merge_ready.not_records(root, f"{HEADS}{default}", paths)
    if not paths or strangers:
        what = ", ".join(strangers) if strangers else "nothing uncommitted"
        print(
            f"merge: --records lands records alone, and the work tree holds {what}; a change goes"
            " on a branch of its own and through `merge <branch>`"
        )
        return 2
    made = commit_records(root, default, paths, branch=branch)
    if isinstance(made, Reason):
        print(f"records: {made}")
        return 1
    print(f"records: {len(paths)} path(s) committed on `{made}`", flush=True)
    return merge_records(root, made)


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
    default = local_default(Path(top))
    only_records = default is not None and holds_only_records(Path(top), default, options.branch)
    return locked_run(Path(top), options.branch, merge_records if only_records else merge)


def locked_run(root: Path, branch: str, run: Callable[[Path, str], int]) -> int:
    landing = os.environ.pop(LANDING_VAR, None)
    with locked_retrying(merge_lock_path(root, branch), LOCK_TRIES, LOCK_POLL) as held:
        if held is None:
            print(f"merge: a merge of `{branch}` is already running; nothing was merged")
            return 2
        state = "alive" if landing is None else landing_state(root, landing)
        if state == "invalid":
            print("merge: RATCHET_LANDING names no landing of this repository; nothing was merged")
            return 2
        if state == "gone":
            print(
                "merge: the land that started this merge has stopped; nothing was merged; run"
                " land again"
            )
            return 2
        return run(root, branch)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
