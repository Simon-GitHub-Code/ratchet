#!/usr/bin/env python3
"""The publish check: did a pipeline role commit, push or open a pull request? ADR 0060.

The pipeline's roles write files and leave them uncommitted; the orchestrator commits, pushes and
opens the pull request once it has read the result. A Workflow script cannot run a command, so
nothing checks a role while the run lasts. The orchestrator runs this script once a run, after the
run returns and before its first commit. It compares the checkout and the forge now with the start
state, read when the run started, and its exit decides: a branch that moved ends the ticket as a
role that published, never as an approval.

The start state is a JSON file holding an object whose ``start`` member is one of::

    {"branch": "feature/4-x", "head": "<commit>", "forge": null}
    {"branch": "feature/4-x", "head": "<commit>", "forge": {"remote": null, "pull_requests": []}}

``branch`` is the ticket's branch and ``head`` the commit it was at, 40 or 64 hex digits.
``forge`` is ``null`` when the checkout had no ``origin`` remote. Otherwise it is what the forge
held for the branch: ``remote``, the commit of the branch there, or ``null`` when it had none; and
``pull_requests``, the numbers of the open pull requests whose head is the branch. Other members
are ignored, so a report that carries ``start`` is passed as it was printed. ``start_of`` reads a
start state the way this script reads the present one.

On a rejudge whose rebase conflicted, the branch was left as it was and the Reconcile role is
told to finish the rebase itself. That start carries one more member, ``rebase``, which is
``null`` or absent on any other run::

    {"branch": "feature/4-x", "head": "<commit>", "forge": null, "rebase": {"base": "main",
     "commits": [{"author_date": "2026-11-15T10:13:20Z", "message_sha256": "<64 hex digits>"}]}}

``base`` names the ref the branch is to be rebased onto, read by its name when the check runs;
``commits`` are the commits ahead of it at the start, oldest first, each as its author date in
git's strict ISO 8601 and the SHA-256 of its whole message. ``start_of(..., rebase_onto=base)``
records it.

What counts as moved, each said in a line of its own:

- ``HEAD`` is not on the branch: a role switched to another branch or detached it;
- the branch is gone, has commits on top of where it started, or is at a commit that does not
  hold that one (amended, reset or rebased);
- with a forge: the branch appeared there, moved or is gone; a pull request for the branch is
  open that was not, or one that was open is not.

With ``rebase``, a branch at a commit that does not hold where it started is not a move when the
commits ahead of the base now are the recorded ones, by author date and message, in the same order,
with none missing or with each missing one held by the base as a commit of its own with the same
author date and message that the start did not hold: git dropped it because its change had already
landed, and a reconcile that finished the rebase did not publish (ADR 0060). A dropped commit the
base does not hold, a twin with another message or author date, a changed order and an added commit
are each a move, as is any other rewrite, and so is a base that names no commit. A rebase left
under way in the checkout is never a pass: it is said in a line of its own, in place of ``HEAD``
being detached, and with nothing else moved the exit is 3, since a role that stopped halfway did
not publish.

Uncommitted, staged and untracked changes are not a move: the roles write files and leave them.

What it does not see: a tag, a stash or another branch a role made, locally or on the forge; a
remote other than ``origin``, and any remote at all when the start had no forge; a pull request
whose head is another branch, and an edit to one that stays open; whatever was undone before the
check ran, such as a pull request opened and closed again; a start file a role rewrote, so
the orchestrator keeps that file outside the work tree; and, with ``rebase``, a role that changed
what a commit holds and kept its message and author date, and a role that moved the base: the
merge judge and the gates judge the content.

It reads the forge's branch with ``git ls-remote``, which fetches nothing and moves no ref, and
the open pull requests through ``gh`` as the preflight does, in the repository of the working
directory. It writes nothing, so a second run gives the same answer while nothing moves.

Exit status:

- 0: nothing moved;
- 1: a role published: a line for each thing that moved, then the verdict; a forge that could not
  be read is named too, since what moved already decides;
- 2: the start state cannot be read: no file, not JSON, or a member missing or malformed;
- 3: the present state cannot be read: not a git checkout, the forge cannot be reached, or, with
  ``rebase``, a rebase left unfinished.

Neither 2 nor 3 is ever a pass.

Usage: ``publish_check.py START [--root DIR]``.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path

import feature_preflight as preflight
import gatelib

COMMIT = re.compile(r"[0-9a-f]{40}|[0-9a-f]{64}")
DATE = re.compile(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(?:Z|[+-]\d\d:\d\d)")
SHA256 = re.compile(r"[0-9a-f]{64}")
REBASING = ("rebase-merge", "rebase-apply")
REMOTE = "origin"
SHORT = 12
SAID = "publish check:"
NEVER = "that is never a pass"
FINAL_CHECK = "`drain.py check <ticket> --final`"
HALTED = (
    "a rebase is under way in the checkout and was not finished: a role stopped halfway, which"
    " is not publishing, and a branch half rebased cannot be judged"
)
GREEN = f"{SAID} green, nothing was committed, pushed or opened since the run started"
RED = (
    f"{SAID} red, a role published: the ticket ends as a role that published, never as an approval"
)


class MalformedError(ValueError):
    """The start state is missing, or a member of it is missing or malformed."""


class UnreadableError(RuntimeError):
    """The checkout or the forge cannot be read."""


@dataclass(frozen=True)
class Forge:
    """What the forge holds for the branch: its commit there, and its open pull requests."""

    remote: str | None
    pull_requests: tuple[int, ...]


@dataclass(frozen=True)
class Commit:
    """A commit ahead of the base, as a finished rebase keeps it: author date and message."""

    author_date: str
    message_sha256: str


@dataclass(frozen=True)
class Rebase:
    """The rebase a role is to finish: the base's name and the commits ahead of it, oldest first."""

    base: str
    commits: tuple[Commit, ...]


@dataclass(frozen=True)
class Start:
    """The state a run started from: the branch, its commit, the forge, and a rebase to finish."""

    branch: str
    head: str
    forge: Forge | None
    rebase: Rebase | None = None


# --- the start state -------------------------------------------------------------------------


def member(table: dict[str, object], name: str, path: str) -> object:
    if name not in table:
        message = f"`{path}.{name}` is missing"
        raise MalformedError(message)
    return table[name]


def a_commit(value: object, path: str) -> str:
    if not isinstance(value, str) or not COMMIT.fullmatch(value):
        message = f"`{path}` is not a commit (40 or 64 lowercase hex digits)"
        raise MalformedError(message)
    return value


def parse(text: str) -> Start:
    """The start state in ``text``; raises ``MalformedError`` naming what is missing or wrong."""
    try:
        data: object = json.loads(text)
    except ValueError as error:
        message = f"it is not JSON ({error})"
        raise MalformedError(message) from None
    start = data.get("start") if isinstance(data, dict) else None
    if not isinstance(start, dict):
        message = "it is not a JSON object with a `start` object"
        raise MalformedError(message)
    branch = member(start, "branch", "start")
    if not isinstance(branch, str) or not branch:
        message = "`start.branch` is not a branch's name"
        raise MalformedError(message)
    head = a_commit(member(start, "head", "start"), "start.head")
    forge = a_forge(member(start, "forge", "start"))
    return Start(branch, head, forge, a_rebase(start.get("rebase")))


def a_forge(forge: object) -> Forge | None:
    if forge is None:
        return None
    if not isinstance(forge, dict):
        message = "`start.forge` is neither null nor an object"
        raise MalformedError(message)
    remote = member(forge, "remote", "start.forge")
    numbers = member(forge, "pull_requests", "start.forge")
    if not isinstance(numbers, list) or not all(
        type(number) is int and number > 0 for number in numbers
    ):
        message = "`start.forge.pull_requests` is not a list of pull request numbers"
        raise MalformedError(message)
    return Forge(
        None if remote is None else a_commit(remote, "start.forge.remote"),
        tuple(sorted(set(numbers))),
    )


def a_rebase(rebase: object) -> Rebase | None:
    if rebase is None:
        return None
    if not isinstance(rebase, dict):
        message = "`start.rebase` is neither null nor an object"
        raise MalformedError(message)
    base = member(rebase, "base", "start.rebase")
    if not isinstance(base, str) or not base:
        message = "`start.rebase.base` is not a ref's name"
        raise MalformedError(message)
    commits = member(rebase, "commits", "start.rebase")
    if not isinstance(commits, list):
        message = "`start.rebase.commits` is not a list"
        raise MalformedError(message)
    return Rebase(
        base,
        tuple(
            a_commit_ahead(entry, f"start.rebase.commits[{index}]")
            for index, entry in enumerate(commits)
        ),
    )


def a_commit_ahead(entry: object, path: str) -> Commit:
    date = entry.get("author_date") if isinstance(entry, dict) else None
    digest = entry.get("message_sha256") if isinstance(entry, dict) else None
    if not (
        isinstance(date, str)
        and DATE.fullmatch(date)
        and isinstance(digest, str)
        and SHA256.fullmatch(digest)
    ):
        message = (
            f"`{path}` is not an object with an ISO 8601 `author_date` and a SHA-256"
            " `message_sha256`"
        )
        raise MalformedError(message)
    return Commit(date, digest)


def read_start(path: Path) -> Start:
    """The start state in the file at ``path``; raises ``MalformedError`` when it has none."""
    text = gatelib.read_text(path)
    if text is None:
        message = "there is no such file, or it is not text"
        raise MalformedError(message)
    return parse(text)


# --- reading the checkout and the forge ------------------------------------------------------


def head_of(root: Path, branch: str) -> str | None:
    """The commit the local ``branch`` is at, or ``None`` when there is no such branch."""
    return gatelib.git(root, "rev-parse", "--verify", "--quiet", f"refs/heads/{branch}")


def open_pull_requests(output: str | None) -> tuple[int, ...] | None:
    """The numbers in ``gh pr list --json number`` output, or ``None`` when it is not that."""
    if output is None:
        return None
    try:
        data: object = json.loads(output)
    except ValueError:
        return None
    if not isinstance(data, list) or not all(
        isinstance(entry, dict) and type(entry.get("number")) is int for entry in data
    ):
        return None
    return tuple(sorted({entry["number"] for entry in data}))


def forge_of(root: Path, branch: str, gh: preflight.Runner) -> Forge:
    """What the forge holds for ``branch`` now; raises ``UnreadableError`` when it cannot tell."""
    ref = f"refs/heads/{branch}"
    listed = gatelib.git(root, "ls-remote", REMOTE, ref)
    if listed is None:
        message = (
            f"`git ls-remote {REMOTE}` failed, so the forge's branch `{branch}` cannot be read"
        )
        raise UnreadableError(message)
    found = (line.partition("\t") for line in listed.splitlines())
    remote = next((commit for commit, _, name in found if name == ref), None)
    numbers = open_pull_requests(
        gh(["pr", "list", "--head", branch, "--state", "open", "--json", "number"])
    )
    if numbers is None:
        message = f"`gh` did not list the open pull requests for `{branch}`"
        raise UnreadableError(message)
    return Forge(remote, numbers)


def ahead_of(root: Path, base: str, head: str) -> tuple[Commit, ...] | None:
    """The commits on ``head`` that ``base`` does not hold, oldest first; ``None`` with no base."""
    tip = gatelib.git(
        root, "rev-parse", "--verify", "--quiet", "--end-of-options", f"{base}^{{commit}}"
    )
    listed = (
        None
        if tip is None
        else gatelib.git(root, "log", "-z", "--reverse", "--format=%aI%n%B", f"{tip}..{head}")
    )
    if listed is None:
        return None
    return tuple(
        Commit(date, hashlib.sha256(message.encode()).hexdigest())
        for date, _, message in (entry.partition("\n") for entry in listed.split("\0") if entry)
    )


def rebasing(root: Path) -> bool:
    """Whether a rebase is under way in the checkout at ``root``."""
    return any(
        (path := gatelib.git(root, "rev-parse", "--git-path", name)) is not None
        and (root / path).is_dir()
        for name in REBASING
    )


def start_of(
    root: Path, branch: str, gh: preflight.Runner, *, rebase_onto: str | None = None
) -> Start:
    """The start state of a run on ``branch``, read the way the check reads the present.

    With ``rebase_onto``, the start of a rejudge whose rebase onto that base conflicted: it
    records the commits ahead of the base, which a reconcile that finishes the rebase keeps.
    """
    head = head_of(root, branch)
    if head is None:
        message = f"there is no branch `{branch}`"
        raise UnreadableError(message)
    has_forge = gatelib.git(root, "remote", "get-url", REMOTE) is not None
    forge = forge_of(root, branch, gh) if has_forge else None
    if rebase_onto is None:
        return Start(branch, head, forge)
    commits = ahead_of(root, rebase_onto, head)
    if commits is None:
        message = f"there is no base `{rebase_onto}`"
        raise UnreadableError(message)
    return Start(branch, head, forge, Rebase(rebase_onto, commits))


# --- what moved ------------------------------------------------------------------------------


def short(commit: str) -> str:
    return commit[:SHORT]


def branch_moves(root: Path, start: Start, *, halted: bool) -> list[str]:
    """How ``HEAD`` and the local branch moved since ``start``.

    ``halted``: a rebase the start asked for is under way, so a detached ``HEAD`` is said apart.
    """
    on = gatelib.git(root, "rev-parse", "--abbrev-ref", "HEAD")
    if on is None:
        message = f"{root} is not a git checkout with a commit"
        raise UnreadableError(message)
    moves = []
    if on != start.branch and not halted:
        where = "detached" if on == "HEAD" else f"on `{on}`"
        moves.append(f"HEAD is {where}, not on `{start.branch}`, the branch the run started on")
    head = head_of(root, start.branch)
    if head is None:
        moves.append(
            f"`{start.branch}` is gone; it was at {short(start.head)} when the run started"
        )
    elif head != start.head and (move := head_move(root, start, head)) is not None:
        moves.append(move)
    return moves


def head_move(root: Path, start: Start, new: str) -> str | None:
    """How the local branch moved to ``new``; ``None`` when it finished the start's rebase."""
    branch, old = start.branch, short(start.head)
    if gatelib.git(root, "merge-base", "--is-ancestor", start.head, new) is not None:
        return (
            f"`{branch}` moved from {old} to {short(new)}, which holds it with commits on"
            f" top: a role committed, or the orchestrator committed before {FINAL_CHECK}"
        )
    rewritten = f"`{branch}` is at {short(new)}, which does not hold {old}, where it started"
    if start.rebase is None:
        return f"{rewritten}: a role amended, reset or rebased it"
    base, was = start.rebase.base, start.rebase.commits
    now = ahead_of(root, base, new)
    if now is None:
        return (
            f"{rewritten}, and `{base}`, the base it was to be rebased onto, names no commit:"
            " a role amended, reset or rebased it"
        )
    dropped = dropped_from(was, now)
    if dropped is None:
        return (
            f"{rewritten}, and the commits ahead of `{base}` are not the ones it had, by author"
            f" date and message in the same order ({len(was)} then, {len(now)} now): a role"
            " amended, reset or rebased it"
        )
    missing = unmatched(dropped, ahead_of(root, start.head, base) or ())
    if missing:
        return (
            f"{rewritten}, and {len(missing)} of the commits it had ahead of `{base}` were"
            f" dropped, which `{base}` does not hold by author date and message: a role amended,"
            " reset or rebased it"
        )
    return None


def unmatched(dropped: list[Commit], landed: tuple[Commit, ...]) -> list[Commit]:
    """The dropped commits left once each takes a twin of its own from ``landed``."""
    twins = list(landed)
    missing: list[Commit] = []
    for commit in dropped:
        if commit in twins:
            twins.remove(commit)
        else:
            missing.append(commit)
    return missing


def dropped_from(was: tuple[Commit, ...], now: tuple[Commit, ...]) -> list[Commit] | None:
    """The commits of ``was`` ``now`` lacks; ``None`` unless ``now`` keeps the others in order."""
    dropped: list[Commit] = []
    kept = 0
    for commit in was:
        if kept < len(now) and now[kept] == commit:
            kept += 1
        else:
            dropped.append(commit)
    return dropped if kept == len(now) else None


def remote_move(branch: str, old: str | None, new: str | None) -> list[str]:
    """How the forge's branch moved from ``old`` to ``new``, ``None`` meaning it had none."""
    if old is None and new is not None:
        return [f"the forge gained the branch `{branch}` at {short(new)}: a role pushed"]
    if old is not None and new is None:
        return [f"the forge's branch `{branch}` is gone; it was at {short(old)}: a role deleted it"]
    if old is not None and new is not None and old != new:
        return [
            f"the forge's branch `{branch}` moved from {short(old)} to {short(new)}: a role pushed"
        ]
    return []


def forge_moves(branch: str, start: Forge, now: Forge) -> list[str]:
    """How the forge's branch and the branch's open pull requests moved since ``start``."""
    opened = [
        f"pull request #{number} is open for `{branch}` and was not when the run started:"
        " a role opened it"
        for number in now.pull_requests
        if number not in start.pull_requests
    ]
    closed = [
        f"pull request #{number} for `{branch}` was open when the run started and is not now:"
        " a role closed or merged it"
        for number in start.pull_requests
        if number not in now.pull_requests
    ]
    return remote_move(branch, start.remote, now.remote) + opened + closed


def check(root: Path, start: Start, gh: preflight.Runner) -> tuple[list[str], bool, str | None]:
    """Every move since ``start``, a rebase it asked for left halfway, and an unread forge's why.

    Raises ``UnreadableError`` when the checkout cannot be read.
    """
    halted = start.rebase is not None and rebasing(root)
    moves = branch_moves(root, start, halted=halted)
    if start.forge is None:
        return moves, halted, None
    try:
        now = forge_of(root, start.branch, gh)
    except UnreadableError as error:
        return moves, halted, str(error)
    return moves + forge_moves(start.branch, start.forge, now), halted, None


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="publish_check.py", description=__doc__.splitlines()[0])
    parser.add_argument("start", type=Path, help="the start state: a JSON object with `start`")
    parser.add_argument("--root", default=".")
    options = parser.parse_args(argv[1:])
    try:
        start = read_start(options.start)
    except MalformedError as error:
        print(f"{SAID} the start state in {options.start} cannot be read: {error}; {NEVER}")
        return 2
    try:
        moves, halted, unread = check(Path(options.root).resolve(), start, preflight.run_gh)
    except UnreadableError as error:
        print(f"{SAID} the present state cannot be read: {error}; {NEVER}")
        return 3
    for move in moves:
        print(f"{SAID} {move}")
    if halted:
        print(f"{SAID} {HALTED}; {NEVER}")
    if moves and unread is not None:
        print(f"{SAID} the forge cannot be read either: {unread}")
    if moves:
        print(RED)
        return 1
    if unread is not None:
        print(f"{SAID} the present state cannot be read: {unread}; {NEVER}")
        return 3
    if halted:
        return 3
    print(GREEN)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
