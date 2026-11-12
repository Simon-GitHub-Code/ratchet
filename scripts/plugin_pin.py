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
  judges by its gates and CI by the pinned one's, ``scripts/upgrade.py`` names what the newer
  version needs, and the line to change is printed. The newer version's conformance gate, in
  tier 1, is red while the repository lacks something it needs (ADR 0027).
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
    used = (version or "an unknown version") + (f" at {commit}" if commit else "")
    home = gatelib.BINDING
    upgrade = f"python3 {plugin / 'scripts/upgrade.py'}"
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
                f" `{upgrade}`; once it names nothing missing, the upgrade is one line in {home},"
                f" `{line_for(plugin)}`, with every workflow `ref` at that commit"
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
            f" plugin in use is the newer, run `{upgrade}` and change the pin line to"
            f" `{line_for(plugin)}` and every workflow `ref` with it, in one change the owner sees."
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
