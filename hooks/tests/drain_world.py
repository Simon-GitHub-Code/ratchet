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
from tests.route_world import ASKED_TYPES
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
ANSWER = "\n## Answers\n\nWhich case? The lower.\n"
NO_FLAG = (
    "\n## Operability\n\nFlag: none: the owner chose to have it appear as soon as it is"
    " finished, while the project is not deployed\n"
)
FLAG_LANDED = "\n## Operability\n\nFlag: `alpha`, off until the owner switches it on\n"
TEXTS = {**FILES, **WHOLE, BETA: FILES[BETA] + NO_FLAG, GAMMA: FILES[GAMMA] + NO_FLAG}
BINDING = "docs/process/binding.md"
CHANGE = "feat(core): the change"
RECORD = "docs(core): the verdict record"
PUBLISHED = "feat(core): a role's own commit"
ROLE_FILE = "src/role.py"
DRAFT = "src/draft.py"
SLUG = {Name.A: "alpha", Name.D: "save", Name.E: "list"}
STATUSES = {status.value: status for status in Status}
COMMANDS = frozenset({Kind.OPEN, Kind.UPDATE, Kind.LAND, Kind.CLOSE})
NOT_FINAL = frozenset({Kind.CHECK_NOT_FINAL, Kind.CHECK_HALTED_NOT_FINAL})
CHECKS = frozenset({Kind.CHECK, Kind.CHECK_UNREADABLE, Kind.CHECK_HALTED, *NOT_FINAL})
UNREADABLE = "the forge cannot be reached"
HALTED_WHY = "conflict: the rebase onto `main` was left unfinished"


def no_forge(_argv: list[str]) -> None: ...


KILL_AT = "DRAIN_MODEL_KILL_AT"
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


def cut_off(where, *args, **options):
    if args[:2] != ("merge", "--ff-only"):
        return real_git(where, *args, **options)
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
HOLD_AT = "DRAIN_MODEL_HOLD_AT"
LANDING_WAIT = 60
DRIVER = Path(__file__).with_name("drain_driver.py")


def digest(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


class Killed(BaseException): ...


@dataclass(frozen=True)
class Seen:
    statuses: dict[Name, Status]
    start: tuple[Name, ...]
    tickets: tuple[Ticket, ...]
    scratch: bool
    half: Name | None


class World:
    def __init__(self, directory: Path, check: str = "true", before_merge: str = "") -> None:
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
        self.binding = binding(stand_in, check)
        self.root = repository(directory / "repo", {**TEXTS, BINDING: self.binding})
        self.landing_intent = merge.state_dir(self.root) / merge.LANDING
        self.token = ""
        self.printed = ""
        self.code = 0
        self.texts = dict(TEXTS)

    def upgrade(self) -> None:
        pin = "- Default branch: `main`\n"
        write(self.root, BINDING, self.binding.replace(pin, pin + ASKED_TYPES))
        snapshot(self.root, "chore(method): the project is a command, not yet deployed")

    def command(self, *argv: str) -> int:
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
        pointer = (self.tree(name) / ".git").read_text(encoding="utf-8")
        return Path(pointer.removeprefix("gitdir:").strip())

    def lock_of(self, name: Name) -> Path:
        return self.gitdir(name) / "index.lock"

    def argv(self, kind: Kind, ref: str) -> list[str]:
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
        real_git, real_write, real_discard = (
            gatelib.git,
            drain.queue_notes.write_note,
            drain.discard,
        )

        def git_until(stop: tuple[str, str]) -> Callable[..., str | None]:
            def run(root: Path, *args: str, **options: str) -> str | None:
                if args[:2] != stop:
                    return real_git(root, *args, **options)
                if stop[0] == "rebase":
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
        kind, name = action.kind, action.ticket
        if kind is Kind.UPGRADE:
            self.upgrade()
        elif kind is Kind.SPLIT and name is not None:
            self.split(name)
        elif name is None:
            self.command(*self.argv(kind, ""))
        elif kind in model.KILLS:
            with self.killer(kind, name):
                self.command(*self.argv(model.KILLS[kind], REF[name]))
        elif kind in COMMANDS:
            self.command(*self.argv(kind, REF[name]))
        elif kind is Kind.UPDATE_UNREADABLE:
            with self.unreadable():
                self.command(*self.argv(Kind.UPDATE, REF[name]))
        elif kind in CHECKS:
            self.check(kind, name)
        else:
            self.by_hand(kind, name)

    def noted_start(self, name: Name) -> publish_check.Start | None:
        note = drain.queue_notes.read_note(self.root, BRANCH[name])
        if "start" not in note:
            return None
        return publish_check.parse(json.dumps({"start": note["start"]}))

    def unreadable(self) -> contextlib.AbstractContextManager[object]:
        refused = publish_check.UnreadableError(UNREADABLE)
        return mock.patch.object(publish_check, "branch_moves", side_effect=refused)

    def check(self, kind: Kind, name: Name) -> None:
        if kind in {Kind.CHECK_HALTED, Kind.CHECK_HALTED_NOT_FINAL}:
            self.halt(name)
        final = [] if kind in NOT_FINAL else ["--final"]
        with self.unreadable() if kind is Kind.CHECK_UNREADABLE else contextlib.nullcontext():
            self.command("check", REF[name], *final)

    def halt(self, name: Name) -> None:
        branch, tree = BRANCH[name], self.tree(name)
        start = publish_check.start_of(self.root, branch, no_forge, rebase_onto="main")
        path = drain.queue_notes.note_path(self.root, branch)
        note = drain.queue_notes.read_note(self.root, branch)
        started = json.loads(json.dumps(dataclasses.asdict(start)))
        drain.queue_notes.write_note(path, {**note, "start": started})
        git(tree, "-c", "sequence.editor=sed -i 1ibreak", "rebase", "-i", "main")

    def split(self, name: Name) -> None:
        plan = self.root.parent / "plan.json"
        plan.write_text(json.dumps({"parts": HALVES}), encoding="utf-8")
        if self.command("split", REF[name], "--plan", str(plan)) != 0:
            return
        for part in model.PARTS[name]:
            self.texts[REF[part]] = (self.root / REF[part]).read_text(encoding="utf-8")

    def by_hand(self, kind: Kind, name: Name) -> None:
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
        held = drain.queue_notes.read_note(self.root, BRANCH[name])
        if not held:
            return None
        outcome = held.get("outcome")
        rebasing = bool(held.get("rebasing"))
        passed = held.get("passed") is True
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


REAL_ANCESTRY = drain.ancestry
ON_THE_TICKET = {Step.OPEN: Kind.OPEN, Step.UPDATE: Kind.UPDATE, Step.PARK: Kind.CLOSE}


def running(pid: int) -> bool:
    try:
        stat = Path(f"/proc/{pid}/stat").read_text(encoding="utf-8")
    except OSError:
        return False
    return stat.rpartition(")")[2].split()[0] != "Z"


def session() -> subprocess.Popen[bytes]:
    return subprocess.Popen(["sleep", "600"])  # noqa: S607 # -- a sleep, no shell


class LeaseWorld:
    def __init__(self, directory: Path, check: str = "true", before_merge: str = "") -> None:
        self.directory = directory
        self.world = World(directory, check, before_merge)
        root = self.world.root
        self.lease_file = drain.state_dir(root) / drain.LEASE
        self.landing_lock = drain.landing_path(root, BRANCH[Name.A])
        self.merge_lock = drain.merge.merge_lock_path(root, BRANCH[Name.A])
        self.sessions: dict[Drain, subprocess.Popen[bytes]] = {}
        self.program: subprocess.Popen[bytes] | None = None
        self.tokens: dict[Drain, str] = {}
        self.landing: tuple[Drain, subprocess.Popen[str], Path] | None = None
        self.orphan: tuple[Drain, int, Path] | None = None
        self.doomed: tuple[int, Path] | None = None
        self.stopped: tuple[int, str] | None = None
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
        if not drain.ahead(self.world.root, "main", BRANCH[Name.A]):
            self.world.command("check", REF[Name.A], "--final")
            self.world.by_hand(Kind.COMMIT, Name.A)
            self.world.by_hand(Kind.RECORD, Name.A)
        self.start_landing(which)

    def landing_argv(self) -> list[str]:
        root = str(self.world.root)
        return [sys.executable, str(DRIVER), "land", REF[Name.A], "--root", root, "--offline"]

    def land_killed_at_discard(self) -> int:
        return subprocess.run(  # noqa: S603 # -- this interpreter, no shell
            self.landing_argv(),
            capture_output=True,
            cwd=self.world.root,
            env={**os.environ, "DRV_KILL_AT": "discard"},
            check=False,
            timeout=LANDING_WAIT,
        ).returncode

    def land_held(self, which: Drain) -> None:
        self.start_landing(which)
        self.merge_the_landing()

    def start_landing(self, which: Drain) -> None:
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
        assert self.landing is not None
        (self.landing[2] / "merge").touch()
        (self.landing[2] / "checked").touch()
        self.wait_for(self.landing[2] / "landed", "the landing's merge did not finish")

    def wait_for(self, mark: Path, why: str) -> None:
        assert self.landing is not None
        process = self.landing[1]
        deadline = time.monotonic() + LANDING_WAIT
        while not mark.exists() and process.poll() is None:
            assert time.monotonic() < deadline, why
            time.sleep(0.02)

    def close_the_landing(self) -> None:
        assert self.landing is not None
        _, process, gate = self.landing
        if not (gate / "landed").exists():
            self.merge_the_landing()
        (gate / "go").touch()
        self.world.code = process.wait(timeout=LANDING_WAIT)
        self.world.printed = (gate / "printed").read_text(encoding="utf-8")
        self.landing = None

    def kill_the_landing(self) -> None:
        assert self.landing is not None
        _, process, gate = self.landing
        process.kill()
        process.wait()
        if (gate / "pid").exists() and not (gate / "landed").exists():
            with contextlib.suppress(ProcessLookupError):
                os.kill(int((gate / "pid").read_text(encoding="utf-8")), signal.SIGKILL)
        (gate / "go").touch()
        self.landing = None

    def past_the_probe(self) -> None:
        assert self.landing is not None
        (self.landing[2] / "merge").touch()
        self.wait_for(self.landing[2] / "checking", "the landing's merge did not reach its check")

    def hand_merge(self, *, hold: bool) -> tuple[int, Path]:
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
            assert process.poll() is None or mark.exists(), "the hand merge ended before its mark"
            time.sleep(0.02)
        return process.pid, gate

    def kill_the_land_alone(self) -> None:
        assert self.landing is not None
        which, process, gate = self.landing
        self.past_the_probe()
        pid = int((gate / "pid").read_text(encoding="utf-8"))
        process.kill()
        process.wait()
        assert running(pid), "the merge of the killed land runs on"
        self.orphan, self.landing = (which, pid, gate), None

    def let_the_orphan_run(self) -> None:
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
        assert self.landing is not None
        _, process, gate = self.landing
        pid = int((gate / "pid").read_text(encoding="utf-8"))
        process.kill()
        process.wait()
        assert running(pid), "the merge of the killed land waits to start"
        self.doomed, self.landing = (pid, gate), None

    def run_out(self, pid: int, gate: Path) -> None:
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
        assert self.doomed is not None
        self.run_out(*self.doomed)
        self.doomed = None

    def locked_out(self) -> None:
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
            if self.landing is not None and self.sessions[self.landing[0]].poll() is not None:
                self.kill_the_landing()

    def seen(self, ghost: Lease) -> Lease:
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
    deadline = time.monotonic() + 5
    while running(pid) and time.monotonic() < deadline:
        time.sleep(0.02)
    with contextlib.suppress(ProcessLookupError):
        os.kill(pid, signal.SIGKILL)
    while running(pid):
        time.sleep(0.02)


def merged_tips(root: Path) -> set[str]:
    merges = git(root, "rev-list", "--merges", "--first-parent", "--parents", "main")
    return {parent for line in merges.splitlines() for parent in line.split()[2:]}


def lock_held(path: Path) -> bool:
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
