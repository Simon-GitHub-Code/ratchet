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

What counts as moved, each said in a line of its own:

- ``HEAD`` is not on the branch: a role switched to another branch or detached it;
- the branch is gone, has commits on top of where it started, or is at a commit that does not
  hold that one (amended, reset or rebased);
- with a forge: the branch appeared there, moved or is gone; a pull request for the branch is
  open that was not, or one that was open is not.

Uncommitted, staged and untracked changes are not a move: the roles write files and leave them.

What it does not see: a tag, a stash or another branch a role made, locally or on the forge; a
remote other than ``origin``, and any remote at all when the start had no forge; a pull request
whose head is another branch, and an edit to one that stays open; whatever was undone before the
check ran, such as a pull request opened and closed again; and a start file a role rewrote, so
the orchestrator keeps that file outside the work tree.

It reads the forge's branch with ``git ls-remote``, which fetches nothing and moves no ref, and
the open pull requests through ``gh`` as the preflight does, in the repository of the working
directory. It writes nothing, so a second run gives the same answer while nothing moves.

Exit status:

- 0: nothing moved;
- 1: a role published: a line for each thing that moved, then the verdict; a forge that could not
  be read is named too, since what moved already decides;
- 2: the start state cannot be read: no file, not JSON, or a member missing or malformed;
- 3: the present state cannot be read: not a git checkout, or the forge cannot be reached.

Neither 2 nor 3 is ever a pass.

Usage: ``publish_check.py START [--root DIR]``.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path

import feature_preflight as preflight
import gatelib

COMMIT = re.compile(r"[0-9a-f]{40}|[0-9a-f]{64}")
REMOTE = "origin"
SHORT = 12
SAID = "publish check:"
NEVER = "that is never a pass"
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
class Start:
    """The state a run started from: the branch, its commit, and the forge when there is one."""

    branch: str
    head: str
    forge: Forge | None


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
    forge = member(start, "forge", "start")
    if forge is None:
        return Start(branch, head, None)
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
    return Start(
        branch,
        head,
        Forge(
            None if remote is None else a_commit(remote, "start.forge.remote"),
            tuple(sorted(set(numbers))),
        ),
    )


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


def start_of(root: Path, branch: str, gh: preflight.Runner) -> Start:
    """The start state of a run on ``branch``, read the way the check reads the present."""
    head = head_of(root, branch)
    if head is None:
        message = f"there is no branch `{branch}`"
        raise UnreadableError(message)
    has_forge = gatelib.git(root, "remote", "get-url", REMOTE) is not None
    return Start(branch, head, forge_of(root, branch, gh) if has_forge else None)


# --- what moved ------------------------------------------------------------------------------


def short(commit: str) -> str:
    return commit[:SHORT]


def branch_moves(root: Path, start: Start) -> list[str]:
    """How ``HEAD`` and the local branch moved since ``start``."""
    on = gatelib.git(root, "rev-parse", "--abbrev-ref", "HEAD")
    if on is None:
        message = f"{root} is not a git checkout with a commit"
        raise UnreadableError(message)
    moves = []
    if on != start.branch:
        where = "detached" if on == "HEAD" else f"on `{on}`"
        moves.append(f"HEAD is {where}, not on `{start.branch}`, the branch the run started on")
    head = head_of(root, start.branch)
    if head is None:
        moves.append(
            f"`{start.branch}` is gone; it was at {short(start.head)} when the run started"
        )
    elif head != start.head:
        moves.append(head_move(root, start.branch, old=start.head, new=head))
    return moves


def head_move(root: Path, branch: str, *, old: str, new: str) -> str:
    """How the local branch moved from ``old`` to ``new``: on top of it, or rewritten."""
    if gatelib.git(root, "merge-base", "--is-ancestor", old, new) is not None:
        return (
            f"`{branch}` moved from {short(old)} to {short(new)}, which holds it with commits on"
            " top: a role committed"
        )
    return (
        f"`{branch}` is at {short(new)}, which does not hold {short(old)}, where it started:"
        " a role amended, reset or rebased it"
    )


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


def check(root: Path, start: Start, gh: preflight.Runner) -> tuple[list[str], str | None]:
    """Every move since ``start``, and why the forge could not be read, if it could not.

    Raises ``UnreadableError`` when the checkout cannot be read.
    """
    moves = branch_moves(root, start)
    if start.forge is None:
        return moves, None
    try:
        now = forge_of(root, start.branch, gh)
    except UnreadableError as error:
        return moves, str(error)
    return moves + forge_moves(start.branch, start.forge, now), None


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
        moves, unread = check(Path(options.root).resolve(), start, preflight.run_gh)
    except UnreadableError as error:
        print(f"{SAID} the present state cannot be read: {error}; {NEVER}")
        return 3
    for move in moves:
        print(f"{SAID} {move}")
    if moves and unread is not None:
        print(f"{SAID} the forge cannot be read either: {unread}")
    if moves:
        print(RED)
        return 1
    if unread is not None:
        print(f"{SAID} the present state cannot be read: {unread}; {NEVER}")
        return 3
    print(GREEN)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
