#!/usr/bin/env python3
"""Upgrade an adopted repository to the plugin this script runs from, and say what is left.

``upgrade.py --apply`` moves the binding's pin to the plugin in use and then makes every edit the
conformance gate calls ``auto``: the pin line, the workflow refs, the permission rules, the
workflows, files and recipes the template supplies whole, the rows and sections the template
adds. Each edit keeps every line it did not write, and none loosens a gate. What needs judgement
is listed for an agent. What only the owner can say keeps a stated default that loosens nothing,
so the upgrade asks him nothing. When the change moves the pin, every finding it leaves is
written down in ``quality/owed.toml`` as owed: counted, and not red (ADR 0037).

It writes guard paths: the binding, the workflows, `.claude/settings.json`. The merge judge, not the
guard, is the trust boundary: a change that touches a guard path is `merge: owner`, so the upgrade
reaches the default branch only once the owner merges it (ADR 0029).

Usage: ``upgrade.py [--root DIR] [--apply]``. Without ``--apply`` it writes nothing. Exit status:
0 when nothing is left to write and nothing left is red (owed items may remain), 1 when edits are
unwritten or an item left is red, 2 when it
refuses (the plugin is older than the pin, its order against the pin cannot be told, it has no
commit to pin, the root is not an adopted git repository, a file it would write leads out of
the repository or is one git ignores, or ``just`` cannot read the justfile it would write), 3 when
the plan does not settle, which is a bug in the plugin and leaves the repository as it was.
"""

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
from dataclasses import dataclass
from pathlib import Path
from typing import TypeVar

import conformance
import gatelib
import owed
import plugin_pin
from conformance import Agent, Auto, Finding, Owner

# Why: the guard reads the manifest under hooks/, and this script reads it the same way.
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "hooks"))
from ratchet_guards.manifest import EditRule, ManifestError, parse

Kept = TypeVar("Kept", bound=Finding)
MANIFEST = Path("guards/manifest.toml")
HEAD, BODY = "    ", "        "
PASSES = 6
SHORT = 12
EDITORS = {
    EditRule.DENY: "the owner edits it by hand: its guard group `{}` denies agents",
    EditRule.ASK: "a guard path (group `{}`): an agent's edit asks the owner",
    EditRule.APPEND: "a record (group `{}`): an addition passes, a change asks the owner",
}
NOT_GUARDED = "not a guard path: an agent edits it"
REFUSED, STUCK = 2, 3
JUSTFILE = "justfile"
LOCK = ".lock"
TEMPORARY = ".upgrade-"


@dataclass(frozen=True)
class Change:
    """A file the plan writes: its bytes now (``None``: absent) and the bytes it must hold."""

    before: bytes | None
    after: bytes


@dataclass(frozen=True)
class AgentFinding(Finding):
    """A finding left for an agent; ``owed`` when an entry of the owed ledger excuses it."""

    fix: Agent
    owed: bool = False


@dataclass(frozen=True)
class OwnerFinding(Finding):
    """A finding left for the owner, under its stated default; ``owed`` as for an agent's."""

    fix: Owner
    owed: bool = False


@dataclass(frozen=True)
class Plan:
    """What an upgrade writes and what it leaves, for one repository and one plugin.

    ``was`` and ``now`` are the two versions as the owner reads them.
    """

    old: str
    new: str
    changes: dict[str, Change]
    done: dict[str, list[str]]
    agent: list[AgentFinding]
    owner: list[OwnerFinding]
    was: str
    now: str

    @property
    def edits(self) -> int:
        """How many edits were made, counting each one that changed a file."""
        return sum(len(did) for did in self.done.values())

    @property
    def left(self) -> int:
        """How many items remain for an agent and for the owner."""
        return len(self.agent) + len(self.owner)

    @property
    def items(self) -> list[AgentFinding | OwnerFinding]:
        """Every item left, the agent's first."""
        return [*self.agent, *self.owner]

    @property
    def red(self) -> list[AgentFinding | OwnerFinding]:
        """The items no owed entry excuses: each stops a merge."""
        return [finding for finding in self.items if not finding.owed]

    @property
    def owing(self) -> int:
        """How many items the project owes: counted, and none of them red."""
        return self.left - len(self.red)


@dataclass(frozen=True)
class Refused:
    """The reason an upgrade must not start: nothing is written."""

    reason: str


@dataclass(frozen=True)
class Stuck:
    """Auto findings that no edit clears: a bug in the plugin, so the repository is untouched."""

    findings: list[Finding]


def standing(root: Path, plugin: Path) -> str:
    """The plugin version and commit the repository runs now, as the report's first line says."""
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
    """``X.Y.Z `` for the version the plugin's history gives ``commit``, or nothing."""
    shown = gatelib.show(plugin, commit, str(plugin_pin.MANIFEST))
    try:
        version = json.loads(shown or "").get("version")
    except (ValueError, AttributeError):
        return ""
    return f"{version} " if isinstance(version, str) else ""


def refusal(root: Path, plugin: Path) -> str | None:
    """Why no upgrade may start from ``root`` with ``plugin``, or ``None``."""
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
    """Auto findings that no edit clears."""

    def __init__(self, autos: list[Finding]) -> None:
        """Keep the findings that would not clear."""
        super().__init__("auto findings remain")
        self.autos = autos


class NotTextError(Exception):
    """A file the plan must edit is not UTF-8 text, so an edit would lose its other bytes."""

    def __init__(self, name: str) -> None:
        """Keep the file's name for the refusal."""
        super().__init__(f"{name} is not UTF-8 text, so the upgrade does not write it")


def inside(sandbox: Path, name: str) -> str:
    """The path of ``name`` once symlinks are followed, relative to the sandbox.

    An auto edit always lands inside: its trial never writes outside the tree it is made in.
    """
    return str((sandbox / name).resolve().relative_to(sandbox.resolve()))


def pass_over(sandbox: Path, autos: list[Finding], done: dict[str, list[str]]) -> set[str]:
    """Apply each file's auto wants in finding order to the sandbox; the files that changed."""
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
    """Where the auto edits ended: the files they touched, and the findings left."""

    touched: set[str]
    left: list[Finding]


def converge(sandbox: Path, done: dict[str, list[str]]) -> Converged:
    """Run the checks and apply the auto edits until none is left."""
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


def plan(root: Path, plugin: Path) -> Plan | Refused | Stuck:
    """What upgrading ``root`` to ``plugin`` writes, and what it leaves for others."""
    reason = refusal(root, plugin)
    if reason is not None:
        return Refused(reason)
    done: dict[str, list[str]] = {}
    changes: dict[str, Change] = {}
    with tempfile.TemporaryDirectory(prefix="ratchet-upgrade-") as scratch:
        sandbox = Path(scratch) / "tree"
        line = plugin_pin.line_for(plugin)
        pin = Finding(
            str(gatelib.BINDING),
            "the pin moves to the plugin in use",
            Auto(f"pinned: {line}", plugin_pin.with_pin(line)),
        )
        try:
            foreign = conformance.copied(root, sandbox)
            touched = pass_over(sandbox, [pin], done)
            first = converge(sandbox, done)
            touched |= first.touched | pass_over(sandbox, owing(root, sandbox, first.left), done)
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
    reds = {id(finding) for finding in red}
    return Plan(
        old=standing(root, plugin),
        new=plugin_pin.in_use(plugin),
        changes=changes,
        done={name: did for name, did in done.items() if name in changes},
        agent=[
            AgentFinding(f.path, f.problem, f.fix, f.rule, f.identity, id(f) not in reds)
            for f in converged.left
            if isinstance(f.fix, Agent)
        ],
        owner=[
            OwnerFinding(f.path, f.problem, f.fix, f.rule, f.identity, id(f) not in reds)
            for f in converged.left
            if isinstance(f.fix, Owner)
        ],
        was=version_now(root, plugin),
        now=plugin_pin.version_of(plugin) or "an unknown version",
    )


def owing(root: Path, sandbox: Path, left: list[Finding]) -> list[Finding]:
    """The edit that writes down what the upgrade leaves, when the change moves the pin.

    Why: ADR 0037. A release never turns an adopted repository red: what it newly asks is owed,
    one entry per finding no entry excuses yet. The ledger grows only in the change that moves the
    pin to a newer version, against the base the ratchet-lowered gate compares with, so the gate
    and this agree on every run.
    """
    base = gatelib.base_commit(root)
    before = gatelib.show(root, base, str(gatelib.BINDING)) if base else None
    after = gatelib.read_text(sandbox / gatelib.BINDING)
    pin = owed.moved(before, after, floor=plugin_pin.version_of(plugin_pin.PLUGIN))
    if pin is None:
        return []
    red, _ = conformance.owed_split(sandbox, left)
    added = [
        owed.Entry(path, rule, problem, pin.version)
        for rule, path, problem in (finding.key() for finding in red)
        if rule != owed.OWN
    ]
    if not added:
        return []
    count = len(added)
    does = f"write down {count} {'thing' if count == 1 else 'things'} owed since {pin.version}"
    return [Finding(str(owed.LEDGER), "", Auto(does, owed.recorded(added)))]


def version_now(root: Path, plugin: Path) -> str:
    """The plugin version the repository runs now, as the owner's summary names it."""
    pin = plugin_pin.pinned(gatelib.read_text(root / gatelib.BINDING))
    if pin is not None:
        return pin.version
    versions = sorted(
        {version_at(plugin, commit).strip() for commit in plugin_pin.ci_commits(root)}
    )
    return " and ".join(version for version in versions if version) or "an unpinned version"


def joined(names: list[str]) -> str:
    """``a``, ``a and b``, or ``a, b and c``."""
    *head, last = names
    return f"{', '.join(head)} and {last}" if head else last


def unwritable(root: Path, changes: dict[str, Change], foreign: dict[str, str]) -> str | None:
    """Why the plan must not write what it changed, or ``None``.

    A file that leads out of the repository through a link, or lies in a submodule, is not this
    repository's to write; nor is one under ``guards/``, which the manifest gives to the owner. A
    file git ignores is one the plan, which reads only what git sees, would write over unread,
    and one the change would never carry. Git is asked in the repository itself, so every rule it
    reads counts: ignore files tracked or not, the repository's config, the user's.
    """
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
    """What ``just`` says of the planned justfile when it reads the repository's and not that one.

    Why: a justfile ``just`` cannot read turns every gate red. Without ``just`` on the path there
    is nothing to ask, and the edits stand on the checks alone.
    """
    just = shutil.which("just")
    if just is None:
        return None

    def error(tree: Path) -> str | None:
        done = subprocess.run(  # -- a fixed argv, `just` from PATH
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
    """The files in the order they are written: the binding last, so the pin moves after them."""
    binding = str(gatelib.BINDING)
    return sorted(changes, key=lambda name: (name == binding, name))


def write_atomically(path: Path, data: bytes, holder: Path) -> None:
    """Replace ``path`` with ``data`` by renaming a file made in ``holder``, keeping its mode."""
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
    """Write each changed file, the binding last, one apply at a time.

    Why: each file is made whole in the repository's uncommitted state, which git ignores, and
    renamed into place. A run stopped between the two leaves nothing git sees or the plan reads,
    and the next one clears it; a lock keeps that clearing from taking a running apply's file.
    """
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
    """Who may edit ``path``, as the repository's guard manifest says."""
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
    """The findings grouped by file, each file where its first finding stood."""
    files: dict[str, list[Kept]] = {}
    for finding in found:
        files.setdefault(finding.path, []).append(finding)
    return files


def describe(fix: Agent | Owner) -> list[str]:
    """The lines under a finding: what to start from, or what holds and what would settle it."""
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
    """Items left, by file, each file with who may edit it."""
    lines = [title]
    for path, items in by_file(found).items():
        lines.append(f"  {path}: {editor(root, path)}")
        for item in items:
            lines += [f"    - {item.problem}", *describe(item.fix)]
    return "\n".join(lines)


def render(root: Path, result: Plan, *, applied: bool) -> str:
    """The report: the header, what was written (or would be), what is left, the owner's words."""
    blocks = [f"upgrade: {root} from {result.old} to {result.new}."]
    if result.changes:
        verb = "Wrote" if applied else "Would write"
        hint = "" if applied else " (run with --apply)"
        lines = [f"{verb} {len(result.changes)} files{hint}:"]
        for name in write_order(result.changes):
            lines.append(f"  {name}")
            lines += [f"    - {did}" for did in result.done.get(name, [])]
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
    """The agent's line: the counts, and what to do about the guard paths it touched."""
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
    """What the owner reads: the versions, the counts, the assumptions, and his one step.

    Why: ADR 0037. The owner is asked one thing, the merge, so every call the upgrade left open
    is stated with the default that holds, in words without the method's vocabulary.
    """
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
        things = "thing" if result.owing == 1 else "things"
        text += (
            f" The project owes {result.owing} {things} since the upgrade, which an agent works"
            " through one at a time; none of them stops a merge."
        )
    if result.red:
        count = len(result.red)
        text += f" {count} {'thing stops' if count == 1 else 'things stop'} a merge until fixed."
    lines = ["In plain words:", text]
    assumed = list(dict.fromkeys(finding.fix.assumed for finding in result.owner))
    if assumed:
        lines += ["Assumed until you say otherwise:", *(f"- {line}" for line in assumed)]
    if result.changes:
        lines.append("Your one step is the merge.")
    return "\n".join(lines)


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
