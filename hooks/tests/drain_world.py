"""The drain model's actions played on a real repository by the real ``drain.py`` and ``merge.py``.

The repository is the one ``test_drain`` builds: three local tickets, ``Beta`` blocked by
``Alpha``, a binding whose ``merge`` entry point runs ``merge.py`` and whose ``check`` is green.
``merge.py`` runs as it does in a drain, in a process of its own, which a kill ends with SIGKILL.
Each command runs as a session runs it, ``drain.main`` with no tracker; the agent's commits are
the commits the feature skill makes, and the owner's edit is an answer on the default branch.
``b`` and ``c`` state their ``Flag:`` line and ``a`` does not. The upgrade that ships ADR 0061 is
the preflight asking that line before deployment: ``operability_asked`` stands in for it.
``Alpha`` carries two promises, so the agent can split it into ``Save`` and ``List``, the model's
``d`` and ``e``; the owner closes a ticket by its status line.
After each action ``seen`` reads the repository back in the model's terms.

The publish check of ADR 0060 is ``publish_check.py``. Until ``drain.py`` records a start in the
note and runs the check itself, the world records the start where the model says ``open`` and an
``update`` that moves the branch do, and stands in for ``drain.py check`` where the ticket has a
tree: the real check reads the branch against that start, and a red verdict, or a rebase left
unfinished, closes the ticket by the real ``close``. Once a note holds ``start``, the world runs
the script's own ``check`` and reads ``start`` and ``passed`` back from the note.
"""

from __future__ import annotations

import contextlib
import dataclasses
import fcntl
import hashlib
import io
import json
import os
import re
import signal
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING
from unittest import mock

import drain
import gatelib
import merge
import merge_ready
import publish_check
from tests import drain_model as model
from tests import servers
from tests.drain_model import (
    Branch,
    Drain,
    Kind,
    Lease,
    Merge,
    Name,
    Note,
    Outcome,
    Status,
    Step,
    Ticket,
    Tree,
)
from tests.repos import git, repository, snapshot, write
from tests.test_drain import (
    ALPHA,
    BETA,
    BRANCH_A,
    BRANCH_B,
    BRANCH_C,
    FILES,
    GAMMA,
    HALVES,
    SCRIPTS,
    WHOLE,
    approval,
    binding,
)

if TYPE_CHECKING:
    from collections.abc import Callable

# Why: `split` files the parts beside the parent, numbered on from it and named by their titles.
REF = {
    Name.A: ALPHA,
    Name.B: BETA,
    Name.C: GAMMA,
    Name.D: ".scratch/f/issues/04-save.md",
    Name.E: ".scratch/f/issues/05-list.md",
}
BRANCH = {
    Name.A: BRANCH_A,
    Name.B: BRANCH_B,
    Name.C: BRANCH_C,
    Name.D: "feature/4-save",
    Name.E: "feature/5-list",
}
DONE = ("Status: ready-for-agent", "Status: done")
"""The owner's close of a local ticket: its status line, on the default branch."""
ANSWER = "\n## Answers\n\nWhich case? The lower.\n"
NO_FLAG = (
    "\n## Operability\n\nFlag: none: the owner chose to have it appear as soon as it is"
    " finished, while the project is not deployed\n"
)
FLAG_LANDED = "\n## Operability\n\nFlag: `alpha`, off until the owner switches it on\n"
TEXTS = {**FILES, **WHOLE, BETA: FILES[BETA] + NO_FLAG, GAMMA: FILES[GAMMA] + NO_FLAG}
FLAG_ASKED = [(drain.preflight.FLAG_LINE, "the flag the behaviour ships behind")]
CHANGE = "feat(core): the change"
RECORD = "docs(core): the verdict record"
PUBLISHED = "feat(core): a role's own commit"
ROLE_FILE = "src/role.py"
DRAFT = "src/draft.py"
# Why: each ticket the agent builds records its verdict in a folder of its own, as a pipeline does.
SLUG = {Name.A: "alpha", Name.D: "save", Name.E: "list"}
STATUSES = {status.value: status for status in Status}
COMMANDS = frozenset({Kind.OPEN, Kind.UPDATE, Kind.LAND, Kind.CLOSE})
CHECKS = frozenset({Kind.CHECK, Kind.CHECK_UNREADABLE, Kind.CHECK_HALTED})
UNREADABLE = "the forge cannot be reached"
"""Why a check cannot read the present state here: a forge down, as ADR 0060 names it."""
HALTED_WHY = "conflict: the rebase onto `main` was left unfinished"
"""The conflict close's reason, which a check that finds only a rebase left unfinished writes."""


def no_forge(_argv: list[str]) -> None:
    """``gh`` for a checkout with no ``origin``: the check never asks it."""


KILL_AT = "DRAIN_MODEL_KILL_AT"
# Why: the binding's `merge` entry point. It is merge.py, ended by a real SIGKILL at the point
# the environment names, so that what a killed run leaves is what merge.py itself wrote. Mid
# fast-forward it leaves what git does when killed there: files of the merge in the work tree,
# the first written part-way, and the lock on the index.
MERGE_KILLER = """\
import contextlib, io, os, signal, subprocess, sys, time
from pathlib import Path

sys.path.insert(0, {scripts!r})
import gatelib
import merge

AT = os.environ.get({kill_at!r})
HOLD = os.environ.get({hold_at!r})


def die(*_args, **_kwargs):
    os.kill(os.getpid(), signal.SIGKILL)


def cut_off(where, *args):
    if args[:2] != ("merge", "--ff-only"):
        return real_git(where, *args)
    old = real_git(where, "rev-parse", "HEAD")
    rows = [row for row in merge.blobs(where, old, args[-1]) if row[0] != "D"]
    for at, (_status, path, _was, blob) in enumerate(rows):
        body = subprocess.run(
            ["git", "-C", str(where), "cat-file", "blob", blob], capture_output=True, check=True
        ).stdout
        target = Path(where) / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(body[: len(body) // 2] if at == 0 else body)
    gitdir = Path(real_git(where, "rev-parse", "--absolute-git-dir"))
    (gitdir / "index.lock").write_text("")
    die()


def let_go(name):
    # Why: a test run that is killed never writes the file; the wait ends with its directory, or
    # in time, so no process is left running on a machine other work shares. `go` before the
    # merge is the test's end: the merge does not run.
    until = time.monotonic() + {hold_for!r}
    while Path(HOLD).is_dir() and time.monotonic() < until:
        if (Path(HOLD) / name).exists():
            return True
        if (Path(HOLD) / "go").exists():
            return False
        time.sleep(0.02)
    return False


real_git = gatelib.git
if AT == "check":
    merge.run_check = die
elif AT == "fast-forward":
    gatelib.git = cut_off
def held(check):
    def run(*args, **kwargs):
        (Path(HOLD) / "checking").touch()
        if not let_go("checked"):
            raise SystemExit(2)
        return check(*args, **kwargs)

    return run


real_retrying = merge.locked_retrying


@contextlib.contextmanager
def held_at_lock(path, tries, poll):
    with real_retrying(path, tries, poll) as taken:
        if taken is not None and (Path(HOLD) / "hold-lock").exists():
            (Path(HOLD) / "locked").touch()
            let_go("probe")
        yield taken


if HOLD:
    merge.run_check = held(merge.run_check)
    merge.locked_retrying = held_at_lock
    (Path(HOLD) / "pid").write_text(str(os.getpid()))
    (Path(HOLD) / "merging").touch()
    if not let_go("merge"):
        raise SystemExit(2)
    # Why: a merge whose land is gone prints into a pipe nobody reads; the test reads it here.
    said = io.StringIO()
    with contextlib.redirect_stdout(said):
        code = merge.main(sys.argv)
    (Path(HOLD) / "said").write_text(said.getvalue())
    (Path(HOLD) / "code").write_text(str(code))
    sys.stdout.write(said.getvalue())
    (Path(HOLD) / "landed").touch()
    let_go("go")
else:
    code = merge.main(sys.argv)
raise SystemExit(code)
"""
MERGE_KILLS = {
    Kind.KILL_BEFORE_FAST_FORWARD: "check",
    Kind.KILL_MID_FAST_FORWARD: "fast-forward",
}
# Why: with this set to a directory, the merge waits for a file `merge` there before it starts,
# so before it takes its merge lock and probes its land; for `checked` before its check runs,
# once judged and merged in its scratch tree; and once done for `go` before it exits, having
# written what it printed and returned to `said` and `code`. With a file `hold-lock` there, it
# also waits for `probe` once it holds its merge lock. So the `land` that runs it is held where it
# holds no drain lock, its landing lock held: while its merge runs, and between its merge and
# its close.
HOLD_AT = "DRAIN_MODEL_HOLD_AT"
LANDING_WAIT = 60  # seconds a held landing is given to finish its merge, or its close
DRIVER = Path(__file__).with_name("drain_driver.py")


def digest(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


class Killed(BaseException):
    """The command's process died here: nothing after this point of it runs."""


@dataclass(frozen=True)
class Seen:
    """What the repository shows, in the model's terms."""

    statuses: dict[Name, Status]
    start: tuple[Name, ...]
    tickets: tuple[Ticket, ...]
    scratch: bool
    half: Name | None


class World:
    """A repository the drain runs on, with no forge, as a session's commands leave it."""

    def __init__(self, directory: Path, check: str = "true", before_merge: str = "") -> None:
        """``before_merge``, when given, is a shell line the merge entry point runs before merge.py."""  # noqa: E501
        directory.mkdir(parents=True, exist_ok=True)
        killer = directory / "merge_killer.py"
        killer.write_text(
            MERGE_KILLER.format(
                scripts=str(SCRIPTS), kill_at=KILL_AT, hold_at=HOLD_AT, hold_for=2 * LANDING_WAIT
            )
        )
        stand_in = f"{sys.executable} {killer}"
        if before_merge:
            stand_in = f"sh -c '{before_merge}; exec \"$@\"' merge {stand_in}"
        merging = binding(stand_in, check)
        self.root = repository(directory / "repo", {**TEXTS, "docs/process/binding.md": merging})
        # Why: git's directory of a repository never moves, and each read-back would ask git again.
        self.landing_intent = merge.state_dir(self.root) / merge.LANDING
        self.token = ""
        self.printed = ""
        self.code = 0
        self.asked = False
        self.texts = dict(TEXTS)
        """Each ticket's text as filed, which its note's digest names until the owner edits it."""
        self.starts: dict[Name, publish_check.Start] = {}
        """The start the world recorded for a ticket while ``drain.py`` records none."""

    def preflight(self) -> contextlib.AbstractContextManager[object]:
        """The preflight as the upgrade leaves it: asking the ``Flag:`` line once it has run."""
        if not self.asked:
            return contextlib.nullcontext()
        return mock.patch.object(drain.preflight, "operability_asked", return_value=FLAG_ASKED)

    def command(self, *argv: str) -> int:
        """``drain.py argv`` as a session runs it; what it printed is kept."""
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err), self.preflight():
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
        real_git, real_write, real_discard = (
            gatelib.git,
            drain.queue_notes.write_note,
            drain.discard,
        )

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

        def discard_then_killed(*args: Path | str) -> None:
            real_discard(*args)
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
            Kind.KILL_LAND_AFTER_DISCARD: lambda: mock.patch.object(
                drain, "discard", discard_then_killed
            ),
        }
        if kind in MERGE_KILLS:
            return mock.patch.dict(os.environ, {KILL_AT: MERGE_KILLS[kind]})
        return killers[kind]()

    def perform(self, action: model.Action) -> None:
        """Do what ``action`` names."""
        kind, name = action.kind, action.ticket
        if kind is Kind.UPGRADE:
            self.asked = True
        elif kind is Kind.SPLIT and name is not None:
            self.split(name)
        elif name is None:
            self.command(*self.argv(kind, ""))
        elif kind in model.KILLS:
            with self.killer(kind, name):
                self.command(*self.argv(model.KILLS[kind], REF[name]))
        elif kind in COMMANDS:
            self.command(*self.argv(kind, REF[name]))
            self.restart(kind, name)
        elif kind is Kind.UPDATE_UNREADABLE:
            with self.unreadable():
                self.command(*self.argv(Kind.UPDATE, REF[name]))
        elif kind in CHECKS:
            self.check(kind, name)
        else:
            self.by_hand(kind, name)

    def noted_start(self, name: Name) -> publish_check.Start | None:
        """The start the note records, or the one the world recorded while the note holds none."""
        note = drain.queue_notes.read_note(self.root, BRANCH[name])
        if "start" in note:
            return publish_check.parse(json.dumps({"start": note["start"]}))
        return self.starts.get(name)

    def scripted(self, name: Name) -> bool:
        """Whether ``drain.py`` records the start in the ticket's note itself."""
        return "start" in drain.queue_notes.read_note(self.root, BRANCH[name])

    def restart(self, kind: Kind, name: Name) -> None:
        """The start the model says ``open``, or an ``update`` that moved the branch, records."""
        moved = kind is Kind.UPDATE and '"moved": true' in self.printed
        opened = kind is Kind.OPEN and self.code == 0
        if (moved or opened) and not self.scripted(name):
            self.starts[name] = publish_check.start_of(self.root, BRANCH[name], no_forge)

    def unreadable(self) -> contextlib.AbstractContextManager[object]:
        """The check of ADR 0060 meets a present state it cannot read."""
        refused = publish_check.UnreadableError(UNREADABLE)
        return mock.patch.object(publish_check, "branch_moves", side_effect=refused)

    def check(self, kind: Kind, name: Name) -> None:
        """``drain.py check``, or the real check and ``close`` standing in for it."""
        if kind is Kind.CHECK_HALTED:
            self.halt(name)
        if self.scripted(name):
            with self.unreadable() if kind is Kind.CHECK_UNREADABLE else contextlib.nullcontext():
                self.command("check", REF[name])
            return
        start, tree = self.starts[name], self.tree(name)
        assert tree in drain.worktrees(self.root), "a check with no tree needs drain.py check"
        try:
            with self.unreadable() if kind is Kind.CHECK_UNREADABLE else contextlib.nullcontext():
                moves, halted, _ = publish_check.check(tree, start, no_forge)
        except publish_check.UnreadableError as error:
            line = f"{publish_check.SAID} the present state cannot be read: {error}"
            self.command("close", REF[name], "--outcome", "waits", "--reason", line)
            return
        if moves:
            why = [arg for move in moves for arg in ("--reason", f"a role published: {move}")]
            self.command("close", REF[name], "--outcome", "parked", *why)
        elif halted:
            self.command("close", REF[name], "--outcome", "waits", "--reason", HALTED_WHY)

    def halt(self, name: Name) -> None:
        """A rejudge whose rebase conflicted, and whose reconcile stopped halfway through it.

        The model has no conflict, so the start is the one ``update`` records after one, and the
        rebase is stopped before its first commit, as ``killer`` stops one.
        """
        branch, tree = BRANCH[name], self.tree(name)
        start = publish_check.start_of(self.root, branch, no_forge, rebase_onto="main")
        if self.scripted(name):
            path = drain.queue_notes.note_path(self.root, branch)
            note = drain.queue_notes.read_note(self.root, branch)
            started = json.loads(json.dumps(dataclasses.asdict(start)))
            drain.queue_notes.write_note(path, {**note, "start": started})
        else:
            self.starts[name] = start
        git(tree, "-c", "sequence.editor=sed -i 1ibreak", "rebase", "-i", "main")

    def split(self, name: Name) -> None:
        """``drain.py split`` as the feature skill runs it, then the parts committed.

        The parts of a local ticket are files the checkout does not yet hold, and the plan starts
        nothing while it holds work uncommitted.
        """
        plan = self.root.parent / "plan.json"
        plan.write_text(json.dumps({"parts": HALVES}), encoding="utf-8")
        if self.command("split", REF[name], "--plan", str(plan)) != 0:
            return
        printed = self.printed
        for part in model.PARTS[name]:
            self.texts[REF[part]] = (self.root / REF[part]).read_text(encoding="utf-8")
        snapshot(self.root, f"docs(tracker): the parts of {REF[name]}")
        self.printed = printed

    def by_hand(self, kind: Kind, name: Name) -> None:
        """What the agent, the owner or git does, outside the drain."""
        ref, tree = REF[name], self.tree(name)
        if kind is Kind.WORK:
            write(tree, DRAFT, "half done\n")
        elif kind in {Kind.COMMIT, Kind.PUBLISH}:
            text = (tree / ref).read_text(encoding="utf-8")
            write(tree, ref, text.replace("ready-for-agent", "resolved"))
            built = f"src/{name.value}.py" if kind is Kind.COMMIT else ROLE_FILE
            write(tree, built, f"{name.name} = 1\n")
            snapshot(tree, CHANGE if kind is Kind.COMMIT else PUBLISHED)
        elif kind is Kind.RECORD:
            with contextlib.redirect_stdout(io.StringIO()):
                assert merge_ready.write_record(tree, json.dumps(approval(SLUG[name]))) == 0
            snapshot(tree, RECORD)
        elif kind is Kind.EDIT:
            text = (self.root / ref).read_text(encoding="utf-8")
            write(self.root, ref, text + ANSWER)
            snapshot(self.root, f"docs(tracker): the owner answers {ref}")
        elif kind is Kind.LINE:
            text = (self.root / ref).read_text(encoding="utf-8")
            write(self.root, ref, text + FLAG_LANDED)
            snapshot(self.root, f"docs(tracker): the owner lands the Flag: line of {ref}")
        elif kind is Kind.RESOLVE:
            text = (self.root / ref).read_text(encoding="utf-8")
            write(self.root, ref, text.replace(*DONE))
            snapshot(self.root, f"docs(tracker): the owner closes {ref}")
        elif kind is Kind.LOCK:
            self.lock_of(name).write_text("", encoding="utf-8")
        elif kind is Kind.UNLOCK:
            self.lock_of(name).unlink()

    def seen(self, ghost: model.State) -> Seen:
        """The repository read back; ``ghost`` gives what no file holds (the reviews, a role's commit)."""  # noqa: E501
        with self.preflight():
            drafted = drain.plan(drain.Drain(self.root, None, model.CAP))
        by_ref = {entry.ref: STATUSES[entry.state] for entry in drafted.tickets}
        statuses = {name: by_ref[ref] for name, ref in REF.items() if ref in by_ref}
        start = tuple(name for name, ref in REF.items() if ref in drafted.start)
        trees = drain.worktrees(self.root)
        refs = git(
            self.root,
            "for-each-ref",
            "--format=%(refname:short) %(objectname) %(ahead-behind:main) %(contents:subject)",
            "refs/heads/feature/",
        )
        rows = {line.split(" ", 1)[0]: line.split(" ", 4)[1:] for line in refs.splitlines()}
        branches = {branch: row[1:] for branch, row in rows.items()}
        stashes = git(self.root, "stash", "list", "--format=%gs").splitlines()
        tips = merged_tips(self.root)
        on_merge = {branch for branch, row in rows.items() if row[0] in tips}
        half = self.half()
        tickets = []
        for name, was in ghost.each():
            filed = self.root / REF[name]
            if not filed.exists():
                tickets.append(Ticket())
                continue
            written = filed.read_text(encoding="utf-8")
            closed = DONE[1] in written
            merged = name not in statuses and name is not half and not closed
            tree = self.tree_state(name) if self.tree(name) in trees else Tree.NONE
            listed = branches.get(BRANCH[name])
            landed = merged and BRANCH[name] in on_merge
            branch, current = self.branch_state(name, listed, landed=landed, was=was)
            text = int(ANSWER in written or FLAG_LANDED in written or closed)
            note = self.note(name, merged=merged, was=was.note)
            noted = self.noted_start(name)
            head = rows.get(BRANCH[name], [None])[0]
            ticket = Ticket(
                tree=tree,
                branch=branch,
                current=current,
                reviewed=was.reviewed,
                merged=merged,
                note=note,
                text=text,
                stashes=sum(f": {BRANCH[name]}: " in line for line in stashes),
                published=was.published,
                since=noted is not None and head != noted.head,
            )
            tickets.append(model.normal(ticket))
        listing = git(self.root, "worktree", "list", "--porcelain").splitlines()
        scratch = any(
            Path(line.removeprefix("worktree ")).parent.name.startswith(merge.SCRATCH)
            for line in listing
            if line.startswith("worktree ")
        )
        return Seen(statuses, start, tuple(tickets), scratch, half)

    def half(self) -> Name | None:
        """The ticket whose fast-forward the landing intent says was cut off, if one was."""
        landing = self.landing_intent
        intent = merge.intent_of(landing.read_bytes() if landing.exists() else b"")
        if not intent:
            return None
        subject = git(self.root, "log", "-1", "--format=%s", str(intent["new"]))
        return next(name for name, branch in BRANCH.items() if f"'{branch}'" in subject)

    def tree_state(self, name: Name) -> Tree:
        if self.lock_of(name).exists():
            return Tree.LOCKED
        if (self.gitdir(name) / "rebase-merge").is_dir():
            return Tree.REBASING
        return Tree.DIRTY if git(self.tree(name), "status", "--porcelain") else Tree.CLEAN

    def branch_state(
        self, name: Name, listed: list[str] | None, *, landed: bool, was: Ticket
    ) -> tuple[Branch, bool]:
        """The branch and whether it holds the default tip; one on its merge is as it landed."""
        if listed is None:
            return Branch.NONE, True
        if landed:
            return was.branch, was.current
        ahead, behind, subject = listed
        if ahead == "0":
            return Branch.BARE, behind == "0"
        if subject != RECORD:
            return Branch.DRAFT, behind == "0"
        branch = BRANCH[name]
        record = f"{branch}:docs/features/{SLUG[name]}/verdict.json"
        certified = json.loads(git(self.root, "show", record))["tree"]
        reviewed = git(self.root, "rev-parse", f"{branch}~1^{{tree}}")
        return (Branch.JUDGED if certified == reviewed else Branch.STALE), behind == "0"

    def note(self, name: Name, *, merged: bool, was: Note | None) -> Note | None:
        """The drain's note on the ticket; on a merged ticket its text is the model's."""
        held = drain.queue_notes.read_note(self.root, BRANCH[name])
        if not held:
            return None
        outcome = held.get("outcome")
        rebasing = bool(held.get("rebasing"))
        # Why: no file holds the mark until drain.py writes the start; the model's stands in.
        passed = held.get("passed") is True if "start" in held else bool(was and was.passed)
        if outcome is None:
            return Note(Outcome.OPENED, rebasing=rebasing, passed=passed)
        first = self.texts[REF[name]]
        texts = {digest(first): 0, digest(first + ANSWER): 1, digest(first + FLAG_LANDED): 1}
        written = texts.get(str(held.get("digest")), -1)
        attempts = held.get("attempts")
        outcomes = {"merged": Outcome.MERGED, "split": Outcome.SPLIT}
        return Note(
            outcomes.get(str(outcome), Outcome.CLOSED),
            hold=held.get("hold") is True,
            text=was.text if merged and was is not None else written,
            fresh=held.get("reported") is False,
            attempts=min(model.RETRIES, attempts if isinstance(attempts, int) else 0),
            rebasing=rebasing,
            discarding=held.get("discarding") is True,
            passed=passed,
        )


# --- the lease ---------------------------------------------------------------------------------

REAL_ANCESTRY = drain.ancestry
# The lease steps that are a drain command on the ticket, as the main graph's actions name them.
ON_THE_TICKET = {Step.OPEN: Kind.OPEN, Step.UPDATE: Kind.UPDATE, Step.PARK: Kind.CLOSE}


def running(pid: int) -> bool:
    """Whether process ``pid`` runs: one that has exited and waits to be reaped does not."""
    try:
        stat = Path(f"/proc/{pid}/stat").read_text(encoding="utf-8")
    except OSError:
        return False
    return stat.rpartition(")")[2].split()[0] != "Z"


def session() -> subprocess.Popen[bytes]:
    """A process that stands for a drain's session: the agent that runs its commands."""
    return subprocess.Popen(["sleep", "600"])  # noqa: S607 # -- a sleep, no shell


class LeaseWorld:
    """Two drains on one repository: each session a real process, the program a real one too.

    A drain's command runs in this process as its session would run it: ``drain.ancestry`` names
    the session's process above the command, so the real ``drain.rival`` reads real processes.
    A ``land`` runs in a process of its own and is held where it holds no drain lock: its merge
    waits to start, or is done and has not exited. So the commands of either drain meet a live
    landing and its lock, before its merge lands and after.
    """

    def __init__(self, directory: Path, check: str = "true", before_merge: str = "") -> None:
        self.directory = directory
        self.world = World(directory, check, before_merge)
        root = self.world.root
        # Why: the files of the lease and of the two locks never move, and each read-back would
        # ask git for their directory again.
        self.lease_file = drain.state_dir(root) / drain.LEASE
        self.landing_lock = drain.landing_path(root, BRANCH[Name.A])
        self.merge_lock = drain.merge.merge_lock_path(root, BRANCH[Name.A])
        self.sessions: dict[Drain, subprocess.Popen[bytes]] = {}
        self.program: subprocess.Popen[bytes] | None = None
        self.tokens: dict[Drain, str] = {}
        # The drain whose `land` is held, its process, and the directory its merge waits on.
        self.landing: tuple[Drain, subprocess.Popen[str], Path] | None = None
        # The drain whose `land` was killed alone past its merge's probe; that merge's pid and gate.
        self.orphan: tuple[Drain, int, Path] | None = None
        # The pid and gate of the merge of a `land` killed before its probe, still to start.
        self.doomed: tuple[int, Path] | None = None
        # What the last merge to stop printed and returned, read from its gate.
        self.stopped: tuple[int, str] | None = None
        # The owner's own runs of the merge entry point.
        self.hands: list[subprocess.Popen[bytes]] = []

    def chain(self, which: Drain) -> list[str]:
        session = self.sessions[which]
        row = drain.process_row(session.pid)
        return [f"{session.pid}:{row[1] if row else ''}", *REAL_ANCESTRY()]

    def command(self, which: Drain, *argv: str) -> str:
        with mock.patch.object(drain, "ancestry", return_value=self.chain(which)):
            self.world.command(*argv)
        return self.world.printed

    def land(self, which: Drain) -> None:
        """The agent's commits and its record, made once, then ``land`` up to the merge it runs."""
        if not drain.ahead(self.world.root, "main", BRANCH[Name.A]):
            self.world.by_hand(Kind.COMMIT, Name.A)
            self.world.by_hand(Kind.RECORD, Name.A)
        self.start_landing(which)

    def landing_argv(self) -> list[str]:
        root = str(self.world.root)
        return [sys.executable, str(DRIVER), "land", REF[Name.A], "--root", root, "--offline"]

    def land_killed_at_discard(self) -> int:
        """``land``, ended by SIGKILL where its close is about to remove the tree; its exit code."""
        return subprocess.run(  # noqa: S603 # -- this interpreter, no shell
            self.landing_argv(),
            capture_output=True,
            cwd=self.world.root,
            env={**os.environ, "DRV_KILL_AT": "discard"},
            check=False,
            timeout=LANDING_WAIT,
        ).returncode

    def land_held(self, which: Drain) -> None:
        """``land`` up to the end of its merge, whether the merge lands or is refused."""
        self.start_landing(which)
        self.merge_the_landing()

    def start_landing(self, which: Drain) -> None:
        """``land`` past its checks, its landing lock held, and its merge waiting to start."""
        gate = Path(tempfile.mkdtemp(prefix="landing-", dir=self.directory))
        with (gate / "printed").open("w", encoding="utf-8") as printed:
            process = subprocess.Popen(  # noqa: S603 # -- this interpreter, no shell
                self.landing_argv(),
                stdout=printed,
                stderr=subprocess.STDOUT,
                cwd=self.world.root,
                env={**os.environ, HOLD_AT: str(gate)},
            )
        self.landing = (which, process, gate)
        self.wait_for(gate / "merging", "the landing did not reach its merge")

    def merge_the_landing(self) -> None:
        """The held landing's merge, run to its end, whether it lands or is refused."""
        assert self.landing is not None
        (self.landing[2] / "merge").touch()
        (self.landing[2] / "checked").touch()
        self.wait_for(self.landing[2] / "landed", "the landing's merge did not finish")

    def wait_for(self, mark: Path, why: str) -> None:
        """Until the held landing's merge writes ``mark``, or the landing has exited."""
        assert self.landing is not None
        process = self.landing[1]
        deadline = time.monotonic() + LANDING_WAIT
        while not mark.exists() and process.poll() is None:
            assert time.monotonic() < deadline, why
            time.sleep(0.02)

    def close_the_landing(self) -> None:
        """What ``land`` does once its merge has exited: its close, under the drain lock."""
        assert self.landing is not None
        _, process, gate = self.landing
        if not (gate / "landed").exists():
            # Why: the merge of a killed land landed first; this one runs, and is refused.
            self.merge_the_landing()
        (gate / "go").touch()
        self.world.code = process.wait(timeout=LANDING_WAIT)
        self.world.printed = (gate / "printed").read_text(encoding="utf-8")
        self.landing = None

    def kill_the_landing(self) -> None:
        """SIGKILL to the ``land`` held, and to its merge if that has not run; one done exits."""
        assert self.landing is not None
        _, process, gate = self.landing
        process.kill()
        process.wait()
        if (gate / "pid").exists() and not (gate / "landed").exists():
            # Why: a kill of the land and its merge together, as a session's end or a shell's
            # timeout ends them, so no merge lands after the land that ran it is gone.
            with contextlib.suppress(ProcessLookupError):
                os.kill(int((gate / "pid").read_text(encoding="utf-8")), signal.SIGKILL)
        (gate / "go").touch()
        self.landing = None

    def past_the_probe(self) -> None:
        """The held landing's merge, let on to its check: it holds its lock and has probed."""
        assert self.landing is not None
        (self.landing[2] / "merge").touch()
        self.wait_for(self.landing[2] / "checking", "the landing's merge did not reach its check")

    def hand_merge(self, *, hold: bool) -> tuple[int, Path]:
        """The owner's own run of the merge entry point on the branch; its pid and its gate.

        It sets no ``RATCHET_LANDING``. Held, it waits at its check, holding its merge lock;
        otherwise it waits to start.
        """
        gate = Path(tempfile.mkdtemp(prefix="landing-hand-", dir=self.directory))
        env = {key: value for key, value in os.environ.items() if key != merge.LANDING_VAR}
        killer = self.directory / "merge_killer.py"
        process = subprocess.Popen(  # noqa: S603 # -- this interpreter, no shell
            [sys.executable, str(killer), BRANCH[Name.A]],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            cwd=self.world.root,
            env={**env, HOLD_AT: str(gate)},
        )
        self.hands.append(process)
        deadline = time.monotonic() + LANDING_WAIT
        mark = gate / ("checking" if hold else "merging")
        if hold:
            (gate / "merge").touch()
        while not mark.exists():
            assert time.monotonic() < deadline, "the hand merge did not start"
            assert not (gate / "landed").exists(), (gate / "said").read_text(encoding="utf-8")
            # Why: a merge that died before its mark never writes it; the mark is read again
            # since it may have come just before the exit.
            assert process.poll() is None or mark.exists(), "the hand merge ended before its mark"
            time.sleep(0.02)
        return process.pid, gate

    def kill_the_land_alone(self) -> None:
        """SIGKILL to the ``land`` held, by its pid alone, once its merge is judged and checking."""
        assert self.landing is not None
        which, process, gate = self.landing
        self.past_the_probe()
        pid = int((gate / "pid").read_text(encoding="utf-8"))
        process.kill()
        process.wait()
        assert running(pid), "the merge of the killed land runs on"
        self.orphan, self.landing = (which, pid, gate), None

    def let_the_orphan_run(self) -> None:
        """The killed ``land``'s merge, run on from its check: it lands or is refused."""
        assert self.orphan is not None
        _, pid, gate = self.orphan
        (gate / "checked").touch()
        deadline = time.monotonic() + LANDING_WAIT
        while not (gate / "landed").exists() and running(pid):
            assert time.monotonic() < deadline, "the merge of the killed land did not finish"
            time.sleep(0.02)
        (gate / "go").touch()
        self.end_the_orphan()

    def end_the_orphan(self) -> None:
        """SIGKILL to the merge of the killed ``land``, by its pid, if it still runs."""
        assert self.orphan is not None
        pid = self.orphan[1]
        with contextlib.suppress(ProcessLookupError):
            os.kill(pid, signal.SIGKILL)
        deadline = time.monotonic() + LANDING_WAIT
        while running(pid):
            assert time.monotonic() < deadline, f"process {pid} did not end"
            time.sleep(0.02)
        self.orphan = None

    def kill_the_land_at_start(self) -> None:
        """SIGKILL to the ``land`` held, by its pid alone, while its merge waits to start."""
        assert self.landing is not None
        _, process, gate = self.landing
        pid = int((gate / "pid").read_text(encoding="utf-8"))
        process.kill()
        process.wait()
        assert running(pid), "the merge of the killed land waits to start"
        self.doomed, self.landing = (pid, gate), None

    def run_out(self, pid: int, gate: Path) -> None:
        """Let the merge held at ``gate`` run to its end; what it printed and returned."""
        for mark in ("merge", "checked", "probe"):
            (gate / mark).touch()
        deadline = time.monotonic() + LANDING_WAIT
        while not (gate / "landed").exists() and running(pid):
            assert time.monotonic() < deadline, "the merge did not finish"
            time.sleep(0.02)
        (gate / "go").touch()
        while running(pid):
            assert time.monotonic() < deadline, f"process {pid} did not end"
            time.sleep(0.02)
        code = gate / "code"
        said = gate / "said"
        self.stopped = (
            int(code.read_text(encoding="utf-8")) if code.exists() else -signal.SIGKILL,
            said.read_text(encoding="utf-8") if said.exists() else "",
        )

    def doomed_stops(self) -> None:
        """The merge of a ``land`` killed before its probe, run: it finds its land gone."""
        assert self.doomed is not None
        self.run_out(*self.doomed)
        self.doomed = None

    def locked_out(self) -> None:
        """The doomed merge holds its lock past the live landing's merge's tries; both stop.

        The doomed merge is held once it has taken the merge lock and before it probes. The live
        landing's merge then runs, finds the lock held, and stops; its ``land`` exits. Then the
        doomed merge probes and stops.
        """
        assert self.doomed is not None
        assert self.landing is not None
        pid, gate = self.doomed
        (gate / "hold-lock").touch()
        (gate / "merge").touch()
        deadline = time.monotonic() + LANDING_WAIT
        while not (gate / "locked").exists():
            assert time.monotonic() < deadline, "the doomed merge did not take its lock"
            assert running(pid), "the doomed merge ended before it took its lock"
            time.sleep(0.02)
        self.close_the_landing()
        self.doomed_stops()

    def perform(self, said: str) -> None:
        """Do what the lease step ``said`` names."""
        if said in {Step.SEPARATE.value, Step.SHARED.value}:
            first = session()
            self.sessions = {
                Drain.ONE: first,
                Drain.TWO: first if said == Step.SHARED.value else session(),
            }
            return
        if said == Step.PROGRAM_ENDS.value:
            assert self.program is not None
            self.program.kill()
            self.program.wait()
            return
        if said == Step.DOOMED_STOPS.value:
            self.doomed_stops()
            return
        which, step = model.LEASE_STEPS[said]
        if step is Step.ORPHAN_MERGES:
            self.let_the_orphan_run()
            return
        if step is Step.ORPHAN_ENDS:
            self.end_the_orphan()
            return
        # Why: a `land` while a landing is seen is refused at once, so it runs as the other
        # commands do.
        second = step is Step.LAND and (self.landing is not None or self.orphan is not None)
        if second or step in {*model.CLEANS, *model.REPORTS, *ON_THE_TICKET, Step.CLEAN}:
            self.drain_command(which, step)
        else:
            self.by_hand(which, step)

    def drain_command(self, which: Drain, step: Step) -> None:
        token = ["--drain", self.tokens[which]] if step in model.OWN else []
        if step in model.CLEANS:
            over = ["--take-over"] if step is Step.TAKE_OVER else []
            self.command(which, "clean", "--all", *token, *over)
            if self.world.code == 0:
                self.tokens[which] = self.world.token
        elif step in model.REPORTS:
            self.command(which, "report", *token)
        elif step is Step.CLEAN:
            self.command(which, "clean")
        else:
            self.command(which, *self.world.argv(ON_THE_TICKET.get(step, Kind.LAND), REF[Name.A]))

    def by_hand(self, which: Drain, step: Step) -> None:
        """What a drain's agent or its session does, and ``land`` cut where it holds no lock."""
        tree = self.world.tree(Name.A)
        landing = {
            Step.MERGES: self.merge_the_landing,
            Step.LAND_REST: self.close_the_landing,
            Step.LAND_KILLED: self.kill_the_landing,
            Step.MERGE_KILLED: self.kill_the_landing,
            Step.ORPHANED: self.kill_the_land_alone,
            Step.KILLED_AT_START: self.kill_the_land_at_start,
            Step.LOCKED_OUT: self.locked_out,
        }
        if step is Step.LAND:
            self.land(which)
        elif step in landing:
            landing[step]()
        elif step is Step.DRAFT:
            write(tree, DRAFT, "half done\n")
        elif step is Step.PROGRAM:
            self.program = servers.sleeper(tree)
        elif step is Step.DIE:
            self.sessions[which].kill()
            self.sessions[which].wait()
            # Why: a landing is a command of its session, and ends with it.
            if self.landing is not None and self.sessions[self.landing[0]].poll() is not None:
                self.kill_the_landing()

    def seen(self, ghost: Lease) -> Lease:
        """The repository and the processes read back; ``ghost`` gives what no file holds."""
        root, lease = self.world.root, self.lease_file
        held = json.loads(lease.read_text(encoding="utf-8")) if lease.exists() else {}
        holder = next((d for d, token in self.tokens.items() if token == held.get("token")), None)
        tree = self.world.tree(Name.A)
        made = tree in drain.worktrees(root)
        note = drain.queue_notes.read_note(root, BRANCH[Name.A])
        branch = f"refs/heads/{BRANCH[Name.A]}"
        refs = git(
            root,
            "for-each-ref",
            "--format=%(refname) %(objectname) %(ahead-behind:main)",
            "refs/stash",
            branch,
        )
        rows = {ref: row for ref, _, row in (line.partition(" ") for line in refs.splitlines())}
        tip, ahead, _ = rows[branch].split() if branch in rows else ("", "0", "0")
        merge = (
            Merge.NONE
            if not tip or tip not in merged_tips(root)
            else Merge.UNSAID
            if note.get("outcome") != "merged"
            else Merge.SAID
            if note.get("reported") is False
            else Merge.PRINTED
        )
        # Why: the landing the model names must hold its lock. It is read here and not by
        # `drain.landing_running`, so a fault in that reading is not a fault in this one too.
        with drain.merge.locked(self.landing_lock) as free:
            live = self.landing is not None and free is None
        merging = lock_held(self.merge_lock)
        orphan = self.orphan[0] if self.orphan is not None and running(self.orphan[1]) else None
        return ghost._replace(
            holder=holder,
            alive=(
                self.sessions[Drain.ONE].poll() is None,
                self.sessions[Drain.TWO].poll() is None,
            ),
            took=(Drain.ONE in self.tokens, Drain.TWO in self.tokens),
            tree=ghost.tree if made else None,
            draft=made and bool(git(tree, "status", "--porcelain")),
            running=self.program is not None and self.program.poll() is None,
            stashed="refs/stash" in rows,
            landing=self.landing[0] if self.landing is not None and live else None,
            orphan=orphan if merging else None,
            doomed=self.doomed is not None and running(self.doomed[0]),
            merge=merge,
            built=merge is not Merge.NONE or ahead != "0",
            held=note.get("hold") is True,
        )

    def close(self) -> None:
        """End every process this world started, then let every merge held go, and wait for it."""
        if self.orphan is not None:
            self.end_the_orphan()
        held = [self.landing[1]] if self.landing else []
        for process in [*self.sessions.values(), *([self.program] if self.program else []), *held]:
            if process.poll() is None:
                process.kill()
            process.wait()
        gates = list(self.directory.glob("landing-*"))
        for gate in gates:
            (gate / "go").touch()
        for gate in gates:
            if (gate / "pid").exists():
                stop(int((gate / "pid").read_text(encoding="utf-8")))
        for hand in self.hands:
            hand.wait()
        self.doomed = None


def stop(pid: int) -> None:
    """Until process ``pid`` has ended: it is let go, and killed by its pid if it lingers."""
    deadline = time.monotonic() + 5
    while running(pid) and time.monotonic() < deadline:
        time.sleep(0.02)
    with contextlib.suppress(ProcessLookupError):
        os.kill(pid, signal.SIGKILL)
    while running(pid):
        time.sleep(0.02)


def merged_tips(root: Path) -> set[str]:
    """The tips merged into ``main``, as ``route.merged_features`` counts them.

    Each is the second parent of a merge on the first-parent line of ``main``.
    """
    merges = git(root, "rev-list", "--merges", "--first-parent", "--parents", "main")
    return {parent for line in merges.splitlines() for parent in line.split()[2:]}


def lock_held(path: Path) -> bool:
    """Whether a process holds the lock of ``path``, read here and not by the scripts' reader."""
    try:
        descriptor = os.open(path, os.O_RDONLY)
    except FileNotFoundError:
        return False
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        return True
    finally:
        os.close(descriptor)
    return False
