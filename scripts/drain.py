#!/usr/bin/env python3
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
import documents
import feature_preflight as preflight
import gatelib
import merge
import merge_ready
import publish_check
import queue_notes
import route
import run_time
import split

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "hooks"))
from ratchet_guards import records
from ratchet_guards.manifest import Manifest

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator, Sequence

CONCURRENCY = 3
_DECLARED = re.findall(
    r"^const AGENTS_AT_MOST = (\{.*\})$",
    (Path(__file__).resolve().parents[1] / "workflows/pipeline.js").read_text(encoding="utf-8"),
    re.MULTILINE,
)
_AGENTS: dict[str, int] = json.loads(_DECLARED[0])
PIPELINE_AGENTS = _AGENTS["dependedOn"]
YOUNG_PIPELINE_AGENTS = _AGENTS["young"]
UPDATE_AGENTS = _AGENTS["rejudge"]
BUILDABLE, BUILDING, BUILT = "buildable", "building", "built"
WAITING, HELD, MERGED, REFUSED = "waiting", "held", "merged", "refused"
LIVE = frozenset({BUILDABLE, BUILDING, BUILT})
WAITS, PARKED, SPLIT = "waits", "parked", queue_notes.SPLIT
STASHED = {MERGED: "merged", WAITS: "unapproved", PARKED: "blocked", SPLIT: "split"}
INTERRUPTED = "interrupted"
QUEUE = queue_notes.QUEUE
QUEUE_HOME = "ratchet-queue"
OWNER_ONLY = 0o700
OWNER_FILE = 0o600
LANDING_TRIES, LANDING_POLL = 20, 0.05
LANDING_RUNNING = 3
UNREAD = 3
PLAIN = "100644"
NOT_LANDED = 4
ON_LANDING = "not landed: the parts are on `{}`, and the checkout does not hold them"
IN_CHECKOUT = "not landed: the parts stay uncommitted in the checkout"
CHECKED_OUT = (
    "not landed: `{}` is checked out in a work tree, so it stays as it is: switch that work tree"
    " to another branch"
)
HOLDS_OTHER = (
    "not landed: `{}` holds other than just the parts this split writes, so it stays as it is:"
    " take what is yours from it, delete it"
)
FOREIGN = (
    "not landed: `{}` holds a commit this split did not make, so it stays as it is: take what is"
    " yours from it, delete it"
)
TAKEN = (
    "not landed: `{}` exists, and the split's note does not name it as the split's own, so it"
    " stays as it is: rename it"
)
FORGED = (
    "not landed: the split's note {} names `{}` as its landing branch, and this split lands on"
    " `{}`, so no branch is touched: correct the note"
)
STAGED = (
    "not landed: the checkout's index holds {}, so the parts stay uncommitted in the checkout:"
    " unstage them"
)
HOOKS_ADDED = (
    "not landed: `{}` holds what the commit hooks made besides the parts, so it stays as it is:"
    " take what is yours from it, delete it; the parts stay uncommitted in the checkout"
)
HOOKS_CHANGED = (
    "not landed: the commit hooks changed {} as they ran, so the parts stay uncommitted in the"
    " checkout, and `{}` holds them as they were filed"
)
HOOKS_HURT = (
    "not landed: what the owner staged changed in the checkout as the parts were committed, as"
    " the lines above say, so nothing is merged: `{}` holds the parts, and they stay uncommitted"
    " in the checkout"
)
CHANGED_BY = "a commit hook or another process {} `{}` as the parts were committed"
WROTE = (
    ", which the split wrote and no ref holds, so git's collection removes it once its grace"
    " period is over"
)
NOT_READ = (0, "")
SHA256_HEX = 64
HOOKS_EMPTIED = (
    "not landed: the commit hooks took the parts out of the commit, so `{}` holds a commit that"
    " adds nothing, and it stays as it is: delete it"
)
HOOKS_SILENT = (
    "`git commit` exited {} and printed nothing: a commit hook refused them, or moved the commit"
    " off `{}`"
)
INDEX_LOCKED = (
    "not landed: the parts are on `{}`, and `{}` locks the checkout's index, so the merge could"
    " not move it: once no git command runs in this repository, delete that file"
)
UNFINISHED = (
    "split: the checkout is in the middle of a merge or a rebase, or its index holds a conflict,"
    " so nothing is filed: finish it"
)
IN_PROGRESS = ("MERGE_HEAD", "CHERRY_PICK_HEAD", "REVERT_HEAD", "rebase-merge", "rebase-apply")
CANNOT_LAND = "blocked: {}, and no branch holds it, so it cannot land"
AGAIN = "{}; once the cause above is gone, run {} again"
FORGE_PARTS = (
    "next: this repository has a forge (`origin`): commit {} on a branch of their own and open"
    " the pull request; its merge job merges records alone, and the plan lists the parts once the"
    " default branch holds them"
)
CUT_OFF = "is not checked: its update was cut off after it moved the branch; run `update` again"
LANDING_MAY_RUN = "a landing may be in flight in another drain"
LOCK, LEASE = "ratchet-drain.lock", "ratchet-drain.lease"
LOCK_WAIT, LOCK_POLL = 30.0, 0.05
PROC_START = 19
DEPTH = 64
TOKEN_BYTES = 4
OTHERS_WRITE = 0o022
NOT_OURS = "the queue's work trees go in a directory only this user can write; remove it"
NOT_A_TREE = "it is not a work tree of this repository or an empty directory, so it stays"
MERGE = "merge"
SLOWEST = 3
LOCKED = frozenset({"open", "update", "check", "close", "clean", "report", "split"})
EXPIRED = "docs(documents): delete expired plans and keep their answers"
SIDES = {frozenset({1, 2}): "--ours", frozenset({1, 3}): "--theirs"}
OWED_RED = re.compile(r"triggers: '([^']+)' gained \d+ instance")
REVIEW_RED = re.compile(r"triggers: architecture review owed: '([^']+)' gained \d+ instance")
DEFAULT_WORDS = "the default branch"
UNREADABLE_CODE = "refs"
START_DROPPED = frozenset({"hold", "attempts", "rebasing", "discarding", "start", "passed"})
CLOSERS = frozenset({"open", "update", "check"})
NO_TICKET = "it is not an open agent-ready ticket"
BEHIND = (
    "behind the default branch: `update` it and judge it again before it lands, since its last"
    " review was of a tree the default branch has moved past"
)


@dataclass(frozen=True)
class Entry:
    ref: str
    title: str
    branch: str
    state: str
    why: tuple[str, ...] = ()
    waits_on: tuple[str, ...] = ()
    worktree: str = ""


@dataclass(frozen=True)
class Plan:
    base: str
    forge: bool
    concurrency: int
    blocked: tuple[str, ...]
    tickets: tuple[Entry, ...]
    start: tuple[str, ...]
    agents_at_most: int


@dataclass(frozen=True)
class Seen:
    base: str
    merged: frozenset[str]
    trees: frozenset[Path]
    queued: frozenset[str]


@dataclass(frozen=True)
class Exposed:
    files: dict[str, tuple[int, str] | None]
    written: frozenset[str]
    stash: str | None
    algorithm: str


@dataclass(frozen=True)
class Drain:
    root: Path
    gh: preflight.Runner | None
    concurrency: int = CONCURRENCY

    @property
    def tracker(self) -> preflight.Runner:
        return self.gh or (lambda _args: None)


def now() -> datetime:
    return datetime.now(tz=UTC).astimezone()


def key(ref: str) -> str:
    return ref.lstrip("#")


def main_tree(start: Path) -> Path | None:
    listing = gatelib.git(start, "worktree", "list", "--porcelain")
    return Path(listing.splitlines()[0].removeprefix("worktree ")) if listing else None


def users_home() -> Path:
    return Path(tempfile.gettempdir()).resolve() / f"{QUEUE_HOME}-{os.getuid()}"


def queue_home(root: Path) -> Path:
    name = f"{root.name}-{hashlib.sha256(str(root).encode()).hexdigest()[:8]}"
    return users_home() / name


def foreign(root: Path) -> str | None:
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
    home = queue_home(root)
    listing = gatelib.git(root, "worktree", "list", "--porcelain") or ""
    registered = [line.removeprefix("worktree ") for line in listing.splitlines()]
    return frozenset(Path(path) for path in registered if Path(path).parent == home)


def dirty(tree: Path) -> bool:
    status = gatelib.git(tree, "status", "--porcelain", "--untracked-files=all")
    return status is None or bool(status)


def unsaved(tree: Path) -> bool:
    status = gatelib.git(tree, "status", "--porcelain", "--untracked-files=all")
    if status is None:
        return True
    return any(
        not (line.startswith("?? ") and (tree / line[3:] / ".git").exists())
        for line in status.splitlines()
    )


def stash_away(tree: Path, label: str) -> str | None:
    if not unsaved(tree):
        return None
    if gatelib.git(tree, "stash", "push", "--include-untracked", "--quiet", "-m", label) is None:
        return (
            "git could not stash what it holds uncommitted (a stale `index.lock`, a merge left"
            " unfinished or a damaged index): finish or commit it there, then run `clean` again"
        )
    return "what it holds uncommitted is still there after the stash" if unsaved(tree) else None


def discard(root: Path, tree: Path, label: str) -> str | None:
    if tree.is_symlink():
        return NOT_A_TREE
    if tree in worktrees(root):
        gatelib.git(tree, "rebase", "--abort")
        if gatelib.git(tree, "rev-parse", "--verify", "--quiet", "HEAD") is None:
            gatelib.git(tree, "switch", "--detach", "--quiet", merge_ready.default_ref(root))
        kept = stash_away(tree, label)
        if kept is None:
            for pid in gatelib.end_running_from(tree.resolve()):
                print(f"ended: process {pid}, which ran a program of {tree}")
            kept = stash_away(tree, label)
        if kept is not None:
            return kept
        gatelib.git(root, "worktree", "remove", "--force", str(tree))
        shutil.rmtree(tree, ignore_errors=True)
    elif tree.is_dir() and not any(tree.iterdir()):
        tree.rmdir()
    gatelib.git(root, "worktree", "prune")
    if tree.parent.is_dir() and not any(tree.parent.iterdir()):
        tree.parent.rmdir()
    return None if not tree.exists() else NOT_A_TREE


class RefusedError(Exception): ...


def state_dir(root: Path) -> Path:
    return Path(gatelib.git(root, "rev-parse", "--absolute-git-dir") or root / "git-state")


@contextlib.contextmanager
def exclusive(root: Path) -> Iterator[None]:
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
    try:
        text = Path(f"/proc/{pid}/stat").read_text(encoding="utf-8")
    except OSError:
        return None
    fields = text.rpartition(")")[2].split()
    return int(fields[1]), fields[PROC_START]


def ancestry() -> list[str]:
    chain: list[str] = []
    pid = os.getpid()
    while pid > 1 and len(chain) < DEPTH and (row := process_row(pid)):
        chain.append(f"{pid}:{row[1]}")
        pid = row[0]
    return chain


def in_the_trees(root: Path, mine: set[str]) -> str | None:
    home = queue_home(root)
    for entry in Path("/proc").glob("[0-9]*"):
        try:
            places = [(entry / "exe").readlink(), (entry / "cwd").readlink()]
        except OSError:
            continue
        if entry.name not in mine and any(place.is_relative_to(home) for place in places):
            return entry.name
    return None


def rival(root: Path, held: dict[str, object]) -> str | None:
    chain = ancestry()
    mine = {entry.partition(":")[0] for entry in chain}
    for entry in queue_notes.texts(held.get("chain")):
        pid, _, start = entry.partition(":")
        row = process_row(int(pid)) if pid.isdigit() else None
        if entry not in chain and row is not None and row[1] == start:
            return f"process {pid}"
    found = in_the_trees(root, mine)
    return f"process {found} runs in its work trees" if found else None


def lease(root: Path, *, take_over: bool, token: str = "") -> None:
    held = queue_notes.loaded(gatelib.read_text(state_dir(root) / LEASE))
    ours = bool(token) and token == held.get("token")
    why = None if take_over or ours else rival(root, held)
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


def release(root: Path, token: str = "") -> str | None:
    why = other_drain(root, token)
    if why is None:
        (state_dir(root) / LEASE).unlink(missing_ok=True)
    return why


def other_drain(root: Path, token: str = "") -> str | None:
    held = queue_notes.loaded(gatelib.read_text(state_dir(root) / LEASE))
    ours = bool(token) and token == held.get("token")
    return None if ours else rival(root, held)


def question(item: object) -> str:
    if not isinstance(item, dict):
        return f"open question: {item}"
    options = "; ".join(queue_notes.texts(item.get("options")))
    return (
        f"open question ({item.get('kind')}): {item.get('question')}"
        f" Options, the recommended first: {options}. Why the first: {item.get('recommendation')}"
    )


def findings_of(result: dict[str, object]) -> list[str]:
    listed = [
        finding
        for key in ("unresolvedBlockingFindings", "reviewIssues")
        for finding in queue_notes.tables(result.get(key))
        if finding.get("severity") in {"blocker", "major"}
    ]
    titles = {finding.get("title") for finding in listed}
    record = queue_notes.texts(gatelib.table(result, "verdictRecord").get("unresolved"))
    lines = [finding_line(finding) for finding in listed]
    lines += [f"unresolved: {title}" for title in record if title not in titles]
    return list(dict.fromkeys(lines))


def finding_line(finding: dict[str, object]) -> str:
    breaks = gatelib.table(finding, "breaks")
    quote = f' (breaks {breaks.get("record")}: "{breaks.get("quote")}")' if breaks else ""
    return f"unresolved: {finding.get('title')}{quote}"


def reasons_of(result: dict[str, object]) -> list[str]:
    failures = queue_notes.tables(gatelib.table(result, "preflight").get("failures"))
    found = [f"{f.get('check')}: {f.get('detail')} -> {f.get('produce')}" for f in failures]
    asked = result.get("openQuestions")
    found += [question(item) for item in (asked if isinstance(asked, list) else [])]
    edits = queue_notes.tables(result.get("guardEdits"))
    found += [f"guard edit: {edit.get('path')}: {edit.get('edit')}" for edit in edits]
    found += queue_notes.texts(result.get("verdictForcedBy"))
    found += findings_of(result)
    disputes = queue_notes.tables(result.get("disputes"))
    found += [f"dispute: {d.get('path')}: {d.get('recommendation')}" for d in disputes]
    found += [
        f"safeguard owed: `{kind}` gained an instance while a row it fires is still `later:`;"
        f" run {route.SAFEGUARD} on {DEFAULT_WORDS}, and the drain builds this ticket again"
        for kind in owed_kinds(result)
    ]
    return found + [
        f"architecture review owed: `{kind}` gained an instance while the review it reopens has"
        f" not landed; run {route.ARCHITECTURE} on {DEFAULT_WORDS}, and the drain builds this"
        " ticket again"
        for kind in owed_kinds(result, REVIEW_RED)
    ]


def owed_kinds(result: dict[str, object], red: re.Pattern[str] = OWED_RED) -> list[str]:
    output = result.get("checkOutput")
    found = red.findall(output) if isinstance(output, str) else []
    return list(dict.fromkeys(found))


def change_of(root: Path, base: str, branch: str) -> merge_ready.Change | None:
    tip = merge_ready.commit(root, f"refs/heads/{branch}")
    if tip is None:
        return None
    merges = gatelib.git(root, "rev-list", "--merges", "--first-parent", "--parents", base) or ""
    lines = [line.split() for line in merges.splitlines()]
    before = next((parents[1] for parents in lines if tip in parents[2:]), base)
    change = merge_ready.read(root, before, tip)
    return change if isinstance(change, merge_ready.Change) else None


def assumptions(change: merge_ready.Change | None) -> list[dict[str, object]]:
    found = [path for path in (change.paths if change else ()) if merge_ready.RECORD.match(path)]
    return (
        queue_notes.tables(queue_notes.loaded(change.after(found[0])).get("assumptions"))
        if change and found
        else []
    )


def additions(change: merge_ready.Change | None) -> list[str]:
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
    lines = (gatelib.read_text(tree / gatelib.STATE / "timing.jsonl") or "").splitlines()
    rows = [queue_notes.loaded(line) for line in lines]
    return {str(row["key"]): row["seconds"] for row in rows if "key" in row and "seconds" in row}


def ahead(root: Path, base: str, branch: str) -> bool:
    count = gatelib.git(root, "rev-list", "--count", f"{base}..refs/heads/{branch}")
    return bool(count) and count != "0"


def pending(entry: Entry, ticket: preflight.Ticket, *, drain: Drain, seen: Seen) -> Entry:
    report = preflight.Report(ref=ticket.ref)
    label = preflight.ready_label(drain.root)
    preflight.check_ticket(report, ticket, label, root=drain.root, gh=drain.tracker)
    refused = [
        f"{failure.check}: {failure.detail} -> {failure.produce}"
        for failure in report.failures
        if failure.check != "blocked"
    ]
    waits = []
    for blocker in preflight.blockers(ticket, drain.root, drain.tracker):
        why = preflight.unmet(blocker)
        if why is None:
            continue
        part = blocker.ticket is None and blocker.split_from and blocker.ref
        if (blocker.ticket is not None and key(blocker.ref) in seen.queued) or (
            part and on_a_branch(drain.root, blocker.ref)
        ):
            waits.append(key(blocker.ref))
        elif part:
            refused.append(CANNOT_LAND.format(why))
        else:
            agent_ready = "" if blocker.ticket is None else " and is not agent-ready"
            refused.append(f"blocked: {why}{agent_ready}")
    if refused:
        return replace(entry, state=REFUSED, why=tuple(refused))
    return replace(entry, state=WAITING if waits else BUILDABLE, waits_on=tuple(waits))


def on_a_branch(root: Path, path: str) -> bool:
    tips = gatelib.git(root, "for-each-ref", "--format=%(objectname)", merge.HEADS, "refs/remotes/")
    asked = "".join(f"{tip}:{path}\n" for tip in (tips or "").split())
    found = merge.porcelain(root, "cat-file", "--batch-check", given=asked).stdout.splitlines()
    return any(not line.endswith(" missing") for line in found)


def classify(ticket: preflight.Ticket, drain: Drain, seen: Seen) -> Entry:
    branch = route.own_branch(ticket)
    tree = tree_of(drain.root, branch)
    entry = Entry(ticket.ref, ticket.title, branch, BUILDABLE, worktree=str(tree))
    if branch in seen.merged:
        why = f"`{branch}` is merged, yet the ticket is still open: mark it resolved"
        return replace(entry, state=MERGED, why=(why,))
    hold = queue_notes.held(drain.root, branch, ticket)
    if hold is not None:
        return replace(entry, state=HELD, why=tuple(hold.reasons))
    if ahead(drain.root, seen.base, branch):
        waited = queue_notes.read_note(drain.root, branch)
        again = queue_notes.texts(waited.get("reasons")) if waited.get("outcome") == WAITS else []
        current = gatelib.git(
            drain.root, "merge-base", "--is-ancestor", seen.base, f"refs/heads/{branch}"
        ) == "" and not waited.get("rebasing")
        behind = tuple(again) if current else (BEHIND,)
        return replace(entry, state=BUILT, why=behind)
    if tree in seen.trees:
        return replace(entry, state=BUILDING)
    return pending(entry, ticket, drain=drain, seen=seen)


def settled(entries: list[Entry]) -> list[Entry]:
    waiting = [entry for entry in entries if entry.state == WAITING]
    live = unlanded(entries) | {key(entry.ref) for entry in entries if entry.state in LIVE}
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


def unlanded(entries: list[Entry]) -> set[str]:
    return {ref for entry in entries for ref in entry.waits_on} - {
        key(entry.ref) for entry in entries
    }


def ordered(entries: list[Entry]) -> list[Entry]:
    placed: list[Entry] = []
    done = unlanded(entries)
    rest = entries
    while rest:
        placed += [entry for entry in rest if done.issuperset(entry.waits_on)]
        done = unlanded(entries) | {key(entry.ref) for entry in placed}
        rest = [entry for entry in rest if key(entry.ref) not in done]
    return placed


def pipeline_agents(root: Path) -> int:
    binding = gatelib.read_text(root / gatelib.BINDING) or ""
    depended_on = preflight.CRITIC_EVENT in application_types.life_events(binding)
    return PIPELINE_AGENTS if depended_on else YOUNG_PIPELINE_AGENTS


def plan(drain: Drain) -> Plan:
    root = drain.root
    base = merge_ready.default_ref(root)
    tickets, _local = route.agent_ready(root, drain.gh)
    checkout = preflight.Report(ref="")
    preflight.check_checkout(checkout, root, base=base, branch=None)
    preflight.check_hooks(checkout, root)
    preflight.check_fill_first(checkout, root)
    default = base.removeprefix("origin/")
    head = gatelib.git(root, "rev-parse", "--abbrev-ref", "HEAD")
    if head != default:
        checkout.fail("branch", f"HEAD is `{head}`, not `{default}`", f"`git switch {default}`")
    queued = frozenset(key(ticket.ref) for ticket in tickets)
    seen = Seen(base, frozenset(route.merged_features(root, base)), worktrees(root), queued)
    entries = ordered(settled([classify(ticket, drain, seen) for ticket in tickets]))
    blocked = tuple(f"{f.check}: {f.detail} -> {f.produce}" for f in checkout.failures)
    unfit = tuple(f"not started, the checkout is not fit: {line}" for line in blocked)
    entries = [
        replace(entry, why=unfit) if entry.state == BUILDABLE else entry for entry in entries
    ]
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


def not_started(entry: Entry | None, drafted: Plan) -> list[str]:
    if entry is None:
        return [NO_TICKET]
    if entry.state != BUILDABLE:
        return [f"it is {entry.state}", *entry.why, *(f"waits on {r}" for r in entry.waits_on)]
    return list(drafted.blocked) or [f"{drafted.concurrency} tickets are building: the cap"]


def forge_gh(drain: Drain) -> preflight.Runner:
    if drain.gh is not preflight.run_gh:
        return drain.tracker

    def run(args: Sequence[str]) -> str | None:
        try:
            done = subprocess.run(
                ["gh", *args], capture_output=True, text=True, check=False, cwd=drain.root
            )
        except OSError:
            return None
        return done.stdout if done.returncode == 0 else None

    return run


def open_ticket(drain: Drain, ref: str) -> int:
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
    try:
        start = asdict(publish_check.start_of(tree, entry.branch, forge_gh(drain)))
    except publish_check.UnreadableError as error:
        print(f"open: the start of `{entry.branch}` cannot be read: {error}")
        discard(drain.root, tree, f"{entry.branch}: {INTERRUPTED} run")
        return 1
    opened = now().isoformat(timespec="seconds")
    path = queue_notes.note_path(drain.root, entry.branch)
    prior = queue_notes.loaded(gatelib.read_text(path))
    keep = prior.get("reported") is False and prior.get("outcome") not in {None, MERGED}
    kept = {k: v for k, v in prior.items() if k not in START_DROPPED} if keep else {}
    queue_notes.write_note(path, {**kept, **asdict(entry), "opened": opened, "start": start})
    named = [asdict(n) for n in preflight.ticket_named_gates(entry.ref, tree, drain.tracker)]
    print(
        json.dumps(
            {
                **asdict(entry),
                "state": BUILDING,
                "base": drafted.base,
                "start": start,
                "named_gates": named,
            },
            indent=2,
        )
    )
    return 0


def rebasing(tree: Path) -> bool:
    for name in ("rebase-merge", "rebase-apply"):
        path = gatelib.git(tree, "rev-parse", "--git-path", name)
        if path and (tree / path).is_dir():
            return True
    return False


def revive(drain: Drain, branch: str, tree: Path) -> str | None:
    root = drain.root
    if len(worktrees(root)) >= drain.concurrency:
        return f"{drain.concurrency} tickets are building: the cap"
    for directory in (users_home(), tree.parent):
        directory.mkdir(mode=OWNER_ONLY, exist_ok=True)
    theirs = foreign(root)
    if theirs is not None:
        return f"{theirs}: {NOT_OURS}"
    if gatelib.git(root, "worktree", "add", "--quiet", str(tree), branch) is None:
        return f"no work tree of `{branch}` could be made at {tree}"
    return None


def drop_record(tree: Path) -> None:
    last = gatelib.git(tree, "diff-tree", "--no-commit-id", "--name-only", "-r", "HEAD") or ""
    paths = last.splitlines()
    if paths and all(merge_ready.RECORD.match(path) for path in paths):
        gatelib.git(tree, "reset", "--quiet", "--hard", "HEAD~1")


def tree_for_update(
    drain: Drain, branch: str, *, base: str, marked: bool
) -> tuple[str | None, bool]:
    root = drain.root
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
        if not marked and gatelib.git(root, "merge-base", "--is-ancestor", base, branch) == "":
            return None, True
        return revive(drain, branch, tree), False
    if dirty(tree):
        return "its work tree holds uncommitted changes: commit the change first", False
    return None, False


def update(drain: Drain, ticket: preflight.Ticket) -> int:
    branch = route.own_branch(ticket)
    if merged_by_note(drain.root, branch):
        print(f"update: {ticket.ref} is merged and is not rebased")
        return 1
    drop_mark(drain.root, branch)
    return rebase(drain, ticket)


def merged_by_note(root: Path, branch: str) -> bool:
    if recorded_merge(root, branch):
        record_merge_aloud(root, branch)
    if queue_notes.read_note(root, branch).get("outcome") != MERGED:
        return False
    say_kept(finish_close(root, branch))
    return True


def drop_mark(root: Path, branch: str) -> None:
    note = queue_notes.read_note(root, branch)
    if note.get("discarding"):
        done = {k: v for k, v in note.items() if k != "discarding"}
        queue_notes.write_note(queue_notes.note_path(root, branch), done)


def noted_start(root: Path, branch: str) -> publish_check.Start | None:
    start = queue_notes.read_note(root, branch).get("start")
    try:
        return publish_check.parse(json.dumps({"start": start}))
    except publish_check.MalformedError:
        return None


def passed(root: Path, branch: str) -> bool:
    return queue_notes.read_note(root, branch).get("passed") is True


def mark_passed(root: Path, branch: str) -> None:
    note = queue_notes.read_note(root, branch)
    queue_notes.write_note(queue_notes.note_path(root, branch), {**note, "passed": True})


def gate_start(root: Path, branch: str, *, passed: bool, done: str = "nothing was changed") -> bool:
    try:
        return queue_notes.gate_feature_start(root, branch, tree_of(root, branch), passed=passed)
    except OSError as error:
        stays = "unpassed, so a commit in its work tree is refused" if passed else "passed"
        message = (
            f"`{queue_notes.feature_start_name(branch)}` in the git directory cannot be written"
            f" ({error.strerror}): the one-ticket start of `{branch}` stays {stays}, and {done};"
            " make that folder writable, then run this again"
        )
        raise RefusedError(message) from None


def read_present(
    drain: Drain, tree: Path, start: publish_check.Start
) -> tuple[list[str], bool, str | None]:
    try:
        return publish_check.check(tree, start, forge_gh(drain), drained=True)
    except publish_check.UnreadableError as error:
        return [], False, str(error)


def park_published(drain: Drain, ticket: preflight.Ticket, moves: list[str]) -> None:
    for move in moves:
        print(f"{publish_check.SAID} {move}")
    print(publish_check.RED)
    reasons = [f"a role published: {move}" for move in moves]
    close(drain.root, ticket, outcome=PARKED, reasons=reasons)


def unreadable_line(why: str) -> str:
    return f"{publish_check.SAID} the present state cannot be read: {why}; {publish_check.NEVER}"


def cut_off(root: Path, branch: str) -> bool:
    mark = queue_notes.read_note(root, branch).get("rebasing")
    return bool(mark) and gatelib.git(root, "rev-parse", f"refs/heads/{branch}") != mark


def checkable(drain: Drain, ticket: preflight.Ticket) -> tuple[publish_check.Start, Path] | int:
    root, branch = drain.root, route.own_branch(ticket)
    if merged_by_note(root, branch):
        print(f"check: {ticket.ref} is merged and is not checked")
        return 1
    start = noted_start(root, branch)
    if start is None:
        print(f"check: {ticket.ref} has no start to check against: `open` records it")
        return 2
    unread = (
        "is held and is not checked: its text must change first"
        if queue_notes.held(root, branch, ticket) is not None
        else CUT_OFF
        if cut_off(root, branch)
        else None
    )
    if unread is not None:
        print(f"check: {ticket.ref} {unread}")
        return UNREAD
    tree = tree_of(root, branch)
    if tree in worktrees(root):
        return start, tree
    refusal = (
        revive(drain, branch, tree)
        if ahead(root, merge_ready.default_ref(root), branch)
        else f"`{branch}` has no commit of its own and no work tree, so nothing is to be read"
    )
    if refusal is not None:
        print(f"check: {ticket.ref} is not checked: {refusal}")
        return UNREAD
    drop_mark(root, branch)
    return start, tree


def check(drain: Drain, ticket: preflight.Ticket, *, final: bool) -> int:
    ready = checkable(drain, ticket)
    if isinstance(ready, int):
        return ready
    start, tree = ready
    root, branch = drain.root, route.own_branch(ticket)
    moves, halted, unread = read_present(drain, tree, start)
    if moves:
        park_published(drain, ticket, moves)
        return 1
    if unread is not None:
        line = unreadable_line(unread)
        print(line)
        close(root, ticket, outcome=WAITS, reasons=[f"{UNREADABLE_CODE}: {line}"])
        return UNREAD
    if final and not halted:
        gate_start(root, branch, passed=True, done="the check was green, yet no pass is recorded")
    if final:
        mark_passed(root, branch)
    if halted:
        print(f"{publish_check.SAID} {publish_check.HALTED}; {publish_check.NEVER}")
        base = (start.rebase or publish_check.Rebase(DEFAULT_WORDS, ())).base
        why = f"conflict: the rebase onto `{base}` was left unfinished"
        close(root, ticket, outcome=WAITS, reasons=[why])
        return UNREAD
    print(publish_check.GREEN)
    return 0


def first_check(drain: Drain, ticket: preflight.Ticket, tree: Path) -> int | None:
    root, branch = drain.root, route.own_branch(ticket)
    start = noted_start(root, branch)
    if start is None or passed(root, branch):
        return None
    moves, _, unread = read_present(drain, tree, start)
    if moves:
        park_published(drain, ticket, moves)
        return 1
    if unread is not None:
        print(f"update: {ticket.ref} is not rebased: {unreadable_line(unread)}")
        return UNREAD
    mark_passed(root, branch)
    return None


def restarted(
    drain: Drain,
    tree: Path,
    note: dict[str, object],
    *,
    branch: str,
    rebase_onto: str | None = None,
) -> dict[str, object]:
    kept = {k: v for k, v in note.items() if k not in {"start", "passed"}}
    try:
        start = publish_check.start_of(tree, branch, forge_gh(drain), rebase_onto=rebase_onto)
    except publish_check.UnreadableError as error:
        print(f"update: the start of `{branch}` cannot be read: {error}", file=sys.stderr)
        return kept
    return {**kept, "start": asdict(start)}


def rebase(drain: Drain, ticket: preflight.Ticket) -> int:
    branch = route.own_branch(ticket)
    tree = tree_of(drain.root, branch)
    base = merge_ready.default_ref(drain.root)
    path = queue_notes.note_path(drain.root, branch)
    held = queue_notes.read_note(drain.root, branch)
    refusal, current = tree_for_update(drain, branch, base=base, marked=bool(held.get("rebasing")))
    if current:
        print(json.dumps({"moved": False, "conflicts": []}))
        return 0
    if refusal is None and ahead(drain.root, base, branch):
        stopped = first_check(drain, ticket, tree)
        if stopped is not None:
            return stopped
        held = queue_notes.read_note(drain.root, branch)
    note = {k: v for k, v in held.items() if k not in {"rebasing", "discarding"}}
    before = str(held.get("rebasing") or "")
    head = gatelib.git(tree, "rev-parse", "HEAD") if refusal is None else None
    current = (
        refusal is None and gatelib.git(tree, "merge-base", "--is-ancestor", base, "HEAD") == ""
    )
    if current and before:
        if head != before:
            waiting = after_rebase(tree)
            queue_notes.write_note(
                path, {**restarted(drain, tree, note, branch=branch), "unexpired": waiting}
            )
            print(json.dumps({"moved": True, "conflicts": []}))
            return 0
        queue_notes.write_note(path, note)
    if current:
        print(json.dumps({"moved": False, "conflicts": []}))
        return 0
    if refusal is None and gatelib.git(tree, "branch", "--remotes", "--contains", "HEAD"):
        refusal = "the branch is pushed, and a rebase would rewrite what the forge holds"
    if refusal is not None:
        print(f"update: {ticket.ref} is not rebased: {refusal}")
        return 1
    queue_notes.write_note(path, {**note, "rebasing": head})
    return rebase_tree(drain, tree, note, branch=branch)


def unmerged(tree: Path) -> dict[str, frozenset[int]]:
    found: dict[str, set[int]] = {}
    for line in (gatelib.git(tree, "ls-files", "--unmerged") or "").splitlines():
        head, _, name = line.partition("\t")
        found.setdefault(name, set()).add(int(head.split()[2]))
    return {name: frozenset(stages) for name, stages in found.items()}


def settled_stop(tree: Path, stages: dict[str, frozenset[int]]) -> bool:
    deleted = [name for name, held in stages.items() if held in SIDES]
    words = [name for name, held in stages.items() if name == documents.BRIEF and held >= {2, 3}]
    if not stages or len(deleted) + len(words) != len(stages):
        return False
    if not all(documents.is_steering(name) for name in deleted):
        return False
    for name in deleted:
        gatelib.git(tree, "checkout", SIDES[stages[name]], "--", name)
    for name in words:
        text = documents.settled(gatelib.read_text(tree / name) or "")
        if text is None:
            return False
        (tree / name).write_text(text, encoding="utf-8")
    gatelib.git(tree, "add", "--", *stages)
    return set(deleted) <= documents.listed(documents.work_tree(tree))


def carry_on(tree: Path) -> list[str] | None:
    while True:
        stages = unmerged(tree)
        if not settled_stop(tree, stages):
            return sorted(stages)
        if gatelib.git(tree, "-c", "core.editor=true", "rebase", "--continue") is not None:
            return None


def after_rebase(tree: Path) -> list[str]:
    drop_record(tree)
    done = documents.expire(tree, now().date())
    if done.problem is not None:
        print(f"update: {done.problem}", file=sys.stderr)
        return [f"update: {done.problem}"]
    if done.deleted:
        gatelib.git(tree, "add", "--all")
        gatelib.git(tree, "commit", "--quiet", "--no-verify", "-m", EXPIRED)
    return []


def rebase_tree(drain: Drain, tree: Path, note: dict[str, object], *, branch: str) -> int:
    base = merge_ready.default_ref(drain.root)
    path = queue_notes.note_path(drain.root, branch)
    conflicts = None if gatelib.git(tree, "rebase", "--quiet", base) is not None else carry_on(tree)
    if conflicts is None:
        waiting = after_rebase(tree)
        print(json.dumps({"moved": True, "conflicts": []}))
        queue_notes.write_note(
            path, {**restarted(drain, tree, note, branch=branch), "unexpired": waiting}
        )
        return 0
    gatelib.git(tree, "rebase", "--abort")
    queue_notes.write_note(path, restarted(drain, tree, note, branch=branch, rebase_onto=base))
    print(json.dumps({"moved": False, "conflicts": conflicts}))
    return 2


def close(  # noqa: PLR0913 -- the note's parts
    root: Path,
    ticket: preflight.Ticket,
    *,
    outcome: str,
    reasons: list[str],
    result: dict[str, object] | None = None,
    children: list[str] | None = None,
) -> None:
    branch = route.own_branch(ticket)
    if outcome != MERGED and recorded_merge(root, branch):
        record_merge_aloud(root, branch)
    prior = queue_notes.read_note(root, branch)
    if prior.get("outcome") == MERGED:
        say_kept(finish_close(root, branch))
        if outcome == MERGED:
            print(f"closed: {ticket.ref}: {MERGED}")
            return
        message = f"{ticket.ref} merged, and a merge is not unsaid by closing it as {outcome}"
        raise RefusedError(message)
    retry = outcome == WAITS and queue_notes.mechanical(reasons)
    before = prior.get("attempts")
    same = prior.get("digest") == queue_notes.digest(ticket) and isinstance(before, int)
    counted = 0 if prior.get("discarding") else 1
    attempts = (before if same and isinstance(before, int) else 0) + counted if retry else 0
    exhausted = retry and attempts >= queue_notes.RETRIES
    if exhausted:
        errored = all(queue_notes.code_of(r) == queue_notes.ERRORED for r in reasons)
        why = (
            f"the pipeline run errored on each of {attempts} tries, each resumed once: an agent"
            " kept dying, which the drain cannot resolve itself"
            if errored
            else (
                f"retried {attempts} times and each left it waiting for the same kind of reason,"
                " which the drain cannot resolve itself"
            )
        )
        reasons = [why, *reasons]
    who: dict[str, object] = {
        "ref": ticket.ref,
        "title": ticket.title,
        "digest": queue_notes.digest(ticket),
        "branch": branch,
        "hold": outcome != MERGED and (exhausted or not retry),
        "attempts": attempts,
        **({"children": children} if children is not None else {}),
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
    branch = str(who["branch"])
    tree = tree_of(root, branch)
    change = change_of(root, merge_ready.default_ref(root), branch)
    gates = timings(tree)
    if tree.is_dir():
        run_time.stamp_end(tree)
    note = {
        **{
            k: v
            for k, v in queue_notes.read_note(root, branch).items()
            if k not in {"rebasing", "discarding"}
        },
        "hold": outcome != MERGED,
        "attempts": 0,
        **who,
        "outcome": outcome,
        "reasons": reasons,
        "assumptions": assumptions(change),
        "additions": additions(change),
        "gates": gates,
        "run_time": run_time.line(tree),
        "closed": now().isoformat(timespec="seconds"),
        "owes": owed_kinds(result or {}),
        "reviews": owed_kinds(result or {}, REVIEW_RED),
        "holds": queue_notes.holds_of(result or {}),
        "causes": queue_notes.causes_of(result or {}),
        "base": queue_notes.tip(root),
        "reported": False,
    }
    path = queue_notes.note_path(root, branch)
    queue_notes.write_note(path, {**note, "discarding": True})
    kept = discard(root, tree, f"{branch}: {STASHED[outcome]} run")
    stays = [f"the work tree stays at {tree}: {kept}"] if kept else []
    queue_notes.write_note(path, {**note, "reasons": [*reasons, *stays]})
    return stays


def recorded_merge(root: Path, branch: str) -> bool:
    note = queue_notes.read_note(root, branch)
    return (
        branch in route.merged_features(root, merge_ready.default_ref(root))
        and note.get("outcome") != MERGED
        and isinstance(note.get("ref"), str)
    )


def finish_close(root: Path, branch: str) -> list[str]:
    note = queue_notes.read_note(root, branch)
    tree = tree_of(root, branch)
    if not note.get("discarding"):
        return []
    if tree not in worktrees(root):
        drop_mark(root, branch)
        return []
    kept = discard(root, tree, f"{branch}: {STASHED[str(note.get('outcome'))]} run")
    stays = [f"the work tree stays at {tree}: {kept}"] if kept else []
    done = {k: v for k, v in note.items() if k != "discarding"}
    reasons = queue_notes.texts(done.get("reasons"))
    queue_notes.write_note(
        queue_notes.note_path(root, branch), {**done, "reasons": [*reasons, *stays]}
    )
    return stays


def say_kept(lines: list[str]) -> None:
    for line in lines:
        print(f"kept: {line}")


def record_merge(root: Path, branch: str) -> list[str]:
    note = queue_notes.read_note(root, branch)
    who: dict[str, object] = {n: str(note.get(n) or "") for n in ("ref", "title", "digest")}
    reason = "the merge landed, and the run that landed it stopped before it said so"
    return settle(root, {**who, "branch": branch}, outcome=MERGED, reasons=[reason])


def landing_path(root: Path, branch: str) -> Path:
    return state_dir(root) / f"ratchet-landing-{merge.branch_digest(branch)}"


@contextlib.contextmanager
def landing_lock(root: Path, branch: str) -> Iterator[int | None]:
    with merge.locked_retrying(landing_path(root, branch), LANDING_TRIES, LANDING_POLL) as held:
        yield held


@contextlib.contextmanager
def private_lock(root: Path) -> Iterator[Path]:
    path = merge.private_lock_path(root, secrets.token_hex(8))
    descriptor = os.open(path, os.O_RDWR | os.O_CREAT | os.O_EXCL, merge.OWNER_FILE)
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        yield path
    finally:
        path.unlink(missing_ok=True)
        os.close(descriptor)


def sweep_private_locks(root: Path) -> None:
    for path in merge.state_dir(root).glob(f"{merge.PRIVATE}*"):
        if path.is_file() and not path.is_symlink() and not merge.lock_held(path):
            path.unlink(missing_ok=True)


def landing_holder(root: Path, branch: str) -> str | None:
    for name, path in (
        ("landing", landing_path(root, branch)),
        ("merge", merge.merge_lock_path(root, branch)),
    ):
        if path.exists():
            with merge.locked(path) as descriptor:
                if descriptor is None:
                    return name
    return None


def landing_running(root: Path, branch: str) -> bool:
    return landing_holder(root, branch) is not None


def landing_alive(root: Path, branch: str, said: str) -> bool:
    holder = landing_holder(root, branch)
    if holder is not None:
        what = "a landing" if holder == "landing" else "a merge"
        print(f"{said}: {what} of `{branch}` is running in another command, so it is left alone")
    return holder is not None


def record_merge_aloud(root: Path, branch: str) -> None:
    print(f"recorded: {branch}: merged, and the run that landed it stopped before it said so")
    say_kept(record_merge(root, branch))


def land(drain: Drain, ticket: preflight.Ticket) -> int:
    command = gatelib.entry_point(drain.root, MERGE)
    if command is None or gatelib.git(drain.root, "remote", "get-url", "origin") is not None:
        print(
            "land: with a forge the pull request's merge job merges; with none the binding names"
            " the `merge` entry point this runs"
        )
        return 2
    branch = route.own_branch(ticket)
    if landing_holder(drain.root, branch) == "merge":
        print(
            f"land: a merge of `{branch}` is running in another command; run land again once it"
            " ends"
        )
        return 2
    with landing_lock(drain.root, branch) as held, private_lock(drain.root) as private:
        if held is None:
            print(
                f"land: a landing of `{branch}` is already running in another command; run land"
                " again once it ends"
            )
            return 2
        return landed(drain, ticket, branch, command=command, private=private)


def landed(
    drain: Drain, ticket: preflight.Ticket, branch: str, *, command: str, private: Path
) -> int:
    with exclusive(drain.root):
        if recorded_merge(drain.root, branch):
            record_merge_aloud(drain.root, branch)
        if queue_notes.read_note(drain.root, branch).get("outcome") == MERGED:
            close(drain.root, ticket, outcome=MERGED, reasons=[])
            return 0
        hold = queue_notes.held(drain.root, branch, ticket)
        if hold is not None:
            say_kept(finish_close(drain.root, branch))
            print(f"land: {ticket.ref} is held and is not landed: " + "; ".join(hold.reasons))
            return 2
        if noted_start(drain.root, branch) is None or not passed(drain.root, branch):
            print(
                f"land: {ticket.ref} is not landed: no publish check has passed its start; run"
                f" `drain.py check {ticket.ref} --final` first"
            )
            return UNREAD
    argv = [*shlex.split(command), branch]
    try:
        done = subprocess.run(
            argv,
            cwd=drain.root,
            capture_output=True,
            text=True,
            check=False,
            env={**os.environ, merge.LANDING_VAR: str(private)},
        )
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
    if everything:
        lease(root, take_over=take_over, token=token)
    sweep_private_locks(root)
    merged = route.merged_features(root, merge_ready.default_ref(root))
    heads = gatelib.git(root, "for-each-ref", "--format=%(refname:short)", "refs/heads/") or ""
    owners = {tree_of(root, branch): branch for branch in heads.splitlines()}
    registered = worktrees(root)
    busy = None if everything else other_drain(root, token)
    record_orphans(root, heads.splitlines(), registered, busy=busy)
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
        held_back = in_flight(root, branch, busy)
        if held_back:
            print(f"kept: {tree}: {held_back}")
            continue
        if branch is not None and tree in registered:
            if recorded_merge(root, branch):
                record_merge_aloud(root, branch)
            say_kept(finish_close(root, branch))
        stays = discard(root, tree, f"{branch or tree.name}: {INTERRUPTED} run")
        print(f"removed: {tree}: {why}" if stays is None else f"left: {tree}: {why}, and {stays}")
    if everything:
        end_marks(root, heads.splitlines())
    return 0


def end_marks(root: Path, branches: list[str]) -> None:
    for branch in branches:
        if not landing_running(root, branch):
            unmark(root, branch)
            finish_close(root, branch)


def in_flight(root: Path, branch: str | None, busy: str | None) -> str | None:
    if branch is None:
        return None
    holder = landing_holder(root, branch)
    if holder is not None:
        return f"`{branch}` has a {holder} running"
    if busy is not None and recorded_merge(root, branch):
        return f"`{branch}` is merged and no note says so"
    return None


def record_orphans(
    root: Path, branches: list[str], registered: frozenset[Path], *, busy: str | None
) -> None:
    if busy is not None:
        print(f"clean: {LANDING_MAY_RUN} ({busy}), so a merge no note says is left alone")
        return
    for branch in branches:
        if (
            tree_of(root, branch) not in registered
            and recorded_merge(root, branch)
            and not landing_alive(root, branch, "clean")
        ):
            record_merge_aloud(root, branch)


def unmark(root: Path, branch: str) -> None:
    held = queue_notes.read_note(root, branch)
    tip = gatelib.git(root, "rev-parse", f"refs/heads/{branch}")
    if held.get("rebasing") and (
        tip == held["rebasing"] or not ahead(root, merge_ready.default_ref(root), branch)
    ):
        queue_notes.write_note(
            queue_notes.note_path(root, branch), {k: v for k, v in held.items() if k != "rebasing"}
        )


def minutes(note: dict[str, object]) -> str:
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
    if "run_time" in note:
        parts.append(str(note["run_time"]))
    return "; ".join(parts) or "not timed"


def section(title: str, rows: list[str]) -> list[str]:
    return [f"## {title}", "", *(rows or ["- none"]), ""]


def listed(note: dict[str, object]) -> list[str]:
    head = f"- `{note.get('ref')}` {note.get('title')} (`{note.get('branch')}`)"
    return [head, *(f"  - {reason}" for reason in queue_notes.texts(note.get("reasons")))]


def render(notes: list[dict[str, object]], idle: list[Entry], stamp: datetime) -> str:
    by = {
        outcome: [row for note in notes if note.get("outcome") == outcome for row in listed(note)]
        for outcome in (MERGED, PARKED, SPLIT)
    }
    by[WAITS] = [
        row
        for note in notes
        if note.get("outcome") == WAITS
        for row in listed(
            {
                **note,
                "reasons": [
                    *queue_notes.texts(note.get("reasons")),
                    *queue_notes.texts(note.get("unexpired")),
                ],
            }
        )
    ]
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
            *(section("Split by size into tickets of their own", by[SPLIT]) if by[SPLIT] else []),
            *section("Parked", by[PARKED] + held),
            *section("Not built", unbuilt),
            *section("Assumptions", assumed),
            *section("Records added", added),
            *section("Timings", [f"- `{n.get('ref')}`: {minutes(n)}" for n in notes]),
        ]
    )


def report(drain: Drain, token: str = "") -> int:
    heads = gatelib.git(drain.root, "for-each-ref", "--format=%(refname:short)", "refs/heads/")
    busy = other_drain(drain.root, token)
    for branch in (heads or "").splitlines() if busy is None else []:
        if recorded_merge(drain.root, branch) and not landing_alive(drain.root, branch, "report"):
            record_merge_aloud(drain.root, branch)
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
    stays = release(drain.root, token)
    if stays is not None:
        print(
            f"report: the drain lease stays: another drain is running ({stays}); {LANDING_MAY_RUN},"
            " so a merge no note says is left alone"
        )
    return 0


def noted(root: Path, ref: str) -> preflight.Ticket | None:
    for path in sorted((gatelib.state_dir(root) / queue_notes.QUEUE / "tickets").glob("*.json")):
        note = queue_notes.loaded(gatelib.read_text(path))
        named, title = note.get("ref"), note.get("title")
        if isinstance(named, str) and isinstance(title, str) and named.endswith(".md"):
            number = re.match(r"\d+", Path(named).name)
            if key(named) == key(ref) or (
                number and ref.isdigit() and int(number.group()) == int(ref)
            ):
                return preflight.local_ticket(named, f"# {title}\n")
    return None


def on_ticket(drain: Drain, options: argparse.Namespace) -> int:
    ticket = preflight.fetch(options.ref or "", drain.root, drain.tracker) or noted(
        drain.root, options.ref or ""
    )
    if ticket is None and options.action == "open":
        print(f"open: {options.ref} is not started: {NO_TICKET}")
        return 1
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
    return act_on(drain, ticket, options)


def act_on(drain: Drain, ticket: preflight.Ticket, options: argparse.Namespace) -> int:
    if options.action == "land":
        return land(drain, ticket)
    branch = route.own_branch(ticket)
    holder = None if options.action == "open" else landing_holder(drain.root, branch)
    if holder is not None:
        what, why = (
            ("a landing", ": the landing records the ticket's outcome itself")
            if holder == "landing"
            else ("a merge", "")
        )
        print(
            f"{options.action}: {what} of `{branch}` is running in another command; nothing was"
            f" done; wait for it to end, then run plan{why}"
        )
        return LANDING_RUNNING
    if options.action in CLOSERS and (options.action != "check" or options.final):
        return closing(drain, ticket, options)
    return acted(drain, ticket, options)


def closing(drain: Drain, ticket: preflight.Ticket, options: argparse.Namespace) -> int:
    root, branch = drain.root, route.own_branch(ticket)
    tip = gatelib.git(root, "rev-parse", "--verify", "--quiet", f"refs/heads/{branch}")
    was_passed = gate_start(root, branch, passed=False)
    code = acted(drain, ticket, options)
    if (
        not was_passed
        or options.action == "check"
        or not passed(root, branch)
        or gatelib.git(root, "rev-parse", "--verify", "--quiet", f"refs/heads/{branch}") != tip
    ):
        return code
    try:
        gate_start(root, branch, passed=True, done=f"`{options.action}` ran")
    except RefusedError as refused:
        print(f"drain: {refused}")
    return code


def acted(drain: Drain, ticket: preflight.Ticket, options: argparse.Namespace) -> int:
    if options.action == "open":
        return open_ticket(drain, options.ref or "")
    if options.action == "update":
        return update(drain, ticket)
    if options.action == "check":
        return check(drain, ticket, final=options.final)
    if options.action == "split":
        return split_ticket(drain, ticket, options)
    result = result_of(options.result)
    reasons = [*options.reason, *reasons_of(result)]
    close(
        drain.root,
        ticket,
        outcome=options.outcome,
        reasons=reasons,
        result=result,
    )
    return 0


def result_of(path: Path | None) -> dict[str, object]:
    if path is None:
        return {}
    try:
        data: object = json.loads(gatelib.read_text(path) or "")
    except ValueError:
        data = None
    if not isinstance(data, dict):
        message = (
            f"{path} holds no JSON object, so nothing was closed: write the pipeline's `result`"
            " object to it as JSON, then run this again"
        )
        raise RefusedError(message)
    return data


def split_ticket(drain: Drain, ticket: preflight.Ticket, options: argparse.Namespace) -> int:
    try:
        plan_text = json.loads(gatelib.read_text(options.plan) or "") if options.plan else None
    except ValueError:
        plan_text = None
    result = result_of(options.result)
    root, branch = drain.root, route.own_branch(ticket)
    again = f"`drain.py split {ticket.ref} --plan {options.plan or '<file>'}`"
    local = ticket.ref.endswith(".md")
    if local and unfinished(root):
        print(f"{UNFINISHED}, then run {again} again")
        return 2
    path = queue_notes.note_path(root, branch)
    noted = queue_notes.read_note(root, branch)
    filed = queue_notes.split_into(root, branch, ticket) or queue_notes.texts(noted.get("parts"))
    landing = f"{merge.RECORDS}split-{branch.rpartition('/')[2]}"
    kept = local and let_go(
        root,
        path,
        landing=landing,
        made=split.bodies(root, ticket.ref, plan_text, filed=filed, gh=drain.tracker),
    )
    if kept:
        print(f"{kept}, then run {again} again")
        return NOT_LANDED
    made, refused = split.apply(
        root,
        ticket.ref,
        plan_text,
        gh=drain.tracker,
        filed=filed,
        noting=lambda paths: queue_notes.write_note(
            path, {**queue_notes.read_note(root, branch), "parts": paths}
        ),
    )
    if refused:
        reasons = [f"too large, and the split was refused: {line}" for line in refused]
        close(root, ticket, outcome=PARKED, reasons=reasons, result={**result, "tooLarge": True})
        return 1
    close(
        root,
        ticket,
        outcome=SPLIT,
        reasons=[f"too large; split into {', '.join(made)}"],
        result=result,
        children=made,
    )
    landed = land_parts(root, branch, landing=landing, made=made) if local else None
    if landed is not None:
        print(AGAIN.format(landed, again))
    print(json.dumps({"children": made}))
    return 0 if landed is None else NOT_LANDED


def unfinished(root: Path) -> bool:
    return bool(gatelib.git(root, "ls-files", "--unmerged")) or any(
        (root / (gatelib.git(root, "rev-parse", "--git-path", name) or name)).exists()
        for name in IN_PROGRESS
    )


def let_go(root: Path, note: Path, *, landing: str, made: dict[str, str]) -> str | None:
    noted = queue_notes.loaded(gatelib.read_text(note))
    named = noted.get("landing")
    if not isinstance(named, str):
        return None
    if named != landing:
        return FORGED.format(note, named, landing)
    held = merge.tip(root, landing)
    if held is None:
        return None
    return drop_landing(root, noted, landing=landing, held=held, made=made)


def drop_landing(
    root: Path, noted: dict[str, object], *, landing: str, held: str, made: dict[str, str]
) -> str | None:
    if merge.checked_out(root, landing) is not None:
        return CHECKED_OUT.format(landing)
    base = gatelib.git(root, "merge-base", merge.local_default(root) or "", held) or ""
    if base != held and (
        not made
        or gatelib.git(root, "rev-list", "--count", f"{base}..{held}") != "1"
        or added(root, base, held) != {path: blob(root, path, text) for path, text in made.items()}
    ):
        return HOLDS_OTHER.format(landing)
    if (
        noted.get("committed", held) != held
        or gatelib.git(root, "update-ref", "-d", f"{merge.HEADS}{landing}", held) is None
    ):
        return FOREIGN.format(landing)
    return None


def added(root: Path, base: str, tip: str) -> dict[str, str]:
    raw = gatelib.git(root, "diff", "--raw", "-z", "--no-abbrev", "--no-renames", base, tip) or ""
    fields = raw.split("\0")
    rows = {fields[at + 1]: fields[at].split() for at in range(0, len(fields) - 1, 2)}
    return {path: row[3] if row[1] == PLAIN and row[4] == "A" else "" for path, row in rows.items()}


def blob(root: Path, path: str, text: str) -> str:
    hashed = merge.porcelain(root, "hash-object", "--stdin", f"--path={path}", given=text)
    return hashed.stdout.strip()


def land_parts(root: Path, branch: str, *, landing: str, made: list[str]) -> str | None:
    default = merge.local_default(root) or ""
    if all(gatelib.show(root, default, path) is not None for path in made):
        return None
    if gatelib.git(root, "remote", "get-url", "origin") is not None:
        print(FORGE_PARTS.format(", ".join(made)))
        return None
    unfit = unlandable(root, default, landing=landing, made=made)
    if unfit is not None:
        return unfit
    path = queue_notes.note_path(root, branch)
    noted = {k: v for k, v in queue_notes.read_note(root, branch).items() if k != "committed"}
    queue_notes.write_note(path, {**noted, "landing": landing})
    commit, why = commit_parts(root, merge.tip(root, default) or "", landing=landing, made=made)
    if commit is None:
        return why
    queue_notes.write_note(path, {**queue_notes.read_note(root, branch), "committed": commit})
    for part in made:
        (root / part).unlink()
    lock = root / (gatelib.git(root, "rev-parse", "--git-path", "index.lock") or "index.lock")
    if lock.is_file():
        return INDEX_LOCKED.format(landing, lock)
    return None if merge.merge_records(root, landing) == 0 else ON_LANDING.format(landing)


def unlandable(root: Path, default: str, *, landing: str, made: list[str]) -> str | None:
    if merge.tip(root, landing) is not None:
        return TAKEN.format(landing)
    current = merge.on_branch(root)
    if current is None:
        print(
            f"merge: the checkout's HEAD is detached, and the parts land from `{default}`: switch"
            f" it to `{default}`"
        )
        return IN_CHECKOUT
    if current != default:
        print(f"merge: the checkout is on `{current}`, and the parts land from `{default}`")
        return IN_CHECKOUT
    staged = gatelib.git(root, "ls-files", "--", *(f":(literal){part}" for part in made))
    return STAGED.format(", ".join(staged.splitlines())) if staged else None


def commit_parts(root: Path, base: str, *, landing: str, made: list[str]) -> tuple[str | None, str]:
    scratch = Path(tempfile.mkdtemp(prefix=merge.SCRATCH))
    tree = scratch / "tree"
    with merge.locked(scratch / merge.HELD):
        try:
            done, exposed = commit_aside(root, tree, base=base, landing=landing, made=made)
        finally:
            gatelib.git(root, "worktree", "remove", "--force", str(tree))
            shutil.rmtree(scratch, ignore_errors=True)
            gatelib.git(root, "worktree", "prune")
    held, stash = tips(root, landing)
    hurt = [] if exposed is None else harmed(root, exposed, stash)
    for line in hurt:
        print(f"records: {line}")
    if held in {None, base}:
        gatelib.git(root, "update-ref", "-d", f"{merge.HEADS}{landing}", base)
        said = " / ".join((done.stdout + done.stderr).strip().splitlines()[-merge.HOOK_LINES :])
        said = said or HOOKS_SILENT.format(done.returncode, landing)
        print(f"records: the parts were not committed: {said}")
        return None, IN_CHECKOUT
    kept = merge.hashed(root, made)
    alone = gatelib.git(
        root, "rev-list", "--parents", "-n", "1", held
    ) == f"{held} {base}" and added(root, base, held).keys() == set(made)
    if not alone:
        return None, (HOOKS_ADDED if added(root, base, held) else HOOKS_EMPTIED).format(landing)
    if added(root, base, held) != {part: kept.get(part, "") for part in made}:
        return None, HOOKS_CHANGED.format(", ".join(made), landing)
    if hurt:
        return None, HOOKS_HURT.format(landing)
    return held, ""


def tips(root: Path, landing: str) -> tuple[str | None, str | None]:
    names = (f"{merge.HEADS}{landing}", "refs/stash")
    shown = gatelib.git(root, "for-each-ref", "--format=%(refname) %(objectname)", *names) or ""
    found = {name: tip for name, _, tip in (line.partition(" ") for line in shown.splitlines())}
    return found.get(names[0]), found.get(names[1])


def commit_aside(
    root: Path, tree: Path, *, base: str, landing: str, made: list[str]
) -> tuple[subprocess.CompletedProcess[str], Exposed | None]:
    branched = merge.porcelain(
        root, "worktree", "add", "--quiet", "--no-checkout", "-b", landing, str(tree), base
    )
    shown = merge.porcelain(
        tree, "rev-parse", "--absolute-git-dir", "--verify", "--quiet", "refs/stash"
    ).stdout.splitlines()
    if branched.returncode or not shown:
        return branched, None
    aside = (f"--git-dir={shown[0]}", f"--work-tree={root}")
    merge.porcelain(root, *aside, "read-tree", base)
    listed = "".join(f"{part}\0" for part in made)
    staged = merge.porcelain(
        root,
        *aside,
        "--literal-pathspecs",
        "add",
        "--pathspec-from-file=-",
        "--pathspec-file-nul",
        given=listed,
    )
    if staged.returncode:
        return staged, None
    exposed = kept(root, protected(root, base), shown[1] if len(shown) > 1 else None)
    message = f"{merge.SUBJECT}\n\nThe parts of a split, landed by `drain.py split`:\n\n" + "".join(
        f"- {part}\n" for part in made
    )
    done = merge.porcelain(root, *aside, "commit", "-q", "--file=-", given=message)
    return done, exposed


def protected(root: Path, base: str) -> dict[str, tuple[str, ...]]:
    listing = ["diff-index", "--cached", "-z", "--no-renames", "--no-abbrev", base]
    listed = subprocess.run(
        ["git", "-C", str(root), *listing],
        capture_output=True,
        text=True,
        errors="surrogateescape",
        check=False,
    ).stdout.split("\0")
    return {listed[at + 1]: tuple(listed[at].split()[2:4]) for at in range(0, len(listed) - 1, 2)}


def kept(root: Path, listed: dict[str, tuple[str, ...]], stash: str | None) -> Exposed:
    wide = any(len(known[0]) == SHA256_HEX for known in listed.values())
    algorithm = "sha256" if wide else "sha1"
    files: dict[str, tuple[int, str] | None] = {}
    written: set[str] = set()
    for path, known in listed.items():
        try:
            files[path], body = look(root / path, algorithm)
        except OSError as error:
            print(f"records: not saved: {path}: {error.strerror}")
            files[path] = NOT_READ
            continue
        held = files[path]
        if held is not None and stat.S_ISREG(held[0]) and held[1] not in known:
            subprocess.run(
                ["git", "-C", str(root), "hash-object", "-w", "--stdin"],
                input=body,
                capture_output=True,
                check=False,
            )
            written.add(held[1])
    return Exposed(files, frozenset(written), stash, algorithm)


def look(path: Path, algorithm: str) -> tuple[tuple[int, str] | None, bytes]:
    try:
        mode = path.lstat().st_mode
        body = path.read_bytes() if stat.S_ISREG(mode) else b""
        target = str(path.readlink()) if stat.S_ISLNK(mode) else ""
    except FileNotFoundError:
        return None, b""
    if not stat.S_ISREG(mode):
        return (mode, target), body
    return (mode, hashlib.new(algorithm, b"blob %d\0" % len(body) + body).hexdigest()), body


def harmed(root: Path, exposed: Exposed, stash: str | None) -> list[str]:
    lines = []
    for path, was in exposed.files.items():
        try:
            now, _ = look(root / path, exposed.algorithm)
        except OSError:
            now = NOT_READ
        if now != was:
            lines.append(harm(path, was, now, written=exposed.written))
    if stash != exposed.stash:
        top = stash or exposed.stash
        holds = gatelib.git(root, "diff", "--name-only", f"{top}^1", f"{top}") or ""
        lines.append(
            f"the commit hooks moved the stash: `stash@{{0}}` is now {stash or 'gone'}, and {top}"
            f" holds {', '.join(holds.splitlines()) or 'no tracked file'} from the checkout:"
            f" `git stash show -p {top}` prints them"
        )
    return lines


def harm(
    path: str, was: tuple[int, str] | None, now: tuple[int, str] | None, *, written: frozenset[str]
) -> str:
    if was is None:
        return f"{CHANGED_BY.format('restored', path)}; the checkout did not have it"
    mode, held = was
    said = CHANGED_BY.format("deleted" if now is None else "changed", path)
    if stat.S_ISREG(mode):
        wrote = WROTE if held in written else ""
        return (
            f"{said}; the bytes it had are object {held}{wrote}: `git cat-file blob {held}` prints"
            " them"
        )
    if was == NOT_READ:
        return f"{said}; the split could not read it, so it was not saved"
    return f"{said}; it was {f'a link to `{held}`' if stat.S_ISLNK(mode) else 'a folder'}"


def show_plan(drain: Drain, _options: argparse.Namespace) -> int:
    print(json.dumps(asdict(plan(drain)), indent=2))
    return 0


WHOLE: dict[str, Callable[[Drain, argparse.Namespace], int]] = {
    "plan": show_plan,
    "clean": lambda drain, options: clean(
        drain.root,
        everything=options.everything,
        take_over=options.take_over,
        token=options.drain,
    ),
    "report": lambda drain, options: report(drain, options.drain),
}


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "action", choices=[*WHOLE, "open", "update", "check", "land", "close", "split"]
    )
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
    parser.add_argument("--plan", type=Path, help="the split of a ticket too large, as JSON")
    parser.add_argument("--all", action="store_true", dest="everything")
    parser.add_argument(
        "--final", action="store_true", help="with check: the run is over, so mark the start passed"
    )
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
    lines, finished = merge.recovery(root)
    for line in lines:
        print(line, file=sys.stderr if options.action == "plan" else sys.stdout)
    if not finished:
        return 2
    drain = Drain(root, None if options.offline else preflight.run_gh, options.concurrency)
    action = WHOLE.get(options.action, on_ticket)
    try:
        with exclusive(root) if options.action in LOCKED else contextlib.nullcontext():
            return action(drain, options)
    except RefusedError as refused:
        print(f"{options.action}: {refused}" if options.action == "open" else f"drain: {refused}")
        return 1 if options.action == "open" else 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
