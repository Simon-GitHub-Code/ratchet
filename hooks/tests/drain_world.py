"""The drain model's actions played on a real repository by the real ``drain.py`` and ``merge.py``.

The repository is the one ``test_drain`` builds: three local tickets, ``Beta`` blocked by
``Alpha``, a binding whose ``merge`` entry point runs ``merge.py`` and whose ``check`` is green.
``merge.py`` runs as it does in a drain, in a process of its own, which a kill ends with SIGKILL.
Each command runs as a session runs it, ``drain.main`` with no tracker; the agent's commits are
the commits the feature skill makes, and the owner's edit is an answer on the default branch.
After each action ``seen`` reads the repository back in the model's terms.
"""

from __future__ import annotations

import contextlib
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
    SCRIPTS,
    approval,
    binding,
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
KILL_AT = "DRAIN_MODEL_KILL_AT"
# Why: the binding's `merge` entry point. It is merge.py, ended by a real SIGKILL at the point
# the environment names, so that what a killed run leaves is what merge.py itself wrote. Mid
# fast-forward it leaves what git does when killed there: files of the merge in the work tree,
# the first written part-way, and the lock on the index.
MERGE_KILLER = """\
import os, signal, subprocess, sys, time
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


real_git = gatelib.git
if AT == "check":
    merge.run_check = die
elif AT == "fast-forward":
    gatelib.git = cut_off
code = merge.main(sys.argv)
if HOLD:
    (Path(HOLD) / "landed").touch()
    # Why: a test run that is killed never writes `go`; the wait ends with its directory, or
    # in time, so no process is left running on a machine other work shares.
    until = time.monotonic() + {hold_for!r}
    while Path(HOLD).is_dir() and not (Path(HOLD) / "go").exists() and time.monotonic() < until:
        time.sleep(0.02)
raise SystemExit(code)
"""
MERGE_KILLS = {
    Kind.KILL_BEFORE_FAST_FORWARD: "check",
    Kind.KILL_MID_FAST_FORWARD: "fast-forward",
}
# Why: with this set to a directory, the merge, once done, waits for a file `go` there before it
# exits, so the `land` that runs it is held between its merge and its close, its landing lock held.
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
    tickets: tuple[Ticket, Ticket, Ticket]
    scratch: bool
    half: Name | None


class World:
    """A repository the drain runs on, with no forge, as a session's commands leave it."""

    def __init__(self, directory: Path) -> None:
        directory.mkdir(parents=True, exist_ok=True)
        killer = directory / "merge_killer.py"
        killer.write_text(
            MERGE_KILLER.format(
                scripts=str(SCRIPTS), kill_at=KILL_AT, hold_at=HOLD_AT, hold_for=2 * LANDING_WAIT
            )
        )
        merging = binding(f"{sys.executable} {killer}")
        self.root = repository(directory / "repo", {**FILES, "docs/process/binding.md": merging})
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
        on_merge = drain.route.merged_features(self.root, "main")
        half = self.half()
        tickets = []
        for name, was in ghost.each():
            merged = name not in statuses and name is not half
            tree = self.tree_state(name) if self.tree(name) in trees else Tree.NONE
            listed = branches.get(BRANCH[name])
            landed = merged and BRANCH[name] in on_merge
            branch, current = self.branch_state(name, listed, landed=landed, was=was)
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
        listing = git(self.root, "worktree", "list", "--porcelain").splitlines()
        scratch = any(
            Path(line.removeprefix("worktree ")).parent.name.startswith(merge.SCRATCH)
            for line in listing
            if line.startswith("worktree ")
        )
        return Seen(statuses, start, (tickets[0], tickets[1], tickets[2]), scratch, half)

    def half(self) -> Name | None:
        """The ticket whose fast-forward the landing intent says was cut off, if one was."""
        landing = merge.state_dir(self.root) / merge.LANDING
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
            discarding=held.get("discarding") is True,
        )


# --- the lease ---------------------------------------------------------------------------------

REAL_ANCESTRY = drain.ancestry


def session() -> subprocess.Popen[bytes]:
    """A process that stands for a drain's session: the agent that runs its commands."""
    return subprocess.Popen(["sleep", "600"])  # noqa: S607 # -- a sleep, no shell


class LeaseWorld:
    """Two drains on one repository: each session a real process, the program a real one too.

    A drain's command runs in this process as its session would run it: ``drain.ancestry`` names
    the session's process above the command, so the real ``drain.rival`` reads real processes.
    A ``land`` runs in a process of its own and is held where it holds no drain lock: its merge
    is done and has not exited, so the other drain's commands meet a live landing and its lock.
    """

    def __init__(self, directory: Path) -> None:
        self.directory = directory
        self.world = World(directory)
        self.sessions: dict[Drain, subprocess.Popen[bytes]] = {}
        self.program: subprocess.Popen[bytes] | None = None
        self.tokens: dict[Drain, str] = {}
        # The drain whose `land` is held, its process, and the directory its merge waits on.
        self.landing: tuple[Drain, subprocess.Popen[str], Path] | None = None

    def chain(self, which: Drain) -> list[str]:
        session = self.sessions[which]
        row = drain.process_row(session.pid)
        return [f"{session.pid}:{row[1] if row else ''}", *REAL_ANCESTRY()]

    def command(self, which: Drain, *argv: str) -> str:
        with mock.patch.object(drain, "ancestry", return_value=self.chain(which)):
            self.world.command(*argv)
        return self.world.printed

    def land(self, which: Drain) -> None:
        """The agent's commits and its verdict record, then ``land`` up to the end of its merge."""
        self.world.by_hand(Kind.COMMIT, Name.A)
        self.world.by_hand(Kind.RECORD, Name.A)
        gate = Path(tempfile.mkdtemp(prefix="landing-", dir=self.directory))
        root = str(self.world.root)
        with (gate / "printed").open("w", encoding="utf-8") as printed:
            process = subprocess.Popen(  # noqa: S603 # -- this interpreter, no shell
                [sys.executable, str(DRIVER), "land", REF[Name.A], "--root", root, "--offline"],
                stdout=printed,
                stderr=subprocess.STDOUT,
                cwd=root,
                env={**os.environ, HOLD_AT: str(gate)},
            )
        self.landing = (which, process, gate)
        deadline = time.monotonic() + LANDING_WAIT
        while not (gate / "landed").exists() and process.poll() is None:
            assert time.monotonic() < deadline, "the landing's merge did not finish"
            time.sleep(0.02)

    def close_the_landing(self) -> None:
        """What ``land`` does once its merge has exited: its close, under the drain lock."""
        assert self.landing is not None
        _, process, gate = self.landing
        (gate / "go").touch()
        self.world.code = process.wait(timeout=LANDING_WAIT)
        self.world.printed = (gate / "printed").read_text(encoding="utf-8")
        self.landing = None

    def kill_the_landing(self) -> None:
        """SIGKILL to the ``land`` held; its merge, left with no parent, is let go to exit."""
        assert self.landing is not None
        _, process, gate = self.landing
        process.kill()
        process.wait()
        (gate / "go").touch()
        self.landing = None

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
        which, step = model.LEASE_STEPS[said]
        if step in {*model.CLEANS, *model.REPORTS, Step.CLEAN, Step.OPEN}:
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
            self.command(which, "open", REF[Name.A])

    def by_hand(self, which: Drain, step: Step) -> None:
        """What a drain's agent or its session does, and ``land`` cut in two."""
        tree = self.world.tree(Name.A)
        if step is Step.LAND:
            self.land(which)
        elif step is Step.LAND_REST:
            self.close_the_landing()
        elif step is Step.LAND_KILLED:
            self.kill_the_landing()
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
        lease = drain.state_dir(self.world.root) / drain.LEASE
        held = json.loads(lease.read_text(encoding="utf-8")) if lease.exists() else {}
        holder = next((d for d, token in self.tokens.items() if token == held.get("token")), None)
        tree = self.world.tree(Name.A)
        made = tree in drain.worktrees(self.world.root)
        note = drain.queue_notes.read_note(self.world.root, BRANCH[Name.A])
        merge = (
            Merge.NONE
            if BRANCH[Name.A] not in drain.route.merged_features(self.world.root, "main")
            else Merge.UNSAID
            if note.get("outcome") != "merged"
            else Merge.SAID
            if note.get("reported") is False
            else Merge.PRINTED
        )
        # Why: the landing the model names must hold its lock. It is read here and not by
        # `drain.landing_running`, so a fault in that reading is not a fault in this one too.
        with drain.merge.locked(drain.landing_path(self.world.root, BRANCH[Name.A])) as free:
            live = self.landing is not None and free is None
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
            stashed=bool(git(self.world.root, "stash", "list")),
            landing=self.landing[0] if self.landing is not None and live else None,
            merge=merge,
        )

    def close(self) -> None:
        """End every process this world started, then let every merge held go."""
        held = [self.landing[1]] if self.landing else []
        for process in [*self.sessions.values(), *([self.program] if self.program else []), *held]:
            if process.poll() is None:
                process.kill()
            process.wait()
        for gate in self.directory.glob("landing-*"):
            (gate / "go").touch()
