#!/usr/bin/env python3
"""The plugin pin: a tier runs from the plugin version the binding names, or it is red.

CI checks the plugin out at one commit. A local run takes it from wherever the harness keeps
it, and that copy moves whenever the harness updates the plugin, so the two could run different
gates with nothing saying so. The version is therefore one fact with one home, a line of
``docs/process/binding.md``::

    - Ratchet plugin: 0.9.0 at 0123456789abcdef0123456789abcdef01234567

The conformance gate holds every workflow's plugin checkout to that commit. This script holds
the plugin a tier runs from to it, before any gate runs.

Usage: ``plugin_pin.py [--root DIR] [--plugin DIR]``. The plugin is the one this script runs
from unless ``--plugin`` names another.

* A git checkout (the Claude Code marketplace clone, the Codex and Grok plugin folders, a clone
  made for pi or OpenCode, CI's own checkout) is green when its HEAD is the pinned commit and
  its ``.claude-plugin/plugin.json`` carries the pinned version.
* A copy with no history, as a harness leaves when it unpacks a plugin without ``.git``, has no
  commit to compare. It is green when its version is the pinned one, and the line it prints
  says only the version was compared.
* A plugin whose version is newer than the pin is green, and says so on every run: this run
  judges by its gates and CI by the pinned one's, and ``python3 <plugin>/scripts/upgrade.py
  --apply`` moves the pin, every workflow ``ref`` and the permission rules to it in one change.
  The newer version's conformance gate, in tier 1, is red while the repository lacks something
  it needs (ADR 0027).
* A plugin older than the pin is red: a local tier would run older gates than CI. So is one
  whose order against the pin cannot be told: the same version at another commit, or a version
  that is not dotted numbers. The red names both ways out: run the gates from a checkout of the
  pinned commit, by setting ``RATCHET_PLUGIN`` to it, or upgrade by changing the pin and every
  workflow ``ref`` in one change. The binding and the workflows are guard paths, so the owner
  sees an upgrade.

Exit status: 0 green, 1 red.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

import gatelib

PLUGIN = Path(__file__).resolve().parents[1]
MANIFEST = Path(".claude-plugin/plugin.json")
PIN = re.compile(r"^- Ratchet plugin: (\S+) at ([0-9a-f]{40})$", re.MULTILINE)
# Why: a line that only starts like the pin, such as a note on where the plugin comes from, is
# the project's own; only a line of the pin's shape, real or placeholder, is the pin.
PIN_LINE = re.compile(r"^- Ratchet plugin: \S+ at \S+[ \t]*(?=\r?$)", re.MULTILINE)
PLUGIN_REF = re.compile(r'repository:\s*\S*ratchet\S*?[,\s]+ref:\s*"?([^",\s}]+)')
COMMIT = re.compile(r"[0-9a-f]{40}")
RUNNER = "## Runner"
WORKFLOWS = Path(".github/workflows")
UNREADABLE = "a commit git cannot read"
DOTTED = re.compile(r"\d+(?:\.\d+)*")


class Relation(Enum):
    """Where the plugin in use stands against the pin."""

    UNPINNED = "unpinned"
    PINNED = "pinned"
    NEWER = "newer"
    OLDER = "older"
    UNORDERED = "unordered"


@dataclass(frozen=True)
class Pin:
    """The plugin version a repository's gates run at."""

    version: str
    commit: str


def pinned(binding: str | None) -> Pin | None:
    """The pin a binding carries, or ``None`` while the line is absent or a placeholder."""
    match = PIN.search(binding or "")
    return Pin(*match.groups()) if match else None


def version_of(plugin: Path) -> str | None:
    """The version in the plugin's manifest, or ``None`` when there is none to read."""
    try:
        data = json.loads(gatelib.read_text(plugin / MANIFEST) or "")
    except ValueError:
        return None
    version = data.get("version") if isinstance(data, dict) else None
    return version if isinstance(version, str) else None


def commit_of(plugin: Path) -> str | None:
    """The commit a plugin checkout is at; ``None`` for a copy that carries no history."""
    if not (plugin / ".git").exists():
        return None
    # Why: git exports GIT_DIR to a hook, and `git -C` obeys it over the directory it is given.
    env = {name: value for name, value in os.environ.items() if not name.startswith("GIT_")}
    result = subprocess.run(
        ["git", "-C", str(plugin), "rev-parse", "HEAD"],
        capture_output=True,
        text=True,
        check=False,
        env=env,
    )
    return result.stdout.strip() if result.returncode == 0 else UNREADABLE


def line_for(plugin: Path) -> str:
    """The binding's pin line naming ``plugin``, with a stand-in for what the copy cannot say."""
    version, commit = version_of(plugin) or "<version>", commit_of(plugin)
    if commit in (None, UNREADABLE):
        commit = f"<the full commit of {version}>"
    return f"- Ratchet plugin: {version} at {commit}"


def refs(text: str) -> set[str]:
    """The values of the plugin-checkout ``ref:`` in a workflow, placeholders included."""
    return set(PLUGIN_REF.findall(text))


def with_refs(commit: str) -> gatelib.Want:
    """The want that sets every plugin-checkout ``ref:`` to ``commit`` and changes nothing else."""

    def set_ref(match: re.Match[str]) -> str:
        whole, start = match.group(0), match.start()
        return whole[: match.start(1) - start] + commit + whole[match.end(1) - start :]

    return lambda text: PLUGIN_REF.sub(set_ref, text or "")


def with_pin(line: str) -> gatelib.Want:
    """The want that makes ``line`` the binding's pin line: replaced where it stands, else added."""

    def pin(text: str | None) -> str:
        text = text or ""
        if PIN_LINE.search(text):
            return PIN_LINE.sub(lambda _: line, text)
        lines = text.split("\n")
        heading = next((i for i, held in enumerate(lines) if held.rstrip() == RUNNER), None)
        if heading is None:
            kept = text if text.endswith("\n") or not text else text + "\n"
            gap = "\n" if kept else ""
            return f"{kept}{gap}{RUNNER}\n\n{line}\n"
        end = heading + 1
        while end < len(lines) and not lines[end].strip():
            end += 1
        while end < len(lines) and lines[end].strip() and not lines[end].startswith("## "):
            end += 1
        return "\n".join([*lines[:end], "", line, *lines[end:]])

    return pin


def ci_commits(root: Path) -> set[str]:
    """The commits CI checks the plugin out at: the pin's and each full ``ref`` of a workflow."""
    commits = {pin.commit} if (pin := pinned(gatelib.read_text(root / gatelib.BINDING))) else set()
    for workflow in sorted((root / WORKFLOWS).glob("*.yml")):
        commits.update(
            ref for ref in refs(gatelib.read_text(workflow) or "") if COMMIT.fullmatch(ref)
        )
    return commits


def write_refusal(root: Path, plugin: Path) -> str | None:
    """Why ``plugin`` must not write a file CI reads, or ``None`` when it may."""
    commits = ci_commits(root)
    if not commits:
        return None
    used = commit_of(plugin)
    pin = pinned(gatelib.read_text(root / gatelib.BINDING))
    if commits == {used} or (
        len(commits) == 1 and used is None and pin and version_of(plugin) == pin.version
    ):
        return None
    if len(commits) > 1:
        named = ", ".join(sorted(commit[:12] for commit in commits))
        return (
            f"the pin and the workflows name {len(commits)} plugin commits ({named}), so an"
            " upgrade is half done and a file written now would suit one of them only. Finish it"
            f" with `{upgrade_command(plugin)}`, or point RATCHET_PLUGIN at a checkout of the"
            " commit CI should run"
        )
    (commit,) = commits
    return (
        f"CI runs the plugin at {commit[:12]} and this one is {in_use(plugin)}, so a file it"
        " writes may be one CI rejects. Run it from a checkout of that commit, by pointing"
        " RATCHET_PLUGIN at it, or move the pin, the workflow refs and this file together with"
        f" `{upgrade_command(plugin)}`"
    )


def upgrade_command(plugin: Path) -> str:
    """The command that moves a repository to ``plugin``: the pin, the workflow refs, the rules."""
    return f"python3 {plugin / 'scripts/upgrade.py'} --apply"


def in_use(plugin: Path) -> str:
    """``plugin`` as a line names it: its version, and its commit when it has one."""
    version, commit = version_of(plugin), commit_of(plugin)
    return (version or "an unknown version") + (f" at {commit}" if commit else "")


def newer(used: str | None, pinned: str) -> bool | None:
    """Whether version ``used`` is newer than ``pinned``; ``None`` when that cannot be told."""
    if used is None or not DOTTED.fullmatch(used) or not DOTTED.fullmatch(pinned):
        return None
    left, right = (tuple(int(part) for part in v.split(".")) for v in (used, pinned))
    return None if left == right else left > right


def relation(pin: Pin | None, plugin: Path) -> Relation:
    """Where ``plugin`` stands against ``pin``."""
    if pin is None:
        return Relation.UNPINNED
    version, commit = version_of(plugin), commit_of(plugin)
    if version == pin.version and commit in (None, pin.commit):
        return Relation.PINNED
    order = None if commit == UNREADABLE else newer(version, pin.version)
    if order is None:
        return Relation.UNORDERED
    return Relation.NEWER if order else Relation.OLDER


def judged(pin: Pin | None, plugin: Path) -> tuple[str, bool]:
    """The line to print about ``plugin`` against ``pin``, and whether it is a problem."""
    version, commit = version_of(plugin), commit_of(plugin)
    used = in_use(plugin)
    home = gatelib.BINDING
    apply = upgrade_command(plugin)
    standing = relation(pin, plugin)
    if pin is None:
        absent = f"{home}: no `- Ratchet plugin: <version> at <commit>` line"
        return (
            f"{absent}, so nothing says which plugin the gates run at; this run uses {used}",
            True,
        )
    if standing is Relation.PINNED:
        if commit is None:
            compared = "this copy carries no commit, so only the version was compared"
            return f"plugin-pin: {version}, as {home} pins; {compared}", False
        return f"plugin-pin: {version} at {commit[:12]}, as {home} pins", False
    if standing is Relation.NEWER:
        return (
            (
                f"plugin-pin: the plugin at {plugin} is {used}, newer than the {pin.version} that"
                f" {home} pins: this run judges by {version}'s gates and CI by {pin.version}'s. Run"
                f" `{apply}`: it moves the pin in {home} to `{line_for(plugin)}`, every workflow"
                " `ref` and the permission rules together, and lists what it leaves to an agent"
                " or to you"
            ),
            False,
        )
    pinned = f"{pin.version} at {pin.commit}"
    if standing is Relation.OLDER:
        return (
            (
                f"the plugin at {plugin} is {used}, older than the {pinned} that {home} pins, so a"
                " local tier would run older gates than CI. Bring the plugin up to date, or point"
                " RATCHET_PLUGIN at a checkout of the pinned commit."
            ),
            True,
        )
    return (
        (
            f"the plugin at {plugin} is {used}; {home} pins {pinned}, and which is newer cannot be"
            " told. Either set RATCHET_PLUGIN to a checkout of the pinned commit, or, where the"
            f" plugin in use is the newer, set the pin line to `{line_for(plugin)}` and run"
            f" `{apply}`, which moves every workflow `ref` and the permission rules to it."
        ),
        True,
    )


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="plugin_pin.py")
    parser.add_argument("--root", type=Path, default=Path())
    parser.add_argument("--plugin", type=Path, default=PLUGIN)
    options = parser.parse_args(argv[1:])
    line, problem = judged(
        pinned(gatelib.read_text(options.root / gatelib.BINDING)), options.plugin
    )
    if not problem:
        print(line)
    return gatelib.report("plugin-pin", [line] if problem else [])


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
