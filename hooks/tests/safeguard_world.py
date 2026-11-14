"""The safeguard model's actions on a real repository, judged by the real triggers gate.

Each instance of the kind is a file of its own under ``src/`` holding one line the Triggers row's
pattern matches; the binding is written from the model's ``Tree``, a field to a line. A branch is
committed as ``merge.attempt`` would meet it, and landed the way it lands one: merged ``--no-ff``
on a detached copy of the default branch's tip, the gate run on the merge result, and the default
branch moved forward to it only when the merge is clean and the gate is green. The gate also runs
on the branch, against the commit it forked from, as the branch's own check does.
"""

from __future__ import annotations

import contextlib
import io
import subprocess
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

import triggers
from tests.repos import git, repository, write
from tests.safeguard_model import (
    INITIAL,
    Debt,
    Event,
    Landing,
    Row,
    State,
    Step,
    Table,
    Tree,
    Verb,
)

if TYPE_CHECKING:
    from pathlib import Path

BINDING = "docs/process/binding.md"
KIND = "beacon"
EVENT = "launched"
QUESTIONS = {Debt.KIND: "beacon calls fail safely", Debt.EVENT: "the launch is watched"}
PATHS = {Table.FINDS: "src/**", Table.BLIND: "elsewhere/**"}


def binding(tree: Tree) -> str:
    """The binding ``tree`` describes, each field on a line with an unchanged line beside it."""
    kind_tool = f"later: code:{KIND}" if tree.row is Row.LATER else "a beacon check"
    event_tool = f"later: life:{EVENT}" if tree.event_row is Row.LATER else "a launch check"
    kind_row = f"| {QUESTIONS[Debt.KIND]} | {kind_tool} | 1 | | `just beacon` |\n"
    return (
        "# Binding\n\n- Application types: service\n"
        f"- Life events: {EVENT if tree.event is Event.DECLARED else 'none'}\n\n## Gates\n\n"
        "| Question | Tool | Tier | Threshold source | Entry point |\n"
        "| --- | --- | --- | --- | --- |\n"
        "| format | a formatter | 0 | | `just fmt` |\n"
        + (kind_row if tree.row is not Row.ABSENT else "")
        + "| lint | a linter | 0 | | `just lint` |\n"
        "| types | a type checker | 0 | | `just types` |\n"
        f"| {QUESTIONS[Debt.EVENT]} | {event_tool} | weekly | | `just watch` |\n"
        "| tests | a test runner | 1 | | `just test` |\n\n"
        "## Triggers\n\n| Kind | Paths | Pattern |\n| --- | --- | --- |\n"
        f"| {KIND} | `{PATHS[tree.table]}` | `beacon\\(` |\n"
    )


def read_binding(text: str) -> tuple[Row, Table, Event, Row]:
    """The fields of a binding ``binding`` wrote, read back from its lines alone."""
    lines = text.splitlines()

    def row(question: str, later: str) -> Row:
        line = next((line for line in lines if line.startswith(f"| {question} |")), None)
        if line is None:
            return Row.ABSENT
        return Row.LATER if later in line else Row.BOUND

    events = next(line for line in lines if line.startswith("- Life events:"))
    blind = any(line.startswith(f"| {KIND} | `{PATHS[Table.BLIND]}`") for line in lines)
    return (
        row(QUESTIONS[Debt.KIND], f"later: code:{KIND}"),
        Table.BLIND if blind else Table.FINDS,
        Event.DECLARED if EVENT in events else Event.UNDECLARED,
        row(QUESTIONS[Debt.EVENT], f"later: life:{EVENT}"),
    )


@dataclass(frozen=True)
class Run:
    """One run of the gate: its exit code and what it printed."""

    code: int
    out: str

    @property
    def arrived(self) -> bool:
        return f"note: '{KIND}' has arrived" in self.out

    @property
    def event_owes(self) -> bool:
        return f"note: life event '{EVENT}' is declared" in self.out


@dataclass(frozen=True)
class Attempt:
    """What the world saw on one landing: both runs of the gate, the trees, and the move."""

    landing: Landing
    branch_run: Run
    merge_run: Run | None


@dataclass
class World:
    """A scratch repository the model's actions are taken on, with every command it ran."""

    root: Path
    files: dict[int, str] = field(default_factory=dict)
    made: int = 0
    forked: int = 0
    open: str = ""
    log: list[str] = field(default_factory=list)

    @classmethod
    def create(cls, root: Path) -> World:
        repository(root, {BINDING: binding(INITIAL.main), "README.md": "# A\n"})
        return cls(root)

    def git(self, *args: str) -> str:
        self.log.append("git " + " ".join(args))
        return git(self.root, *args)

    def gate(self) -> Run:
        printed = io.StringIO()
        with contextlib.redirect_stdout(printed):
            code = triggers.main(["triggers", "--root", str(self.root)])
        self.log.append(f"triggers.py -> exit {code}\n{printed.getvalue()}")
        return Run(code, printed.getvalue())

    def commit(self, tree: Tree, before: Tree, subject: str) -> None:
        """Commit ``tree`` on the checked-out branch, its new instances as new files."""
        for instance in tree.instances - before.instances:
            self.made += 1
            self.files[instance] = f"src/part{self.made}.txt"
        for instance in before.instances - tree.instances:
            (self.root / self.files[instance]).unlink()
            self.log.append(f"rm {self.files[instance]}")
        for instance in tree.instances - before.instances:
            # Why: each instance's text is its own, as code is; files alike would read to git as
            # one moved, and a move beside a removal of the same file conflicts.
            text = f"beacon('{self.files[instance]}')\n"
            write(self.root, self.files[instance], text)
            self.log.append(f"write {self.files[instance]}: {text.strip()}")
        if tree.binding != before.binding:
            write(self.root, BINDING, binding(tree))
            self.log.append(f"write {BINDING}: {', '.join(f.value for f in tree.binding)}")
        self.git("add", "-A")
        self.git("commit", "-q", "-m", subject)

    def tree(self) -> Tree:
        """What the work tree holds, read from its files: the model's ``Tree`` of it."""
        names = {path: instance for instance, path in self.files.items()}
        listed = [f"src/{path.name}" for path in (self.root / "src").glob("*")]
        instances = frozenset(names[path] for path in listed)
        text = (self.root / BINDING).read_text(encoding="utf-8")
        return Tree(instances, *read_binding(text))

    def owed(self) -> dict[Debt, int]:
        """What ``triggers.owed`` says the default branch's work tree owes, by debt."""
        by_question = {question: debt for debt, question in QUESTIONS.items()}
        return {by_question[row.question]: row.count for row in triggers.owed(self.root)}

    def take(
        self, step: Step, before: State, *, after: State, result: Landing | None
    ) -> Attempt | None:
        """Take ``step`` from ``before``; what was seen when it lands a branch.

        A branch landed at once forks from the tip, so its merge result holds the branch's tree
        and the gate on the branch judges the same change as on the merge: it runs once. Every
        branch has a name of its own, so an abandoned one is left where it is.
        """
        if step.verb is Verb.FORK:
            assert after.branch is not None
            self.open = self.fork(after.branch.head, before.main, step)
            return None
        if step.verb is Verb.ABANDON:
            self.log.append(f"abandon {self.open}")
            return None
        if step.verb is Verb.LAND:
            assert before.branch is not None
            fork, head = before.branch.fork, before.branch.head
            self.git("switch", "-q", self.open)
            return self.land(self.open, (before.main, fork, head), self.gate())
        assert result is not None
        name = self.fork(result.head, before.main, step)
        return self.land(name, (before.main, before.main, result.head), None)

    def fork(self, head: Tree, tip: Tree, step: Step) -> str:
        """Commit ``head`` on a new branch from the default branch; the branch's name."""
        self.forked += 1
        name = f"change-{self.forked}"
        self.git("switch", "-q", "-c", name, "main")
        assert step.change is not None
        self.commit(head, tip, f"feat(core): {step.change.value}")
        return name

    def land(self, name: str, trees: tuple[Tree, Tree, Tree], branch_run: Run | None) -> Attempt:
        """``merge.attempt`` of branch ``name``: merge, gate, and move the default branch."""
        self.git("switch", "-q", "--detach", "main")
        try:
            self.git("merge", "--no-ff", "-q", "-m", f"Merge branch '{name}'", name)
        except subprocess.CalledProcessError:
            self.git("merge", "--abort")
            assert branch_run is not None
            green = branch_run.code == 0
            return Attempt(
                Landing(*trees, None, landed=False, branch_green=green), branch_run, None
            )
        merge_run = self.gate()
        if merge_run.code == 0:
            self.git("update-ref", "refs/heads/main", "HEAD")
        branch_run = branch_run or merge_run
        landing = Landing(*trees, self.tree(), merge_run.code == 0, branch_run.code == 0)
        return Attempt(landing, branch_run, merge_run)
