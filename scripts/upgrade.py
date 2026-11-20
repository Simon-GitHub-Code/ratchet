#!/usr/bin/env python3
from __future__ import annotations

import argparse
import contextlib
import fcntl
import json
import os
import shutil
import subprocess
import sys
import tempfile
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import TypeVar

import conformance
import gatelib
import owed
import plugin_pin
from conformance import Agent, Auto, Finding, Owner

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "hooks"))
from ratchet_guards.manifest import EditRule, ManifestError, parse

Kept = TypeVar("Kept", bound=Finding)
MANIFEST = Path("guards/manifest.toml")
HEAD, BODY = "    ", "        "
PASSES = 6
INSTRUCTION_FILES = ("AGENTS.md", "CLAUDE.md")
SHORT = 12
EDITORS = {
    EditRule.DENY: "the owner edits it by hand: its guard group `{}` denies agents",
    EditRule.ASK: "a guard path (group `{}`): an agent edits it on a branch, the owner merges",
    EditRule.APPEND: "a record (group `{}`): an addition passes, a change goes to the owner",
}
NOT_GUARDED = "not a guard path: an agent edits it"
REFUSED, STUCK = 2, 3
JUSTFILE = "justfile"
LOCK = ".lock"
TEMPORARY = ".upgrade-"


@dataclass(frozen=True)
class Change:
    before: bytes | None
    after: bytes


@dataclass(frozen=True)
class AgentFinding(Finding):
    fix: Agent
    owed: bool = False


@dataclass(frozen=True)
class OwnerFinding(Finding):
    fix: Owner
    owed: bool = False


@dataclass(frozen=True)
class Plan:
    old: str
    new: str
    changes: dict[str, Change]
    done: dict[str, list[str]]
    agent: list[AgentFinding]
    owner: list[OwnerFinding]
    was: str
    now: str
    waiting: tuple[str, ...] = ()
    approved: int = 0

    @property
    def edits(self) -> int:
        return sum(len(did) for did in self.done.values())

    @property
    def left(self) -> int:
        return len(self.agent) + len(self.owner)

    @property
    def items(self) -> list[AgentFinding | OwnerFinding]:
        return [*self.agent, *self.owner]

    @property
    def red(self) -> list[AgentFinding | OwnerFinding]:
        return [finding for finding in self.items if not finding.owed]

    @property
    def owing(self) -> int:
        return self.left - len(self.red)


@dataclass(frozen=True)
class Refused:
    reason: str


@dataclass(frozen=True)
class Stuck:
    findings: list[Finding]


def standing(root: Path, plugin: Path) -> str:
    pin = plugin_pin.pinned(gatelib.read_text(root / gatelib.BINDING))
    if pin is not None:
        return f"{pin.version} at {pin.commit[:SHORT]}"
    commits = sorted(plugin_pin.ci_commits(root))
    if not commits:
        return "no plugin pinned"
    named = ", ".join(f"{version_at(plugin, commit)}at {commit[:SHORT]}" for commit in commits)
    if len(commits) == 1:
        return f"{named} (CI checks it out; the binding pins nothing)"
    return f"{named} (CI checks them all out; the binding pins nothing)"


def version_at(plugin: Path, commit: str) -> str:
    shown = gatelib.show(plugin, commit, str(plugin_pin.MANIFEST))
    try:
        version = json.loads(shown or "").get("version")
    except (ValueError, AttributeError):
        return ""
    return f"{version} " if isinstance(version, str) else ""


def refusal(root: Path, plugin: Path) -> str | None:
    if gatelib.git(root, "rev-parse", "--is-inside-work-tree") != "true":
        return f"{root} is not a git work tree: the upgrade copies what git sees"
    if not (root / gatelib.BINDING).is_file():
        return f"{root / gatelib.BINDING} is missing: adopt the method first (/ratchet:adopt)"
    commit = plugin_pin.commit_of(plugin)
    if commit in (None, plugin_pin.UNREADABLE):
        return (
            f"the plugin at {plugin} has no commit to pin: it is a copy without history, or git"
            " cannot read it; run the upgrade from a clone of the plugin"
        )
    pin = plugin_pin.pinned(gatelib.read_text(root / gatelib.BINDING))
    standing_now = plugin_pin.relation(pin, plugin)
    if pin is not None and standing_now is plugin_pin.Relation.OLDER:
        return (
            f"the plugin in use is {plugin_pin.in_use(plugin)}, older than the {pin.version} at"
            f" {pin.commit} that the binding pins: update the plugin and run this again"
        )
    if pin is not None and standing_now is plugin_pin.Relation.UNORDERED:
        return (
            f"the plugin in use is {plugin_pin.in_use(plugin)}, and which is newer than the"
            f" {pin.version} at {pin.commit} that the binding pins cannot be told: point the"
            " binding at the plugin you mean, or run this from the newer clone"
        )
    return None


class StuckError(Exception):
    def __init__(self, autos: list[Finding]) -> None:
        super().__init__("auto findings remain")
        self.autos = autos


class NotTextError(Exception):
    def __init__(self, name: str) -> None:
        super().__init__(f"{name} is not UTF-8 text, so the upgrade does not write it")


def inside(sandbox: Path, name: str) -> str:
    return str((sandbox / name).resolve().relative_to(sandbox.resolve()))


def pass_over(sandbox: Path, autos: list[Finding], done: dict[str, list[str]]) -> set[str]:
    fixes: dict[str, list[Auto]] = {}
    for path, fix in [(f.path, f.fix) for f in autos if isinstance(f.fix, Auto)]:
        fixes.setdefault(inside(sandbox, path), []).append(fix)
    changed = set()
    for name, wants in fixes.items():
        target = sandbox / name
        data = target.read_bytes() if target.is_file() else None
        for fix in wants:
            try:
                made = gatelib.edited(fix.want, data)
            except UnicodeDecodeError:
                raise NotTextError(name) from None
            if made is None:
                continue
            data = made
            changed.add(name)
            if fix.does not in done.setdefault(name, []):
                done[name].append(fix.does)
        if name in changed and data is not None:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
    return changed


@dataclass(frozen=True)
class Converged:
    touched: set[str]
    left: list[Finding]


def converge(sandbox: Path, done: dict[str, list[str]]) -> Converged:
    touched: set[str] = set()
    number = 0
    while True:
        number += 1
        found = conformance.findings(sandbox, owned=True)
        autos = [finding for finding in found if isinstance(finding.fix, Auto)]
        if not autos:
            return Converged(touched, found)
        changed = pass_over(sandbox, autos, done) if number < PASSES else set()
        if not changed:
            raise StuckError(autos)
        touched |= changed


def pinned_to(plugin: Path) -> Finding:
    line = plugin_pin.line_for(plugin)
    return Finding(
        str(gatelib.BINDING),
        "the pin moves to the plugin in use",
        Auto(f"pinned: {line}", plugin_pin.with_pin(line)),
    )


def plan(root: Path, plugin: Path) -> Plan | Refused | Stuck:
    reason = refusal(root, plugin)
    if reason is not None:
        return Refused(reason)
    done: dict[str, list[str]] = {}
    changes: dict[str, Change] = {}
    with tempfile.TemporaryDirectory(prefix="ratchet-upgrade-") as scratch:
        sandbox = Path(scratch) / "tree"
        try:
            foreign = conformance.copied(root, sandbox)
            touched = pass_over(sandbox, [pinned_to(plugin)], done)
            first = converge(sandbox, done)
            touched |= first.touched | pass_over(
                sandbox, owing(root, sandbox, first.left, plugin=plugin), done
            )
            converged = converge(sandbox, done)
        except StuckError as error:
            return Stuck(error.autos)
        except NotTextError as error:
            return Refused(str(error))
        except UnicodeDecodeError as error:
            return Refused(
                f"a file the checks read is not UTF-8 text, so nothing is written: {error}"
            )
        for name in sorted(touched | converged.touched):
            before = (root / name).read_bytes() if (root / name).is_file() else None
            changes[name] = Change(before, (sandbox / name).read_bytes())
        reason = (
            (unwritable(root, changes, foreign) or unreadable(root, sandbox)) if changes else None
        )
        if reason is not None:
            return Refused(reason)
        red, _ = conformance.owed_split(sandbox, converged.left)
        waiting = tuple(conformance.not_running(sandbox, converged.left))
        reds = {id(finding) for finding in red}
        approved = sum(
            editor(sandbox, f.path) != NOT_GUARDED for f in converged.left if id(f) not in reds
        )
    return Plan(
        old=standing(root, plugin),
        new=plugin_pin.in_use(plugin),
        changes=changes,
        done={name: did for name, did in done.items() if name in changes},
        agent=[
            AgentFinding(f.path, f.problem, f.fix, f.rule, f.identity, f.stops, id(f) not in reds)
            for f in converged.left
            if isinstance(f.fix, Agent)
        ],
        owner=[
            OwnerFinding(f.path, f.problem, f.fix, f.rule, f.identity, f.stops, id(f) not in reds)
            for f in converged.left
            if isinstance(f.fix, Owner)
        ],
        was=version_now(root, plugin),
        now=plugin_pin.version_of(plugin) or "an unknown version",
        waiting=waiting,
        approved=approved,
    )


def owing(root: Path, sandbox: Path, left: list[Finding], *, plugin: Path) -> list[Finding]:
    base = owed.merged(root)
    before = gatelib.show(root, base, str(gatelib.BINDING)) if base else None
    after = gatelib.read_text(sandbox / gatelib.BINDING)
    pin = owed.moved(before, after, floor=plugin_pin.version_of(plugin))
    if pin is None:
        return []
    held = owed.parse(gatelib.show(root, base, str(owed.LEDGER)) if base else None).entries
    brought = owed.brought(
        held, owed.read(sandbox).entries, gatelib.renamed(root, base) if base else {}
    )
    mine = sorted(entry for entry in brought.elements() if entry.rule not in owed.GATE_RULES)
    red, _ = conformance.owed_split(sandbox, left)
    allowed = red_at_base(root, base, plugin)
    added = []
    for key in [*(finding.key() for finding in red), *(entry.key for entry in mine)]:
        if allowed[key] > 0 and not owed.never(key):
            allowed[key] -= 1
            added.append(owed.Entry(key[1], key[0], key[2], pin.version))
    dropped = sorted((Counter(e.key for e in mine) - Counter(e.key for e in added)).elements())
    does = []
    if added:
        count = len(added)
        things = "thing" if count == 1 else "things"
        does.append(f"write down {count} {things} owed since {pin.version}")
    if dropped:
        count = len(dropped)
        does.append(
            f"drop {count} {'entry' if count == 1 else 'entries'} this change brought, which one"
            f" upgrade from the merge base {str(base)[:12]} would not write: "
            + "; ".join(f"'{problem}' for {path} ({rule})" for rule, path, problem in dropped)
        )
    if not does:
        return []
    return [Finding(str(owed.LEDGER), "", Auto("; ".join(does), owed.recorded(added, mine)))]


def red_at_base(root: Path, base: str | None, plugin: Path) -> Counter[owed.Key]:
    if base is None:
        return Counter()
    with tempfile.TemporaryDirectory(prefix="ratchet-base-") as scratch:
        tree = Path(scratch) / "tree"
        gatelib.export(root, base, tree)
        pass_over(tree, [pinned_to(plugin)], {})
        left = converge(tree, {}).left
        red, _ = conformance.owed_split(tree, left)
    return Counter(key for key in (finding.key() for finding in red) if not owed.never(key))


def version_now(root: Path, plugin: Path) -> str:
    pin = plugin_pin.pinned(gatelib.read_text(root / gatelib.BINDING))
    if pin is not None:
        return pin.version
    versions = sorted(
        {version_at(plugin, commit).strip() for commit in plugin_pin.ci_commits(root)}
    )
    return " and ".join(version for version in versions if version) or "an unpinned version"


def joined(names: list[str]) -> str:
    *head, last = names
    return f"{', '.join(head)} and {last}" if head else last


def unwritable(root: Path, changes: dict[str, Change], foreign: dict[str, str]) -> str | None:
    names = write_order(changes)
    for name in names:
        for prefix, why in foreign.items():
            if name == prefix or name.startswith(f"{prefix}/"):
                return f"{name} {why}, so the upgrade does not write it"
        if conformance.under_guards(name):
            return f"{name} is under guards/, which the upgrade never writes"
    asked = ("-c", "core.quotepath=off", "check-ignore", "--", *names)
    ignored = gatelib.git_checked(root, *asked, ok=(0, 1)).splitlines()
    if ignored:
        return (
            f"git ignores {joined(ignored)}, so the upgrade, which reads only what git sees, would"
            " write there unread and the change would not carry it; track it (`git add -f`) or"
            " stop ignoring it, then run this again"
        )
    return None


def unreadable(root: Path, sandbox: Path) -> str | None:
    just = shutil.which("just")
    if just is None:
        return None

    def error(tree: Path) -> str | None:
        done = subprocess.run(
            [just, "--justfile", str(tree / JUSTFILE), "--list"],
            capture_output=True,
            text=True,
            check=False,
        )
        return done.stderr.strip().splitlines()[0] if done.returncode else None

    planned = error(sandbox)
    if planned is None or error(root) is not None:
        return None
    return f"`just` cannot read the planned justfile, so nothing is written: {planned}"


def write_order(changes: dict[str, Change]) -> list[str]:
    binding = str(gatelib.BINDING)
    return sorted(changes, key=lambda name: (name == binding, name))


def write_atomically(path: Path, data: bytes, holder: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, temporary = tempfile.mkstemp(dir=holder, prefix=TEMPORARY)
    try:
        with os.fdopen(handle, "wb") as out:
            out.write(data)
        if path.is_file():
            shutil.copymode(path, temporary)
        else:
            Path(temporary).chmod(0o644)
        Path(temporary).replace(path)
    finally:
        with contextlib.suppress(FileNotFoundError):
            Path(temporary).unlink()


def apply(root: Path, changes: dict[str, Change]) -> None:
    if not changes:
        return
    holder = gatelib.state_dir(root) / "upgrade"
    holder.mkdir(exist_ok=True)
    with (holder / LOCK).open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        for left in holder.glob(f"{TEMPORARY}*"):
            left.unlink()
        for name in write_order(changes):
            write_atomically(root / name, changes[name].after, holder)


def editor(root: Path, path: str) -> str:
    try:
        manifest = parse(gatelib.read_text(root / MANIFEST) or "")
    except ManifestError:
        manifest = None
    if manifest is None or not manifest.groups:
        return f"{MANIFEST} does not say who edits it"
    group = manifest.group_below(path.split(" ", maxsplit=1)[0])
    if group is None:
        return NOT_GUARDED
    return EDITORS[group.edit].format(group.name)


def by_file(found: list[Kept]) -> dict[str, list[Kept]]:
    files: dict[str, list[Kept]] = {}
    for finding in found:
        files.setdefault(finding.path, []).append(finding)
    return files


def describe(fix: Agent | Owner) -> list[str]:
    if isinstance(fix, Owner):
        lines = [
            f"{BODY}assumed: {fix.assumed}",
            f"{BODY}settled by the owner's answer to: {fix.ask}",
        ]
        lines += [f"{BODY}seen: {line}" for line in fix.evidence]
        return lines + [f"{BODY}then: {line}" for line in fix.then]
    lines = [f"{BODY}{line}" if line else "" for line in fix.start]
    return lines + [f"{BODY}then: {line}" for line in fix.then]


def items_block(root: Path, title: str, found: list[AgentFinding | OwnerFinding]) -> str:
    lines = [title]
    for path, items in by_file(found).items():
        lines.append(f"  {path}: {editor(root, path)}")
        for item in items:
            lines += [f"    - {item.problem}", *describe(item.fix)]
    return "\n".join(lines)


def moved_lines(change: Change) -> list[str]:
    text = change.after.decode("utf-8", errors="replace")
    kept = conformance.moved_items((change.before or b"").decode("utf-8", errors="replace"))
    return [line for line in conformance.moved_items(text) if line not in kept]


def render(root: Path, result: Plan, *, applied: bool) -> str:
    blocks = [f"upgrade: {root} from {result.old} to {result.new}."]
    if result.changes:
        verb = "Wrote" if applied else "Would write"
        hint = "" if applied else " (run with --apply)"
        lines = [f"{verb} {len(result.changes)} files{hint}:"]
        for name in write_order(result.changes):
            lines.append(f"  {name}")
            lines += [f"    - {did}" for did in result.done.get(name, [])]
            moved = moved_lines(result.changes[name]) if name in INSTRUCTION_FILES else []
            if moved:
                lines.append(f"    - moved to '{conformance.MOVED}' for the owner to read:")
                lines += [f"      {line}" for line in moved]
        blocks.append("\n".join(lines))
    if result.red:
        title = (
            "Red until fixed (no owed entry excuses these, and only a change that moves the pin"
            " adds one):"
        )
        blocks.append(items_block(root, title, result.red))
    owing = [finding for finding in result.items if finding.owed]
    if owing:
        title = f"Owed, none of them red, for an agent to work through ({owed.LEDGER}):"
        blocks.append(items_block(root, title, owing))
    blocks += [summary(root, result, applied=applied), plain(result, applied=applied)]
    return "\n\n".join(blocks) + "\n"


def summary(root: Path, result: Plan, *, applied: bool) -> str:
    left = (
        f"{result.owing} items are owed and {len(result.red)} red; {len(result.owner)} of them"
        " hold an assumption until the owner says otherwise."
    )
    if not result.changes:
        return f"upgrade: nothing to write. {left}"
    verb = "wrote" if applied else "would write"
    text = f"upgrade: {verb} {result.edits} edits in {len(result.changes)} files. {left}"
    if any(editor(root, name) != NOT_GUARDED for name in result.changes):
        text += (
            " The change touches guard paths, so the merge judge leaves the merge to the owner:"
            " commit it as one change. An owed item needs no edit before it merges."
        )
    return text


def plain(result: Plan, *, applied: bool) -> str:
    moves = "moves" if applied else "would move"
    made = "made" if applied else "would make"
    if not result.changes:
        text = f"This project is on Ratchet {result.now}, and the upgrade has nothing to change."
    else:
        changes = "change" if result.edits == 1 else "changes"
        files = "file" if len(result.changes) == 1 else "files"
        text = (
            f"This project {moves} from Ratchet {result.was} to {result.now}. The upgrade {made}"
            f" {result.edits} {changes} in {len(result.changes)} {files}."
        )
    if result.owing:
        text += " " + owing_words(result.owing, approved=result.approved)
    if result.red:
        count = len(result.red)
        text += f" {count} {'thing stops' if count == 1 else 'things stop'} a merge until fixed."
    lines = ["In plain words:", text]
    if result.waiting:
        lines.append(f"Checks that do not run until then: {', '.join(result.waiting)}.")
    assumed = list(dict.fromkeys(finding.fix.assumed for finding in result.owner))
    if assumed:
        lines += ["Assumed until you say otherwise:", *(f"- {line}" for line in assumed)]
    if result.changes:
        lines.append("Your one step is the merge.")
    return "\n".join(lines)


def owing_words(count: int, *, approved: int) -> str:
    things = "thing" if count == 1 else "things"
    text = (
        f"The project owes {count} {things} since the upgrade. None of them turns a check red or"
        " stops a merge, and between upgrades the list only gets shorter. Nothing fixes them by"
        " itself: ask an agent to."
    )
    if approved:
        files = "Each is in a guard path" if approved >= count else f"{approved} are in guard paths"
        text += (
            f" {files}: an agent can make all the fixes in one change on a branch, and you decide"
            " at that one merge."
        )
    return text


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="upgrade.py")
    parser.add_argument("--root", type=Path, default=Path())
    parser.add_argument("--apply", action="store_true")
    options = parser.parse_args(argv[1:])
    root = options.root.resolve()
    result = plan(root, plugin_pin.PLUGIN)
    if isinstance(result, Refused):
        print(f"upgrade: refused. {result.reason}")
        return REFUSED
    if isinstance(result, Stuck):
        print(
            "upgrade: the plan does not settle, which is a bug in the plugin; nothing was written."
        )
        for finding in result.findings:
            print(f"  {finding}")
        return STUCK
    if options.apply:
        apply(root, result.changes)
    print(render(root, result, applied=options.apply), end="")
    return 1 if result.red or (result.changes and not options.apply) else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
