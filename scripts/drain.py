#!/usr/bin/env python3
"""The queue: every agent-ready ticket built at once, each in a work tree of its own. ADR 0023.

``/ratchet:feature`` with no ticket drains the queue. An agent runs the pipelines; this script
decides everything about a drain that needs no judgement, and keeps what must outlive a session.

- ``plan`` prints, as JSON, what each open agent-ready ticket is to this drain, in the order
  their ``Blocked by`` lines give: ``buildable`` (the ticket passes the preflight and nothing
  is built; while the checkout is not fit to build on, its ``why`` says what is missing),
  ``building`` (its work tree exists), ``built`` (its branch holds commits and is not merged, so
  the judge is asked; when it is behind the default branch its ``why`` says it is brought up to
  date and judged again first), ``waiting`` (blocked only by tickets this drain can still merge),
  ``held`` (its last run parked or was not approved, and the ticket has not changed since; a hold
  outranks commits on the branch, but a wait for a reason the drain resolves itself is
  retried; a ticket waiting on a held one is refused naming it),
  ``merged`` (its branch is merged and the ticket is still open) and ``refused`` (the preflight
  is red, with what produces each missing piece). ``start`` names the buildable tickets to start
  now: at most ``--concurrency`` less those in a work tree, building or built and not yet
  settled, and none while the checkout is not fit.
- ``open <ticket>`` is the gate: it makes the ticket's work tree and branch only when the plan
  starts that ticket, so the order and the cap are a script's exit code.
- ``update <ticket>`` rebases the ticket's branch onto the default branch when that has moved,
  first making the work tree of a built branch that has none and dropping a last commit that is
  the verdict record, which the rebase outdated.
  Exit 0 with ``moved``; exit 2 with the conflicted paths and the branch as it was. A run killed
  part-way is run again: the rebase it left is abandoned, which puts the branch back, and starts
  over; one killed after the rebase finished still answers ``moved`` and drops the record.
- ``land <ticket>`` runs the binding's ``merge`` entry point with no forge and closes the ticket
  with what the merge judge printed: merged, or waiting for the owner with every reason verbatim.
  Run again after the merge was killed, it first puts right what the merge left (``merge.py``'s
  work tree, a fast-forward cut off half-way), and a merge that landed is recorded as merged.
- ``close <ticket> --outcome merged|waits|parked`` removes the work tree, stashing what was left
  uncommitted, and writes the ticket's note for the report. A work tree whose uncommitted work
  git cannot stash (a stale lock, a merge left unfinished, a damaged index) is not removed and
  nothing running from it is ended: the note and a ``kept:`` line say so. ``--result FILE`` is the
  pipeline's result: its open questions, guard edits and forced verdict become the reasons. A
  ``triggers`` red in its ``checkOutput`` (another instance of a kind whose safeguard is owed,
  ADR 0028) adds
  a reason naming ``/ratchet:safeguard``, and the ticket is held only until no such kind is owed.
- ``clean`` removes every queue work tree whose branch is merged or gone; ``--all`` removes the
  rest too, their uncommitted work stashed as ``<branch>: interrupted run``. A drain starts with
  ``clean --all``: one drain runs on a repository at a time, so every work tree found then is
  left over. Which the script holds to: ``clean --all`` takes a lease naming the processes above
  it, refuses while a process of another drain on this repository is alive, and ``report`` ends
  the lease and prints the drain's token on stderr; ``--take-over`` replaces one whose drain
  is gone for good. Two drains under one long-lived process are told apart by the lease's
  record of commands run since it started, and by the token: ``clean --all --drain TOKEN``
  resumes the drain that holds it. Every command that
  changes a work tree also runs under a lock of the kernel's, freed when its holder dies however
  it dies, so two commands at once (two ``open`` of one ticket) are run one at a time.
- ``report`` writes ``.ratchet/queue/<date>-<time>.md`` and prints it: per ticket merged, waiting
  for the owner or parked, with the reasons; every assumption with its refuter; every glossary
  term and ADR added; the time each ticket took.

Work trees live outside the repository, under the temporary directory, in a directory per user
and repository that only that user can write. The script refuses one that is a link or another
user's, and empties only what git lists as a work tree of this repository. The notes and reports
live in ``.ratchet/``, which no commit carries.

Usage: ``drain.py plan|open|update|land|close|clean|report [ticket] [--root DIR]
[--concurrency N] [--offline] [--outcome O] [--reason TEXT]... [--result FILE] [--all]``.
"""

from __future__ import annotations

import argparse
import contextlib
import fcntl
import hashlib
import json
import os
import re
import secrets
import shlex
import shutil
import stat
import subprocess
import sys
import tempfile
import time
from dataclasses import asdict, dataclass, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING

import application_types
import feature_preflight as preflight
import gatelib
import merge
import merge_ready
import queue_notes
import route

# Why: the additive-record check the hook and the judge share lives under hooks/.
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "hooks"))
from ratchet_guards import records
from ratchet_guards.manifest import Manifest

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator

CONCURRENCY = 3
# Why: the workflow declares the most agents a run spawns, by role, and a test counts them in a
# worst-case run, so a new role changes one line there and not a number here.
_DECLARED = re.findall(
    r"^const AGENTS_AT_MOST = (\{.*\})$",
    (Path(__file__).resolve().parents[1] / "workflows/pipeline.js").read_text(encoding="utf-8"),
    re.MULTILINE,
)
_AGENTS: dict[str, int] = json.loads(_DECLARED[0])
# The critic and its fix loop join once the project is depended-on (ADR 0028).
PIPELINE_AGENTS = _AGENTS["dependedOn"]
YOUNG_PIPELINE_AGENTS = _AGENTS["young"]
# A branch brought up to date behind another merge: one agent to reconcile, one to review again.
UPDATE_AGENTS = _AGENTS["rejudge"]
BUILDABLE, BUILDING, BUILT = "buildable", "building", "built"
WAITING, HELD, MERGED, REFUSED = "waiting", "held", "merged", "refused"
LIVE = frozenset({BUILDABLE, BUILDING, BUILT})
WAITS, PARKED = "waits", "parked"
# Why: `<branch>: blocked run` is the stash the route reads as a held ticket.
STASHED = {MERGED: "merged", WAITS: "unapproved", PARKED: "blocked"}
INTERRUPTED = "interrupted"
QUEUE = queue_notes.QUEUE
QUEUE_HOME = "ratchet-queue"
OWNER_ONLY = 0o700
OWNER_FILE = 0o600
LOCK, LEASE = "ratchet-drain.lock", "ratchet-drain.lease"
LOCK_WAIT, LOCK_POLL = 30.0, 0.05
# Why: the start time is the 22nd field of /proc/<pid>/stat, the 20th after the command's name.
PROC_START = 19
DEPTH = 64
TOKEN_BYTES = 4
# Why: a drain's agents build for an hour at a time between its commands.
LEASE_FRESH = 2 * 60 * 60
OTHERS_WRITE = 0o022
NOT_OURS = "the queue's work trees go in a directory only this user can write; remove it"
NOT_A_TREE = "it is not a work tree of this repository or an empty directory, so it stays"
MERGE = "merge"
SLOWEST = 3
LOCKED = frozenset({"open", "update", "close", "clean", "report"})
STAMPED = frozenset({"open", "update", "close"})
# Why: the triggers gate's red line; the kind it names is what the safeguard binds (ADR 0028).
OWED_RED = re.compile(r"triggers: '([^']+)' gained \d+ instance")
DEFAULT_WORDS = "the default branch"
BEHIND = (
    "behind the default branch: `update` it and judge it again before it lands, since its last"
    " review was of a tree the default branch has moved past"
)


@dataclass(frozen=True)
class Entry:
    """One agent-ready ticket as a drain sees it."""

    ref: str
    title: str
    branch: str
    state: str
    why: tuple[str, ...] = ()
    waits_on: tuple[str, ...] = ()
    worktree: str = ""


@dataclass(frozen=True)
class Plan:
    """What a drain would do now."""

    base: str
    forge: bool
    concurrency: int
    blocked: tuple[str, ...]
    tickets: tuple[Entry, ...]
    start: tuple[str, ...]
    agents_at_most: int


@dataclass(frozen=True)
class Seen:
    """What a plan reads once: the default branch, what merged, the work trees, the tickets."""

    base: str
    merged: frozenset[str]
    trees: frozenset[Path]
    queued: frozenset[str]


@dataclass(frozen=True)
class Drain:
    """The repository a drain runs on, how it reaches the tracker, and its cap."""

    root: Path
    gh: preflight.Runner | None
    concurrency: int = CONCURRENCY

    @property
    def tracker(self) -> preflight.Runner:
        """The tracker as the preflight asks it; offline, it answers nothing."""
        return self.gh or (lambda _args: None)


def now() -> datetime:
    return datetime.now(tz=UTC).astimezone()


def key(ref: str) -> str:
    """A ticket's ref as two spellings of it compare: ``#4`` is ``4``."""
    return ref.lstrip("#")


# --- the work trees --------------------------------------------------------------------------


def main_tree(start: Path) -> Path | None:
    """The repository's main work tree, from anywhere inside it or one of its work trees."""
    listing = gatelib.git(start, "worktree", "list", "--porcelain")
    return Path(listing.splitlines()[0].removeprefix("worktree ")) if listing else None


def users_home() -> Path:
    """This user's directory of queue work trees: under the temp directory, named for the user."""
    return Path(tempfile.gettempdir()).resolve() / f"{QUEUE_HOME}-{os.getuid()}"


def queue_home(root: Path) -> Path:
    """This repository's directory of queue work trees: outside it, under the user's own."""
    name = f"{root.name}-{hashlib.sha256(str(root).encode()).hexdigest()[:8]}"
    return users_home() / name


def foreign(root: Path) -> str | None:
    """Why the queue's directories are not this user's alone, or ``None`` when they are.

    The temp directory is shared and the names are predictable, so anybody can make them first:
    as a link to a directory of the owner's, which `clean` would then empty, or as a directory
    of their own to read what is built there. A directory that does not exist yet is fine.
    """
    for directory in (users_home(), queue_home(root)):
        try:
            status = directory.lstat()
        except FileNotFoundError:
            continue
        if not stat.S_ISDIR(status.st_mode):
            return f"{directory} is a link or a file, not a directory"
        if status.st_uid != os.getuid():
            return f"{directory} belongs to another user"
        if status.st_mode & OTHERS_WRITE:
            return f"{directory} can be written by other users"
    return None


def tree_of(root: Path, branch: str) -> Path:
    return queue_home(root) / branch.replace("/", "-")


def worktrees(root: Path) -> frozenset[Path]:
    """The queue work trees git has registered for this repository.

    A queue work tree is a directory directly in the queue's own. A work tree one of them holds
    inside itself (a gate's `.ratchet/base`) is that tree's, and goes when it goes.
    """
    home = queue_home(root)
    listing = gatelib.git(root, "worktree", "list", "--porcelain") or ""
    registered = [line.removeprefix("worktree ") for line in listing.splitlines()]
    return frozenset(Path(path) for path in registered if Path(path).parent == home)


def dirty(tree: Path) -> bool:
    """Whether ``tree`` holds uncommitted work, or git cannot say: unknown is never clean."""
    status = gatelib.git(tree, "status", "--porcelain", "--untracked-files=all")
    return status is None or bool(status)


def unsaved(tree: Path) -> bool:
    """Whether ``tree`` holds uncommitted work a stash would keep, or git cannot say.

    A work tree inside it (a gate's `.ratchet/base`) is its own, goes when ``tree`` goes, and no
    stash holds it.
    """
    status = gatelib.git(tree, "status", "--porcelain", "--untracked-files=all")
    if status is None:
        return True
    return any(
        not (line.startswith("?? ") and (tree / line[3:] / ".git").exists())
        for line in status.splitlines()
    )


def stash_away(tree: Path, label: str) -> str | None:
    """Why the uncommitted work of ``tree`` is not saved as a stash; ``None`` when none is left."""
    if not unsaved(tree):
        return None
    if gatelib.git(tree, "stash", "push", "--include-untracked", "--quiet", "-m", label) is None:
        return (
            "git could not stash what it holds uncommitted (a stale `index.lock`, a merge left"
            " unfinished or a damaged index): finish or commit it there, then run `clean` again"
        )
    return "what it holds uncommitted is still there after the stash" if unsaved(tree) else None


def discard(root: Path, tree: Path, label: str) -> str | None:
    """Remove a queue work tree, what it held uncommitted stashed under ``label`` first.

    Only a directory git lists as a work tree of this repository is emptied. A link is never
    followed, and anything else stays unless it is an empty directory. A work tree is removed
    only once its uncommitted work is provably a stash: when git cannot make the stash, nothing
    is ended and nothing is removed. ``None`` when nothing is left; else why the tree stays.
    """
    if tree.is_symlink():
        return NOT_A_TREE
    if tree in worktrees(root):
        gatelib.git(tree, "rebase", "--abort")
        if gatelib.git(tree, "rev-parse", "--verify", "--quiet", "HEAD") is None:
            # Why: a stash needs a commit, and the tree's branch may have been deleted.
            gatelib.git(tree, "switch", "--detach", "--quiet", merge_ready.default_ref(root))
        kept = stash_away(tree, label)
        if kept is None:
            for pid in gatelib.end_running_from(tree.resolve()):
                print(f"ended: process {pid}, which ran a program of {tree}")
            # Why: a program that ran from the tree may have written until it was ended.
            kept = stash_away(tree, label)
        if kept is not None:
            return kept
        gatelib.git(root, "worktree", "remove", "--force", str(tree))
        shutil.rmtree(tree, ignore_errors=True)
    elif tree.is_dir() and not any(tree.iterdir()):
        tree.rmdir()
    gatelib.git(root, "worktree", "prune")
    # Why: the directory above is this repository's, and nothing else is kept in it.
    if tree.parent.is_dir() and not any(tree.parent.iterdir()):
        tree.parent.rmdir()
    return None if not tree.exists() else NOT_A_TREE


# --- one drain at a time ---------------------------------------------------------------------


class RefusedError(Exception):
    """A command that must not run now; its message says why."""


def state_dir(root: Path) -> Path:
    """Git's own directory of this repository: where the drain keeps what no commit may carry."""
    return Path(gatelib.git(root, "rev-parse", "--absolute-git-dir") or root / "git-state")


@contextlib.contextmanager
def exclusive(root: Path) -> Iterator[None]:
    """Hold the repository's drain lock: one command that changes a work tree runs at a time.

    A lock the kernel holds for an open file, so a command killed with SIGKILL frees it, and no
    file is left that says a dead process still holds it. A command waits ``LOCK_WAIT`` seconds
    for the one that runs, and is refused after that.
    """
    descriptor = os.open(state_dir(root) / LOCK, os.O_RDWR | os.O_CREAT, OWNER_FILE)
    try:
        deadline = time.monotonic() + LOCK_WAIT
        while True:
            try:
                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    message = "another drain command is running on this repository; run this again"
                    raise RefusedError(message) from None
                time.sleep(LOCK_POLL)
        yield
    finally:
        os.close(descriptor)


def process_row(pid: int) -> tuple[int, str] | None:
    """The parent and the start of process ``pid``; ``None`` when it is gone or cannot be read.

    Without ``/proc`` no process can be read, so no other drain is ever seen.
    """
    try:
        text = Path(f"/proc/{pid}/stat").read_text(encoding="utf-8")
    except OSError:
        return None
    fields = text.rpartition(")")[2].split()
    return int(fields[1]), fields[PROC_START]


def ancestry() -> list[str]:
    """This process and every process above it, each as ``pid:start`` so a reused id is not it."""
    chain: list[str] = []
    pid = os.getpid()
    while pid > 1 and len(chain) < DEPTH and (row := process_row(pid)):
        chain.append(f"{pid}:{row[1]}")
        pid = row[0]
    return chain


def rival(held: dict[str, object]) -> str | None:
    """Why the lease ``held`` names a drain that is still running, or ``None`` when it is gone.

    The lease holds the processes above the command that started the drain. A process of that
    chain which is alive and not above this command is another session's. When only processes
    above this command are alive (two drains under one long-lived process, which no process
    tells apart) the drain's own record decides: its commands that came after it started, which
    stamp the lease, were run within the last ``LEASE_FRESH`` seconds.
    """
    mine = set(ancestry())
    alive = []
    for entry in queue_notes.texts(held.get("chain")):
        pid, _, start = entry.partition(":")
        row = process_row(int(pid)) if pid.isdigit() else None
        if row is not None and row[1] == start:
            alive.append(entry)
    elsewhere = [entry.partition(":")[0] for entry in alive if entry not in mine]
    if elsewhere:
        return f"process {elsewhere[0]}"
    beat = held.get("beat")
    age = time.time() - beat if isinstance(beat, float) else LEASE_FRESH
    return (
        f"its last command ran {round(age / 60)} minutes ago"
        if alive and age < LEASE_FRESH
        else None
    )


def lease(root: Path, *, take_over: bool, token: str = "") -> None:
    """Start a drain: refused while another drain is running here, unless ``token`` is its own.

    The token names the drain: ``clean --all`` prints one on stderr, and the commands that follow
    may pass it back as ``--drain`` to say whose they are.
    """
    held = queue_notes.loaded(gatelib.read_text(state_dir(root) / LEASE))
    ours = bool(token) and token == held.get("token")
    why = None if take_over or ours else rival(held)
    if why is not None:
        message = (
            f"another drain is running on this repository ({why}), and `clean --all` would end"
            " its programs and remove its work trees. End that drain, or when it is gone for good"
            " run `clean --all --take-over`"
        )
        raise RefusedError(message)
    token = token if ours else secrets.token_hex(TOKEN_BYTES)
    queue_notes.write_note(state_dir(root) / LEASE, {"chain": ancestry(), "token": token})
    print(f"drain: {token}", file=sys.stderr)


def stamp(root: Path) -> None:
    """Record that a drain command ran now, if a drain holds the lease."""
    path = state_dir(root) / LEASE
    held = queue_notes.loaded(gatelib.read_text(path))
    if held:
        queue_notes.write_note(path, {**held, "beat": time.time()})


def release(root: Path) -> None:
    """End a drain: the report is its last command."""
    (state_dir(root) / LEASE).unlink(missing_ok=True)


# --- the notes -------------------------------------------------------------------------------


def question(item: object) -> str:
    """One open question of the spec role as a reason: its options, and why the first."""
    if not isinstance(item, dict):
        return f"open question: {item}"
    options = "; ".join(queue_notes.texts(item.get("options")))
    return (
        f"open question ({item.get('kind')}): {item.get('question')}"
        f" Options, the recommended first: {options}. Why the first: {item.get('recommendation')}"
    )


def reasons_of(result: dict[str, object]) -> list[str]:
    """Why a pipeline's result is not an approval, in the result's own words."""
    failures = queue_notes.tables(gatelib.table(result, "preflight").get("failures"))
    found = [f"{f.get('check')}: {f.get('detail')} -> {f.get('produce')}" for f in failures]
    asked = result.get("openQuestions")
    found += [question(item) for item in (asked if isinstance(asked, list) else [])]
    edits = queue_notes.tables(result.get("guardEdits"))
    found += [f"guard edit: {edit.get('path')}: {edit.get('edit')}" for edit in edits]
    found += queue_notes.texts(result.get("verdictForcedBy"))
    unresolved = queue_notes.texts(gatelib.table(result, "verdictRecord").get("unresolved"))
    found += [f"unresolved: {title}" for title in unresolved]
    disputes = queue_notes.tables(result.get("disputes"))
    found += [f"dispute: {d.get('path')}: {d.get('recommendation')}" for d in disputes]
    return found + [
        f"safeguard owed: `{kind}` gained an instance while a row it fires is still `later:`;"
        f" run {route.SAFEGUARD} on {DEFAULT_WORDS}, and the drain builds this ticket again"
        for kind in owed_kinds(result)
    ]


def owed_kinds(result: dict[str, object]) -> list[str]:
    """The kinds whose ``triggers`` red the result's last ``check`` shows (ADR 0028)."""
    output = result.get("checkOutput")
    found = OWED_RED.findall(output) if isinstance(output, str) else []
    return list(dict.fromkeys(found))


# --- what a branch holds ---------------------------------------------------------------------


def change_of(root: Path, base: str, branch: str) -> merge_ready.Change | None:
    """What ``branch`` changed against the default branch, merged into it already or not."""
    tip = merge_ready.commit(root, f"refs/heads/{branch}")
    if tip is None:
        return None
    merges = gatelib.git(root, "rev-list", "--merges", "--first-parent", "--parents", base) or ""
    lines = [line.split() for line in merges.splitlines()]
    before = next((parents[1] for parents in lines if tip in parents[2:]), base)
    change = merge_ready.read(root, before, tip)
    return change if isinstance(change, merge_ready.Change) else None


def assumptions(change: merge_ready.Change | None) -> list[dict[str, object]]:
    """The assumptions the branch's verdict record lists: what the merge judge read."""
    found = [path for path in (change.paths if change else ()) if merge_ready.RECORD.match(path)]
    return (
        queue_notes.tables(queue_notes.loaded(change.after(found[0])).get("assumptions"))
        if change and found
        else []
    )


def additions(change: merge_ready.Change | None) -> list[str]:
    """Each glossary term, ``_Avoid_`` item and ADR the branch added, as ``guard-diff`` names it."""
    manifest = merge_ready.manifest_of(change) if change else None
    if change is None or not isinstance(manifest, Manifest):
        return []
    root = change.root
    found = []
    for path in change.paths:
        group = manifest.group_of(path)
        if group is not None and group.additive:
            before = records.before(lambda args: merge_ready.git(root, *args), change.fork, path)
            note = records.note(path, before, change.after(path))
            if note.startswith(records.ADDITIONS):
                found.append(f"{path}: {note.removeprefix(records.ADDITIONS).strip()}")
    return found


def timings(tree: Path) -> dict[str, object]:
    """The seconds each gate last took in ``tree``, from the report its runner appends to."""
    lines = (gatelib.read_text(tree / gatelib.STATE / "timing.jsonl") or "").splitlines()
    rows = [queue_notes.loaded(line) for line in lines]
    return {str(row["key"]): row["seconds"] for row in rows if "key" in row and "seconds" in row}


# --- the plan --------------------------------------------------------------------------------


def ahead(root: Path, base: str, branch: str) -> bool:
    """Whether ``branch`` holds a commit the default branch does not."""
    count = gatelib.git(root, "rev-list", "--count", f"{base}..refs/heads/{branch}")
    return bool(count) and count != "0"


def pending(entry: Entry, ticket: preflight.Ticket, *, drain: Drain, seen: Seen) -> Entry:
    """``entry`` as the preflight leaves it: refused, waiting on queued tickets, or buildable."""
    report = preflight.Report(ref=ticket.ref)
    label = preflight.ready_label(drain.root)
    preflight.check_ticket(report, ticket, label, root=drain.root, gh=drain.tracker)
    refused = [
        f"{failure.check}: {failure.detail} -> {failure.produce}"
        for failure in report.failures
        if failure.check != "blocked"
    ]
    waits = []
    for ref in ticket.blockers:
        resolved = preflight.blocker_ref(ticket, ref, drain.root)
        blocker = preflight.fetch(resolved, drain.root, drain.tracker)
        if blocker is None:
            refused.append(f"blocked: blocker {ref} was not found")
        elif blocker.is_open and key(resolved) in seen.queued:
            waits.append(key(resolved))
        elif blocker.is_open:
            refused.append(f"blocked: blocker {resolved} is still open and is not agent-ready")
    if refused:
        return replace(entry, state=REFUSED, why=tuple(refused))
    return replace(entry, state=WAITING if waits else BUILDABLE, waits_on=tuple(waits))


def classify(ticket: preflight.Ticket, drain: Drain, seen: Seen) -> Entry:
    """What ``ticket`` is to this drain, read from git, its note and the preflight."""
    branch = route.own_branch(ticket)
    tree = tree_of(drain.root, branch)
    entry = Entry(ticket.ref, ticket.title, branch, BUILDABLE, worktree=str(tree))
    if branch in seen.merged:
        why = f"`{branch}` is merged, yet the ticket is still open: mark it resolved"
        return replace(entry, state=MERGED, why=(why,))
    # Why: a hold outranks the commits: a ticket parked or left to the owner after its branch got
    # commits is held until its text changes, and is not landed again for nothing (ADR 0023).
    hold = queue_notes.held(drain.root, branch, ticket)
    if hold is not None:
        return replace(entry, state=HELD, why=tuple(hold.reasons))
    if ahead(drain.root, seen.base, branch):
        waited = queue_notes.read_note(drain.root, branch)
        again = queue_notes.texts(waited.get("reasons")) if waited.get("outcome") == WAITS else []
        behind = (
            tuple(again)
            if gatelib.git(
                drain.root, "merge-base", "--is-ancestor", seen.base, f"refs/heads/{branch}"
            )
            == ""
            else (BEHIND,)
        )
        return replace(entry, state=BUILT, why=behind)
    if tree in seen.trees:
        return replace(entry, state=BUILDING)
    return pending(entry, ticket, drain=drain, seen=seen)


def settled(entries: list[Entry]) -> list[Entry]:
    """``entries`` with each ticket refused that waits on one this drain cannot merge."""
    live = {key(entry.ref) for entry in entries if entry.state in LIVE}
    waiting = [entry for entry in entries if entry.state == WAITING]
    reached = True
    while reached:
        reached = False
        for entry in waiting:
            if key(entry.ref) not in live and live.issuperset(entry.waits_on):
                live.add(key(entry.ref))
                reached = True
    by_ref = {key(entry.ref): entry for entry in entries}
    return [
        entry
        if entry.state != WAITING or key(entry.ref) in live
        else replace(
            entry,
            state=REFUSED,
            waits_on=(),
            why=tuple(
                f"blocked: waits on {ref}, which this drain cannot merge ({by_ref[ref].state})"
                + "".join(
                    f": {reason}" for reason in by_ref[ref].why[:1] if by_ref[ref].state == HELD
                )
                for ref in entry.waits_on
                if ref not in live
            ),
        )
        for entry in entries
    ]


def ordered(entries: list[Entry]) -> list[Entry]:
    """``entries`` with every ticket after the tickets it waits on, otherwise as listed."""
    placed: list[Entry] = []
    done: set[str] = set()
    rest = entries
    while rest:
        placed += [entry for entry in rest if done.issuperset(entry.waits_on)]
        done = {key(entry.ref) for entry in placed}
        rest = [entry for entry in rest if key(entry.ref) not in done]
    return placed


def pipeline_agents(root: Path) -> int:
    """The most agents one pipeline runs here: the critic joins once the binding is depended-on."""
    binding = gatelib.read_text(root / gatelib.BINDING) or ""
    depended_on = preflight.CRITIC_EVENT in application_types.life_events(binding)
    return PIPELINE_AGENTS if depended_on else YOUNG_PIPELINE_AGENTS


def plan(drain: Drain) -> Plan:
    """What each open agent-ready ticket is to a drain now, and which to start."""
    root = drain.root
    base = merge_ready.default_ref(root)
    tickets, _local = route.agent_ready(root, drain.gh)
    checkout = preflight.Report(ref="")
    preflight.check_checkout(checkout, root, base=base, branch=None)
    preflight.check_hooks(checkout, root)
    default = base.removeprefix("origin/")
    head = gatelib.git(root, "rev-parse", "--abbrev-ref", "HEAD")
    if head != default:
        # Why: the tickets are files of the work tree, and another branch may hold other tickets.
        checkout.fail("branch", f"HEAD is `{head}`, not `{default}`", f"`git switch {default}`")
    queued = frozenset(key(ticket.ref) for ticket in tickets)
    seen = Seen(base, frozenset(route.merged_features(root, base)), worktrees(root), queued)
    entries = ordered(settled([classify(ticket, drain, seen) for ticket in tickets]))
    blocked = tuple(f"{f.check}: {f.detail} -> {f.produce}" for f in checkout.failures)
    # Why: a row that says `buildable` beside a red preflight of the same ticket misleads.
    unfit = tuple(f"not started, the checkout is not fit: {line}" for line in blocked)
    entries = [
        replace(entry, why=unfit) if entry.state == BUILDABLE else entry for entry in entries
    ]
    # Why: a built ticket whose work tree is still there is being settled in it (rebased, judged
    # again), and holds its place under the cap as a building one does.
    building = sum(Path(entry.worktree) in seen.trees for entry in entries)
    buildable = [entry.ref for entry in entries if entry.state == BUILDABLE]
    to_build = sum(entry.state in {BUILDABLE, WAITING} for entry in entries)
    return Plan(
        base=base,
        forge=gatelib.git(root, "remote", "get-url", "origin") is not None,
        concurrency=drain.concurrency,
        blocked=blocked,
        tickets=tuple(entries),
        start=() if checkout.failures else tuple(buildable[: max(0, drain.concurrency - building)]),
        agents_at_most=max(0, to_build * (pipeline_agents(root) + UPDATE_AGENTS) - UPDATE_AGENTS),
    )


# --- the actions -----------------------------------------------------------------------------


def not_started(entry: Entry | None, drafted: Plan) -> list[str]:
    """Why the plan does not start a ticket."""
    if entry is None:
        return ["it is not an open agent-ready ticket"]
    if entry.state != BUILDABLE:
        return [f"it is {entry.state}", *entry.why, *(f"waits on {r}" for r in entry.waits_on)]
    return list(drafted.blocked) or [f"{drafted.concurrency} tickets are building: the cap"]


def open_ticket(drain: Drain, ref: str) -> int:
    """Make the work tree of ``ref`` when the plan starts it; say why not when it does not."""
    drafted = plan(drain)
    entry = next((e for e in drafted.tickets if key(e.ref) == key(ref)), None)
    if entry is None or entry.ref not in drafted.start:
        print(f"open: {ref} is not started: " + "; ".join(not_started(entry, drafted)))
        return 1
    tree = Path(entry.worktree)
    for directory in (users_home(), tree.parent):
        directory.mkdir(mode=OWNER_ONLY, exist_ok=True)
    theirs = foreign(drain.root)
    if theirs is not None:
        print(f"open: {theirs}: {NOT_OURS}")
        return 1
    added = gatelib.git(
        drain.root, "worktree", "add", "--quiet", "-B", entry.branch, str(tree), drafted.base
    )
    if added is None:
        print(f"open: no work tree of `{entry.branch}` could be made at {tree}")
        discard(drain.root, tree, f"{entry.branch}: {INTERRUPTED} run")
        return 1
    opened = now().isoformat(timespec="seconds")
    queue_notes.write_note(
        queue_notes.note_path(drain.root, entry.branch), {**asdict(entry), "opened": opened}
    )
    print(json.dumps({**asdict(entry), "state": BUILDING, "base": drafted.base}, indent=2))
    return 0


def rebasing(tree: Path) -> bool:
    """Whether a rebase of ``tree`` is under way: a run of ``update`` that stopped part-way."""
    for name in ("rebase-merge", "rebase-apply"):
        path = gatelib.git(tree, "rev-parse", "--git-path", name)
        if path and (tree / path).is_dir():
            return True
    return False


def revive(root: Path, branch: str, tree: Path) -> str | None:
    """Check out a built branch in its queue work tree again; why it could not be, if not."""
    for directory in (users_home(), tree.parent):
        directory.mkdir(mode=OWNER_ONLY, exist_ok=True)
    theirs = foreign(root)
    if theirs is not None:
        return f"{theirs}: {NOT_OURS}"
    if gatelib.git(root, "worktree", "add", "--quiet", str(tree), branch) is None:
        return f"no work tree of `{branch}` could be made at {tree}"
    return None


def drop_record(tree: Path) -> None:
    """Drop a last commit that is the verdict record: the rebase outdated what it certified."""
    last = gatelib.git(tree, "diff-tree", "--no-commit-id", "--name-only", "-r", "HEAD") or ""
    paths = last.splitlines()
    if paths and all(merge_ready.RECORD.match(path) for path in paths):
        gatelib.git(tree, "reset", "--quiet", "--hard", "HEAD~1")


def tree_for_update(root: Path, branch: str, *, base: str) -> tuple[str | None, bool]:
    """Why ``branch`` cannot be rebased in ``tree``, and whether it holds ``base`` already.

    A built branch with no work tree gets one, unless it holds ``base`` and needs none.
    """
    tree = tree_of(root, branch)
    if (
        tree in worktrees(root)
        and rebasing(tree)
        and gatelib.git(tree, "rebase", "--abort") is None
    ):
        return "an earlier rebase stopped, git cannot abort it", False
    if tree not in worktrees(root):
        if not ahead(root, base, branch):
            return f"`{branch}` has no queue work tree", False
        if gatelib.git(root, "merge-base", "--is-ancestor", base, branch) == "":
            return None, True
        return revive(root, branch, tree), False
    if dirty(tree):
        return "its work tree holds uncommitted changes: commit the change first", False
    return None, False


def update(drain: Drain, ticket: preflight.Ticket) -> int:
    """Rebase the ticket's branch onto the default branch when that moved; never half-way.

    A run killed mid-rebase leaves the tree detached at the default branch with the rebase
    under way; the next run abandons that rebase, which puts the branch back as it was, and
    starts again. A run killed after the rebase finished and before it said so is told by the
    branch tip the note recorded before the rebase began, and still drops a verdict record the
    rebase outdated.

    A built branch an earlier drain left, with no work tree, gets one. A last commit that is the
    verdict record is dropped after the rebase: it certified a tree that is gone, and the second
    review writes the record that replaces it.
    """
    branch = route.own_branch(ticket)
    tree = tree_of(drain.root, branch)
    base = merge_ready.default_ref(drain.root)
    refusal, current = tree_for_update(drain.root, branch, base=base)
    if current:
        print(json.dumps({"moved": False, "conflicts": []}))
        return 0
    path = queue_notes.note_path(drain.root, branch)
    held = queue_notes.read_note(drain.root, branch)
    note = {k: v for k, v in held.items() if k != "rebasing"}
    before = str(held.get("rebasing") or "")
    head = gatelib.git(tree, "rev-parse", "HEAD") if refusal is None else None
    current = (
        refusal is None and gatelib.git(tree, "merge-base", "--is-ancestor", base, "HEAD") == ""
    )
    if current and before:
        queue_notes.write_note(path, note)
        if head != before:
            # Why: the run was killed after the rebase; its record is still on top.
            drop_record(tree)
            print(json.dumps({"moved": True, "conflicts": []}))
            return 0
    if current:
        print(json.dumps({"moved": False, "conflicts": []}))
        return 0
    if refusal is None and gatelib.git(tree, "branch", "--remotes", "--contains", "HEAD"):
        refusal = "the branch is pushed, and a rebase would rewrite what the forge holds"
    if refusal is not None:
        print(f"update: {ticket.ref} is not rebased: {refusal}")
        return 1
    queue_notes.write_note(path, {**note, "rebasing": head})
    if gatelib.git(tree, "rebase", "--quiet", base) is not None:
        drop_record(tree)
        print(json.dumps({"moved": True, "conflicts": []}))
        queue_notes.write_note(path, note)
        return 0
    conflicts = (gatelib.git(tree, "diff", "--name-only", "--diff-filter=U") or "").splitlines()
    gatelib.git(tree, "rebase", "--abort")
    queue_notes.write_note(path, note)
    print(json.dumps({"moved": False, "conflicts": conflicts}))
    return 2


def close(
    root: Path,
    ticket: preflight.Ticket,
    *,
    outcome: str,
    reasons: list[str],
    result: dict[str, object] | None = None,
) -> None:
    """Remove the ticket's work tree and write its note: the outcome and what the report prints."""
    branch = route.own_branch(ticket)
    prior = queue_notes.read_note(root, branch)
    # Why: reasons the drain resolves itself leave the ticket built to be retried, a few times in
    # a row; after that, or for any other reason, the owner decides and the ticket is held.
    retry = outcome == WAITS and queue_notes.mechanical(reasons)
    before = prior.get("attempts")
    same = prior.get("digest") == queue_notes.digest(ticket) and isinstance(before, int)
    attempts = (before if same and isinstance(before, int) else 0) + 1 if retry else 0
    exhausted = retry and attempts >= queue_notes.RETRIES
    if exhausted:
        reasons = [
            (
                f"retried {attempts} times and each left it waiting for the same kind of reason,"
                " which the drain cannot resolve itself"
            ),
            *reasons,
        ]
    who: dict[str, object] = {
        "ref": ticket.ref,
        "title": ticket.title,
        "digest": queue_notes.digest(ticket),
        "branch": branch,
        "hold": outcome != MERGED and (exhausted or not retry),
        "attempts": attempts,
    }
    stays = settle(root, who, outcome=outcome, reasons=reasons, result=result)
    print(f"closed: {who['ref']}: {outcome}")
    for line in stays:
        print(f"kept: {line}")


def settle(
    root: Path,
    who: dict[str, object],
    *,
    outcome: str,
    reasons: list[str],
    result: dict[str, object] | None = None,
) -> list[str]:
    """``close`` from the ticket's ref, title and digest alone: a merge is recorded without it.

    A work tree that cannot be removed with its work saved stays, and the note says so; the lines
    returned say it too.
    """
    branch = str(who["branch"])
    tree = tree_of(root, branch)
    change = change_of(root, merge_ready.default_ref(root), branch)
    gates = timings(tree)
    kept = discard(root, tree, f"{branch}: {STASHED[outcome]} run")
    stays = [f"the work tree stays at {tree}: {kept}"] if kept else []
    note = {
        **queue_notes.read_note(root, branch),
        "hold": outcome != MERGED,
        "attempts": 0,
        **who,
        "outcome": outcome,
        "reasons": [*reasons, *stays],
        "assumptions": assumptions(change),
        "additions": additions(change),
        "gates": gates,
        "closed": now().isoformat(timespec="seconds"),
        "owes": owed_kinds(result or {}),
        "holds": queue_notes.holds_of(result or {}),
        "causes": queue_notes.causes_of(result or {}),
        "reported": False,
    }
    queue_notes.write_note(queue_notes.note_path(root, branch), note)
    return stays


def recorded_merge(root: Path, branch: str) -> bool:
    """Whether ``branch`` is merged and no note says so: a landing that was cut off."""
    note = queue_notes.read_note(root, branch)
    return (
        branch in route.merged_features(root, merge_ready.default_ref(root))
        and note.get("outcome") != MERGED
        and isinstance(note.get("ref"), str)
    )


def record_merge(root: Path, branch: str) -> list[str]:
    """Finish what a landing cut off after the default branch moved: the note, and the tree."""
    note = queue_notes.read_note(root, branch)
    who: dict[str, object] = {n: str(note.get(n) or "") for n in ("ref", "title", "digest")}
    reason = "the merge landed, and the run that landed it stopped before it said so"
    return settle(root, {**who, "branch": branch}, outcome=MERGED, reasons=[reason])


def land(drain: Drain, ticket: preflight.Ticket) -> int:
    """Run the binding's ``merge`` entry point on the ticket's branch; close it by the answer.

    Run again after a stop, it finishes from what git holds: a merge that landed is recorded, and
    what a stopped merge left in the repository is put back before the merge runs again.
    """
    command = gatelib.entry_point(drain.root, MERGE)
    if command is None or gatelib.git(drain.root, "remote", "get-url", "origin") is not None:
        print(
            "land: with a forge the pull request's merge job merges; with none the binding names"
            " the `merge` entry point this runs"
        )
        return 2
    branch = route.own_branch(ticket)
    with exclusive(drain.root):
        stamp(drain.root)
        for line in merge.recover(drain.root):
            print(line)
        if recorded_merge(drain.root, branch):
            print(f"closed: {ticket.ref}: {MERGED}", *record_merge(drain.root, branch), sep="\n")
            return 0
    argv = [*shlex.split(command), branch]
    try:
        done = subprocess.run(argv, cwd=drain.root, capture_output=True, text=True, check=False)
    except OSError as error:
        print(f"land: `{command}` could not run: {error}")
        return 2
    print(done.stdout + done.stderr, end="")
    if done.returncode not in {0, 1}:
        print(f"land: `{command}` stopped (exit {done.returncode}); run land again to finish it")
        return 2
    lines = done.stdout.splitlines()
    at = lines.index(merge_ready.OWNER) if merge_ready.OWNER in lines else len(lines)
    reasons = [line for line in lines[at + 1 :] if line.strip() and not line.startswith(merge.NEXT)]
    with exclusive(drain.root):
        close(drain.root, ticket, outcome=WAITS if done.returncode else MERGED, reasons=reasons)
    return done.returncode


def clean(root: Path, *, everything: bool, take_over: bool = False, token: str = "") -> int:
    """Remove the queue work trees whose branch is merged or gone; with ``everything``, all.

    ``everything`` starts a drain, so it is refused while another drain is running here.
    """
    if everything:
        lease(root, take_over=take_over, token=token)
    for line in merge.recover(root):
        print(line)
    merged = route.merged_features(root, merge_ready.default_ref(root))
    heads = gatelib.git(root, "for-each-ref", "--format=%(refname:short)", "refs/heads/") or ""
    owners = {tree_of(root, branch): branch for branch in heads.splitlines()}
    registered = worktrees(root)
    for tree in sorted(registered | set(queue_home(root).glob("*"))):
        branch = owners.get(tree)
        why = (
            "no work tree is registered there"
            if tree not in registered
            else "its branch is gone"
            if branch is None
            else "its branch is merged"
            if branch in merged
            else "the drain that made it was interrupted"
            if everything
            else None
        )
        if why is None:
            print(f"kept: {tree}: `{branch}` is not merged")
            continue
        if branch is not None and tree in registered and recorded_merge(root, branch):
            for line in record_merge(root, branch):
                print(f"kept: {line}")
        stays = discard(root, tree, f"{branch or tree.name}: {INTERRUPTED} run")
        print(f"removed: {tree}: {why}" if stays is None else f"left: {tree}: {why}, and {stays}")
    return 0


# --- the report ------------------------------------------------------------------------------


def minutes(note: dict[str, object]) -> str:
    """How long the ticket was in the queue and its slowest gates, as the report says it."""
    parts = []
    try:
        opened = datetime.fromisoformat(str(note.get("opened")))
        closed = datetime.fromisoformat(str(note.get("closed")))
    except ValueError:
        pass
    else:
        parts.append(f"{round((closed - opened).total_seconds() / 60)} min in the queue")
    gates = gatelib.table(note, "gates").items()
    timed = sorted(
        ((float(s), name) for name, s in gates if isinstance(s, int | float)), reverse=True
    )
    if timed:
        slowest = ", ".join(f"{name} {seconds:.1f}s" for seconds, name in timed[:SLOWEST])
        parts.append(f"slowest gates of its last check: {slowest}")
    return "; ".join(parts) or "not timed"


def section(title: str, rows: list[str]) -> list[str]:
    return [f"## {title}", "", *(rows or ["- none"]), ""]


def listed(note: dict[str, object]) -> list[str]:
    """A ticket's line and, under it, each reason as it was given."""
    head = f"- `{note.get('ref')}` {note.get('title')} (`{note.get('branch')}`)"
    return [head, *(f"  - {reason}" for reason in queue_notes.texts(note.get("reasons")))]


def render(notes: list[dict[str, object]], idle: list[Entry], stamp: datetime) -> str:
    """The drain's one report: what merged, what waits and why, every assumption."""
    by = {
        outcome: [row for note in notes if note.get("outcome") == outcome for row in listed(note)]
        for outcome in (MERGED, WAITS, PARKED)
    }
    held = [
        row
        for entry in idle
        if entry.state == HELD
        for row in listed({**asdict(entry), "reasons": list(entry.why)})
    ]
    unbuilt = [
        row
        for entry in idle
        if entry.state != HELD
        for row in (
            f"- `{entry.ref}` {entry.title}: {entry.state}",
            *(f"  - {reason}" for reason in entry.why),
            *(f"  - waits on {ref}" for ref in entry.waits_on),
        )
    ]
    assumed = [
        f"- `{note.get('ref')}`: {a.get('question')} Assumed: {a.get('assumed')}"
        f" Refuter: {a.get('refuter')}"
        for note in notes
        for a in queue_notes.tables(note.get("assumptions"))
    ]
    added = [
        f"- `{n.get('ref')}`: {text}"
        for n in notes
        for text in queue_notes.texts(n.get("additions"))
    ]
    return "\n".join(
        [
            f"# Queue drain, {stamp:%Y-%m-%d %H:%M}",
            "",
            *section("Merged", by[MERGED]),
            *section("Waits for you", by[WAITS]),
            *section("Parked", by[PARKED] + held),
            *section("Not built", unbuilt),
            *section("Assumptions", assumed),
            *section("Records added", added),
            *section("Timings", [f"- `{n.get('ref')}`: {minutes(n)}" for n in notes]),
        ]
    )


def report(drain: Drain) -> int:
    """Write the report of every ticket closed since the last one, and print it."""
    directory = gatelib.state_dir(drain.root) / QUEUE
    paths = sorted((directory / "tickets").glob("*.json"))
    notes = {path: queue_notes.loaded(gatelib.read_text(path)) for path in paths}
    fresh = {path: note for path, note in notes.items() if note.get("reported") is False}
    closed = {key(str(note.get("ref"))) for note in fresh.values()}
    idle = [e for e in plan(drain).tickets if key(e.ref) not in closed and e.state != BUILDING]
    stamp = now()
    text = render(list(fresh.values()), idle, stamp)
    target = directory / f"{stamp:%Y-%m-%d-%H%M%S}.md"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text, encoding="utf-8")
    for path, note in fresh.items():
        queue_notes.write_note(path, {**note, "reported": True})
    print(text)
    print(f"report: {target}")
    release(drain.root)
    return 0


def on_ticket(drain: Drain, options: argparse.Namespace) -> int:
    """The actions on one ticket's work tree: update it, land it, close it."""
    ticket = preflight.fetch(options.ref or "", drain.root, drain.tracker)
    if ticket is None:
        candidates = preflight.numbered_locally(options.ref or "", drain.root)
        if candidates:
            print(
                f"drain: {options.ref}: more than one local ticket starts with it: "
                + ", ".join(candidates)
                + "; give the path"
            )
            return 2
        print(
            f"drain: {options.ref}: no such ticket "
            "(a number on the tracker or starting a local file's name, or a file path)"
        )
        return 2
    if options.action == "update":
        return update(drain, ticket)
    if options.action == "land":
        return land(drain, ticket)
    result = queue_notes.loaded(gatelib.read_text(options.result) if options.result else None)
    reasons = [*options.reason, *reasons_of(result)]
    close(
        drain.root,
        ticket,
        outcome=options.outcome,
        reasons=reasons,
        result=result,
    )
    return 0


def show_plan(drain: Drain, _options: argparse.Namespace) -> int:
    print(json.dumps(asdict(plan(drain)), indent=2))
    return 0


WHOLE: dict[str, Callable[[Drain, argparse.Namespace], int]] = {
    "plan": show_plan,
    "open": lambda drain, options: open_ticket(drain, options.ref or ""),
    "clean": lambda drain, options: clean(
        drain.root,
        everything=options.everything,
        take_over=options.take_over,
        token=options.drain,
    ),
    "report": lambda drain, _options: report(drain),
}


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=[*WHOLE, "update", "land", "close"])
    parser.add_argument(
        "ref",
        nargs="?",
        help="the ticket: a number, #number, or a local file (a number may start its name)",
    )
    parser.add_argument("--root", default=".")
    parser.add_argument("--concurrency", type=int, default=CONCURRENCY)
    parser.add_argument("--offline", action="store_true", help="read no tracker")
    parser.add_argument("--outcome", choices=[MERGED, WAITS, PARKED], default=WAITS)
    parser.add_argument("--reason", action="append", default=[])
    parser.add_argument("--result", type=Path, help="the pipeline's result, as JSON")
    parser.add_argument("--all", action="store_true", dest="everything")
    parser.add_argument("--drain", default="", help="the token `clean --all` printed")
    parser.add_argument(
        "--take-over", action="store_true", help="with --all: another drain is gone"
    )
    options = parser.parse_args(argv[1:])
    root = main_tree(Path(options.root))
    if root is None:
        print(f"drain: {options.root} is not a git repository")
        return 2
    theirs = foreign(root)
    if theirs is not None:
        print(f"drain: {theirs}: {NOT_OURS}")
        return 2
    drain = Drain(root, None if options.offline else preflight.run_gh, options.concurrency)
    action = WHOLE.get(options.action, on_ticket)
    try:
        # Why: `land` holds the lock only at its two ends, since the merge it runs takes minutes.
        with exclusive(root) if options.action in LOCKED else contextlib.nullcontext():
            if options.action in STAMPED:
                stamp(root)
            return action(drain, options)
    except RefusedError as refused:
        print(f"{options.action}: {refused}" if options.action == "open" else f"drain: {refused}")
        return 1 if options.action == "open" else 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
