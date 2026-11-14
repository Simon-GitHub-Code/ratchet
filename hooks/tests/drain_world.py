"""The drain model's actions played on a real repository by the real ``drain.py`` and ``merge.py``.

The repository is the one ``test_drain`` builds: three local tickets, ``Beta`` blocked by
``Alpha``, a binding whose ``merge`` entry point runs ``merge.py`` and whose ``check`` is green.
Each command runs as a session runs it, ``drain.main`` with no tracker; the agent's commits are
the commits the feature skill makes, and the owner's edit is an answer on the default branch.
After each action ``seen`` reads the repository back in the model's terms.
"""

from __future__ import annotations

import contextlib
import hashlib
import io
import json
import re
from dataclasses import dataclass
from pathlib import Path

import drain
import merge_ready
from tests import drain_model as model
from tests.drain_model import Branch, Kind, Name, Note, Outcome, Status, Ticket, Tree
from tests.repos import git, repository, snapshot, write
from tests.test_drain import (
    ALPHA,
    BETA,
    BRANCH_A,
    BRANCH_B,
    BRANCH_C,
    FILES,
    GAMMA,
    approval,
)

REF = {Name.A: ALPHA, Name.B: BETA, Name.C: GAMMA}
BRANCH = {Name.A: BRANCH_A, Name.B: BRANCH_B, Name.C: BRANCH_C}
ANSWER = "\n## Answers\n\nWhich case? The lower.\n"
CHANGE = "feat(core): the change"
RECORD = "docs(core): the verdict record"
DRAFT = "src/draft.py"
STATUSES = {status.value: status for status in Status}


def digest(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


@dataclass(frozen=True)
class Seen:
    """What the repository shows, in the model's terms."""

    statuses: dict[Name, Status]
    start: tuple[Name, ...]
    tickets: tuple[Ticket, Ticket, Ticket]


class World:
    """A repository the drain runs on, with no forge, as a session's commands leave it."""

    def __init__(self, directory: Path) -> None:
        self.root = repository(directory / "repo", dict(FILES))
        self.token = ""
        self.printed = ""
        self.code = 0

    def command(self, *argv: str) -> int:
        """``drain.py argv`` as a session runs it; what it printed is kept."""
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            self.code = drain.main(
                ["drain", *argv, "--root", str(self.root), "--offline", "--concurrency", "1"]
            )
        self.printed = out.getvalue() + err.getvalue()
        token = re.search(r"^drain: (\w+)$", err.getvalue(), re.MULTILINE)
        self.token = token.group(1) if token else self.token
        return self.code

    def tree(self, name: Name) -> Path:
        return drain.tree_of(self.root, BRANCH[name])

    def lock_of(self, name: Name) -> Path:
        """The index lock of the ticket's work tree, where git keeps it for a linked tree."""
        pointer = (self.tree(name) / ".git").read_text(encoding="utf-8")
        return Path(pointer.removeprefix("gitdir:").strip()) / "index.lock"

    def perform(self, action: model.Action) -> None:  # noqa: C901 -- one arm per kind
        """Do what ``action`` names."""
        kind, name = action.kind, action.ticket
        if name is None:
            drained = ["--drain", self.token] if self.token else []
            argv = {
                Kind.CLEAN: ["clean"],
                Kind.CLEAN_ALL: ["clean", "--all", *drained],
                Kind.REPORT: ["report"],
            }[kind]
            self.command(*argv)
            return
        ref, tree = REF[name], self.tree(name)
        if kind is Kind.OPEN:
            self.command("open", ref)
        elif kind is Kind.UPDATE:
            self.command("update", ref)
        elif kind is Kind.LAND:
            self.command("land", ref)
        elif kind is Kind.CLOSE:
            self.command("close", ref, "--outcome", "parked")
        elif kind is Kind.WORK:
            write(tree, DRAFT, "half done\n")
        elif kind is Kind.COMMIT:
            text = (tree / ref).read_text(encoding="utf-8")
            write(tree, ref, text.replace("Status: ready-for-agent", "Status: resolved"))
            write(tree, f"src/{name.value}.py", f"{name.name} = 1\n")
            snapshot(tree, CHANGE)
        elif kind is Kind.RECORD:
            with contextlib.redirect_stdout(io.StringIO()):
                assert merge_ready.write_record(tree, json.dumps(approval("alpha"))) == 0
            snapshot(tree, RECORD)
        elif kind is Kind.EDIT:
            text = (self.root / ref).read_text(encoding="utf-8")
            write(self.root, ref, text + ANSWER)
            snapshot(self.root, f"docs(tracker): the owner answers {ref}")
        elif kind is Kind.LOCK:
            self.lock_of(name).write_text("", encoding="utf-8")
        elif kind is Kind.UNLOCK:
            self.lock_of(name).unlink()

    def seen(self, ghost: model.State) -> Seen:
        """The repository read back; ``ghost`` gives what no file holds (the reviews)."""
        drafted = drain.plan(drain.Drain(self.root, None, model.CAP))
        by_ref = {entry.ref: STATUSES[entry.state] for entry in drafted.tickets}
        statuses = {name: by_ref[ref] for name, ref in REF.items() if ref in by_ref}
        start = tuple(name for name, ref in REF.items() if ref in drafted.start)
        trees = drain.worktrees(self.root)
        refs = git(
            self.root,
            "for-each-ref",
            "--format=%(refname:short) %(ahead-behind:main) %(contents:subject)",
            "refs/heads/feature/",
        )
        branches = {line.split(" ", 1)[0]: line.split(" ", 3)[1:] for line in refs.splitlines()}
        stashes = git(self.root, "stash", "list", "--format=%gs").splitlines()
        tickets = []
        for name, was in ghost.each():
            merged = name not in statuses
            tree = self.tree_state(name) if self.tree(name) in trees else Tree.NONE
            branch, current = self.branch_state(branches.get(BRANCH[name]), merged=merged, was=was)
            text = int(ANSWER in (self.root / REF[name]).read_text(encoding="utf-8"))
            note = self.note(name, merged=merged, was=was.note)
            ticket = Ticket(
                tree=tree,
                branch=branch,
                current=current,
                reviewed=was.reviewed,
                merged=merged,
                note=note,
                text=text,
                stashes=sum(f": {BRANCH[name]}: " in line for line in stashes),
            )
            tickets.append(model.normal(ticket))
        return Seen(statuses, start, (tickets[0], tickets[1], tickets[2]))

    def tree_state(self, name: Name) -> Tree:
        if self.lock_of(name).exists():
            return Tree.LOCKED
        return Tree.DIRTY if git(self.tree(name), "status", "--porcelain") else Tree.CLEAN

    @staticmethod
    def branch_state(listed: list[str] | None, *, merged: bool, was: Ticket) -> tuple[Branch, bool]:
        """The branch and whether it holds the default tip; a merged one is as it landed."""
        if listed is None:
            return Branch.NONE, True
        if merged:
            return was.branch, was.current
        ahead, behind, subject = listed
        if ahead == "0":
            return Branch.BARE, behind == "0"
        return (Branch.JUDGED if subject == RECORD else Branch.DRAFT), behind == "0"

    def note(self, name: Name, *, merged: bool, was: Note | None) -> Note | None:
        """The drain's note on the ticket; on a merged ticket its text is the model's."""
        held = drain.queue_notes.read_note(self.root, BRANCH[name])
        if not held:
            return None
        outcome = held.get("outcome")
        if outcome is None:
            return Note(Outcome.OPENED)
        texts = {digest(FILES[REF[name]]): 0, digest(FILES[REF[name]] + ANSWER): 1}
        written = texts.get(str(held.get("digest")), -1)
        attempts = held.get("attempts")
        return Note(
            Outcome.MERGED if outcome == "merged" else Outcome.CLOSED,
            hold=held.get("hold") is True,
            text=was.text if merged and was is not None else written,
            fresh=outcome == "merged" and held.get("reported") is False,
            attempts=min(model.RETRIES, attempts if isinstance(attempts, int) else 0),
        )
