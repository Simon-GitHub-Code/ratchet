from __future__ import annotations

import contextlib
import io
import re
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
    Start,
    State,
    Step,
    Table,
    Tree,
    Verb,
)

if TYPE_CHECKING:
    from pathlib import Path

BINDING = "docs/process/binding.md"
KIND = "outbound-call"
EVENT = "launched"
QUESTIONS = {Debt.KIND: "beacon calls fail safely", Debt.EVENT: "the launch is watched"}
PATHS = {Table.FINDS: "src/**", Table.BLIND: "elsewhere/**"}
REVIEW_ROW = (
    f"| {triggers.REVIEW_QUESTION} | `ratchet` plugin `scripts/triggers.py` | 1 | |"
    " `just triggers` |\n"
)
RECORD = "docs/adr/0002-architecture-review.md"
EARLY = "docs/adr/0001-architecture-review.md"
REVIEWED = (
    f"# The architecture holds\n\n- Architecture review: `code:{KIND}`\n- Outcome: holds\n"
    "- Decided: 2026-11-14\n\n## Decision\n\nThe calls go through one part.\n\n"
    "## Refutation criterion\n\nA second part calls out.\n"
)


def binding(tree: Tree) -> str:
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
        "| tests | a test runner | 1 | | `just test` |\n"
        + (REVIEW_ROW if tree.review is Row.BOUND else "")
        + "\n## Triggers\n\n| Kind | Paths | Pattern |\n| --- | --- | --- |\n"
        f"| {KIND} | `{PATHS[tree.table]}` | `beacon\\(` |\n"
    )


def read_binding(text: str) -> tuple[Row, Table, Event, Row, Row]:
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
        Row.BOUND if REVIEW_ROW.strip() in lines else Row.ABSENT,
    )


@dataclass(frozen=True)
class Run:
    code: int
    out: str

    @property
    def arrived(self) -> bool:
        return re.search(rf"note: '{KIND}' has arrived \(.*\) and owes ", self.out) is not None

    @property
    def event_owes(self) -> bool:
        return f"note: life event '{EVENT}' is declared" in self.out

    @property
    def reopens(self) -> bool:
        return re.search(rf"note: '{KIND}' has arrived \(.*\) and reopens ", self.out) is not None


@dataclass(frozen=True)
class Attempt:
    landing: Landing
    branch_run: Run
    merge_run: Run | None


@dataclass
class World:
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
        for instance in tree.instances - before.instances:
            self.made += 1
            self.files[instance] = f"src/part{self.made}.txt"
        for instance in before.instances - tree.instances:
            (self.root / self.files[instance]).unlink()
            self.log.append(f"rm {self.files[instance]}")
        for instance in tree.instances - before.instances:
            text = f"beacon('{self.files[instance]}')\n"
            write(self.root, self.files[instance], text)
            self.log.append(f"write {self.files[instance]}: {text.strip()}")
        if tree.binding != before.binding:
            write(self.root, BINDING, binding(tree))
            self.log.append(f"write {BINDING}: {', '.join(f.value for f in tree.binding)}")
        for held, was, path in (
            (tree.early, before.early, EARLY),
            (tree.reviewed, before.reviewed, RECORD),
        ):
            if held and not was:
                write(self.root, path, REVIEWED)
                self.log.append(f"write {path}")
        self.git("add", "-A")
        self.git("commit", "-q", "-m", subject)

    def tree(self) -> Tree:
        names = {path: instance for instance, path in self.files.items()}
        listed = [f"src/{path.name}" for path in (self.root / "src").glob("*")]
        instances = frozenset(names[path] for path in listed)
        text = (self.root / BINDING).read_text(encoding="utf-8")
        return Tree(
            instances,
            *read_binding(text),
            reviewed=(self.root / RECORD).exists(),
            early=(self.root / EARLY).exists(),
        )

    def owed(self) -> dict[Debt, int]:
        by_question = {question: debt for debt, question in QUESTIONS.items()}
        return {by_question[row.question]: row.count for row in triggers.owed(self.root)}

    def reviews(self) -> int | None:
        return next((row.count for row in triggers.reviews_owed(self.root)), None)

    def take(
        self, step: Step, before: State, *, after: State, result: Landing | None
    ) -> Attempt | None:
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
            held = (before.held, before.branch.held)
            start = (before.start, before.branch.start)
            trees = (before.main, fork, head)
            return self.land(self.open, trees, self.gate(), held=held, start=start)
        assert result is not None
        name = self.fork(result.head, before.main, step)
        held, start = (before.held, before.held), (before.start, before.start)
        trees = (before.main, before.main, result.head)
        return self.land(name, trees, None, held=held, start=start)

    def fork(self, head: Tree, tip: Tree, step: Step) -> str:
        self.forked += 1
        name = f"change-{self.forked}"
        self.git("switch", "-q", "-c", name, "main")
        assert step.change is not None
        self.commit(head, tip, f"feat(core): {step.change.value}")
        return name

    def land(
        self,
        name: str,
        trees: tuple[Tree, Tree, Tree],
        branch_run: Run | None,
        *,
        held: tuple[bool, bool],
        start: tuple[Start, Start],
    ) -> Attempt:
        self.git("switch", "-q", "--detach", "main")
        try:
            self.git("merge", "--no-ff", "-q", "-m", f"Merge branch '{name}'", name)
        except subprocess.CalledProcessError:
            self.git("merge", "--abort")
            assert branch_run is not None
            green = branch_run.code == 0
            return Attempt(
                Landing(
                    *trees,
                    None,
                    landed=False,
                    branch_green=green,
                    tip_held=held[0],
                    fork_held=held[1],
                    tip_start=start[0],
                    fork_start=start[1],
                ),
                branch_run,
                None,
            )
        merge_run = self.gate()
        if merge_run.code == 0:
            self.git("update-ref", "refs/heads/main", "HEAD")
        branch_run = branch_run or merge_run
        green = (merge_run.code == 0, branch_run.code == 0)
        landing = Landing(*trees, self.tree(), *green, *held, *start)
        return Attempt(landing, branch_run, merge_run)
