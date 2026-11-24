#!/usr/bin/env python3
from __future__ import annotations

import argparse
import contextlib
import datetime as dt
import functools
import json
import posixpath
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
import documents
import expect_outputs
import feature_preflight as preflight
import flags as registry
import gatelib
import lowered
import markdown_text
import owed
import plugin_pin
import project_bite
import queue_notes
import split
import unproven

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
WAIT = "merge: wait"
MARKER = "<!-- ratchet:merge -->"
SCHEMA = gatelib.SCHEMA
THRESHOLDS = gatelib.THRESHOLDS.as_posix()
LEDGER = owed.LEDGER.as_posix()
BINDING = gatelib.BINDING.as_posix()
SLUG = gatelib.SLUG
RECORD = re.compile(rf"^docs/features/({SLUG})/verdict\.json$")
DISPUTES = "docs/disputes"
DISPUTE = re.compile(rf"^{DISPUTES}/\d{{4}}-[a-z0-9-]+\.md$")
STATUS = re.compile(r"^- Status:[ \t]*(\S+)[ \t]*$", re.MULTILINE)
DECIDED = frozenset({"upheld", "rejected"})
TREE = re.compile(r"[0-9a-f]{40,64}")
APPROVE = gatelib.APPROVE
TIER_STATES = ("green", "red", "none")
RED = "red"
DOOR_EVENTS = frozenset({"deployed", "irreplaceable-data"})
SURFACE_EVENTS = frozenset({"deployed"})
EXPECT_EVENTS = frozenset({"depended-on"})
FLAGS_QUESTION = "changed flags off and on"
DOCS = "docs/"
PRUNE_SLUG = "prune-pages"
PRUNE_SUBJECT = re.compile(r"docs\(prune\)!?:")
HARNESS = Group("harness", "The manifest says what a guard path is.", (MANIFEST_PATH,))
INSTRUCTION_FILES = frozenset({"agents.md", "claude.md", "claude.local.md", "gemini.md"})
INSTRUCTION_DIRECTORY = ".claude"
INSTRUCTS = "(instructions): an agent reads it as instructions, wherever it sits"
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
REGULAR = frozenset({"100644", "100755", "000000"})
IRREGULAR = {"120000": "a symbolic link", "160000": "a submodule"}
RECORD_MODES = frozenset({"100644", "000000"})
BOUND_KEY = ("ceilings", "deep", "unproven")
LEAVES = "The merge judge leaves this pull request to the owner"
WAITS = (
    "Nothing here is the owner's to decide: the merge judge holds this pull request until what"
    " each reason waits for has happened"
)


@dataclass(frozen=True)
class Reason:
    code: str
    text: str

    def __str__(self) -> str:
        return f"{self.code}: {self.text}"


@dataclass(frozen=True)
class Change:
    root: Path
    base: str
    fork: str
    head: str
    paths: tuple[str, ...]

    def before(self, path: str) -> str | None:
        return git(self.root, "show", f"{self.fork}:{path}")

    def tip(self, path: str) -> str | None:
        return git(self.root, "show", f"{self.base}:{path}")

    def after(self, path: str) -> str | None:
        return git(self.root, "show", f"{self.head}:{path}")

    def held(self, path: str) -> bool:
        return git(self.root, "cat-file", "-e", f"{self.fork}:{path}") is not None

    def kept(self, path: str) -> bool:
        return git(self.root, "cat-file", "-e", f"{self.head}:{path}") is not None

    def moved(self, path: str) -> str | None:
        listed = git(self.root, "diff", "-M", "--name-status", "-z", self.fork, self.head) or ""
        fields = iter(listed.split("\0"))
        for status in fields:
            if status[:1] == "R":
                old, new = next(fields), next(fields)
                if old == path:
                    return new
            else:
                next(fields, None)
        return None


@dataclass(frozen=True)
class Uncommitted(Change):
    def after(self, path: str) -> str | None:
        return gatelib.read_text(self.root / path)

    def kept(self, path: str) -> bool:
        return (self.root / path).is_file()


def git(root: Path, *args: str, env: dict[str, str] | None = None) -> str | None:
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
    found = git(root, "rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}")
    return found.strip() if found else None


def default_ref(root: Path) -> str:
    candidates = gatelib.default_branches(root)
    return next((ref for ref in candidates if commit(root, ref)), candidates[0])


def read(root: Path, base_ref: str, head_ref: str) -> Change | Reason:
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
    return manifest_at(change.root, change.base)


def manifest_at(root: Path, ref: str) -> Manifest | Reason:
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
    change = read(root, base_ref, head_ref)
    return isinstance(change, Change) and adoption(change) is not None


def folded(manifest: Manifest) -> Manifest:
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


def shape(value: object) -> object:
    if isinstance(value, dict):
        return {key: shape(item) for key, item in value.items()}
    return None if isinstance(value, (bool, int, float)) else value


def more_than_tightened(before: str | None, after: str | None) -> str | None:
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
    before = records.before(lambda args: git(change.root, *args), change.fork, path)
    note = records.note(path, before, change.after(path))
    return None if note.startswith(records.ADDITIONS) else note


def dependency_notes(
    change: Change,
    paths: Sequence[str],
    entry: Callable[[Path, str], AbstractContextManager[dependencies.Runner]] = Entry,
) -> dict[str, str]:
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
    if group.additive:
        return addition(change, path)
    if path == THRESHOLDS:
        return more_than_tightened(change.before(path), change.after(path))
    if path == LEDGER:
        return more_than_shrunk(change)
    return notes.get(path, "")


def instructs(path: str) -> bool:
    *folders, name = path.casefold().split("/")
    return name in INSTRUCTION_FILES or INSTRUCTION_DIRECTORY in folders


def guard_reasons(change: Change, manifest: Manifest) -> list[Reason]:
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
    raw = git(change.root, "diff", "--raw", "--no-renames", "-z", change.fork, change.head) or ""
    fields = raw.split("\0")
    return {
        path: (meta[1:].split()[0], meta.split()[1])
        for meta, path in zip(fields[::2], fields[1::2], strict=False)
    }


def file_reasons(change: Change, manifest: Manifest) -> list[Reason]:
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


def unique(pairs: list[tuple[str, object]]) -> dict[str, object]:
    keys = [key for key, _ in pairs]
    if len(set(keys)) != len(keys):
        message = "a key is given twice"
        raise ValueError(message)
    return dict(pairs)


def holds(new: object, old: object) -> bool:
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
    lower = folded(manifest)
    return [
        (path, door)
        for path in change.paths
        for door in manifest.doors_of(path) or lower.doors_of(path.casefold())
    ]


def door_reasons(change: Change, manifest: Manifest) -> list[Reason]:
    reasons = []
    for path, door in doors(change, manifest):
        why = shut(change, path, door)
        if why is not None:
            reasons.append(Reason("door", f"{path} ({door.name}): {why}"))
    return reasons


def flag_doors(old: list[registry.Flag], new: list[registry.Flag]) -> list[str]:
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
        prior_date, now_date = prior.get("remove-by"), flag.get("remove-by")
        if (
            isinstance(prior_date, dt.date)
            and not isinstance(prior_date, dt.datetime)
            and isinstance(now_date, dt.date)
            and not isinstance(now_date, dt.datetime)
            and now_date > prior_date
        ):
            found.append(f"flag '{key}' moves its removal date later ({prior_date} to {now_date})")
    found += [
        f"flag '{key}' leaves the registry while off by default: its behaviour ships on, or goes"
        for key, flag in sorted(was.items())
        if key not in now and flag.get("default") is not True
    ]
    return found


def registry_state(change: Change) -> list[str] | None:
    before, after = change.before(registry.REGISTRY), change.after(registry.REGISTRY)
    old, unread = registry.parse(before or "")
    new, unreadable = registry.parse(after or "")
    if after is None or unread or unreadable:
        return None
    return flag_doors(old, new)


def registry_reasons(change: Change) -> list[Reason]:
    if registry.REGISTRY not in change.paths or registry_state(change) is not None:
        return []
    text = "the registry is gone or does not parse, so no flag's default can be compared"
    return [Reason("door", f"{registry.REGISTRY}: {text}")]


def flag_reasons(change: Change) -> list[Reason]:
    found = registry_state(change) if registry.REGISTRY in change.paths else None
    return [Reason("door", f"{registry.REGISTRY}: {text}") for text in found or []]


def flag_row_asked(change: Change) -> bool:
    binding = change.tip(BINDING) or ""
    kinds = frozenset(application_types.declared(binding) or ())
    if not (application_types.load().asked_of(FLAGS_QUESTION) & kinds):
        return False
    row = next(
        (r for r in project_bite.gate_rows(binding) if r.question == FLAGS_QUESTION),
        None,
    )
    return row is None or application_types.NONE.match(row.tool) is None


def flags_asked(change: Change) -> bool:
    binding = git(change.root, "show", f"{change.base}:{gatelib.BINDING.as_posix()}")
    kinds = application_types.declared(binding or "")
    catalogue = application_types.load()
    asked_of = catalogue.asked_of(FLAGS_QUESTION)
    return not kinds or any(kind in asked_of or kind not in catalogue.types for kind in kinds)


def grown(change: Change, path: str) -> bool:
    if not change.held(path):
        return True
    before, after = change.before(path) or "", change.after(path) or ""
    if path.endswith(".json"):
        return bool(json.loads(before) != json.loads(after))
    return [line for line in before.splitlines() if line.strip()] != [
        line for line in after.splitlines() if line.strip()
    ]


def new_surface(change: Change, manifest: Manifest) -> list[str]:
    return [
        path
        for path, door in doors(change, manifest)
        if door.on is DoorRule.REMOVAL and shut(change, path, door) is None and grown(change, path)
    ]


def dark_flag(change: Change, surface: list[str], *, keys: frozenset[str] | None = None) -> bool:
    old, _ = registry.parse(change.before(registry.REGISTRY) or "")
    new, _ = registry.parse(change.after(registry.REGISTRY) or "")
    was = {registry.name(flag) for flag in old}
    dark = {registry.name(flag) for flag in new if flag.get("default") is False}
    if keys is not None:
        dark &= keys
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


def unflagged_reasons(change: Change, surface: list[str]) -> list[Reason]:
    if not surface or not flags_asked(change) or dark_flag(change, surface):
        return []
    text = (
        f"{', '.join(surface)}: the change adds surface a consumer can meet, and it neither"
        f" declares a flag that is off in {registry.REGISTRY} nor names one in the code it adds;"
        " ship it dark, or the owner merges it"
    )
    return [Reason("unflagged", text)]


def yes_flag_problem(change: Change, key: str, surface: list[str]) -> str | None:
    after_text = change.after(registry.REGISTRY)
    new, errors = registry.parse(after_text or "")
    if after_text is None or errors:
        return f"requires {registry.REGISTRY} to exist and parse"
    matches_key = [flag for flag in new if registry.name(flag) == key]
    if len(matches_key) != 1:
        return "is not declared" if not matches_key else f"is declared {len(matches_key)} times"
    flag = matches_key[0]
    due = flag.get("remove-by")
    if (
        flag.get("permanent") is True
        or not isinstance(due, dt.date)
        or isinstance(due, dt.datetime)
    ):
        return "needs a `remove-by` date and not `permanent = true`"
    old, old_errors = registry.parse(change.before(registry.REGISTRY) or "")
    was = [entry for entry in old if not old_errors and registry.name(entry) == key]
    already_on = len(was) == 1 and was[0].get("default") is True
    default = flag.get("default")
    if not isinstance(default, bool) or (default and not already_on):
        return "is not off (`default = false`)"
    if not already_on and not dark_flag(change, surface, keys=frozenset({key})):
        return (
            f"requires the change to declare `{key}` off in {registry.REGISTRY} or name"
            f" `{key}` in the code it adds"
        )
    return None


def yes_flag_reasons(change: Change, key: str, surface: list[str]) -> list[Reason]:
    problem = yes_flag_problem(change, key, surface)
    if problem is None:
        return []
    return [Reason("unflagged", f"{registry.REGISTRY}: the ticket's flag `{key}` {problem}")]


def surface_reasons(
    change: Change,
    manifest: Manifest,
    found: FoundTicket | None,
    *,
    asked_flags: bool,
    notes: list[str],
) -> list[Reason]:
    surface = new_surface(change, manifest)
    if found is not None and found.flag.form == "hidden" and found.flag.key is not None:
        return yes_flag_reasons(change, found.flag.key, surface)
    reasons = unflagged_reasons(change, surface)
    if not reasons:
        return []
    can_wait = not asked_flags or (
        found is not None and found.flag.form == "appears" and not open_yes(change)
    )
    if can_wait:
        return waiting(
            change,
            reasons,
            needs=SURFACE_EVENTS,
            notes=notes,
            what="new surface with no flag that is off",
        )
    return reasons


def dispute_reasons(change: Change) -> list[Reason]:
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


def holders(manifest: Manifest, path: str) -> tuple[list[Group], list[Door]]:
    lower = folded(manifest)
    groups = [
        group
        for group, low in zip(manifest.groups, lower.groups, strict=True)
        if Manifest((group,)).group_of(path) or Manifest((low,)).group_of(path.casefold())
    ]
    return groups, manifest.doors_of(path) + lower.doors_of(path.casefold())


LINK_REF = re.compile(r"[^`\s,()\[\]]+\.md")


def spellings(change: Change) -> tuple[str, ...]:
    spelled = label_for_role(change.tip(LABELS_RECORD.as_posix()) or "", READY_ROLE)
    return tuple(sorted({READY_ROLE, spelled or READY_ROLE}))


def carries(text: str, labels: tuple[str, ...]) -> bool:
    return any(label.casefold() in text.casefold() for label in labels)


@functools.cache
def labelled(change: Change) -> frozenset[str]:
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
    listed = git(change.root, "ls-tree", "-r", "--name-only", "-z", ref, "--", TRACKER_DIRECTORY)
    with tempfile.TemporaryDirectory() as scratch:
        for path in filter(None, (listed or "").split("\0")):
            target = Path(scratch, path)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.touch()
        yield Path(scratch)


@functools.cache
def build_inputs(change: Change) -> tuple[dict[str, str], dict[str, str]]:
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
    return text is None or preflight.local_ticket(path, text).is_open


def resolved(change: Change, ticket: str, ref: str) -> str | None:
    if ref.startswith("/"):
        return None
    walked: list[str] = []
    parts = [*PurePosixPath(ticket).parent.parts, *ref.split("/")]
    for index, part in enumerate(parts):
        if part in ("", "."):
            continue
        if part == "..":
            if not walked:
                return None
            walked.pop()
            continue
        walked.append(part)
        found = git(change.root, "ls-tree", "-z", change.head, "--", "/".join(walked)) or ""
        mode, _, name = found.rstrip("\0").partition("\t")
        want = "100644" if index == len(parts) - 1 else "040000"
        if name != "/".join(walked) or mode.split(" ")[0] != want:
            return None
    return "/".join(walked)


def one_file(change: Change, ticket: str, refs: tuple[str, str]) -> bool:
    before, after = refs
    target = resolved(change, ticket, before)
    return before == after or (target is not None and target == resolved(change, ticket, after))


def link_only(change: Change, path: str) -> bool:
    old, new = change.before(path), change.after(path)
    if old is None or new is None:
        return False
    lines = (old.splitlines(), new.splitlines())
    if len(lines[0]) != len(lines[1]):
        return False
    for was, now in zip(*lines, strict=True):
        if was == now:
            continue
        links = all(
            preflight.PARENT_LINE.match(x) or preflight.BLOCKED_LINE.match(x) for x in (was, now)
        )
        found = (LINK_REF.findall(was), LINK_REF.findall(now))
        same = LINK_REF.sub("\0", was) == LINK_REF.sub("\0", now) and len(found[0]) == len(found[1])
        if (
            not links
            or not same
            or not all(one_file(change, path, r) for r in zip(*found, strict=True))
        ):
            return False
    return same_refs(change, path, (old, new))


def same_refs(change: Change, path: str, texts: tuple[str, str]) -> bool:
    folder = PurePosixPath(path).parent
    refs = []
    for text in texts:
        ticket = preflight.local_ticket(path, text)
        blockers = [
            b if not b.endswith(".md") else (folder / b).as_posix() for b in ticket.blockers
        ]
        refs.append([*([ticket.parent] if ticket.parent else []), *blockers])

    def same(before: str, after: str) -> bool:
        found = (resolved(change, "", before), resolved(change, "", after))
        return before == after or (
            before.endswith(".md")
            and after.endswith(".md")
            and None not in found
            and found[0] == found[1]
        )

    return len(refs[0]) == len(refs[1]) and all(same(*pair) for pair in zip(*refs, strict=False))


@dataclass(frozen=True)
class FoundTicket:
    ticket: preflight.Ticket
    flag: preflight.Flag


def ticket_flag(change: Change, ref: str, ticket: preflight.Ticket) -> preflight.Flag:
    with tracker_names(change, ref) as names:
        parent_ref = preflight.parent_of(ticket, names)
    parent = None
    if parent_ref is not None and not parent_ref.startswith(("/", "../")):
        parent_text = git(change.root, "show", f"{ref}:{posixpath.normpath(parent_ref)}")
        if parent_text is not None:
            parent = preflight.local_ticket(parent_ref, parent_text)
    lines = preflight.operability_lines(ticket, parent)
    return preflight.flag_reading(lines.get(preflight.FLAG_LINE, ()))[0]


def labelled_ticket(change: Change, path: str) -> tuple[str, preflight.Ticket]:
    labels = spellings(change)
    before = change.before(path)
    if before is not None and carries(before, labels):
        return change.fork, preflight.local_ticket(path, before)
    return change.base, preflight.local_ticket(path, change.tip(path) or "")


def ticket_of(change: Change, slug: str) -> FoundTicket | None:
    found = [
        (ref, ticket)
        for path in sorted(labelled(change))
        for ref, ticket in (labelled_ticket(change, path),)
        if preflight.slug_of(ticket.title, ticket.number) == slug
    ]
    if len(found) != 1:
        return None
    ref, ticket = found[0]
    return FoundTicket(ticket, ticket_flag(change, ref, ticket))


def open_yes(change: Change) -> bool:
    labels = spellings(change)
    for path in sorted(labelled(change)):
        for ref in dict.fromkeys((change.fork, change.base)):
            text = git(change.root, "show", f"{ref}:{path}")
            if text is None or not carries(text, labels):
                continue
            ticket = preflight.local_ticket(path, text)
            if ticket.is_open and ticket_flag(change, ref, ticket).form == "hidden":
                return True
    return False


def verdict_slug(change: Change) -> str | None:
    found = [path for path in change.paths if RECORD.match(path)]
    if len(found) != 1:
        return None
    matched = RECORD.match(found[0])
    return matched.group(1) if matched else None


def status_resolved(change: Change, path: str) -> bool:
    before, after = change.before(path), change.after(path)
    if before is None or after is None:
        return False
    old, new = before.splitlines(keepends=True), after.splitlines(keepends=True)
    if len(old) != len(new):
        return False
    diffs = [(was, now) for was, now in zip(old, new, strict=True) if was != now]
    if len(diffs) != 1:
        return False
    was_match = preflight.STATUS_LINE.fullmatch(diffs[0][0].rstrip("\r\n"))
    now_match = preflight.STATUS_LINE.fullmatch(diffs[0][1].rstrip("\r\n"))
    if was_match is None or now_match is None:
        return False
    labels = {label.casefold() for label in spellings(change)}
    return (
        was_match.group(1).strip().casefold() in labels
        and now_match.group(1).strip().casefold() == "resolved"
    )


def agreement(change: Change, path: str, *, allowed_close: str | None = None) -> str | None:
    if path == allowed_close and status_resolved(change, path):
        return None
    whole, status = build_inputs(change)
    if path in whole:
        ticket = whole[path]
        if ticket == path:
            return (
                None
                if link_only(change, path)
                else "is a ticket the owner agreed to build, and only they change it"
            )
        return f"is what {ticket} inherits its approach from, a ticket the owner agreed to build"
    if path in status and not change.held(path):
        return (
            f"is a new ticket the `Blocked by` line of {status[path]} can name, which changes"
            " whether that ticket may start, a ticket the owner agreed to build"
        )
    if path in status and open_ticket(path, change.before(path)) != open_ticket(
        path, change.after(path)
    ):
        return f"changes whether {status[path]} may start, a ticket the owner agreed to build"
    return None


def records_only(change: Change, manifest: Manifest) -> bool:
    return bool(change.paths) and all(
        not RECORD.match(path) and record_kind(manifest, path) is not None for path in change.paths
    )


def agreement_reasons(
    change: Change, manifest: Manifest, found: FoundTicket | None
) -> list[Reason]:
    if records_only(change, manifest):
        return []
    allowed = None if found is None else found.ticket.ref
    reasons = []
    for path in change.paths:
        if not change.kept(path) and path in expiring(change):
            continue
        why = agreement(change, path, allowed_close=allowed)
        if why is not None:
            reasons.append(Reason("record", f"{path} {why}"))
    return reasons


def closes(change: Change, path: str) -> str | None:
    if open_ticket(path, change.before(path)) and not open_ticket(path, change.after(path) or ""):
        return "closes a ticket, which only the merge of its build does"
    return None


def part_of_a_split(change: Change, path: str) -> preflight.Ticket | None:
    text = change.after(path) or ""
    ref = splits.split_from(text)
    if ref is None:
        return None
    folder = PurePosixPath(path).parent
    ticket = ref if "/" in ref else (folder / ref).as_posix()
    parent = change.tip(ticket)
    listed = git(change.root, "ls-tree", "--name-only", "-z", change.head, "--", f"{folder}/")
    others = [name for name in (listed or "").split("\0") if name not in ("", path)]
    siblings = [
        found
        for name in others
        if (found := change.after(name)) is not None and splits.split_from(found) == ref
    ]
    part = (
        parent is not None
        and not splits.scope_problems(parent, [text, *siblings])
        and any(
            splits.refusal(text, parent, label, siblings=siblings) is None
            for label in spellings(change)
        )
    )
    return preflight.local_ticket(ticket, parent) if part and parent is not None else None


def inheritance(change: Change, path: str, ticket: preflight.Ticket, *, label: str) -> str | None:
    text = change.after(path) or ""
    read = preflight.local_ticket(ticket.ref, text)
    part = split.Part(read.title, tuple(preflight.criteria(text)), "", ())
    body, why = split.child_body(ticket, part, waits=list(read.blockers), label=label)
    wanted, held = split.inherited(body), split.inherited(ticket.body)
    for piece, value in split.inherited(text).items():
        if value == wanted[piece]:
            continue
        if not value:
            return f"it has no {piece} and {ticket.ref} has one on the default branch"
        if wanted[piece]:
            return f"its {piece} is not the one {ticket.ref} has on the default branch"
        if held[piece] and why:
            return (
                f"it has a {piece} and the split writes none from {ticket.ref} on the default"
                f" branch, because {why}"
            )
        return f"it has a {piece} and {ticket.ref} has none on the default branch"
    filed = (PurePosixPath(ticket.ref).parent / PurePosixPath(path).name).as_posix()
    with tracker_names(change, change.head) as names:
        reads, meant = (
            preflight.parent_of(preflight.local_ticket(where, words), names) or "no spec"
            for where, words in ((path, text), (filed, body))
        )
    if reads == meant:
        return None
    return (
        f"it reads {reads} as its spec and the part the split writes from {ticket.ref} on the"
        f" default branch reads {meant}"
    )


def label_given(change: Change, path: str) -> str | None:
    before, after = (change.before(path) or "").casefold(), (change.after(path) or "").casefold()
    for label in spellings(change):
        if after.count(label.casefold()) > before.count(label.casefold()):
            ticket = part_of_a_split(change, path)
            if ticket is None:
                return f"gives a ticket the agent-ready label `{label}`, which is the owner's act"
            differs = inheritance(change, path, ticket, label=label)
            return differs and (
                f"gives a part the agent-ready label `{label}`, which is the owner's act when"
                f" {differs}"
            )
    return None


def namesake(change: Change, path: str) -> str | None:
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
    return label_given(change, path) or namesake(change, path) or closes(change, path)


def nothing(_change: Change, _path: str) -> None:
    return None


STATUS_LINE = re.compile(r"^Status:.*$", re.MULTILINE)


@functools.cache
def expiring(change: Change) -> frozenset[str]:
    held = gatelib.tracked_paths(change.root, change.fork)
    units = {
        documents.unit_of(path) for path in (*held, *change.paths) if documents.is_steering(path)
    }
    gone = {
        unit
        for unit in units
        if not any(
            change.kept(path)
            for path in (*held, *change.paths)
            if documents.is_steering(path) and documents.unit_of(path) == unit
        )
    }

    def read(path: str) -> str | None:
        text = change.before(path)
        if text is None or not documents.is_ticket(path) or documents.unit_of(path) not in gone:
            return text
        return STATUS_LINE.sub("Status: resolved", text)

    return documents.listed(documents.snapshot(held, read))


def spent(change: Change, path: str) -> bool:
    ticket = preflight.local_ticket(path, change.before(path) or "")
    slug = preflight.slug_of(ticket.title, ticket.number)
    return not ticket.is_open and change.held(f"docs/features/{slug}/verdict.json")


def inheriting(change: Change, spec: str) -> list[str]:
    found = []
    with tracker_names(change, change.fork) as names:
        for path in sorted(labelled(change)):
            ticket = preflight.local_ticket(path, change.before(path) or "")
            parent = preflight.parent_of(ticket, names)
            if parent is not None and posixpath.normpath(parent) == spec:
                found.append(path)
    return found


def agreed(change: Change, path: str) -> list[str]:
    if documents.is_ticket(path):
        return [path] if path in labelled(change) else []
    return inheriting(change, path)


def deletion(change: Change, path: str) -> str | None:
    if path not in expiring(change):
        return "deletes a record"
    if all(spent(change, ticket) for ticket in agreed(change, path)):
        return None
    return (
        "is a ticket the owner agreed to build, and no verdict record of its build is at the merge"
        " base: deleting it is the owner's merge (ADR 0061)"
    )


def only_deleted(_change: Change, _path: str) -> str | None:
    return "is a feature spec: a records change may delete it but not edit it"


def carried_words(change: Change, path: str) -> str | None:
    gone = [other for other in change.paths if not change.kept(other) and other in expiring(change)]
    answers = {
        documents.parts(line)[1]
        for other in gone
        for line in documents.answers_of(change.before(other) or "")
    }
    if documents.carried(change.before(path), change.after(path), answers):
        return None
    return (
        f": a records change may only append, under `{documents.OWNERS_WORDS}`, the dated"
        " `## Answers` lines of the files it deletes; this one changes more"
    )


def not_an_addition(change: Change, path: str) -> str | None:
    note = addition(change, path)
    return None if note is None else f"is {note}"


@dataclass(frozen=True)
class RecordKind:
    shape: re.Pattern[str]
    refuses: Callable[[Change, str], str | None]
    appended: bool = False
    decides: bool = False
    strict: bool = False


RECORDS = (
    RecordKind(re.compile(rf"\.scratch/{SLUG}/issues/\d+-{SLUG}\.md"), ticket_refusal),
    RecordKind(re.compile(rf"\.scratch/{SLUG}/spec\.md"), nothing),
    RecordKind(re.compile(rf"\.scratch/changes/{CHANGE_NAME}\.md"), nothing),
    RecordKind(re.compile(r"GLOSSARY\.md"), not_an_addition, appended=True),
    RecordKind(re.compile(rf"docs/adr/\d{{4}}-{SLUG}\.md"), nothing, appended=True, decides=True),
    RecordKind(re.compile(rf"docs/features/{SLUG}/spec\.md"), only_deleted, strict=True),
    RecordKind(re.compile(re.escape(documents.BRIEF)), carried_words, strict=True),
)


def record_kind(manifest: Manifest, path: str) -> RecordKind | None:
    kind = next((kind for kind in RECORDS if kind.shape.fullmatch(path)), None)
    if kind is None or instructs(path):
        return None
    groups, doors = holders(manifest, path)
    held = bool(groups) and all(group.additive for group in groups) if kind.appended else not groups
    return kind if held and not doors else None


@functools.cache
def spelled(change: Change) -> dict[str, frozenset[str]]:
    found: dict[str, set[str]] = {}
    for ref in (change.head, change.base):
        for path in (git(change.root, "ls-tree", "-r", "--name-only", "-z", ref) or "").split("\0"):
            found.setdefault(path.casefold(), set()).add(path)
    return {key: frozenset(paths) for key, paths in found.items()}


def twin(change: Change, path: str) -> str | None:
    others = sorted(spelled(change).get(path.casefold(), frozenset()) - {path})
    if not others:
        return None
    return (
        f"has a twin in another letter case, {others[0]}, which a file system that ignores case"
        " opens as this file"
    )


def refusal(change: Change, path: str, *, kind: RecordKind, sides: tuple[str, str]) -> str | None:
    odd = [mode for mode in sides if mode not in RECORD_MODES]
    if odd:
        return f"is {IRREGULAR.get(odd[0], f'of mode {odd[0]}')}, where a record is a plain file"
    if change.tip(path) != change.before(path):
        return (
            "changed on the default branch after the change forked, so the merge holds text the"
            " judge did not read; bring the change up to date"
        )
    if not change.kept(path):
        return deletion(change, path)
    return (
        twin(change, path)
        or unreadable(change, path)
        or agreement(change, path)
        or kind.refuses(change, path)
    )


def decision(change: Change, path: str) -> Reason:
    found = TITLE.search(change.after(path) or "")
    named = found.group(1).strip() if found else PurePosixPath(path).stem
    title = "".join(character for character in named if character.isprintable())[:TITLE_WIDTH]
    return Reason("record", f'{path} records a decision: "{title}". Yours to agree.')


def records_checked(change: Change, manifest: Manifest) -> tuple[dict[str, str], list[Reason]]:
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
    return refused, decided


def records_alone(change: Change, manifest: Manifest) -> list[Reason]:
    refused, decided = records_checked(change, manifest)
    text = (
        "no verdict record (docs/features/<slug>/verdict.json) is part of the change, so it is"
        " not the product of the pipeline"
    )
    if not change.paths or NOT_A_RECORD in refused.values():
        return [Reason("record", text)]
    if not refused:
        return decided
    lines = [
        f"{path}{why}" if why.startswith(":") else f"{path} {why}" for path, why in refused.items()
    ]
    rest = f"; and {len(lines) - SHOWN} more" if len(lines) > SHOWN else ""
    return [
        Reason("record", f"{text}; its paths are records, but {'; '.join(lines[:SHOWN])}{rest}"),
        *decided,
    ]


def shaped(change: Change, manifest: Manifest, path: str) -> bool:
    kind = record_kind(manifest, path)
    if kind is None:
        return False
    if not kind.strict:
        return True
    if not change.kept(path):
        return path in expiring(change)
    return kind.refuses(change, path) is None


def not_records(
    root: Path, base_ref: str, paths: Iterable[str], *, change: Change | None = None
) -> list[str]:
    manifest = manifest_at(root, base_ref)
    listed_paths = tuple(paths)
    if isinstance(manifest, Reason):
        return list(listed_paths)
    head = commit(root, "HEAD") or ""
    seen = change or Uncommitted(root, head, head, head, listed_paths)
    return [path for path in listed_paths if not shaped(seen, manifest, path)]


reviewed_tree = gatelib.reviewed_tree


def listed(value: object) -> list[str] | None:
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        return None
    return [str(item) for item in value]


def record_problems(data: dict[str, object], slug: str, tree: str | None) -> list[str]:
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
    except (ValueError, RecursionError):
        data = None
    if not isinstance(data, dict):
        return [Reason("record", f"{path} is gone or is not a JSON object")]
    slug = RECORD.fullmatch(path)
    problems = record_problems(
        data, slug.group(1) if slug else "", reviewed_tree(change.root, change.head, path)
    )
    return [Reason("record", f"{path}: {problem}") for problem in problems]


@dataclass(frozen=True)
class Asked:
    target: str | None = None
    current: bool = False
    tier1: str = "none"
    tier2: str = "none"
    deep: unproven.Deep | None = None


def declared_bound(text: str | None) -> tuple[int | None, str]:
    try:
        data: object = tomllib.loads(text or "")
    except tomllib.TOMLDecodeError:
        return 0, (
            f"; {THRESHOLDS} does not parse at the merge base, so `ceilings.deep.unproven` is"
            " read as 0"
        )
    for key in BOUND_KEY:
        data = data.get(key) if isinstance(data, dict) else None
    if data is None:
        return None, ""
    if isinstance(data, bool) or not isinstance(data, int) or data < 0:
        return 0, (
            "; `ceilings.deep.unproven` at the merge base is not a whole number of 0 or more,"
            " so it is read as 0"
        )
    return data, ""


def deep_reasons(change: Change, deep: unproven.Deep) -> list[Reason]:
    gated = any(
        row.tier == "2" and (row.bound or project_bite.SHIPPED.fullmatch(row.tool))
        for row in project_bite.gate_rows(change.before(BINDING) or "")
    )
    bound, why = declared_bound(change.before(THRESHOLDS))
    line = gatelib.chain(change.root, change.base, [run.commit for run in deep.runs or ()])
    hold = unproven.held(line, deep, gated=gated, bound=bound)
    if hold is None:
        return []
    return [Reason(hold.code, hold.text + (why if hold.code == unproven.UNPROVEN else ""))]


def setting_reasons(change: Change, base_ref: str, asked: Asked) -> list[Reason]:
    reasons = []
    default = (
        base_ref.removeprefix("refs/remotes/").removeprefix("refs/heads/").removeprefix("origin/")
    )
    if asked.target is not None and asked.target != default:
        reasons.append(Reason("target", gatelib.stacked(asked.target, default)))
    if asked.current and change.fork != change.base:
        reasons.append(
            Reason(
                "behind",
                f"the branch does not hold the tip of `{default}` ({change.base[:12]}), so its"
                " gates ran on a merge with an older one; bring it up to date and they rerun",
            )
        )
    for tier, state in (("1", asked.tier1), ("2", asked.tier2)):
        if state == RED:
            reasons.append(
                Reason(
                    f"tier{tier}",
                    f"tier {tier} is red on `{default}`: that is the next piece of work, and the"
                    " owner merges what fixes it",
                )
            )
    if asked.deep is not None:
        reasons.extend(deep_reasons(change, asked.deep))
    return reasons


def waiting(
    change: Change,
    reasons: list[Reason],
    *,
    needs: frozenset[str],
    notes: list[str],
    what: str,
) -> list[Reason]:
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


def expiry_reasons(change: Change, *, extra: Mapping[str, str] | None = None) -> list[Reason]:
    before = documents.snapshot(gatelib.tracked_paths(change.root, change.fork), change.before)
    after = documents.snapshot(gatelib.tracked_paths(change.root, change.head), change.after)
    if extra:
        after.update(extra)
    return [
        Reason(
            "expired",
            f"{unit} has expired and the change leaves it in the tree: run `documents.py expire`"
            " and commit what it deletes",
        )
        for unit in documents.left(before, after)
    ]


def head_kinds(change: Change) -> tuple[documents.Kind, ...]:
    return documents.all_kinds(change.after(BINDING))


def pages(change: Change) -> list[str]:
    return [path for path in change.paths if path.endswith(documents.MARKDOWN)]


def lost_headings(change: Change) -> list[Reason]:
    kinds = head_kinds(change)
    reasons: list[Reason] = []
    for path in pages(change):
        kind = documents.kind_of(path, kinds)
        before = change.before(path)
        if kind is None or before is None:
            continue
        target = path if change.kept(path) else change.moved(path)
        if target is None or documents.kind_of(target, kinds) != kind:
            reasons.extend(deleted_whole(path, kind))
            continue
        after = change.after(target) or ""
        reasons.extend(
            Reason(
                "document",
                f"`{target}` loses `{heading}`, which a {kind.name} page keeps, so the merge is the"
                " owner's",
            )
            for heading in kind.keeps
            if markdown_text.has_section(before, heading)
            and not markdown_text.has_section(after, heading)
        )
    return reasons


def deleted_whole(path: str, kind: documents.Kind) -> list[Reason]:
    if not kind.keeps:
        return []
    return [
        Reason(
            "document",
            f"`{path}` is deleted, and a {kind.name} page keeps `{kind.keeps[0]}`, so the merge"
            " is the owner's",
        )
    ]


def prune_pass(change: Change) -> bool:
    record = f"docs/features/{PRUNE_SLUG}/{documents.VERDICT}"
    subjects = git(change.root, "log", "--format=%s", f"{change.fork}..{change.head}") or ""
    return record in change.paths or any(PRUNE_SUBJECT.match(line) for line in subjects.split("\n"))


def prune_reasons(change: Change) -> list[Reason]:
    if not prune_pass(change):
        return []
    kinds = head_kinds(change)
    return [
        Reason(
            "prune",
            f"`{path}` is a {kind.name} page, which a prune pass does not cut, so the"
            " merge is the owner's",
        )
        for path in pages(change)
        if change.before(path) is not None
        and (kind := documents.kind_of(path, kinds))
        and kind.prune != documents.CUT
    ]


def own_units(change: Change, found: FoundTicket | None) -> frozenset[str]:
    units: set[str] = set()
    slug = verdict_slug(change)
    if slug is not None and change.kept(f"docs/features/{slug}/{documents.VERDICT}"):
        units |= {f"docs/features/{slug}", f".scratch/{slug}"}
    if found is not None:
        units.add(documents.unit_of(found.ticket.ref))
    units.update(
        documents.unit_of(path)
        for path in change.paths
        if documents.is_ticket(path) and status_resolved(change, path)
    )
    return frozenset(units)


def removed_lines(change: Change, path: str) -> bool:
    if not change.kept(path):
        return True
    compared = ("diff", "-U0", "--no-renames", change.fork, change.head)
    diff = git(change.root, "--literal-pathspecs", *compared, "--", path) or ""
    return any(line.startswith("-") and not line.startswith("---") for line in diff.splitlines())


def has_tickets(change: Change, unit: str) -> bool:
    folder = f"{unit}/{documents.TICKETS}/"
    return any(
        git(change.root, "ls-tree", "--name-only", "-z", ref, "--", folder)
        for ref in (change.fork, change.head)
    )


def drafting_spec(change: Change, path: str) -> bool:
    if documents.is_ticket(path) or documents.BRIEF in change.paths:
        return False
    unit = documents.unit_of(path)
    steering_units = {documents.unit_of(p) for p in change.paths if documents.is_steering(p)}
    return steering_units == {unit} and not has_tickets(change, unit)


def steering_cut(
    change: Change,
    path: str,
    *,
    owned: frozenset[str],
    records: bool,
    refused: Mapping[str, str],
) -> bool:
    if not (documents.is_steering(path) and change.held(path)):
        return False
    if documents.unit_of(path) in owned:
        return False
    if not change.kept(path) and path in expiring(change):
        return False
    if records and (
        not change.kept(path)
        or (documents.is_ticket(path) and link_only(change, path))
        or drafting_spec(change, path)
        or path in refused
    ):
        return False
    return removed_lines(change, path)


def steering_reasons(change: Change, manifest: Manifest, found: FoundTicket | None) -> list[Reason]:
    records = records_only(change, manifest)
    owned = frozenset() if records else own_units(change, found)
    refused = records_checked(change, manifest)[0] if records else {}
    cut = [
        path
        for path in change.paths
        if steering_cut(change, path, owned=owned, records=records, refused=refused)
    ]
    if not cut:
        return []
    return [
        Reason(
            "steering-cut",
            f"{', '.join(cut)}: removes lines from another unit's steering file, which is the"
            " owner's merge (ADR 0093)",
        )
    ]


def expect_reasons(change: Change) -> list[Reason]:
    paths = expect_outputs.altered(change.root, change.fork, head=change.head)
    if not paths:
        return []
    return [
        Reason(
            "expect-output",
            f"{', '.join(paths)}: alters a recorded output the default branch holds, and"
            " re-accepting an expected output is the owner's merge (ADR 0092)",
        )
    ]


def judge(
    root: Path,
    base_ref: str,
    head_ref: str,
    *,
    asked: Asked | None = None,
    notes: list[str] | None = None,
) -> list[Reason]:
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
    asked_flags = flag_row_asked(change)
    flags = flag_reasons(change)
    slug = verdict_slug(change)
    found = None if slug is None else ticket_of(change, slug)
    return [
        *setting_reasons(change, base_ref, asked or Asked()),
        *file_reasons(change, manifest),
        *guard_reasons(change, manifest),
        *registry_reasons(change),
        *waiting(
            change,
            [*door_reasons(change, manifest), *([] if asked_flags else flags)],
            needs=DOOR_EVENTS,
            notes=notes,
            what="a one-way door",
        ),
        *(flags if asked_flags else ()),
        *surface_reasons(change, manifest, found, asked_flags=asked_flags, notes=notes),
        *waiting(
            change,
            expect_reasons(change),
            needs=EXPECT_EVENTS,
            notes=notes,
            what="an altered recorded output",
        ),
        *dispute_reasons(change),
        *expiry_reasons(change),
        *lost_headings(change),
        *prune_reasons(change),
        *steering_reasons(change, manifest, found),
        *agreement_reasons(change, manifest, found),
        *record_reasons(change, manifest),
    ]


def verdict(reasons: list[Reason]) -> str:
    codes = {reason.code for reason in reasons}
    if unproven.UNPROVEN in codes and codes <= queue_notes.MECHANICAL:
        return WAIT
    return OWNER if reasons else AGENT


def render(reasons: list[Reason]) -> str:
    return "\n".join([verdict(reasons), *map(str, reasons)]) + "\n"


def report(reasons: list[Reason], head: str) -> str:
    if not reasons:
        return (
            f"{MARKER}\n**{AGENT}** at `{head[:12]}`: the merge judge found nothing to wait for.\n"
        )
    rows = "".join(f"- `{reason.code}`: {reason.text}\n" for reason in reasons)
    said = verdict(reasons)
    lead = WAITS if said == WAIT else LEAVES
    return f"{MARKER}\n**{said}** at `{head[:12]}`. {lead}:\n\n{rows}"


uncommitted = gatelib.uncommitted
dirty = gatelib.dirty
write_record = gatelib.write_record


def record_slug(raw: str) -> str | None:
    try:
        data: object = json.loads(raw)
    except ValueError:
        return None
    slug = data.get("slug") if isinstance(data, dict) else None
    if isinstance(slug, str) and re.fullmatch(SLUG, slug):
        return slug
    return None


def pending_expiry(root: Path, raw: str, *, base_ref: str, head_ref: str) -> list[Reason]:
    slug = record_slug(raw)
    if slug is None:
        return []
    record = f"docs/features/{slug}/{documents.VERDICT}"
    if dirty(root, record):
        return []
    change = read(root, base_ref, head_ref)
    if isinstance(change, Reason):
        return []
    return expiry_reasons(change, extra={record: raw})


RECORD_HELP = (
    "Subcommand:\n"
    "  record [--root ROOT] [--base BASE] [--head HEAD]\n"
    "      Read a verdict JSON object from stdin (requiring slug, verdict, unresolved,\n"
    "      openQuestions), stamp it with HEAD's tree, and write\n"
    "      docs/features/<slug>/verdict.json."
)


def main(argv: list[str]) -> int:
    recording = argv[1:2] == ["record"]
    parser = argparse.ArgumentParser(
        prog="merge_ready.py record" if recording else "merge_ready.py",
        description=(
            "Write docs/features/<slug>/verdict.json from a verdict JSON object on stdin"
            " (fields: slug, verdict, unresolved, openQuestions), stamped with HEAD's tree."
            if recording
            else "Judge whether a change can merge automatically or needs the owner."
        ),
        epilog=None if recording else RECORD_HELP,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--root", default=".")
    parser.add_argument("--base")
    parser.add_argument("--head", default="HEAD")
    parser.add_argument("--target")
    parser.add_argument("--current", action="store_true")
    parser.add_argument("--tier1", choices=TIER_STATES, default="none")
    deep_or_word = parser.add_mutually_exclusive_group()
    deep_or_word.add_argument("--tier2", choices=TIER_STATES)
    deep_or_word.add_argument("--deep", type=Path)
    parser.add_argument("--report", type=Path)
    options = parser.parse_args(argv[2:] if recording else argv[1:])
    root = Path(options.root).resolve()
    base = options.base or default_ref(root)
    if recording:
        raw = sys.stdin.read()
        if reasons := pending_expiry(root, raw, base_ref=base, head_ref=options.head):
            for reason in reasons:
                print(f"record: {reason.text}")
            return 1
        return write_record(root, raw)
    deep = None if options.deep is None else unproven.read(options.deep)
    tier2 = options.tier2 or "none"
    asked = Asked(options.target, options.current, tier1=options.tier1, tier2=tier2, deep=deep)
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
