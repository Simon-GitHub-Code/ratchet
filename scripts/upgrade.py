#!/usr/bin/env python3
"""Upgrade an adopted repository to the plugin this script runs from, and say what is left.

``upgrade.py --apply`` moves the binding's pin to the plugin in use and then makes every edit the
conformance gate calls ``auto``: the pin line, the workflow refs, the permission rules, the
workflows, files and recipes the template supplies whole, the rows and sections the template
adds. Each edit keeps every line it did not write, and none loosens a gate. What needs judgement
is listed for an agent, and what only the owner can say is a question for the owner.

It writes guard paths: the binding, the workflows, `.claude/settings.json`. The merge judge, not the
guard, is the trust boundary: a change that touches a guard path is `merge: owner`, so the upgrade
reaches the default branch only once the owner merges it (ADR 0029).

Usage: ``upgrade.py [--root DIR] [--apply]``. Without ``--apply`` it writes nothing. Exit status:
0 when nothing is left to write or to do, 1 when edits are unwritten or items remain, 2 when it
refuses (the plugin is older than the pin, its order against the pin cannot be told, it has no
commit to pin, or the root is not an adopted git repository), 3 when the plan does not settle,
which is a bug in the plugin and leaves the repository as it was.
"""

from __future__ import annotations

import argparse
import contextlib
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
# Why: a hook exports these, and `git -C` obeys them over the directory it is given; the scratch
# copy must be a repository of its own, not the one the hook is committing in.
REPOSITORY_VARIABLES = frozenset(
    {
        "GIT_DIR",
        "GIT_WORK_TREE",
        "GIT_INDEX_FILE",
        "GIT_COMMON_DIR",
        "GIT_OBJECT_DIRECTORY",
        "GIT_ALTERNATE_OBJECT_DIRECTORIES",
        "GIT_PREFIX",
    }
)


@dataclass(frozen=True)
class Change:
    """A file the plan writes: its bytes now (``None``: absent) and the bytes it must hold."""

    before: bytes | None
    after: bytes


@dataclass(frozen=True)
class AgentFinding(Finding):
    """A finding left for an agent."""

    fix: Agent


@dataclass(frozen=True)
class OwnerFinding(Finding):
    """A finding left for the owner."""

    fix: Owner


@dataclass(frozen=True)
class Plan:
    """What an upgrade writes and what it leaves, for one repository and one plugin."""

    old: str
    new: str
    changes: dict[str, Change]
    done: dict[str, list[str]]
    agent: list[AgentFinding]
    owner: list[OwnerFinding]

    @property
    def edits(self) -> int:
        """How many edits were made, counting each one that changed a file."""
        return sum(len(did) for did in self.done.values())

    @property
    def left(self) -> int:
        """How many items remain for an agent and for the owner."""
        return len(self.agent) + len(self.owner)


@dataclass(frozen=True)
class Refused:
    """The reason an upgrade must not start: nothing is written."""

    reason: str


@dataclass(frozen=True)
class Stuck:
    """Auto findings that no edit clears: a bug in the plugin, so the repository is untouched."""

    findings: list[Finding]


def git(root: Path, *args: str) -> None:
    """Run git in the scratch copy."""
    environment = {k: v for k, v in os.environ.items() if k not in REPOSITORY_VARIABLES}
    subprocess.run(
        ["git", "-C", str(root), *args], capture_output=True, check=True, env=environment
    )


def edited(want: gatelib.Want, data: bytes | None) -> bytes | None:
    """The bytes ``want`` makes of a file's, or ``None`` when it leaves the file as it is."""
    text = None if data is None else data.decode("utf-8", errors="replace")
    crlf = text is not None and "\r\n" in text and text.count("\n") == text.count("\r\n")
    plain = text.replace("\r\n", "\n") if text is not None and crlf else text
    made = want(plain)
    if plain is not None and made == plain:
        return None
    return (made.replace("\n", "\r\n") if crlf else made).encode("utf-8")


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


def copy_tree(root: Path, sandbox: Path) -> None:
    """What git sees of ``root`` (tracked, not ignored), copied to ``sandbox`` as a repository."""
    listed = gatelib.git(root, "ls-files", "-z", "--cached", "--others", "--exclude-standard")
    for name in sorted({name for name in (listed or "").split("\0") if name}):
        source, target = root / name, sandbox / name
        if source.is_symlink():
            target.parent.mkdir(parents=True, exist_ok=True)
            target.symlink_to(source.readlink())
        elif source.is_file():
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)
    git(sandbox, "init", "-q")
    git(sandbox, "add", "-A")


class EscapeError(Exception):
    """A file the plan must write leads out of the repository's tree."""


class StuckError(Exception):
    """Auto findings that no edit clears."""

    def __init__(self, autos: list[Finding]) -> None:
        """Keep the findings that would not clear."""
        super().__init__("auto findings remain")
        self.autos = autos


def inside(sandbox: Path, name: str) -> str:
    """The path of ``name`` once symlinks are followed, relative to the sandbox."""
    resolved = (sandbox / name).resolve()
    try:
        return str(resolved.relative_to(sandbox.resolve()))
    except ValueError:
        message = f"{name} leads outside the repository, so the upgrade does not write it"
        raise EscapeError(message) from None


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
            made = edited(fix.want, data)
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
        found = conformance.findings(sandbox)
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
        sandbox.mkdir()
        copy_tree(root, sandbox)
        line = plugin_pin.line_for(plugin)
        pin = Finding(
            str(gatelib.BINDING),
            "the pin moves to the plugin in use",
            Auto(f"pinned: {line}", plugin_pin.with_pin(line)),
        )
        try:
            touched = pass_over(sandbox, [pin], done)
            converged = converge(sandbox, done)
            for name in touched | converged.touched:
                before = (root / name).read_bytes() if (root / name).is_file() else None
                changes[name] = Change(before, (sandbox / name).read_bytes())
        except EscapeError as error:
            return Refused(str(error))
        except StuckError as error:
            return Stuck(error.autos)
    return Plan(
        old=standing(root, plugin),
        new=plugin_pin.in_use(plugin),
        changes=changes,
        done={name: did for name, did in done.items() if name in changes},
        agent=[
            AgentFinding(f.path, f.problem, f.fix)
            for f in converged.left
            if isinstance(f.fix, Agent)
        ],
        owner=[
            OwnerFinding(f.path, f.problem, f.fix)
            for f in converged.left
            if isinstance(f.fix, Owner)
        ],
    )


def write_order(changes: dict[str, Change]) -> list[str]:
    """The files in the order they are written: the binding last, so the pin moves after them."""
    binding = str(gatelib.BINDING)
    return sorted(changes, key=lambda name: (name == binding, name))


def write_atomically(path: Path, data: bytes) -> None:
    """Replace ``path`` with ``data`` by renaming a file made beside it, keeping its mode."""
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, temporary = tempfile.mkstemp(dir=path.parent, prefix=".upgrade-")
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
    """Write each changed file, the binding last."""
    for name in write_order(changes):
        write_atomically(root / name, changes[name].after)


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
    """The lines under a finding that say what to start from, or what is asked and seen."""
    if isinstance(fix, Owner):
        asked = [f"{HEAD}? {fix.ask}"]
        asked += [f"{BODY}seen: {line}" for line in fix.evidence]
        return asked + [f"{BODY}then: {line}" for line in fix.then]
    lines = [f"{BODY}{line}" if line else "" for line in fix.start]
    return lines + [f"{BODY}then: {line}" for line in fix.then]


def agent_block(root: Path, found: list[AgentFinding]) -> str:
    """The items an agent answers, by file, each file with who may edit it."""
    lines = ["For an agent:"]
    for path, items in by_file(found).items():
        lines.append(f"  {path}: {editor(root, path)}")
        for item in items:
            lines += [f"    - {item.problem}", *describe(item.fix)]
    return "\n".join(lines)


def owner_block(found: list[OwnerFinding]) -> str:
    """The questions only the owner answers, by file."""
    lines = ["For you:"]
    for path, items in by_file(found).items():
        lines.append(f"  {path}")
        for item in items:
            lines += describe(item.fix)
    return "\n".join(lines)


def render(root: Path, result: Plan, *, applied: bool) -> str:
    """The report: the header, what was written (or would be), then what is left."""
    blocks = [f"upgrade: {root} from {result.old} to {result.new}."]
    if result.changes:
        verb = "Wrote" if applied else "Would write"
        hint = "" if applied else " (run with --apply)"
        lines = [f"{verb} {len(result.changes)} files{hint}:"]
        for name in write_order(result.changes):
            lines.append(f"  {name}")
            lines += [f"    - {did}" for did in result.done.get(name, [])]
        blocks.append("\n".join(lines))
    if result.agent:
        blocks.append(agent_block(root, result.agent))
    if result.owner:
        blocks.append(owner_block(result.owner))
    blocks.append(summary(root, result, applied=applied))
    return "\n\n".join(blocks) + "\n"


def summary(root: Path, result: Plan, *, applied: bool) -> str:
    """The last line: the counts, and what to do about the guard paths it touched."""
    left = f"{len(result.agent)} items are for an agent and {len(result.owner)} questions for you."
    if not result.changes:
        return f"upgrade: nothing to write. {left}"
    verb = "wrote" if applied else "would write"
    text = f"upgrade: {verb} {result.edits} edits in {len(result.changes)} files. {left}"
    if any(editor(root, name) != NOT_GUARDED for name in result.changes):
        text += (
            " The change touches guard paths, so the merge judge leaves it to you: commit it"
            " with the agent's edits as one change, and run `upgrade.py --apply` again after"
            " them; it writes what an answer made decidable."
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
    return 1 if result.left or (result.changes and not options.apply) else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
