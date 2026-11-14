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
import signal
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING
from unittest import mock

import drain
import gatelib
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

if TYPE_CHECKING:
    from collections.abc import Callable

REF = {Name.A: ALPHA, Name.B: BETA, Name.C: GAMMA}
BRANCH = {Name.A: BRANCH_A, Name.B: BRANCH_B, Name.C: BRANCH_C}
ANSWER = "\n## Answers\n\nWhich case? The lower.\n"
CHANGE = "feat(core): the change"
RECORD = "docs(core): the verdict record"
DRAFT = "src/draft.py"
VERDICT = "docs/features/alpha/verdict.json"
STATUSES = {status.value: status for status in Status}
COMMANDS = frozenset({Kind.OPEN, Kind.UPDATE, Kind.LAND, Kind.CLOSE})


def digest(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


class Killed(BaseException):
    """The command's process died here: nothing after this point of it runs."""


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
            try:
                self.code = drain.main(
                    ["drain", *argv, "--root", str(self.root), "--offline", "--concurrency", "1"]
                )
            except Killed:
                self.code = -signal.SIGKILL
        self.printed = out.getvalue() + err.getvalue()
        token = re.search(r"^drain: (\w+)$", err.getvalue(), re.MULTILINE)
        self.token = token.group(1) if token else self.token
        return self.code

    def tree(self, name: Name) -> Path:
        return drain.tree_of(self.root, BRANCH[name])

    def gitdir(self, name: Name) -> Path:
        """Where git keeps the state of the ticket's linked work tree."""
        pointer = (self.tree(name) / ".git").read_text(encoding="utf-8")
        return Path(pointer.removeprefix("gitdir:").strip())

    def lock_of(self, name: Name) -> Path:
        return self.gitdir(name) / "index.lock"

    def argv(self, kind: Kind, ref: str) -> list[str]:
        """The command line of a drain command; ``ref`` names the ticket of one that takes it."""
        drained = ["--drain", self.token] if self.token else []
        return {
            Kind.OPEN: ["open", ref],
            Kind.UPDATE: ["update", ref],
            Kind.LAND: ["land", ref],
            Kind.CLOSE: ["close", ref, "--outcome", "parked"],
            Kind.CLEAN: ["clean"],
            Kind.CLEAN_ALL: ["clean", "--all", *drained],
            Kind.REPORT: ["report"],
        }[kind]

    def killer(self, kind: Kind, name: Name) -> contextlib.AbstractContextManager[object]:
        """What stops the command at the point ``kind`` names, as SIGKILL would stop it there.

        The command runs in this process, and an exception no ``except`` of the drain catches
        stands for the kill. The drain lock it holds is freed either way, as the kernel frees it.
        """
        real_git, real_write = gatelib.git, drain.queue_notes.write_note

        def git_until(stop: tuple[str, str]) -> Callable[..., str | None]:
            def run(root: Path, *args: str) -> str | None:
                if args[:2] != stop:
                    return real_git(root, *args)
                if stop[0] == "rebase":
                    # Why: a rebase stopped at a `break` before its first pick is where git is
                    # when the kill lands right after its first checkout: detached at the base.
                    git(root, "-c", "sequence.editor=sed -i 1ibreak", "rebase", "-i", *args[1:])
                raise Killed

            return run

        def write_until(path: Path, note: dict[str, object]) -> None:
            if path == drain.queue_notes.note_path(self.root, BRANCH[name]):
                raise Killed
            real_write(path, note)

        def killed(*_args: object) -> None:
            raise Killed

        killers = {
            Kind.KILL_MID_REBASE: lambda: mock.patch.object(
                gatelib, "git", git_until(("rebase", "--quiet"))
            ),
            Kind.KILL_AFTER_REBASE: lambda: mock.patch.object(drain, "drop_record", killed),
            Kind.KILL_BEFORE_DISCARD: lambda: mock.patch.object(drain, "discard", killed),
            Kind.KILL_AFTER_STASH: lambda: mock.patch.object(
                gatelib, "git", git_until(("worktree", "remove"))
            ),
            Kind.KILL_LAND_BEFORE_NOTE: lambda: mock.patch.object(
                drain.queue_notes, "write_note", write_until
            ),
            Kind.KILL_LAND_BEFORE_DISCARD: lambda: mock.patch.object(drain, "discard", killed),
        }
        return killers[kind]()

    def perform(self, action: model.Action) -> None:
        """Do what ``action`` names."""
        kind, name = action.kind, action.ticket
        if name is None:
            self.command(*self.argv(kind, ""))
        elif kind in model.KILLS:
            with self.killer(kind, name):
                self.command(*self.argv(model.KILLS[kind], REF[name]))
        elif kind in COMMANDS:
            self.command(*self.argv(kind, REF[name]))
        else:
            self.by_hand(kind, name)

    def by_hand(self, kind: Kind, name: Name) -> None:
        """What the agent, the owner or git does, outside the drain."""
        ref, tree = REF[name], self.tree(name)
        if kind is Kind.WORK:
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
            listed = branches.get(BRANCH[name])
            branch, current = self.branch_state(name, listed, merged=merged, was=was)
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
        if (self.gitdir(name) / "rebase-merge").is_dir():
            return Tree.REBASING
        return Tree.DIRTY if git(self.tree(name), "status", "--porcelain") else Tree.CLEAN

    def branch_state(
        self, name: Name, listed: list[str] | None, *, merged: bool, was: Ticket
    ) -> tuple[Branch, bool]:
        """The branch and whether it holds the default tip; a merged one is as it landed."""
        if listed is None:
            return Branch.NONE, True
        if merged:
            return was.branch, was.current
        ahead, behind, subject = listed
        if ahead == "0":
            return Branch.BARE, behind == "0"
        if subject != RECORD:
            return Branch.DRAFT, behind == "0"
        branch = BRANCH[name]
        certified = json.loads(git(self.root, "show", f"{branch}:{VERDICT}"))["tree"]
        reviewed = git(self.root, "rev-parse", f"{branch}~1^{{tree}}")
        return (Branch.JUDGED if certified == reviewed else Branch.STALE), behind == "0"

    def note(self, name: Name, *, merged: bool, was: Note | None) -> Note | None:
        """The drain's note on the ticket; on a merged ticket its text is the model's."""
        held = drain.queue_notes.read_note(self.root, BRANCH[name])
        if not held:
            return None
        outcome = held.get("outcome")
        rebasing = bool(held.get("rebasing"))
        if outcome is None:
            return Note(Outcome.OPENED, rebasing=rebasing)
        texts = {digest(FILES[REF[name]]): 0, digest(FILES[REF[name]] + ANSWER): 1}
        written = texts.get(str(held.get("digest")), -1)
        attempts = held.get("attempts")
        return Note(
            Outcome.MERGED if outcome == "merged" else Outcome.CLOSED,
            hold=held.get("hold") is True,
            text=was.text if merged and was is not None else written,
            fresh=held.get("reported") is False,
            attempts=min(model.RETRIES, attempts if isinstance(attempts, int) else 0),
            rebasing=rebasing,
        )
