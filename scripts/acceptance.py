#!/usr/bin/env python3
"""The acceptance harness: boot the work tree, put a logging proxy before it, hold a record to it.

A tester who sees only the ticket and an address tries the product the way its owner would and
writes a record. This script starts the product, logs every request the tester makes, and then
holds the record to that log, so a verdict rests on requests the harness saw and not on what the
tester says it did. For the application types ``service`` and ``web-ui``; any other type is
refused before anything starts.

Usage::

    acceptance.py up      --slug S --ticket TICKET.md [--root R]
    acceptance.py down    --slug S [--root R]
    acceptance.py check   --slug S [--root R] [--commit C] [--seal D]
    acceptance.py replay  --slug S [--root R] [--control]
    acceptance.py replay  --checks FILE [--root R] [--control]
    acceptance.py summary --slug S [--root R]
    acceptance.py audit   --transcript FILE [--root R]

``up`` boots the work tree at ``R`` as it is, committed or not, the way the project's justfile
says (``boot``, ``ready``, ``boot_timeout``, and ``flags_on`` so every flag due for removal is
on) through ``with_service.py``, on a port the kernel picks, puts ``acceptance_proxy.py`` in
front of it, and prints two lines: the one address the tester gets, and the directory the tester
writes its record (``record.json``) and screenshots in. It returns while the product keeps
running. Run again while that session lives, it prints the same two lines, waiting first when it
still boots.
A session of the same work tree that serves a tree the work tree no longer holds is stopped
first and sealed stale, so one work tree serves one tree at a time. When the product cannot start
(``boot`` is ``"none"`` or a placeholder, ``ready`` is ``"none"``, the boot exits or never
answers) it writes the record itself, a blocker in plain words with every line of the ticket
``unsure``, and exits 1. Exit 2 is a harness that cannot run here: a type it does not try, a
ticket with no checklist, no justfile, not a git work tree.

``down`` stops every session of the slug and waits until nothing it started runs and nothing
listens on the addresses it used, then prints the digest of each seal it blesses; run again, it
finds nothing and exits 0. ``check`` prints what is wrong with the latest record of the slug for
the tree the work tree holds now, or with ``--commit C`` the tree of ``C``, one sentence each,
and exits 1 when anything is; with ``--seal D`` the seal must also be the one ``down`` printed.
``replay`` runs the record's kept checks, or a committed checks file, each against a fresh boot;
with ``--control`` against a product that does nothing, where each must fail.
``summary`` writes ``docs/features/<slug>/acceptance.json`` and ``acceptance-checks.json`` from
an accepted record and its session, the same bytes on every run. ``audit`` says whether a
tester's transcript named a file under the product (``acceptance_audit.py``).

Evidence lives in ``<git common dir>/ratchet/acceptance/<slug>/<tree>/<session>/``, never in the
work tree and never committed. ``<tree>`` is the tree a commit of the whole work tree would have,
so the same evidence holds once the orchestrator commits what was served. A session that ended
more than 14 days ago is removed by the next ``up``::

    session.json      the harness: what was booted, for which tree and work tree
    ticket.md         the ticket as ``up`` was given it
    boot.log          what the boot and ``with_service.py`` printed
    live.lock         held while the session runs; it holds the supervisor's process id
    proxy.json        the proxy: its address and the product's, once it listens
    requests.jsonl    the proxy: one line per request the tester made
    probes/           the proxy: each request and answer as sent, cut at 1 MB
    seal.json         the proxy, when it stops: how many lines it wrote, their SHA-256, and
                      how many answers reached the tester with no line
    tester/           the tester's: record.json and its screenshots; ``check`` refuses a link
                      in it that leads out of it
    replay.json       the last replay of the record's kept checks; control.json, against nothing

Every command stops what it started on SIGINT, SIGTERM and SIGHUP, removes its temporary
directories, and exits 128 plus the signal. ``up`` interrupted removes the session it began.
"""

from __future__ import annotations

import argparse
import contextlib
import datetime as dt
import fcntl
import json
import os
import re
import secrets
import shlex
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import IO, TYPE_CHECKING

import acceptance_audit
import acceptance_proxy
import acceptance_record
import application_types
import gatelib
import kept_checks

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator

HERE = Path(__file__).resolve().parent
WITH_SERVICE = HERE / "with_service.py"
PROXY = HERE / "acceptance_proxy.py"
SUPPORTED = ("service", "web-ui")
KEEP = dt.timedelta(days=14)
# Why 30: `with_service.py` stops the proxy and then the product, each with five seconds of grace
# and one to settle; a supervisor that gave up sooner would leave the product running.
STOP_GRACE = 30.0
DOWN_WAIT = 60.0
POLL = 0.05
SLUG = re.compile(r"[a-z0-9][a-z0-9-]*")
BIND = "bind:"
NONE = "none"
SUMMARY, CHECKS = "acceptance.json", "acceptance-checks.json"
RECORD = Path("tester/record.json")
# Why in the background of a shell: the supervisor outlives the `up` that starts it, so it is
# nobody's child there; the shell prints its process id and exits.
BACKGROUND = '"$@" </dev/null >&2 & echo "$!"'
EXITED = re.compile(r"^the service exited with code (-?\d+) before")
SILENT = re.compile(r" did not answer within (\S+?);")


class Usage(Exception):  # noqa: N818 # -- a refusal to run, said to the caller
    """The harness cannot run here; the message says why. Exit 2."""


@dataclass(frozen=True)
class Boot:
    """How to start the product: the binding's boot and ready, or the do-nothing control."""

    command: str
    ready: str
    timeout: float
    env: tuple[str, ...] = ()

    def argv(self, directory: Path) -> list[str]:
        """``with_service.py`` booting this, with the proxy for ``directory`` as its command."""
        pairs = [word for pair in self.env for word in ("--env", pair)]
        return [
            sys.executable,
            str(WITH_SERVICE),
            *("--boot", self.command, "--ready", self.ready, "--timeout", f"{self.timeout:g}"),
            *pairs,
            *("--", sys.executable, str(PROXY), str(directory)),
        ]


NULL = Boot(
    f"exec {shlex.quote(sys.executable)} {shlex.quote(str(PROXY))} --null",
    "http://127.0.0.1:{port}/",
    30.0,
)


@dataclass(frozen=True)
class Binding:
    """The project's types and how it boots; ``boot`` is ``None`` with ``why`` when it cannot."""

    types: tuple[str, ...]
    boot: Boot | None
    why: str = ""


class State(Enum):
    """Where a session is: running, ended with a sealed log, or never served at all."""

    STARTING = "starting"
    LIVE = "live"
    ENDED = "ended"
    UNSERVED = "unserved"
    BROKEN = "broken"


def git(root: Path, *args: str) -> str:
    found = gatelib.git(root, *args)
    if found is None:
        message = f"{root} is not a git work tree with a commit (git {' '.join(args)} failed)"
        raise Usage(message)
    return found


def evaluate(root: Path, name: str) -> str | None:
    """A variable of the project's justfile, or ``None`` when it has none by that name."""
    try:
        done = subprocess.run(
            [
                "just",
                "--justfile",
                str(root / "justfile"),
                "--working-directory",
                str(root),
                "--evaluate",
                name,
            ],
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError as error:
        message = f"the harness reads boot and ready with `just`, which is not here: {error}"
        raise Usage(message) from None
    return done.stdout if done.returncode == 0 else None


def why_not(boot: str | None, ready: str | None) -> str:
    """Why these values start nothing, in plain words; empty when they can."""
    for name, value in (("boot", boot), ("ready", ready)):
        if value is None:
            return f"The project gives no way to start it: its justfile sets no `{name}`."
        if value.strip() == NONE:
            return f"The project gives no way to start it: its justfile sets `{name}` to none."
        if value.startswith(BIND):
            return (
                f"The project gives no way to start it: `{name}` still holds the template's text."
            )
    return ""


def read_binding(root: Path) -> Binding:
    """The binding's types (``docs/process/binding.md``) and boot (the ``justfile``)."""
    text = gatelib.read_text(root / gatelib.BINDING)
    types = application_types.declared(text or "")
    if not types:
        message = f"{root / gatelib.BINDING} declares no `- Application types:` line"
        raise Usage(message)
    if not (root / "justfile").is_file():
        message = f"the harness reads boot and ready from the justfile, and {root} has none"
        raise Usage(message)
    boot, ready = evaluate(root, "boot"), evaluate(root, "ready")
    why = why_not(boot, ready)
    if why or boot is None or ready is None:
        return Binding(tuple(types), None, why)
    timeout = evaluate(root, "boot_timeout") or ""
    flags = (evaluate(root, "flags_on") or NONE).strip()
    env: tuple[str, ...] = ()
    if flags != NONE and not flags.startswith(BIND):
        # Why the shell: `flags_on` is written to be expanded as the recipes expand it, inside
        # double quotes, so a `$(flags.py --temporary ...)` in it runs on every boot.
        shown = subprocess.run(
            ["bash", "-c", f'printf "%s" "{flags}"'],
            cwd=root,
            capture_output=True,
            text=True,
            check=False,
        )
        env = (shown.stdout,)
    seconds = float(timeout) if re.fullmatch(r"\d+(\.\d+)?", timeout.strip()) else 30.0
    return Binding(tuple(types), Boot(boot, ready, seconds, env))


def store(root: Path) -> Path:
    """Where the evidence of ``root`` lives: inside its git common directory."""
    common = Path(git(root, "rev-parse", "--git-common-dir"))
    return (root / common).resolve() / "ratchet" / "acceptance"


def tree_of(root: Path, slug: str, commit: str | None = None) -> str:
    """The tree evidence of ``slug`` is filed under: ``commit``'s, or the work tree's at ``root``.

    The work tree's is the tree a commit of all of it would have: tracked changes, and untracked
    files git does not ignore. The slug's own summary files are left out of either, so the
    evidence of a tree still holds once the orchestrator commits that tree with its summary.
    Built in an index of its own, so the project's index is untouched.
    """
    with tempfile.TemporaryDirectory(prefix="ratchet-tree-") as scratch:
        env = {**os.environ, "GIT_INDEX_FILE": str(Path(scratch) / "index")}

        def index(*args: str) -> str:
            done = subprocess.run(
                ["git", "-C", str(root), *args],
                env=env,
                capture_output=True,
                text=True,
                check=False,
            )
            if done.returncode != 0:
                said = (done.stderr.strip().splitlines() or ["no reason given"])[0]
                message = f"git {' '.join(args)} failed in {root}: {said}"
                raise Usage(message)
            return done.stdout.strip()

        if commit is None:
            index("read-tree", "HEAD")
            index("add", "-A")
        else:
            index("read-tree", f"{commit}^{{tree}}")
        summaries = [f"docs/features/{slug}/{name}" for name in (SUMMARY, CHECKS)]
        index("rm", "--cached", "-q", "--ignore-unmatch", "--", *summaries)
        return index("write-tree")


def state(directory: Path) -> State:
    held, served = acceptance_proxy.locked(directory), (directory / "proxy.json").is_file()
    if held:
        return State.LIVE if served else State.STARTING
    if (directory / "seal.json").is_file():
        return State.ENDED
    return State.BROKEN if served else State.UNSERVED


def read_json(path: Path) -> dict[str, object] | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def sessions(place: Path, depth: int) -> list[Path]:
    """The session directories ``depth`` levels under ``place``, oldest first by name."""
    pattern = "/".join(["*"] * depth + ["session.json"])
    return sorted(path.parent for path in place.glob(pattern))


@contextlib.contextmanager
def serialized(place: Path, slug: str) -> Iterator[None]:
    """One ``up`` or ``down`` of ``slug`` at a time, so two at once converge on one session.

    The kernel drops the lock when its holder dies, however it dies.
    """
    place.mkdir(parents=True, exist_ok=True)
    with (place / f"{slug}.lock").open("w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        yield


def latest(root: Path, slug: str, commit: str | None = None) -> Path:
    """The newest session of ``slug`` that served the tree of ``commit``, or of the work tree."""
    place = store(root)
    tree = tree_of(root, slug, commit)
    found = sessions(place / slug / tree, 1)
    if not found:
        where = "the work tree" if commit is None else f"the tree of {commit}"
        message = f"no session of {slug} served {where} ({tree[:12]}): run `up` on it first"
        raise Usage(message)
    return found[-1]


def prune(place: Path, now: dt.datetime) -> None:
    """Remove each session that ended more than ``KEEP`` ago, then the directories left empty."""
    for directory in sessions(place, 3):
        newest = max(path.stat().st_mtime for path in [directory, *directory.iterdir()])
        ended = dt.datetime.fromtimestamp(newest, dt.UTC)
        if state(directory) not in (State.LIVE, State.STARTING) and now - ended > KEEP:
            shutil.rmtree(directory)
    for empty in sorted(place.glob("*/*"), reverse=True) + sorted(place.glob("*")):
        with contextlib.suppress(OSError):
            empty.rmdir()


def what_it_did(log: Path) -> str:
    """What the boot did instead of answering, in plain words, from what ``with_service.py`` said.

    It indents the boot's own output by two spaces under a ``boot output`` line.
    """
    lines = (gatelib.read_text(log) or "").splitlines()
    why = "it never answered"
    for line in lines:
        if exited := EXITED.match(line):
            why = f"it exited with code {exited[1]}"
            break
        if silent := SILENT.search(line):
            why = f"it gave no answer within {silent[1]}"
            break
    printed = [line.strip() for line in lines if line.startswith("  ") and line.strip()]
    return f"{why}, and " + (f"its first line was: {printed[0]}" if printed else "printed nothing")


def write_record(directory: Path, record: dict[str, object]) -> None:
    path = directory / RECORD
    path.write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")


def cannot_start(directory: Path, said: str) -> int:
    session = read_json(directory / "session.json") or {}
    ticket = acceptance_record.promises((directory / "ticket.md").read_text(encoding="utf-8"))
    record = acceptance_record.cannot_start(
        str(session["session"]), str(session["tree"]), ticket=ticket, said=said
    )
    write_record(directory, record)
    print(said)
    print(f"acceptance up: cannot start; the record is {directory / RECORD}")
    return 1


def wait_serving(going: Callable[[], bool], directory: Path, limit: float) -> str | None:
    """The proxy's address once it listens; ``None`` when the boot stopped or took too long."""
    deadline = time.monotonic() + limit
    while True:
        proxy = read_json(directory / "proxy.json")
        if proxy is not None:
            return str(proxy["url"])
        if not going() or time.monotonic() > deadline:
            return None
        time.sleep(POLL)


def stop(process: subprocess.Popen[bytes] | None) -> None:
    """Stop a process this one started in a session of its own, unless it has already exited."""
    if process is not None and process.poll() is None:
        gatelib.stop_group(process, grace=STOP_GRACE)


def start(argv: list[str], cwd: Path, out: IO[bytes]) -> subprocess.Popen[bytes]:
    """``argv`` in a session of its own, writing to ``out``."""
    return subprocess.Popen(
        argv,
        cwd=cwd,
        stdin=subprocess.DEVNULL,
        stdout=out,
        stderr=subprocess.STDOUT,
        start_new_session=True,
    )


def launch(root: Path, directory: Path, boot: Boot) -> int:
    """Start the supervisor, which outlives this command, and print the address once it serves.

    The supervisor holds the session's lock from here on: this command takes it, hands the
    supervisor the same open file, writes the supervisor's process id into it and lets go.
    """
    argv = [sys.executable, str(Path(__file__).resolve()), "_supervise", str(directory)]
    try:
        with (
            (directory / "live.lock").open("w") as lock,
            (directory / "boot.log").open("ab") as log,
        ):
            fcntl.flock(lock, fcntl.LOCK_EX)
            with gatelib.signals_held():
                started = subprocess.run(
                    ["bash", "-c", BACKGROUND, "supervise", *argv],
                    cwd=root,
                    stdout=subprocess.PIPE,
                    stderr=log,
                    pass_fds=(lock.fileno(),),
                    check=True,
                    text=True,
                )
                lock.write(started.stdout.strip())
        url = awaited(directory)
    except gatelib.Interrupted:
        end(directory)
        shutil.rmtree(directory)
        raise
    if url is None:
        end(directory)
        said = what_it_did(directory / "boot.log")
        return cannot_start(
            directory, f"It was started with `{boot.command}` and did not answer: {said}"
        )
    print(url)
    print(directory / "tester")
    return 0


def up(root: Path, slug: str, ticket: Path) -> int:
    binding = read_binding(root)
    others = [kind for kind in binding.types if kind not in SUPPORTED]
    if others:
        message = (
            f"the harness tries a service and a web-ui; this project is also {', '.join(others)},"
            " which a later version will try: nothing was started"
        )
        raise Usage(message)
    text = gatelib.read_text(ticket)
    if text is None or not acceptance_record.promises(text):
        message = f"{ticket} has no checklist, so there is nothing to accept"
        raise Usage(message)
    place = store(root)
    tree = tree_of(root, slug)
    with serialized(place, slug):
        prune(place, dt.datetime.now(dt.UTC))
        running = [
            directory
            for directory in sessions(place / slug, 2)
            if state(directory) in (State.LIVE, State.STARTING)
        ]
        for directory in running:
            facts = read_json(directory / "session.json") or {}
            if directory.parent.name != tree and facts.get("root") == str(root):
                for line in retire(directory):
                    print(line, file=sys.stderr)
        for directory in running:
            if directory.parent.name == tree and (url := awaited(directory)):
                print(url)
                print(directory / "tester")
                return 0
        directory = begin(root, place / slug / tree, ticket=text, binding=binding)
        if binding.boot is None:
            return cannot_start(directory, binding.why)
        return launch(root, directory, binding.boot)


def awaited(directory: Path) -> str | None:
    """The address of a running session once it serves; ``None`` when it stops before it does."""
    timeout = float(str((read_json(directory / "session.json") or {})["timeout"]))
    return wait_serving(
        lambda: acceptance_proxy.locked(directory), directory, 2 * timeout + DOWN_WAIT
    )


def retire(directory: Path) -> list[str]:
    """Stop a session of a tree its work tree no longer holds, sealed stale; what was said of it."""
    began = acceptance_proxy.now()
    problems = end(directory)
    closed = close(directory, began)
    return problems + (closed.lines if closed else [])


def begin(root: Path, under: Path, *, ticket: str, binding: Binding) -> Path:
    """A new session directory under ``under``, holding the ticket and what is booted."""
    begun = dt.datetime.now(dt.UTC)
    session = f"{begun:%Y%m%dT%H%M%S%fZ}-{secrets.token_hex(4)}"
    directory = under / session
    (directory / "tester").mkdir(parents=True)
    try:
        (directory / "ticket.md").write_text(ticket, encoding="utf-8")
        facts: dict[str, object] = {"session": session, "slug": under.parent.name}
        facts |= {"tree": under.name, "root": str(root), "types": list(binding.types)}
        facts |= {"started": begun.isoformat(timespec="seconds").replace("+00:00", "Z")}
        if binding.boot is not None:
            boot = binding.boot
            facts |= {"boot": boot.command, "ready": boot.ready, "timeout": boot.timeout}
            facts |= {"env": list(boot.env)}
        acceptance_proxy.write_json(directory / "session.json", facts)
    except gatelib.Interrupted:
        shutil.rmtree(directory)
        raise
    return directory


def supervise(directory: Path) -> int:
    """Hold the session: run ``with_service.py`` until it ends or this process is told to stop."""
    session = read_json(directory / "session.json") or {}
    env = session.get("env")
    boot = Boot(
        str(session["boot"]),
        str(session["ready"]),
        float(str(session["timeout"])),
        tuple(str(pair) for pair in env) if isinstance(env, list) else (),
    )
    process: subprocess.Popen[bytes] | None = None
    try:
        with gatelib.signals_held():
            process = subprocess.Popen(
                boot.argv(directory),
                cwd=str(session["root"]),
                stdin=subprocess.DEVNULL,
                start_new_session=True,
            )
        return process.wait()
    finally:
        stop(process)


def wait_unlocked(directory: Path, limit: float) -> bool:
    deadline = time.monotonic() + limit
    while acceptance_proxy.locked(directory):
        if time.monotonic() > deadline:
            return False
        time.sleep(POLL)
    return True


def down(root: Path, slug: str) -> int:
    place = store(root)
    with serialized(place, slug):
        return stop_sessions(place / slug)


def end(directory: Path) -> list[str]:
    """Stop the session's supervisor and wait for it; a sentence for what did not stop.

    Signalled only while it holds the lock, so the process id is never one the kernel reused.
    """
    text = (directory / "live.lock").read_text(encoding="utf-8").strip()
    pid = int(text) if text.isdigit() else None
    if pid is not None and acceptance_proxy.locked(directory):
        with contextlib.suppress(ProcessLookupError):
            os.kill(pid, signal.SIGTERM)
    problems = []
    if not wait_unlocked(directory, DOWN_WAIT):
        if pid is not None:
            with contextlib.suppress(ProcessLookupError):
                os.kill(pid, signal.SIGKILL)
        problems.append(f"session {directory.name} did not stop within {DOWN_WAIT:g}s; ended")
    proxy = read_json(directory / "proxy.json") or {}
    return problems + [
        f"{url} still answers after session {directory.name} stopped"
        for url in (proxy.get("url"), proxy.get("upstream"))
        if isinstance(url, str) and gatelib.listening(url)
    ]


def stop_sessions(place: Path) -> int:
    """Stop every running session under ``place``, wait until nothing of it answers, and end it.

    Every sealed session ``down`` has not yet ended gets ``down``'s word on its seal: when it
    came, and whether the proxy had stopped before then. Only one that ran until ``down`` came
    is blessed, by printing the digest of its seal. Answers that reached the tester with no line
    in the log are said, and do not stop the blessing: a pass can cite only a logged request.
    """
    began = acceptance_proxy.now()
    every = sessions(place, 2)
    running = [d for d in every if state(d) in (State.LIVE, State.STARTING)]
    problems = [line for directory in running for line in end(directory)]
    for line in problems:
        print(line)
    ended = [found for directory in every if (found := close(directory, began))]
    for said in ended:
        for line in said.lines:
            print(line)
        problems += [] if said.blessed else said.lines
    if running or ended:
        print(f"acceptance down: stopped {len(running)}")
    else:
        print("acceptance down: nothing was running")
    return 1 if problems else 0


@dataclass(frozen=True)
class Closed:
    """What ``down`` said of a session it ended: blessed, or why not."""

    blessed: bool
    lines: list[str]


def close(directory: Path, began: str) -> Closed | None:
    """Put ``down``'s word on the seal of a session it has not ended before; what it says of it.

    ``None`` when there is nothing to end: no seal yet, or ``down`` ended it already. A session
    whose proxy stopped before ``down`` came ended early; one whose work tree no longer holds
    the tree it served is stale.
    """
    path = directory / "seal.json"
    raw = read_json(path)
    seal = acceptance_record.parse_seal(raw)
    if raw is None or seal is None or seal.ending is not None:
        return None
    early, stale = seal.stopped < began, not still_served(directory)
    ending = {"at": began, "endedEarly": early, "stale": stale}
    acceptance_proxy.write_json(path, raw | {"down": ending})
    name = directory.name
    lines = [f"session {name} ended at {seal.stopped}, {acceptance_record.EARLY}"] if early else []
    lines += [f"session {name} is stale: {acceptance_record.STALE}"] if stale else []
    said = f"session {name}: answers that reached the tester unlogged: {seal.unlogged}"
    told = [said] if seal.unlogged else []
    if lines:
        return Closed(blessed=False, lines=told + lines)
    digest = acceptance_record.sha256(path.read_bytes())
    return Closed(blessed=True, lines=[*told, f"session {name}: seal {digest}"])


def still_served(directory: Path) -> bool:
    """Whether the work tree the session booted still holds the tree it served."""
    facts = read_json(directory / "session.json") or {}
    try:
        return tree_of(Path(str(facts["root"])), str(facts["slug"])) == facts["tree"]
    except (KeyError, Usage):
        return False


def code_paths(root: Path, ticket: str) -> tuple[str, ...]:
    """The paths of the files the work tree holds that a tester has no business naming.

    Every file the served tree is made of, tracked or not, top-level or not, but a README and
    the paths the ticket itself names, which the tester was given.
    """
    listed = git(root, "ls-files", "-z", "--cached", "--others", "--exclude-standard")
    return tuple(
        path
        for path in dict.fromkeys(listed.split("\0"))
        if path and path not in ticket and not Path(path).name.upper().startswith("README")
    )


def screenshots(directory: Path, cited: list[str]) -> frozenset[str]:
    """The cited paths that are PNG files inside the tester's directory."""
    tester = (directory / "tester").resolve()
    good = set()
    for path in cited:
        full = (tester / path).resolve()
        if full.is_relative_to(tester) and full.is_file():
            with full.open("rb") as image:
                if image.read(len(acceptance_record.PNG)) == acceptance_record.PNG:
                    good.add(path)
    return frozenset(good)


def exchanges(
    directory: Path, log: dict[str, acceptance_record.Entry], cited: set[str]
) -> dict[str, acceptance_record.Exchange]:
    """The stored exchanges of the cited requests whose probes both still match the log."""

    def probe(name: str) -> bytes:
        path = directory / "probes" / name
        return path.read_bytes() if path.is_file() else b""

    found = {}
    for ident in cited & set(log):
        sent, answer = probe(f"{ident}.request"), probe(f"{ident}.response")
        digests = (acceptance_record.sha256(sent), acceptance_record.sha256(answer))
        if digests == (log[ident].request, log[ident].response):
            found[ident] = acceptance_record.Exchange(sent, answer)
    return found


def judged(root: Path, directory: Path) -> tuple[acceptance_record.Record | None, list[str]]:
    """The record of a session, and every sentence about what is wrong with it."""
    if state(directory) in (State.LIVE, State.STARTING):
        return None, ["the session is still running: run `down` first"]
    escapes = links_out(directory)
    record, problems = read_record(directory / RECORD)
    if record is None:
        return None, escapes + problems
    session = read_json(directory / "session.json") or {}
    served = (directory / "proxy.json").is_file()
    data = (directory / "requests.jsonl").read_bytes() if served else b""
    log, problems = acceptance_record.read_log(data)
    if served:
        problems = [
            *acceptance_record.seal_problems(
                data,
                seal_of(directory),
                ticket=(directory / "ticket.md").read_bytes(),
                session=(directory / "session.json").read_bytes(),
            ),
            *problems,
        ]
    problems = escapes + problems
    every: list[acceptance_record.Criterion | acceptance_record.Finding] = [
        *record.criteria,
        *record.findings,
    ]
    types, tree = session.get("types"), directory.parent.name
    if session.get("tree") != tree:
        problems.append(f"the session served tree {session.get('tree')}, but is filed under {tree}")
    ticket = (directory / "ticket.md").read_text(encoding="utf-8")
    facts = acceptance_record.Facts(
        session=str(session.get("session")),
        tree=tree,
        web_ui=isinstance(types, list) and "web-ui" in types,
        promises=tuple(acceptance_record.promises(ticket)),
        log=log,
        exchanges=exchanges(directory, log, {c.request for item in every for c in item.requests}),
        screenshots=screenshots(directory, [p for item in every for p in item.screenshots]),
        code=code_paths(root, ticket),
        served=served,
    )
    problems += acceptance_record.judge(record, facts)
    return (record if not problems else None), problems


def read_record(path: Path) -> tuple[acceptance_record.Record | None, list[str]]:
    """The tester's record at ``path``, or why there is none."""
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None, [f"the tester wrote no record at {path}"]
    except ValueError as error:
        return None, [f"{path} is not JSON: {error}"]
    return acceptance_record.parse_record(raw)


def links_out(directory: Path) -> list[str]:
    """A sentence for each link in the session's ``tester/`` that leads out of it.

    Such a link is a way to the product's code or the harness's log that no path names.
    """
    tester = directory / "tester"
    inside = Path(os.path.realpath(tester))
    found = []
    for folder, folders, files in os.walk(tester):
        folders.sort()
        for path in sorted(Path(folder) / name for name in folders + files):
            target = Path(os.path.realpath(path))
            if path.is_symlink() and not target.is_relative_to(inside):
                where = path.relative_to(directory)
                found.append(f"{where} is a link out of the tester's directory, to {target}")
    return found


def seal_of(directory: Path) -> acceptance_record.Seal | None:
    return acceptance_record.parse_seal(read_json(directory / "seal.json"))


def check(root: Path, slug: str, *, kept: str | None, commit: str | None) -> int:
    """Judge the latest record of the tree asked about: ``commit``'s, else the work tree's.

    With ``kept``, the digest ``down`` printed, the seal is also held to it.
    """
    directory = latest(root, slug, commit)
    path = directory / "seal.json"
    problems = []
    if kept is not None and not path.is_file():
        problems.append(f"session {directory.name} has no seal to hold to {kept[:12]}")
    elif kept is not None and acceptance_record.sha256(path.read_bytes()) != kept:
        problems.append(f"seal.json is not the seal `down` printed ({kept[:12]}): it was rewritten")
    return gatelib.report("acceptance check", problems + judged(root, directory)[1])


def replay_one(
    check: kept_checks.Check, boot: Boot, *, root: Path, scratch: Path
) -> kept_checks.Result:
    """How ``check`` ran on a fresh boot of its own; not run when the product did not start."""
    place = scratch / check.id
    place.mkdir()
    (place / "session.json").write_text(
        json.dumps({"session": f"replay-{secrets.token_hex(4)}"}), encoding="utf-8"
    )
    process: subprocess.Popen[bytes] | None = None
    with (place / "boot.log").open("ab") as log:
        try:
            with gatelib.signals_held():
                process = start(boot.argv(place), root, log)
            url = wait_serving(lambda: process.poll() is None, place, 2 * boot.timeout + DOWN_WAIT)
            if url is None:
                said = f"the product did not start: {what_it_did(place / 'boot.log')}"
                return kept_checks.Result(kept_checks.Outcome.NOT_RUN, said)
            return kept_checks.run(check, url, place)
        finally:
            stop(process)


def kept(listed: object) -> tuple[list[object], list[kept_checks.Check]]:
    """The kept checks as written and as read; a check that does not read is a usage error."""
    written = listed if isinstance(listed, list) else []
    checks, problems = [], []
    for n, item in enumerate(written, 1):
        found, wrong = kept_checks.parse(item, f"kept item {n}")
        problems += wrong
        checks += [found] if found else []
    if problems:
        raise Usage("; ".join(problems))
    return written, checks


def verdicts(
    checks: list[kept_checks.Check], results: list[kept_checks.Result], *, control: bool
) -> tuple[list[str], list[str]]:
    """What held as it should, and what did not, one sentence each.

    A check that was not run is red either way: it showed nothing of the product.
    """
    good, bad = [], []
    against = "against a server that does nothing" if control else "on a fresh boot"
    for item, result in zip(checks, results, strict=True):
        named, reason = f"{item.id} ({item.criterion})", result.reason
        held = result.outcome is kept_checks.Outcome.HELD
        if result.outcome is kept_checks.Outcome.NOT_RUN:
            bad.append(f"{named} was not run {against}: {reason}")
        elif control and held:
            bad.append(f"{named} holds {against}, so it proves nothing")
        elif control:
            good.append(f"{named} fails {against}: {reason}")
        elif held:
            good.append(f"{named} holds {against}")
        else:
            bad.append(f"{named} does not hold {against}: {reason}")
    return good, bad or ([] if checks else ["there are no kept checks to replay"])


def replay(root: Path, slug: str | None, checks_file: Path | None, *, control: bool) -> int:
    directory = latest(root, slug) if slug else None
    source = (read_json(directory / RECORD) or {}).get("kept") if directory else None
    if checks_file is not None:
        data = read_json(checks_file) or {}
        if data.get("schema") != acceptance_record.SCHEMA:
            read = f"schema {acceptance_record.SCHEMA}, which this harness reads"
            message = f"{checks_file} is not a checks file of {read}"
            raise Usage(message)
        source = data.get("checks")
    written, checks = kept(source)
    boot = NULL if control else read_binding(root).boot
    if boot is None:
        return gatelib.report("acceptance replay", [read_binding(root).why])
    with tempfile.TemporaryDirectory(prefix="ratchet-replay-") as scratch:
        ran = [replay_one(item, boot, root=root, scratch=Path(scratch)) for item in checks]
    if directory is not None:
        results = [
            {
                "id": item.id,
                "criterion": item.criterion,
                "outcome": result.outcome.value,
                "reason": result.reason,
            }
            for item, result in zip(checks, ran, strict=True)
        ]
        out = {"checks": acceptance_record.digest(written), "results": results}
        name = "control.json" if control else "replay.json"
        (directory / name).write_text(json.dumps(out, indent=2) + "\n", encoding="utf-8")
    good, bad = verdicts(checks, ran, control=control)
    for line in good:
        print(line)
    return gatelib.report("acceptance replay" + (" --control" if control else ""), bad)


def summary(root: Path, slug: str) -> int:
    directory = latest(root, slug)
    record, problems = judged(root, directory)
    if record is None:
        return gatelib.report("acceptance summary", problems)
    session = read_json(directory / "session.json") or {}
    facts = acceptance_record.Session(slug, str(session["started"]), seal_of(directory))
    data = acceptance_record.summary(
        record, facts, (read_json(directory / "replay.json"), read_json(directory / "control.json"))
    )
    out = root / "docs" / "features" / slug
    out.mkdir(parents=True, exist_ok=True)
    (out / SUMMARY).write_text(
        json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    kept = out / CHECKS
    if record.kept_raw:
        checks = {"schema": acceptance_record.SCHEMA, "slug": slug, "checks": list(record.kept_raw)}
        kept.write_text(json.dumps(checks, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    else:
        kept.unlink(missing_ok=True)
    print(out / SUMMARY)
    return gatelib.report("acceptance summary", [])


def parse(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="acceptance.py")
    commands = parser.add_subparsers(dest="command", required=True)
    for name in COMMANDS:
        command = commands.add_parser(name)
        command.add_argument("--root", type=Path, default=Path.cwd())
        if name in ("up", "down", "check", "summary"):
            command.add_argument("--slug", required=True)
        if name == "check":
            command.add_argument("--seal")
            command.add_argument("--commit")
        if name == "up":
            command.add_argument("--ticket", type=Path, required=True)
        if name == "replay":
            which = command.add_mutually_exclusive_group(required=True)
            which.add_argument("--slug")
            which.add_argument("--checks", type=Path)
            command.add_argument("--control", action="store_true")
        if name == "audit":
            command.add_argument("--transcript", type=Path, required=True)
        if name == "_supervise":
            command.add_argument("directory", type=Path)
    options = parser.parse_args(argv[1:])
    slug = getattr(options, "slug", None)
    if slug is not None and not SLUG.fullmatch(slug):
        parser.error(f"--slug {slug!r} is not lower-case letters, digits and hyphens")
    options.root = options.root.resolve()
    return options


COMMANDS: dict[str, Callable[[argparse.Namespace], int]] = {
    "up": lambda o: up(o.root, o.slug, o.ticket),
    "down": lambda o: down(o.root, o.slug),
    "check": lambda o: check(o.root, o.slug, kept=o.seal, commit=o.commit),
    "replay": lambda o: replay(o.root, o.slug, o.checks, control=o.control),
    "summary": lambda o: summary(o.root, o.slug),
    "audit": lambda o: acceptance_audit.main(o.transcript, o.root, store(o.root)),
    "_supervise": lambda o: supervise(o.directory),
}


def main(argv: list[str]) -> int:
    options = parse(argv)
    try:
        with gatelib.interrupts_raise():
            return COMMANDS[options.command](options)
    except gatelib.Interrupted as stopped:
        print(f"acceptance {options.command}: {stopped}; everything it started was stopped")
        return 128 + stopped.signum
    except Usage as refused:
        print(f"acceptance {options.command}: {refused}")
        return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
