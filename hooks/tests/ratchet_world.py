"""The ratchet model's actions played on a real repository and judged by the real gate.

Each open branch has a work tree of its own. A landing is made the way ``merge.attempt`` makes
one: a detached work tree at the default branch's tip, ``git merge --no-ff`` of the branch there,
the gate run on that merge result, and the default branch moved forward to it only when it is
green. The gate also runs on the branch first, where an agent runs it before asking to merge.
"""

from __future__ import annotations

import contextlib
import io
import itertools
import subprocess
import tomllib
from dataclasses import dataclass
from typing import TYPE_CHECKING

import lowered
from tests.ratchet_model import (
    EDITS,
    INITIAL,
    RECORDS,
    VALUES,
    Action,
    Cell,
    Key,
    Kind,
    Record,
    Slot,
    Step,
    Tree,
    Value,
)
from tests.repos import git, repository, snapshot, write

if TYPE_CHECKING:
    from pathlib import Path

DEFAULT = "main"
THRESHOLDS = "quality/thresholds.toml"
ADRS = "docs/adr"
STEPS = tuple(
    Step(key, old, new)
    for key in Key
    for old, new in itertools.product(VALUES[key], (*VALUES[key], None))
    if old != new
)
NUMBER = {1.0: Value.ONE, 2.0: Value.TWO, 3.0: Value.THREE}


def thresholds(tree: Tree) -> str:
    """The file, each key on a line of its own with unchanged lines between them."""

    def line(name: str, value: Cell) -> str:
        return "" if value is None else f"{name} = {value.value}\n"

    return f"[gate]\n{line('floor', tree.floor)}\n[ceilings.gate]\n{line('seconds', tree.ceiling)}"


def record_path(record: Record) -> str:
    """A record's file, numbered by its line and copy, so two branches adding one add one file."""
    number = STEPS.index(record.step) + 1 + record.copy * len(STEPS)
    return f"{ADRS}/{number:04d}-relaxes.md"


def record_text(record: Record) -> str:
    return f"# Relaxes\n\n{record.step.line}\n"


def record_at(path: Path) -> Record:
    """The record ``path`` is, read from its text and checked against its number."""
    copy, index = divmod(int(path.name[:4]) - 1, len(STEPS))
    record = Record(STEPS[index], copy)
    assert path.read_text(encoding="utf-8") == record_text(record), path
    return record


def files(tree: Tree) -> dict[str, str]:
    held = {record_path(record): record_text(record) for record in tree.records}
    return {THRESHOLDS: thresholds(tree), **held} if tree.file else held


def parsed(text: str | None, held: frozenset[Record]) -> Tree:
    """A tree read back from the file's text (``None``: no file) and its records."""
    if text is None:
        return Tree(floor=None, ceiling=None, records=held, file=False)
    flat = lowered.flatten(tomllib.loads(text))

    def cell(key: Key) -> Cell:
        value = flat.get(key.value)
        return None if value is None else Value.ON if value is True else NUMBER[float(value)]

    return Tree(floor=cell(Key.FLOOR), ceiling=cell(Key.CEILING), records=held)


def tree_in(directory: Path) -> Tree:
    path = directory / THRESHOLDS
    text = path.read_text(encoding="utf-8") if path.is_file() else None
    return parsed(text, frozenset(record_at(path) for path in (directory / ADRS).glob("*.md")))


@dataclass(frozen=True)
class Gate:
    green: bool
    output: str


def gate(root: Path) -> Gate:
    """``lowered.main`` on ``root`` as the binding runs it, its printed lines kept."""
    printed = io.StringIO()
    with contextlib.redirect_stdout(printed):
        code = lowered.main(["lowered", "--root", str(root)])
    return Gate(green=code == 0, output=printed.getvalue())


@dataclass(frozen=True)
class Landing:
    """What a landing attempt saw: the gate on the branch, then on the merge (none: conflict)."""

    branch: Gate
    merge: Gate | None
    landed: bool


@dataclass(frozen=True)
class Landed:
    """One merge the default branch moved to, as trees read from the work trees that made it.

    ``forked_after`` is how many merges the default branch had taken when the branch forked.
    """

    before: Tree
    after: Tree
    fork: Tree
    head: Tree
    forked_after: int


class World:
    """A repository whose default branch only moves by landings, with a work tree per slot.

    The root work tree is detached, so the default branch is checked out nowhere and moves by
    ``update-ref`` from the old tip, as ``merge.land`` moves it then.
    """

    def __init__(self, directory: Path) -> None:
        self.directory = directory
        self.root = repository(directory / "repository", files(INITIAL.default))
        git(self.root, "checkout", "-q", "--detach")
        self.merging = directory / "merge"
        git(self.root, "worktree", "add", "-q", "--detach", str(self.merging), DEFAULT)
        self.forks: dict[Slot, tuple[Tree, int]] = {}
        self.landings: list[Landed] = []

    def work(self, slot: Slot) -> Path:
        return self.directory / self.branch(slot)

    @staticmethod
    def branch(slot: Slot) -> str:
        return f"slot-{slot.name.lower()}"

    def perform(self, action: Action) -> Landing | None:
        """Do what ``action`` names; a landing attempt says what it saw."""
        if action.kind is Kind.OPEN:
            self.open(action.slot)
            return None
        if action.kind is Kind.ABANDON:
            del self.forks[action.slot]
            return None
        if action.kind is Kind.LAND:
            return self.land(action.slot)
        work = self.work(action.slot)
        if action.kind in EDITS and action.key is not None:
            write(work, THRESHOLDS, thresholds(tree_in(work).setting(action.key, action.value)))
        elif action.kind is Kind.DELETE:
            (work / THRESHOLDS).unlink()
        elif action.kind in RECORDS and action.record is not None:
            write(work, record_path(action.record), record_text(action.record))
        snapshot(work, f"chore(quality): {action.name}")
        return None

    def open(self, slot: Slot) -> None:
        work, name = self.work(slot), self.branch(slot)
        if work.is_dir():
            git(work, "checkout", "-q", "-B", name, DEFAULT)
        else:
            git(self.root, "worktree", "add", "-q", "-B", name, str(work), DEFAULT)
        self.forks[slot] = (tree_in(work), len(self.landings))

    def land(self, slot: Slot) -> Landing:
        work = self.work(slot)
        on_branch = gate(work)
        git(self.merging, "reset", "-q", "--hard", DEFAULT)
        old, before = git(self.merging, "rev-parse", "HEAD"), tree_in(self.merging)
        merge = ["git", "-C", str(self.merging), "merge", "--no-ff", "-q", "-m", "Merge"]
        merging = subprocess.run(  # noqa: S603 # -- a fixed argv, no shell
            [*merge, self.branch(slot)], capture_output=True, check=False
        )
        if merging.returncode != 0:
            return Landing(branch=on_branch, merge=None, landed=False)
        on_merge = gate(self.merging)
        if on_merge.green:
            git(self.merging, "update-ref", f"refs/heads/{DEFAULT}", "HEAD", old)
            after = tree_in(self.merging)
            fork, forked_after = self.forks.pop(slot)
            self.landings.append(Landed(before, after, fork, tree_in(work), forked_after))
        return Landing(branch=on_branch, merge=on_merge, landed=on_merge.green)

    def branch_tree(self, slot: Slot) -> Tree:
        return tree_in(self.work(slot))
