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
  outranks commits on the branch, and a ticket waiting on a held one is refused naming it),
  ``merged`` (its branch is merged and the ticket is still open) and ``refused`` (the preflight
  is red, with what produces each missing piece). ``start`` names the buildable tickets to start
  now: at most ``--concurrency`` less those building, and none while the checkout is not fit.
- ``open <ticket>`` is the gate: it makes the ticket's work tree and branch only when the plan
  starts that ticket, so the order and the cap are a script's exit code.
- ``update <ticket>`` rebases the ticket's branch onto the default branch when that has moved,
  first making the work tree of a built branch that has none and dropping a last commit that is
  the verdict record, which the rebase outdated.
  Exit 0 with ``moved``; exit 2 with the conflicted paths and the branch as it was.
- ``land <ticket>`` runs the binding's ``merge`` entry point with no forge and closes the ticket
  with what the merge judge printed: merged, or waiting for the owner with every reason verbatim.
- ``close <ticket> --outcome merged|waits|parked`` removes the work tree, stashing what was left
  uncommitted, and writes the ticket's note for the report. ``--result FILE`` is the pipeline's
  result: its open questions, guard edits and forced verdict become the reasons. A ``triggers``
  red in its ``checkOutput`` (another instance of a kind whose safeguard is owed, ADR 0028) adds
  a reason naming ``/ratchet:safeguard``, and the ticket is held only until no such kind is owed.
- ``clean`` removes every queue work tree whose branch is merged or gone; ``--all`` removes the
  rest too, their uncommitted work stashed as ``<branch>: interrupted run``. A drain starts with
  ``clean --all``: one drain runs on a repository at a time, so every work tree found then is
  left over.
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
import hashlib
import json
import os
import re
import shlex
import shutil
import stat
import subprocess
import sys
import tempfile
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
    from collections.abc import Callable

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
OTHERS_WRITE = 0o022
NOT_OURS = "the queue's work trees go in a directory only this user can write; remove it"
NOT_A_TREE = "it is not a work tree of this repository or an empty directory, so it stays"
MERGE = "merge"
SLOWEST = 3
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
    return bool(gatelib.git(tree, "status", "--porcelain", "--untracked-files=all"))


def discard(root: Path, tree: Path, label: str) -> bool:
    """Remove a queue work tree, what it held uncommitted stashed under ``label`` first.

    Only a directory git lists as a work tree of this repository is emptied. A link is never
    followed, and anything else stays unless it is an empty directory. True when nothing is left.
    """
    if tree.is_symlink():
        return False
    if tree in worktrees(root):
        for pid in gatelib.end_running_from(tree.resolve()):
            print(f"ended: process {pid}, which ran a program of {tree}")
        gatelib.git(tree, "rebase", "--abort")
        if dirty(tree):
            gatelib.git(tree, "stash", "push", "--include-untracked", "--quiet", "-m", label)
        gatelib.git(root, "worktree", "remove", "--force", str(tree))
        shutil.rmtree(tree, ignore_errors=True)
    elif tree.is_dir() and not any(tree.iterdir()):
        tree.rmdir()
    gatelib.git(root, "worktree", "prune")
    # Why: the directory above is this repository's, and nothing else is kept in it.
    if tree.parent.is_dir() and not any(tree.parent.iterdir()):
        tree.parent.rmdir()
    return not tree.exists()


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
        behind = (
            ()
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
    building = sum(entry.state == BUILDING for entry in entries)
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


def update(drain: Drain, ticket: preflight.Ticket) -> int:
    """Rebase the ticket's branch onto the default branch when that moved; never half-way.

    A built branch an earlier drain left, with no work tree, gets one. A last commit that is the
    verdict record is dropped after the rebase: it certified a tree that is gone, and the second
    review writes the record that replaces it.
    """
    branch = route.own_branch(ticket)
    tree = tree_of(drain.root, branch)
    base = merge_ready.default_ref(drain.root)
    refusal = None
    if tree not in worktrees(drain.root):
        current = gatelib.git(drain.root, "merge-base", "--is-ancestor", base, branch) == ""
        if not ahead(drain.root, base, branch):
            refusal = f"`{branch}` has no queue work tree"
        elif current:
            print(json.dumps({"moved": False, "conflicts": []}))
            return 0
        else:
            refusal = revive(drain.root, branch, tree)
    if refusal is None and dirty(tree):
        refusal = "its work tree holds uncommitted changes: commit the change first"
    if refusal is None and gatelib.git(tree, "merge-base", "--is-ancestor", base, "HEAD") == "":
        print(json.dumps({"moved": False, "conflicts": []}))
        return 0
    last = (
        gatelib.git(tree, "diff-tree", "--no-commit-id", "--name-only", "-r", "HEAD") or ""
    ).splitlines()
    judged = bool(last) and all(merge_ready.RECORD.match(path) for path in last)
    if refusal is None and gatelib.git(tree, "branch", "--remotes", "--contains", "HEAD"):
        refusal = "the branch is pushed, and a rebase would rewrite what the forge holds"
    if refusal is not None:
        print(f"update: {ticket.ref} is not rebased: {refusal}")
        return 1
    if gatelib.git(tree, "rebase", "--quiet", base) is not None:
        if judged:
            gatelib.git(tree, "reset", "--quiet", "--hard", "HEAD~1")
        print(json.dumps({"moved": True, "conflicts": []}))
        return 0
    conflicts = (gatelib.git(tree, "diff", "--name-only", "--diff-filter=U") or "").splitlines()
    gatelib.git(tree, "rebase", "--abort")
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
    tree = tree_of(root, branch)
    change = change_of(root, merge_ready.default_ref(root), branch)
    note = {
        **queue_notes.read_note(root, branch),
        "ref": ticket.ref,
        "title": ticket.title,
        "branch": branch,
        "outcome": outcome,
        "reasons": reasons,
        "assumptions": assumptions(change),
        "additions": additions(change),
        "gates": timings(tree),
        "closed": now().isoformat(timespec="seconds"),
        "digest": queue_notes.digest(ticket),
        "hold": outcome != MERGED,
        "owes": owed_kinds(result or {}),
        "holds": queue_notes.holds_of(result or {}),
        "reported": False,
    }
    discard(root, tree, f"{branch}: {STASHED[outcome]} run")
    queue_notes.write_note(queue_notes.note_path(root, branch), note)
    print(f"closed: {ticket.ref}: {outcome}")


def land(drain: Drain, ticket: preflight.Ticket) -> int:
    """Run the binding's ``merge`` entry point on the ticket's branch; close it by the answer."""
    command = gatelib.entry_point(drain.root, MERGE)
    if command is None or gatelib.git(drain.root, "remote", "get-url", "origin") is not None:
        print(
            "land: with a forge the pull request's merge job merges; with none the binding names"
            " the `merge` entry point this runs"
        )
        return 2
    argv = [*shlex.split(command), route.own_branch(ticket)]
    try:
        done = subprocess.run(argv, cwd=drain.root, capture_output=True, text=True, check=False)
    except OSError as error:
        print(f"land: `{command}` could not run: {error}")
        return 2
    print(done.stdout + done.stderr, end="")
    if done.returncode not in {0, 1}:
        return 2
    lines = done.stdout.splitlines()
    at = lines.index(merge_ready.OWNER) if merge_ready.OWNER in lines else len(lines)
    reasons = [line for line in lines[at + 1 :] if line.strip() and not line.startswith(merge.NEXT)]
    close(drain.root, ticket, outcome=WAITS if done.returncode else MERGED, reasons=reasons)
    return done.returncode


def clean(root: Path, *, everything: bool) -> int:
    """Remove the queue work trees whose branch is merged or gone; with ``everything``, all."""
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
        gone = discard(root, tree, f"{branch or tree.name}: {INTERRUPTED} run")
        print(f"removed: {tree}: {why}" if gone else f"left: {tree}: {why}, and {NOT_A_TREE}")
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
    "clean": lambda drain, options: clean(drain.root, everything=options.everything),
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
    return WHOLE.get(options.action, on_ticket)(drain, options)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
