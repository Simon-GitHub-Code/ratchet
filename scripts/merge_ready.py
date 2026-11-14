#!/usr/bin/env python3
"""The merge judge: may this change reach the default branch without the owner? ADR 0021.

Prints ``merge: agent`` or ``merge: owner``, then every reason as ``<code>: <text>``, and exits 0
only for ``agent``. The owner merges when any of these holds:

- ``guard``: a guard path changed, other than by an addition to a record (ADR 0014) or by a
  threshold that only tightened. A dependency change is a guard change here: its note is printed
  and the owner merges it. An instruction file (``AGENTS.md``, ``CLAUDE.md``, ``CLAUDE.local.md``,
  ``GEMINI.md`` in any letter case, or a path under a ``.claude/`` directory) is a guard path at
  any depth, whatever the manifest says.
- ``door``: the change goes through a one-way door. A path in a ``[[door]]`` of the manifest; a
  flag that turns on by default, becomes permanent, or leaves the registry while off.
- ``unflagged``: the change adds to a file behind a door that shuts on removal (a route, a command
  or an exported symbol in a contract artefact or public-surface snapshot) and no flag hides it:
  ``flags.toml`` gains no flag that is off, and no line the change adds outside ``docs/``, the
  registry and those files names one that is off. Asked only of the application types the method
  asks ``changed flags off and on`` of, read from the binding at the default branch's tip, so a
  library is exempt; a binding that names no known type is asked. ADR 0025.
  Both ``door`` and ``unflagged`` wait for a life event the merge base's binding declares
  (ADR 0028): ``unflagged`` for ``deployed``, ``door`` for ``deployed`` or ``irreplaceable-data``.
  Before one is declared nobody is hurt by what they guard, so the judge lets the change through
  and prints a ``note:`` line saying what it let through. The head's binding never counts.
- ``dispute``: the change adds or edits a dispute, or an open dispute names a path it touches.
- ``record``: no verdict record is part of the change, or the one there is not an approval of
  exactly this tree with nothing unresolved and no open question. A change with no verdict record
  that is records alone is not asked for one (ADR 0031): every path it touches has the whole
  shape of a file a skill writes, listed in ``RECORDS``, is a plain file kept by the change and
  as the change found it at the tip, and has no twin in another letter case. Read by the default
  branch's manifest, the glossary and ADRs sit in ``edit = "append"`` groups alone, the rest in
  none, and no door holds any. The glossary takes only additions (ADR 0014). A ticket may not gain
  the agent-ready label, take another's number, be one a labelled ticket's ``Blocked by`` line can
  name, or be closed. A labelled ticket, the spec it inherits from and the status of a ticket it
  waits on do not change (``feature_preflight.reads`` names them). An ADR is a decision the owner
  agrees to: the reason names its title.
- ``file``: a guard, door or record path is a symbolic link or a submodule, at the merge base
  or at the head. Git shows a link's target as its text, which is not what a gate reads there.
- ``target``, ``behind``, ``tier2``: the pull request targets another branch; the branch does not
  hold the default branch's tip, so its gates ran on an older one; tier 2 is red there.
- ``refs``, ``manifest``: the judge could not read what it judges. It fails closed. One change
  is read without a manifest on the default branch: the adoption, which brings the manifest and
  the binding to a default branch whose tip and merge base hold neither. There is no gate before
  it to loosen, so the setting, its files, the flag registry and disputes are asked of it, and
  its manifest must guard at least what the template's does. It may bring no instruction file but
  ``ADOPTION_WRITES`` and no ticket with the agent-ready label. ``merge.py`` runs ``check-deep``
  on its merge result (ADR 0031).

The judge reads commits and nothing else: the manifest at the default branch's tip, every other
file at the merge base and at the head, through git. It never reads the work tree and never runs
anything the change holds, so the forge's merge job can run it on a checkout of the default branch.
The gates are not its question: the merge job runs it once they are green, and ``merge.py`` runs
``check`` on the merge result beside it.

``merge_ready.py record`` writes the verdict record the judge reads. It takes the pipeline's
``verdictRecord`` on stdin, adds the tree of ``HEAD`` with the record set aside, and writes
``docs/features/<slug>/verdict.json``; the orchestrator commits that file last, alone. An agent
writes this record, so an agent can forge it: every other reason above is read from the diff.

Usage: ``merge_ready.py [--root DIR] [--base REF] [--head REF] [--target BRANCH] [--current]
[--tier2 green|red|none] [--report FILE]`` and ``merge_ready.py record [--root DIR]``.
"""

from __future__ import annotations

import argparse
import contextlib
import functools
import json
import os
import re
import subprocess
import sys
import tempfile
import tomllib
from collections import Counter
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import TYPE_CHECKING

import application_types
import feature_preflight as preflight
import flags as registry
import gatelib
import lowered
import owed
import plugin_pin

# Why: the manifest and the two classifiers the judge shares with the hook live under hooks/.
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "hooks"))
from ratchet_guards import dependencies, records, splits
from ratchet_guards.dependency_entry import Entry
from ratchet_guards.gh import LABELS_RECORD, READY_ROLE, label_for_role
from ratchet_guards.globs import matches
from ratchet_guards.manifest import (
    MANIFEST_PATH,
    Door,
    DoorRule,
    Group,
    Manifest,
    ManifestError,
    parse,
)

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable, Iterator, Mapping, Sequence
    from contextlib import AbstractContextManager

AGENT = "merge: agent"
OWNER = "merge: owner"
MARKER = "<!-- ratchet:merge -->"
SCHEMA = 1
THRESHOLDS = gatelib.THRESHOLDS.as_posix()
LEDGER = owed.LEDGER.as_posix()
BINDING = gatelib.BINDING.as_posix()
SLUG = r"[a-z0-9]+(?:-[a-z0-9]+)*"
RECORD = re.compile(rf"^docs/features/({SLUG})/verdict\.json$")
DISPUTES = "docs/disputes"
DISPUTE = re.compile(rf"^{DISPUTES}/\d{{4}}-[a-z0-9-]+\.md$")
STATUS = re.compile(r"^- Status:[ \t]*(\S+)[ \t]*$", re.MULTILINE)
DECIDED = frozenset({"upheld", "rejected"})
TREE = re.compile(r"[0-9a-f]{40,64}")
APPROVE = "approve"
TIER2_STATES = ("green", "red", "none")
RED = "red"
DOOR_EVENTS = frozenset({"deployed", "irreplaceable-data"})
SURFACE_EVENTS = frozenset({"deployed"})
FLAGS_QUESTION = "changed flags off and on"
DOCS = "docs/"
HARNESS = Group("harness", "The manifest says what a guard path is.", (MANIFEST_PATH,))
# Why: Claude Code loads a CLAUDE.md beside any file it reads, and the other harnesses read
# theirs the same way, so an agent obeys these at any depth; the manifest names the top only.
INSTRUCTION_FILES = frozenset({"agents.md", "claude.md", "claude.local.md", "gemini.md"})
INSTRUCTION_DIRECTORY = ".claude"
INSTRUCTS = "(instructions): an agent reads it as instructions, wherever it sits"
# Why: `/ratchet:adopt` writes these instruction files and no other: `AGENTS.md` in its step 7,
# `.claude/settings.json` from its template through `just settings`. A test holds the set to the
# skill and its templates, so a template that adds one fails it until the judge knows.
ADOPTION_WRITES = frozenset({"AGENTS.md", ".claude/settings.json"})
TRACKER_DIRECTORY = ".scratch"
TRACKER = f"{TRACKER_DIRECTORY}/**/*.md"
TICKETS = f"{TRACKER_DIRECTORY}/**/issues/*.md"
NUMBER = re.compile(r"\d+")
CHANGE_NAME = r"[a-z0-9]+(?:[._-][a-z0-9]+)*"
TITLE = re.compile(r"^#[ \t]+(\S.*)$", re.MULTILINE)
TITLE_WIDTH = 100
TEMPLATE = Path(__file__).resolve().parents[1] / "skills" / "adopt" / "templates" / MANIFEST_PATH
NOT_A_RECORD = "is not a record"
SHOWN = 3
# Why: `000000` is how a diff writes a path that one side does not hold.
REGULAR = frozenset({"100644", "100755", "000000"})
IRREGULAR = {"120000": "a symbolic link", "160000": "a submodule"}
# Why: a record is read and never run, so it is a plain file on each side that holds it.
RECORD_MODES = frozenset({"100644", "000000"})


@dataclass(frozen=True)
class Reason:
    """Why the owner merges: a code a script can branch on, and the sentence a person reads."""

    code: str
    text: str

    def __str__(self) -> str:
        """The line the judge prints."""
        return f"{self.code}: {self.text}"


@dataclass(frozen=True)
class Change:
    """The commits the judge reads: the default branch's tip, the merge base and the head."""

    root: Path
    base: str
    fork: str
    head: str
    paths: tuple[str, ...]

    def before(self, path: str) -> str | None:
        """``path`` at the merge base, or ``None`` when it is not there or is not text."""
        return git(self.root, "show", f"{self.fork}:{path}")

    def tip(self, path: str) -> str | None:
        """``path`` at the default branch's tip, or ``None`` when it is not there or is not text."""
        return git(self.root, "show", f"{self.base}:{path}")

    def after(self, path: str) -> str | None:
        """``path`` at the head, or ``None`` when it is not there or is not text."""
        return git(self.root, "show", f"{self.head}:{path}")

    def held(self, path: str) -> bool:
        """Whether the merge base holds ``path``."""
        return git(self.root, "cat-file", "-e", f"{self.fork}:{path}") is not None

    def kept(self, path: str) -> bool:
        """Whether the head still holds ``path``."""
        return git(self.root, "cat-file", "-e", f"{self.head}:{path}") is not None


def git(root: Path, *args: str, env: dict[str, str] | None = None) -> str | None:
    """``git -C root args`` stdout, unstripped; ``None`` when git fails or it is not UTF-8."""
    done = subprocess.run(
        ["git", "-C", str(root), *args], capture_output=True, check=False, env=env
    )
    if done.returncode != 0:
        return None
    try:
        return done.stdout.decode()
    except UnicodeDecodeError:
        return None


def commit(root: Path, ref: str) -> str | None:
    """The commit ``ref`` names."""
    found = git(root, "rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}")
    return found.strip() if found else None


def default_ref(root: Path) -> str:
    """The first default-branch candidate that names a commit, else the first by name."""
    candidates = gatelib.default_branches(root)
    return next((ref for ref in candidates if commit(root, ref)), candidates[0])


def read(root: Path, base_ref: str, head_ref: str) -> Change | Reason:
    """The change from ``base_ref`` to ``head_ref``, or why it cannot be read."""
    base, head = commit(root, base_ref), commit(root, head_ref)
    if base is None or head is None:
        missing = base_ref if base is None else head_ref
        return Reason("refs", f"`{missing}` names no commit here, so there is nothing to judge")
    fork = git(root, "merge-base", base, head)
    listed = (
        None
        if fork is None
        else git(root, "diff", "--name-only", "--no-renames", "-z", fork.strip(), head)
    )
    if fork is None or listed is None:
        return Reason("refs", f"`{base_ref}` and `{head_ref}` share no history the judge can read")
    paths = tuple(sorted(path for path in listed.split("\0") if path))
    return Change(root, base, fork.strip(), head, paths)


def manifest_of(change: Change) -> Manifest | Reason:
    """The manifest at the default branch's tip: the owner's, whatever the change holds."""
    return manifest_at(change.root, change.base)


def manifest_at(root: Path, ref: str) -> Manifest | Reason:
    """The manifest ``ref`` holds, or why there is none to read."""
    text = git(root, "show", f"{ref}:{MANIFEST_PATH}")
    if text is None:
        return Reason(
            "manifest",
            f"the default branch has no {MANIFEST_PATH}, so nothing says what a guard path or a"
            " one-way door is",
        )
    try:
        return parse(text)
    except ManifestError as error:
        return Reason("manifest", f"{MANIFEST_PATH} on the default branch does not parse: {error}")


def adoption(change: Change) -> Manifest | None:
    """The manifest the change brings when it is the adoption, else ``None``.

    The adoption brings the manifest and the binding to a default branch whose tip and merge base
    hold neither. Why both commits: a base with a manifest is adopted, whatever a branch deletes,
    and a tip that lost one the merge base held is the owner's to explain, not a fresh start.
    """
    binding = gatelib.BINDING.as_posix()
    held = [
        git(change.root, "cat-file", "-e", f"{side}:{path}")
        for side in (change.base, change.fork)
        for path in (MANIFEST_PATH, binding)
    ]
    text = change.after(MANIFEST_PATH)
    if any(found is not None for found in held) or text is None or not change.kept(binding):
        return None
    try:
        return parse(text)
    except ManifestError:
        return None


def unlike_template(brought: Manifest) -> list[Reason]:
    """Why the adoption's manifest guards less than the template it fills, one reason per group.

    Why: nothing before the adoption judges it, so it must keep every template group in order,
    with its rule, its exclusions and its paths. It may add paths to a group that is not
    ``edit = "append"``, and groups after the template's: neither lets anything through.
    """
    template = parse(TEMPLATE.read_text(encoding="utf-8"))
    names = [group.name for group in brought.groups[: len(template.groups)]]
    if names != [group.name for group in template.groups]:
        text = f"{MANIFEST_PATH}: the adoption does not keep the template's groups in their order"
        return [Reason("guard", text)]
    return [
        Reason(
            "guard", f"{MANIFEST_PATH} ({want.name}): the adoption guards less than the template"
        )
        for want, got in zip(template.groups, brought.groups, strict=False)
        if (got.edit, got.excluded) != (want.edit, want.excluded)
        or not set(want.patterns) <= set(got.patterns)
        or (got.additive and set(got.patterns) != set(want.patterns))
    ]


def smuggled(change: Change) -> list[Reason]:
    """What the adoption carries that it never writes: an instruction file, or the label.

    Why: the guard and record reasons are not asked of the adoption, and from its merge on the
    owner trusts the guards; an instruction file or an agreed ticket must not ride in with it.
    """
    labels = spellings(change)
    reasons = []
    for path in change.paths:
        if instructs(path) and path not in ADOPTION_WRITES:
            text = f"{path} is an instruction file agents obey, and not one the adoption writes"
            reasons.append(Reason("guard", text))
        elif matches(path.casefold(), TICKETS) and carries(change.after(path) or "", labels):
            text = f"{path} is a ticket with the agent-ready label, which only the owner gives"
            reasons.append(Reason("record", text))
    return reasons


def adopts(root: Path, base_ref: str, head_ref: str) -> bool:
    """Whether ``head_ref`` is the adoption onto ``base_ref``: see ``adoption``."""
    change = read(root, base_ref, head_ref)
    return isinstance(change, Change) and adoption(change) is not None


def folded(manifest: Manifest) -> Manifest:
    """``manifest`` with every pattern in lower case, to match a path that differs only by case.

    Why: on a file system that ignores case, ``Justfile`` is ``justfile``.
    """
    groups = tuple(
        Group(
            group.name,
            group.reason,
            tuple(pattern.casefold() for pattern in group.patterns),
            group.edit,
            tuple(pattern.casefold() for pattern in group.excluded),
        )
        for group in manifest.groups
    )
    doors = tuple(
        Door(door.name, door.reason, tuple(p.casefold() for p in door.patterns), door.on)
        for door in manifest.doors
    )
    return Manifest(groups, doors)


# --- guard paths -----------------------------------------------------------------------------


def shape(value: object) -> object:
    """A thresholds table with every number and flag blanked: what a ratchet never changes."""
    if isinstance(value, dict):
        return {key: shape(item) for key, item in value.items()}
    return None if isinstance(value, (bool, int, float)) else value


def more_than_tightened(before: str | None, after: str | None) -> str | None:
    """Why a change to the thresholds is more than a ratchet, or ``None`` when that is all it is."""
    if before is None or after is None:
        return "the file is new, gone or not text"
    try:
        old, new = tomllib.loads(before), tomllib.loads(after)
    except tomllib.TOMLDecodeError:
        return "the file does not parse on one side"
    if shape(old) != shape(new):
        return "a key, a path or a text value changed, where a ratchet moves numbers only"
    return "; ".join(lowered.loosened(lowered.flatten(old), lowered.flatten(new))) or None


def more_than_shrunk(change: Change) -> str | None:
    """Why a change to the owed ledger is more than a shrink, or ``None`` when that is all it is.

    Why: ADR 0037. An entry that goes lets the check it excused run again, so dropping entries
    loosens nothing. An entry that is added or reworded excuses a finding, and the plugin pin
    moving is what allows that, so each of those stays the owner's.
    """
    after = owed.parse(change.after(LEDGER))
    if after.problems:
        return "the ledger does not read: " + "; ".join(after.problems)
    added = Counter(after.entries) - Counter(owed.parse(change.before(LEDGER)).entries)
    if added:
        return "adds or changes " + ", ".join(sorted(f"'{e.problem}' ({e.path})" for e in added))
    if plugin_pin.pinned(change.before(BINDING)) != plugin_pin.pinned(change.after(BINDING)):
        return "the plugin pin moves with it"
    return None


def addition(change: Change, path: str) -> str | None:
    """Why the change of a record is more than an addition (ADR 0014), or ``None``."""
    before = records.before(lambda args: git(change.root, *args), change.fork, path)
    note = records.note(path, before, change.after(path))
    return None if note.startswith(records.ADDITIONS) else note


def dependency_notes(
    change: Change,
    paths: Sequence[str],
    entry: Callable[[Path, str], AbstractContextManager[dependencies.Runner]] = Entry,
) -> dict[str, str]:
    """What each changed dependency manifest or lockfile does, as the merge base's binding names.

    The binding's Dependencies section and the entry point it names are the merge base's: the
    change under judgement cannot rewrite what judges it (ADR 0042).
    """
    contract = dependencies.parse(change.before(str(gatelib.BINDING)))
    if contract is None:
        return {}
    texts = {
        path: (change.before(path), change.after(path))
        for path in paths
        if contract.recognised(path)
    }
    if not texts:
        return {}
    with entry(change.root, change.fork) as run:
        verdicts = dependencies.judge(contract, texts, run)
    return {
        path: (
            f"not dependency-only: {verdict}"
            if isinstance(verdict, dependencies.NotDependencyOnlyError)
            else "a dependency is the owner's to merge: " + ("; ".join(map(str, verdict)) or "none")
        )
        for path, verdict in verdicts.items()
    }


def guard_note(change: Change, path: str, group: Group, *, notes: Mapping[str, str]) -> str | None:
    """What a changed guard path's change is, or ``None`` when the judge lets it through."""
    if group.additive:
        return addition(change, path)
    if path == THRESHOLDS:
        return more_than_tightened(change.before(path), change.after(path))
    if path == LEDGER:
        return more_than_shrunk(change)
    return notes.get(path, "")


def instructs(path: str) -> bool:
    """Whether an agent reads ``path`` as instructions: by its name, or a ``.claude/`` above it.

    Letter case is set aside: a file system that ignores it opens ``claude.md`` as ``CLAUDE.md``.
    """
    *folders, name = path.casefold().split("/")
    return name in INSTRUCTION_FILES or INSTRUCTION_DIRECTORY in folders


def guard_reasons(change: Change, manifest: Manifest) -> list[Reason]:
    """One reason per changed guard path the judge does not let through.

    An instruction file is a guard path wherever it sits, whatever the manifest says.
    """
    lower = folded(manifest)
    reasons = []
    notes = dependency_notes(change, [p for p in change.paths if manifest.group_of(p)])
    for path in change.paths:
        group = manifest.group_of(path) or (HARNESS if path == MANIFEST_PATH else None)
        if group is None and instructs(path):
            reasons.append(Reason("guard", f"{path} {INSTRUCTS}"))
            continue
        if group is None:
            other = lower.group_of(path.casefold())
            if other is not None:
                text = f"{path} ({other.name}): matches a guard pattern but for its case"
                reasons.append(Reason("guard", text))
            continue
        note = guard_note(change, path, group, notes=notes)
        if note is not None:
            reasons.append(
                Reason("guard", f"{path} ({group.name})" + (f": {note}" if note else ""))
            )
    return reasons


def modes(change: Change) -> dict[str, tuple[str, str]]:
    """Each changed path with its file mode at the merge base and at the head."""
    raw = git(change.root, "diff", "--raw", "--no-renames", "-z", change.fork, change.head) or ""
    fields = raw.split("\0")
    return {
        path: (meta[1:].split()[0], meta.split()[1])
        for meta, path in zip(fields[::2], fields[1::2], strict=False)
    }


def file_reasons(change: Change, manifest: Manifest) -> list[Reason]:
    """One reason per guard, door or record path that is not a regular file on either side."""
    lower = folded(manifest)
    reasons = []
    for path, sides in modes(change).items():
        judged = (
            path == MANIFEST_PATH
            or instructs(path)
            or RECORD.match(path)
            or manifest.group_of(path)
            or manifest.doors_of(path)
            or lower.group_of(path.casefold())
            or lower.doors_of(path.casefold())
        )
        for mode, side in zip(sides, ("the merge base", "the head"), strict=True):
            if judged and mode not in REGULAR:
                kind = IRREGULAR.get(mode, f"of mode {mode}")
                text = f"{path} is {kind} at {side}, and the judge reads regular files only"
                reasons.append(Reason("file", text))
    return reasons


# --- one-way doors ---------------------------------------------------------------------------


def unique(pairs: list[tuple[str, object]]) -> dict[str, object]:
    keys = [key for key, _ in pairs]
    if len(set(keys)) != len(keys):
        message = "a key is given twice"
        raise ValueError(message)
    return dict(pairs)


def holds(new: object, old: object) -> bool:
    """Whether ``new`` still holds everything ``old`` does: each key, each item, each value."""
    if isinstance(old, dict):
        return isinstance(new, dict) and all(
            key in new and holds(new[key], item) for key, item in old.items()
        )
    if isinstance(old, list):
        rest = iter(new) if isinstance(new, list) else iter(())
        return all(any(holds(candidate, item) for candidate in rest) for item in old)
    return type(new) is type(old) and new == old


def more_than_lines_added(before: str, after: str) -> str | None:
    old = [line for line in before.splitlines() if line.strip()]
    new = [line for line in after.splitlines() if line.strip()]
    if any(line[0].isspace() for line in old + new):
        return "a line is indented, so its meaning hangs on its neighbours; snapshot it as JSON"
    if len(set(old)) != len(old) or len(set(new)) != len(new):
        return "a line is repeated, so one removed could hide behind one added"
    wanted = set(old)
    if [line for line in new if line in wanted] != old:
        return "a line the default branch holds is gone, changed or moved"
    return None


def more_than_added(path: str, before: str | None, after: str | None) -> str | None:
    """Why a change does more than add to a file, or ``None`` when adding is all it does."""
    if before is None or after is None:
        return "one side is not text the judge can read"
    if not path.endswith(".json"):
        return more_than_lines_added(before, after)
    try:
        old: object = json.loads(before, object_pairs_hook=unique)
        new: object = json.loads(after, object_pairs_hook=unique)
    except ValueError:
        return "one side is not JSON, or gives a key twice"
    return None if holds(new, old) else "a key, an item or a value the default branch holds is gone"


def shut(change: Change, path: str, door: Door) -> str | None:
    """Why ``path``'s change goes through ``door``, or ``None`` when it does not."""
    if door.on is DoorRule.CHANGE:
        return door.reason
    if not change.held(path):
        return None
    if door.on is DoorRule.REWRITE:
        return f"rewrites or deletes a file the default branch holds. {door.reason}"
    if not change.kept(path):
        return f"deletes it. {door.reason}"
    why = more_than_added(path, change.before(path), change.after(path))
    return None if why is None else f"{why}. {door.reason}"


def doors(change: Change, manifest: Manifest) -> list[tuple[str, Door]]:
    """Every changed path with each one-way door it sits behind, letter case set aside."""
    lower = folded(manifest)
    return [
        (path, door)
        for path in change.paths
        for door in manifest.doors_of(path) or lower.doors_of(path.casefold())
    ]


def door_reasons(change: Change, manifest: Manifest) -> list[Reason]:
    """One reason per changed path and one-way door it goes through."""
    reasons = []
    for path, door in doors(change, manifest):
        why = shut(change, path, door)
        if why is not None:
            reasons.append(Reason("door", f"{path} ({door.name}): {why}"))
    return reasons


def flag_doors(old: list[registry.Flag], new: list[registry.Flag]) -> list[str]:
    """What the change does to a flag that ships behaviour on, or fixes it for good."""
    was = {registry.name(flag): flag for flag in old}
    now = {registry.name(flag): flag for flag in new}
    found = []
    if len(now) != len(new):
        found.append("a flag is declared twice, so its default cannot be read")
    for key, flag in sorted(now.items()):
        prior = was.get(key, {})
        if flag.get("default") is True and prior.get("default") is not True:
            found.append(f"flag '{key}' is on by default, which ships its behaviour to everyone")
        if flag.get("permanent") is True and prior.get("permanent") is not True:
            found.append(f"flag '{key}' becomes permanent")
    found += [
        f"flag '{key}' leaves the registry while off by default: its behaviour ships on, or goes"
        for key, flag in sorted(was.items())
        if key not in now and flag.get("default") is not True
    ]
    return found


def registry_state(change: Change) -> list[str] | None:
    """The registry's one-way doors, or ``None`` when it is gone or does not parse."""
    before, after = change.before(registry.REGISTRY), change.after(registry.REGISTRY)
    old, unread = registry.parse(before or "")
    new, unreadable = registry.parse(after or "")
    if after is None or unread or unreadable:
        return None
    return flag_doors(old, new)


def registry_reasons(change: Change) -> list[Reason]:
    """Why a registry that changed cannot be read at all: the owner's, whatever the events.

    Why: it is not a door the project may not have met yet; the judge is blind to every flag, so it
    does not wait for a life event.
    """
    if registry.REGISTRY not in change.paths or registry_state(change) is not None:
        return []
    text = "the registry is gone or does not parse, so no flag's default can be compared"
    return [Reason("door", f"{registry.REGISTRY}: {text}")]


def flag_reasons(change: Change) -> list[Reason]:
    """The flag registry's one-way doors; the registry is always one, whatever the manifest says."""
    found = registry_state(change) if registry.REGISTRY in change.paths else None
    return [Reason("door", f"{registry.REGISTRY}: {text}") for text in found or []]


# --- new surface with no flag ----------------------------------------------------------------


def flags_asked(change: Change) -> bool:
    """Whether the method asks the flag questions of this repository's application types.

    The binding at the default branch's tip says which types it is. One that names no type, or a
    type the catalogue lacks, is asked: the judge fails closed.
    """
    binding = git(change.root, "show", f"{change.base}:{gatelib.BINDING.as_posix()}")
    kinds = application_types.declared(binding or "")
    catalogue = application_types.load()
    asked_of = catalogue.asked_of(FLAGS_QUESTION)
    return not kinds or any(kind in asked_of or kind not in catalogue.types for kind in kinds)


def grown(change: Change, path: str) -> bool:
    """Whether a file the change only added to holds more at the head than at the merge base."""
    if not change.held(path):
        return True
    before, after = change.before(path) or "", change.after(path) or ""
    if path.endswith(".json"):
        return bool(json.loads(before) != json.loads(after))
    return [line for line in before.splitlines() if line.strip()] != [
        line for line in after.splitlines() if line.strip()
    ]


def new_surface(change: Change, manifest: Manifest) -> list[str]:
    """The paths behind a door that shuts on removal which the change adds to, and only adds to."""
    return [
        path
        for path, door in doors(change, manifest)
        if door.on is DoorRule.REMOVAL and shut(change, path, door) is None and grown(change, path)
    ]


def dark_flag(change: Change, surface: list[str]) -> bool:
    """Whether the change declares a flag that is off, or a line it adds names one that is off.

    Why: the second half lets a feature built over several changes stay behind its one flag. The
    lines read are the added ones outside ``docs/``, the registry and the surface files, so a
    spec that names the flag, or a route that shares its name, hides nothing.
    """
    old, _ = registry.parse(change.before(registry.REGISTRY) or "")
    new, _ = registry.parse(change.after(registry.REGISTRY) or "")
    was = {registry.name(flag) for flag in old}
    dark = {registry.name(flag) for flag in new if flag.get("default") is False}
    if dark - was:
        return True
    code = [
        path
        for path in change.paths
        if path != registry.REGISTRY and path not in surface and not path.startswith(DOCS)
    ]
    if not code:
        return False
    compared = ("diff", "-U0", "--no-renames", change.fork, change.head)
    diff = git(change.root, "--literal-pathspecs", *compared, "--", *code) or ""
    added = [
        line for line in diff.splitlines() if line.startswith("+") and not line.startswith("+++")
    ]
    return any(key in line for key in dark for line in added)


def unflagged_reasons(change: Change, manifest: Manifest) -> list[Reason]:
    """New surface that no flag hides: new behaviour ships dark, or the owner sees it."""
    surface = new_surface(change, manifest)
    if not surface or not flags_asked(change) or dark_flag(change, surface):
        return []
    text = (
        f"{', '.join(surface)}: the change adds surface a consumer can meet, and it neither"
        f" declares a flag that is off in {registry.REGISTRY} nor names one in the code it adds;"
        " ship it dark, or the owner merges it"
    )
    return [Reason("unflagged", text)]


# --- disputes --------------------------------------------------------------------------------


def dispute_reasons(change: Change) -> list[Reason]:
    """Disputes the change wrote or edited, and open ones that name a path it touches."""
    reasons = [
        Reason("dispute", f"{path}: the change adds or edits a dispute, and the owner decides one")
        for path in change.paths
        if DISPUTE.match(path)
    ]
    listed = git(change.root, "ls-tree", "-r", "--name-only", "-z", change.head, "--", DISPUTES)
    for path in sorted(p for p in (listed or "").split("\0") if DISPUTE.match(p)):
        if path in change.paths:
            continue
        text = change.after(path) or ""
        status = STATUS.search(text)
        named = [touched for touched in change.paths if touched in text]
        if named and (status is None or status.group(1) not in DECIDED):
            reasons.append(Reason("dispute", f"{path} is open and names {', '.join(named)}"))
    return reasons


# --- records alone ---------------------------------------------------------------------------


def holders(manifest: Manifest, path: str) -> tuple[list[Group], list[Door]]:
    """Every guard group and one-way door that holds ``path``, letter case set aside.

    Why: ``group_of`` names the first group only, and a stricter group further down the
    manifest must keep a path from being read as a record.
    """
    lower = folded(manifest)
    groups = [
        group
        for group, low in zip(manifest.groups, lower.groups, strict=True)
        if Manifest((group,)).group_of(path) or Manifest((low,)).group_of(path.casefold())
    ]
    return groups, manifest.doors_of(path) + lower.doors_of(path.casefold())


def spellings(change: Change) -> tuple[str, ...]:
    """The agent-ready label as the default branch's triage-labels record and the role spell it."""
    spelled = label_for_role(change.tip(LABELS_RECORD.as_posix()) or "", READY_ROLE)
    return tuple(sorted({READY_ROLE, spelled or READY_ROLE}))


def carries(text: str, labels: tuple[str, ...]) -> bool:
    """Whether ``text`` holds the label in any of its spellings, in any case, anywhere."""
    return any(label.casefold() in text.casefold() for label in labels)


@functools.cache
def labelled(change: Change) -> frozenset[str]:
    """The tickets that carry the agent-ready label at the merge base or at the tip.

    Why: the label is the owner's agreement to build a ticket as it stands (ADR 0008). The tip
    counts too: a ticket labelled there after the change forked is agreed in the merge.
    """
    labels = spellings(change)
    found = set()
    for ref in (change.fork, change.base):
        listed = git(
            change.root, "ls-tree", "-r", "--name-only", "-z", ref, "--", TRACKER_DIRECTORY
        )
        for path in (listed or "").split("\0"):
            ticket = matches(path, TRACKER) and matches(path.casefold(), TICKETS)
            text = git(change.root, "show", f"{ref}:{path}") if ticket else None
            if text is not None and carries(text, labels):
                found.add(path)
    return frozenset(found)


@contextlib.contextmanager
def tracker_names(change: Change, ref: str) -> Iterator[Path]:
    """A directory with an empty file for each path ``ref`` holds under ``.scratch/``.

    Why: the preflight finds a ticket's spec and blockers by the names beside it, so the judge
    lends it the names a commit holds and asks it, rather than finding them a second way.
    """
    listed = git(change.root, "ls-tree", "-r", "--name-only", "-z", ref, "--", TRACKER_DIRECTORY)
    with tempfile.TemporaryDirectory() as scratch:
        for path in filter(None, (listed or "").split("\0")):
            target = Path(scratch, path)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.touch()
        yield Path(scratch)


@functools.cache
def build_inputs(change: Change) -> tuple[dict[str, str], dict[str, str]]:
    """What each labelled ticket reads to decide what it builds, each with the ticket reading it.

    The first is read whole: the ticket and the spec it inherits from. The second for its
    ``Status:`` line: a ticket it is blocked by. ``preflight.reads`` names both, at the merge
    base, the tip and the head, so a file the change makes one of them counts too.
    """
    whole: dict[str, str] = {}
    status: dict[str, str] = {}
    for ref in (change.fork, change.base, change.head):
        with tracker_names(change, ref) as names:
            for ticket in sorted(labelled(change)):
                text = git(change.root, "show", f"{ref}:{ticket}")
                read, waited = preflight.reads(preflight.local_ticket(ticket, text or ""), names)
                for path in read:
                    whole.setdefault(path, ticket)
                for path in waited:
                    status.setdefault(path, ticket)
    return whole, status


def unreadable(change: Change, path: str) -> str | None:
    return "is not text the judge can read" if change.after(path) is None else None


def open_ticket(path: str, text: str | None) -> bool:
    """Whether a ticket is open as the preflight reads it; a missing one blocks as an open one."""
    return text is None or preflight.local_ticket(path, text).is_open


def agreement(change: Change, path: str) -> str | None:
    """Why a change of what a labelled ticket reads is the owner's: what it builds, or when."""
    whole, status = build_inputs(change)
    if path in whole:
        ticket = whole[path]
        if ticket == path:
            return "is a ticket the owner agreed to build, and only they change it"
        return f"is what {ticket} inherits its approach from, a ticket the owner agreed to build"
    if path in status and not change.held(path):
        # Why: a `Blocked by` number reads the one ticket that bears it, so a second one, open
        # or closed, turns the blocker from found to "not found".
        return (
            f"is a new ticket the `Blocked by` line of {status[path]} can name, which changes"
            " whether that ticket may start, a ticket the owner agreed to build"
        )
    if path in status and open_ticket(path, change.before(path)) != open_ticket(
        path, change.after(path)
    ):
        return f"changes whether {status[path]} may start, a ticket the owner agreed to build"
    return None


def closes(change: Change, path: str) -> str | None:
    """Why a ticket's change is the owner's: it closes the ticket, and no build of it merged."""
    if open_ticket(path, change.before(path)) and not open_ticket(path, change.after(path) or ""):
        return "closes a ticket, which only the merge of its build does"
    return None


def part_of_a_split(change: Change, path: str) -> bool:
    """Whether ``path`` is a part of a ticket split by size, which inherits the owner's label.

    Why: ADR 0046. The parent is read as the default branch's tip holds it, never as the part's
    own text says, it must carry the label there and be no part itself, and the parts at the
    head carry its promises verbatim and exactly once between them.
    """
    text = change.after(path) or ""
    ref = splits.split_from(text)
    if ref is None:
        return False
    folder = PurePosixPath(path).parent
    parent = change.tip(ref if "/" in ref else (folder / ref).as_posix())
    listed = git(change.root, "ls-tree", "--name-only", "-z", change.head, "--", f"{folder}/")
    others = [name for name in (listed or "").split("\0") if name not in ("", path)]
    siblings = [
        found
        for name in others
        if (found := change.after(name)) is not None and splits.split_from(found) == ref
    ]
    return (
        parent is not None
        and not splits.scope_problems(parent, [text, *siblings])
        and any(
            splits.refusal(text, parent, label, siblings=siblings) is None
            for label in spellings(change)
        )
    )


def label_given(change: Change, path: str) -> str | None:
    if part_of_a_split(change, path):
        return None
    before, after = (change.before(path) or "").casefold(), (change.after(path) or "").casefold()
    for label in spellings(change):
        if after.count(label.casefold()) > before.count(label.casefold()):
            return f"gives a ticket the agent-ready label `{label}`, which is the owner's act"
    return None


def namesake(change: Change, path: str) -> str | None:
    """Why a new ticket is the owner's: it shares its number with another beside it.

    Why: a ``Blocked by`` line that names a number reads the first file of the folder whose name
    starts with it, so a new ticket with that number could stand in for the one the owner meant.
    """
    number = NUMBER.match(PurePosixPath(path).name)
    if change.held(path) or number is None:
        return None
    folder = PurePosixPath(path).parent.as_posix()
    for ref in (change.head, change.base):
        listed = git(change.root, "ls-tree", "--name-only", "-z", ref, "--", f"{folder}/") or ""
        for other in listed.split("\0"):
            lead = NUMBER.match(PurePosixPath(other).name)
            if other not in ("", path) and lead and int(lead.group()) == int(number.group()):
                return f"shares its number with {other}, which a `Blocked by` line could mean"
    return None


def ticket_refusal(change: Change, path: str) -> str | None:
    """Why a ticket's change is the owner's: the label given, a borrowed number, a close."""
    return label_given(change, path) or namesake(change, path) or closes(change, path)


def nothing(_change: Change, _path: str) -> None:
    return None


def not_an_addition(change: Change, path: str) -> str | None:
    note = addition(change, path)
    return None if note is None else f"is {note}"


@dataclass(frozen=True)
class RecordKind:
    """A file the owner's own conversation writes, which no verdict record is asked of.

    ``shape`` is its whole path, as the template or skill that writes it names it. A kind that is
    ``appended`` is held by ``edit = "append"`` groups of the default branch's manifest, as the
    template holds it; any other kind by no group. No one-way door holds either. ``refuses``
    says why one change of it is more than such a record takes. A kind that ``decides`` is a
    decision, which the owner agrees to however it is written: the judge cannot read meaning.
    """

    shape: re.Pattern[str]
    refuses: Callable[[Change, str], str | None]
    appended: bool = False
    decides: bool = False


# Why closed: a file no skill writes is no record, so a name the judge was never told of, an
# instruction file or a script among them, takes the pipeline's path. Every component is lower
# case, so no two records are one file on a file system that ignores case.
RECORDS = (
    # to-tickets, one file per ticket numbered from 01 (the local tracker of mattpocock-skills)
    RecordKind(re.compile(rf"\.scratch/{SLUG}/issues/\d+-{SLUG}\.md"), ticket_refusal),
    # to-spec; /ratchet:conjecture writes the conjecture into it
    RecordKind(re.compile(rf"\.scratch/{SLUG}/spec\.md"), nothing),
    # the change description with no forge: adopt, the feature skill and the route name it
    RecordKind(re.compile(rf"\.scratch/changes/{CHANGE_NAME}\.md"), nothing),
    # a term or an `_Avoid_` item that domain-modeling adds (ADR 0014)
    RecordKind(re.compile(r"GLOSSARY\.md"), not_an_addition, appended=True),
    # a decision record domain-modeling writes: the owner's to agree, so --records can land it
    RecordKind(re.compile(rf"docs/adr/\d{{4}}-{SLUG}\.md"), nothing, appended=True, decides=True),
)


def record_kind(manifest: Manifest, path: str) -> RecordKind | None:
    """The kind of record ``path`` is by ``manifest``, or ``None`` when it is none."""
    kind = next((kind for kind in RECORDS if kind.shape.fullmatch(path)), None)
    if kind is None or instructs(path):
        return None
    groups, doors = holders(manifest, path)
    held = bool(groups) and all(group.additive for group in groups) if kind.appended else not groups
    return kind if held and not doors else None


@functools.cache
def spelled(change: Change) -> dict[str, frozenset[str]]:
    """Every path the head or the tip holds, by its name with letter case set aside."""
    found: dict[str, set[str]] = {}
    for ref in (change.head, change.base):
        for path in (git(change.root, "ls-tree", "-r", "--name-only", "-z", ref) or "").split("\0"):
            found.setdefault(path.casefold(), set()).add(path)
    return {key: frozenset(paths) for key, paths in found.items()}


def twin(change: Change, path: str) -> str | None:
    """Why a record is the owner's: another path is the same file where case is ignored."""
    others = sorted(spelled(change).get(path.casefold(), frozenset()) - {path})
    if not others:
        return None
    return (
        f"has a twin in another letter case, {others[0]}, which a file system that ignores case"
        " opens as this file"
    )


def refusal(change: Change, path: str, *, kind: RecordKind, sides: tuple[str, str]) -> str | None:
    """Why the change of record ``path`` is more than a record takes, or ``None`` when it is not."""
    odd = [mode for mode in sides if mode not in RECORD_MODES]
    if odd:
        return f"is {IRREGULAR.get(odd[0], f'of mode {odd[0]}')}, where a record is a plain file"
    if not change.kept(path):
        return "deletes a record"
    if change.tip(path) != change.before(path):
        return (
            "changed on the default branch after the change forked, so the merge holds text the"
            " judge did not read; bring the change up to date"
        )
    return (
        twin(change, path)
        or unreadable(change, path)
        or agreement(change, path)
        or kind.refuses(change, path)
    )


def decision(change: Change, path: str) -> Reason:
    """The owner's question for a decision record: one sentence that names its title."""
    found = TITLE.search(change.after(path) or "")
    named = found.group(1).strip() if found else PurePosixPath(path).stem
    title = "".join(character for character in named if character.isprintable())[:TITLE_WIDTH]
    return Reason("record", f'{path} records a decision: "{title}". Yours to agree.')


def records_alone(change: Change, manifest: Manifest) -> list[Reason]:
    """Why a change with no verdict record is the owner's; none when it is records alone.

    More than records is the plain reason. Records some change of which is more than a record
    takes name up to three of them, and each decision record asks its own question.
    """
    sides = modes(change)
    refused: dict[str, str] = {}
    decided = []
    for path in change.paths:
        kind = record_kind(manifest, path)
        why = NOT_A_RECORD if kind is None else refusal(change, path, kind=kind, sides=sides[path])
        if why is not None:
            refused[path] = why
        elif kind is not None and kind.decides:
            decided.append(decision(change, path))
    text = (
        "no verdict record (docs/features/<slug>/verdict.json) is part of the change, so it is"
        " not the product of the pipeline"
    )
    if not change.paths or NOT_A_RECORD in refused.values():
        return [Reason("record", text)]
    if not refused:
        return decided
    lines = [f"{path} {why}" for path, why in refused.items()]
    rest = f"; and {len(lines) - SHOWN} more" if len(lines) > SHOWN else ""
    return [
        Reason("record", f"{text}; its paths are records, but {'; '.join(lines[:SHOWN])}{rest}"),
        *decided,
    ]


def not_records(root: Path, base_ref: str, paths: Iterable[str]) -> list[str]:
    """The paths that are no kind of record by the manifest ``base_ref`` holds: all, with none."""
    manifest = manifest_at(root, base_ref)
    return [
        path
        for path in paths
        if isinstance(manifest, Reason) or record_kind(manifest, path) is None
    ]


# --- the verdict record ----------------------------------------------------------------------


def reviewed_tree(root: Path, ref: str, record: str) -> str | None:
    """The tree of ``ref`` with the verdict record set aside: what the record certifies."""
    with tempfile.TemporaryDirectory() as scratch:
        env = {**os.environ, "GIT_INDEX_FILE": str(Path(scratch) / "index")}
        steps = (
            ("read-tree", ref),
            ("update-index", "--force-remove", "--", record),
            ("write-tree",),
        )
        results = [git(root, *step, env=env) for step in steps]
    tree = results[-1]
    return tree.strip() if tree and None not in results else None


def listed(value: object) -> list[str] | None:
    """``value`` as a list of texts, or ``None`` when it is not one."""
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        return None
    return [str(item) for item in value]


def record_problems(data: dict[str, object], slug: str, tree: str | None) -> list[str]:
    """What stops a verdict record from being an approval of ``tree``."""
    problems = []
    if data.get("slug") != slug:
        problems.append(f"it names the feature {data.get('slug')!r} and sits under {slug!r}")
    if tree is None or data.get("tree") != tree:
        problems.append(
            f"it certifies tree {str(data.get('tree'))[:12]} and the head's tree without the"
            f" record is {str(tree)[:12]}: a commit came after the review, or the record is"
            " another change's"
        )
    if data.get("verdict") != APPROVE:
        problems.append(f"its verdict is {data.get('verdict')!r}, not {APPROVE!r}")
    for key, what in (
        ("unresolved", "unresolved blocker or major finding"),
        ("openQuestions", "open question"),
    ):
        items = listed(data.get(key))
        if items is None:
            problems.append(f"its `{key}` is not a list of texts")
        elif items:
            problems.append(f"{len(items)} {what}(s): {'; '.join(items)}")
    return problems


def record_reasons(change: Change, manifest: Manifest) -> list[Reason]:
    """Why the change is not an approved product of the pipeline, read from its verdict record.

    A change with no verdict record that is records alone is asked for none (ADR 0031).
    """
    found = [path for path in change.paths if RECORD.match(path)]
    if not found:
        return records_alone(change, manifest)
    if len(found) != 1:
        text = (
            f"the change touches {len(found)} verdict records ({', '.join(found)}), and one change"
            " carries one"
        )
        return [Reason("record", text)]
    path = found[0]
    try:
        data: object = json.loads(change.after(path) or "")
    except ValueError:
        data = None
    if not isinstance(data, dict):
        return [Reason("record", f"{path} is gone or is not a JSON object")]
    slug = RECORD.fullmatch(path)
    problems = record_problems(
        data, slug.group(1) if slug else "", reviewed_tree(change.root, change.head, path)
    )
    return [Reason("record", f"{path}: {problem}") for problem in problems]


# --- the judge -------------------------------------------------------------------------------


@dataclass(frozen=True)
class Asked:
    """What the caller knows that no commit says: the pull request's target and tier 2's state."""

    target: str | None = None
    current: bool = False
    tier2: str = "none"


def setting_reasons(change: Change, base_ref: str, asked: Asked) -> list[Reason]:
    reasons = []
    default = (
        base_ref.removeprefix("refs/remotes/").removeprefix("refs/heads/").removeprefix("origin/")
    )
    if asked.target is not None and asked.target != default:
        text = f"the pull request targets `{asked.target}`, not the default branch `{default}`"
        reasons.append(Reason("target", text))
    if asked.current and change.fork != change.base:
        reasons.append(
            Reason(
                "behind",
                f"the branch does not hold the tip of `{default}` ({change.base[:12]}), so its"
                " gates ran on a merge with an older one; bring it up to date and they rerun",
            )
        )
    if asked.tier2 == RED:
        reasons.append(
            Reason(
                "tier2",
                f"tier 2 is red on `{default}`: that is the next piece of work, and the owner"
                " merges what fixes it",
            )
        )
    return reasons


def waiting(
    change: Change,
    reasons: list[Reason],
    *,
    needs: frozenset[str],
    notes: list[str],
    what: str,
) -> list[Reason]:
    """``reasons`` once the merge base declares a life event in ``needs``, else none (ADR 0028).

    Why: the events are read at the fork point and at the default branch's tip, never the head,
    and the union counts. The fork point alone would let a change loosen its own judge; the tip
    alone would too, for a branch cut before the owner declared an event and merged without
    ``current``. A binding with no events line declares every event: see ``declared_or_all``. A
    reason let through is named in ``notes``.
    """
    path = gatelib.BINDING.as_posix()
    declared = frozenset().union(
        *(
            application_types.declared_or_all(text)
            for text in (change.before(path), change.tip(path))
            if text is not None
        )
    )
    if not reasons or declared & needs:
        return reasons
    notes.append(
        f"{what} let through: the merge base's binding declares none of"
        f" {', '.join(sorted(needs))} under `- Life events:`, so nobody meets it yet (ADR 0028)"
    )
    return []


def judge(
    root: Path,
    base_ref: str,
    head_ref: str,
    *,
    asked: Asked | None = None,
    notes: list[str] | None = None,
) -> list[Reason]:
    """Every reason the owner merges ``base_ref..head_ref``; with none, an agent may.

    A reason that waits for a life event the project has not declared is left out, and a line
    saying so is appended to ``notes`` when the caller passes one.
    """
    notes = [] if notes is None else notes
    change = read(root, base_ref, head_ref)
    if isinstance(change, Reason):
        return [change]
    manifest = manifest_of(change)
    if isinstance(manifest, Reason):
        brought = adoption(change)
        if brought is None:
            return [manifest]
        notes.append(
            f"the adoption: the default branch holds no {MANIFEST_PATH} and this change brings it,"
            " so there is no gate before it to loosen; its own gates judge it (ADR 0031)"
        )
        return [
            *setting_reasons(change, base_ref, asked or Asked()),
            *unlike_template(brought),
            *smuggled(change),
            *file_reasons(change, brought),
            *registry_reasons(change),
            *dispute_reasons(change),
        ]
    return [
        *setting_reasons(change, base_ref, asked or Asked()),
        *file_reasons(change, manifest),
        *guard_reasons(change, manifest),
        *registry_reasons(change),
        *waiting(
            change,
            [*door_reasons(change, manifest), *flag_reasons(change)],
            needs=DOOR_EVENTS,
            notes=notes,
            what="a one-way door",
        ),
        *waiting(
            change,
            unflagged_reasons(change, manifest),
            needs=SURFACE_EVENTS,
            notes=notes,
            what="new surface with no flag that is off",
        ),
        *dispute_reasons(change),
        *record_reasons(change, manifest),
    ]


def render(reasons: list[Reason]) -> str:
    """The judge's answer as it is printed and quoted: the verdict line, then every reason."""
    return "\n".join([OWNER if reasons else AGENT, *map(str, reasons)]) + "\n"


def report(reasons: list[Reason], head: str) -> str:
    """The answer as one pull request comment, marked so the merge job edits it in place."""
    if not reasons:
        return (
            f"{MARKER}\n**{AGENT}** at `{head[:12]}`: the merge judge found nothing to wait for.\n"
        )
    rows = "".join(f"- `{reason.code}`: {reason.text}\n" for reason in reasons)
    return (
        f"{MARKER}\n**{OWNER}** at `{head[:12]}`. The merge judge leaves this pull request to"
        f" the owner:\n\n{rows}"
    )


# --- writing the record ----------------------------------------------------------------------


def uncommitted(root: Path) -> list[str]:
    """Every path the index or the work tree changes, a rename read as the two paths it is."""
    status = git(root, "status", "--porcelain", "-z", "--no-renames", "--untracked-files=all")
    return sorted({entry[3:] for entry in (status or "").split("\0") if entry})


def dirty(root: Path, record: str) -> list[str]:
    """Paths with uncommitted changes, the record itself aside."""
    return [path for path in uncommitted(root) if path != record]


def write_record(root: Path, text: str) -> int:
    """Stamp the pipeline's verdict with the tree it reviewed and write it; the exit code."""
    try:
        data: object = json.loads(text)
    except ValueError:
        data = None
    slug = data.get("slug") if isinstance(data, dict) else None
    if not isinstance(data, dict) or not isinstance(slug, str) or not re.fullmatch(SLUG, slug):
        print("record: stdin is not the pipeline's verdictRecord: a JSON object with its slug")
        return 1
    record = f"docs/features/{slug}/verdict.json"
    uncommitted = dirty(root, record)
    tree = reviewed_tree(root, "HEAD", record)
    if uncommitted or tree is None:
        print(
            "record: commit the change first, so the record names the tree that was reviewed:"
            f" {', '.join(uncommitted) or 'HEAD cannot be read'}"
        )
        return 1
    target = root / record
    target.parent.mkdir(parents=True, exist_ok=True)
    stamped = {**data, "schema": SCHEMA, "tree": tree}
    target.write_text(json.dumps(stamped, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"record: {record} certifies tree {tree[:12]}; commit it alone, last")
    return 0


def main(argv: list[str]) -> int:
    recording = argv[1:2] == ["record"]
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", default=".")
    parser.add_argument("--base")
    parser.add_argument("--head", default="HEAD")
    parser.add_argument("--target")
    parser.add_argument("--current", action="store_true")
    parser.add_argument("--tier2", choices=TIER2_STATES, default="none")
    parser.add_argument("--report", type=Path)
    options = parser.parse_args(argv[2:] if recording else argv[1:])
    root = Path(options.root).resolve()
    if recording:
        return write_record(root, sys.stdin.read())
    base = options.base or default_ref(root)
    asked = Asked(options.target, options.current, options.tier2)
    notes: list[str] = []
    reasons = judge(root, base, options.head, asked=asked, notes=notes)
    print(render(reasons), end="")
    for note in notes:
        print(f"note: {note}")
    if options.report is not None:
        head = commit(root, options.head) or options.head
        options.report.write_text(report(reasons, head), encoding="utf-8")
    return 1 if reasons else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
