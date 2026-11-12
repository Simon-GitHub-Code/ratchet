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
* Anything else is red and names both sides. There are two ways out. Run the gates from a
  checkout of the pinned commit, by setting ``RATCHET_PLUGIN`` to it. Or upgrade: change the pin
  and every workflow ``ref`` to the new commit in one change. The binding and the workflows are
  guard paths, so the owner sees an upgrade.

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
from pathlib import Path

import gatelib

PLUGIN = Path(__file__).resolve().parents[1]
MANIFEST = Path(".claude-plugin/plugin.json")
PIN = re.compile(r"^- Ratchet plugin: (\S+) at ([0-9a-f]{40})$", re.MULTILINE)
UNREADABLE = "a commit git cannot read"
WAYS_OUT = (
    "A local tier then runs other gates than CI. Either set RATCHET_PLUGIN to a checkout of the "
    "pinned commit, or upgrade: change the pin and every workflow `ref` to the new commit in "
    "one change, a guard-path edit the owner sees."
)


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


def judged(pin: Pin | None, plugin: Path) -> tuple[str, bool]:
    """The line to print about ``plugin`` against ``pin``, and whether it is a problem."""
    version, commit = version_of(plugin), commit_of(plugin)
    used = (version or "an unknown version") + (f" at {commit}" if commit else "")
    home = gatelib.BINDING
    if pin is None:
        absent = f"{home}: no `- Ratchet plugin: <version> at <commit>` line"
        return (
            f"{absent}, so nothing says which plugin the gates run at; this run uses {used}",
            True,
        )
    if version != pin.version or commit not in (None, pin.commit):
        where = f"{home} pins {pin.version} at {pin.commit}"
        return f"the plugin at {plugin} is {used}; {where}. {WAYS_OUT}", True
    if commit is None:
        compared = "this copy carries no commit, so only the version was compared"
        return f"plugin-pin: {version}, as {home} pins; {compared}", False
    return f"plugin-pin: {version} at {commit[:12]}, as {home} pins", False


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
