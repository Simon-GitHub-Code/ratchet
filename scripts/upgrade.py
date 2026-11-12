#!/usr/bin/env python3
"""The upgrade report: what this plugin version needs that an adopted repository lacks.

An upgrade touches files that are nearly all guard paths, so each edit asks the owner, and the
conformance gate's reds come one per line with nothing to tie them together. This script reads
the same rules as the conformance gate (``conformance.findings``) and the pin check
(``plugin_pin``), and prints them for the person doing the upgrade: grouped by file, the pin
first, each with the text or template lines to add, and for each file who may edit it, as the
repository's ``guards/manifest.toml`` says. It is the first thing to run after the plugin
updates.

It changes nothing. An agent makes the edits it lists, and the guard asks the owner for each
edit of a guard path; a script that wrote them would be a way round the guard.

Usage: ``upgrade.py [--root DIR] [--plugin DIR]``. The plugin is the one this script runs from
unless ``--plugin`` names another. Exit status: 0 when nothing is missing, 1 otherwise.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import conformance
import gatelib
import plugin_pin
from conformance import Finding

# Why: the guard reads the manifest under hooks/, and this script reads it the same way.
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "hooks"))
from ratchet_guards.manifest import EditRule, ManifestError, parse

MANIFEST = Path("guards/manifest.toml")
INDENT = "      "
EDITORS = {
    EditRule.DENY: "the owner edits it by hand: its guard group `{}` denies agents",
    EditRule.ASK: "a guard path (group `{}`): an agent's edit asks the owner",
    EditRule.APPEND: "a record (group `{}`): an addition passes, a change asks the owner",
}


def pin_finding(root: Path, plugin: Path) -> Finding | None:
    """What to do about the pin when the binding pins a plugin other than the one in use."""
    pin = plugin_pin.pinned(gatelib.read_text(root / gatelib.BINDING))
    standing = plugin_pin.relation(pin, plugin)
    if pin is None or standing is plugin_pin.Relation.PINNED:
        return None
    used = plugin_pin.version_of(plugin) or "an unknown version"
    pinned = f"{pin.version} at {pin.commit}"
    if standing is plugin_pin.Relation.OLDER:
        return Finding(
            str(gatelib.BINDING),
            f"pins {pinned}, newer than the plugin in use, {used}; update the plugin and run this"
            " report again, since it reads the rules of the plugin it runs from",
        )
    line = plugin_pin.line_for(plugin)
    return Finding(
        str(gatelib.BINDING),
        f"pins {pinned}, and the plugin in use is {used}; replace the pin line with the one"
        " below, and every workflow `ref` that checks the plugin out with its commit",
        (line,),
    )


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
        return "not a guard path: an agent edits it"
    return EDITORS[group.edit].format(group.name)


def by_file(found: list[Finding]) -> dict[str, list[Finding]]:
    """The findings grouped by file, each file where its first finding stood."""
    files: dict[str, list[Finding]] = {}
    for finding in found:
        files.setdefault(finding.path, []).append(finding)
    return files


def render(root: Path, plugin: Path, found: list[Finding]) -> str:
    version = plugin_pin.version_of(plugin) or "of an unknown version"
    if not found:
        return f"upgrade: nothing is missing for the Ratchet plugin {version}\n"
    blocks = [f"upgrade: {root} lacks what the Ratchet plugin {version} needs"]
    guarded = 0
    for path, items in by_file(found).items():
        who = editor(root, path)
        guarded += 0 if who.startswith("not a guard path") else 1
        lines = [f"{path}: {who}"]
        for item in items:
            lines.append(f"  - {item.problem}")
            lines += [f"{INDENT}{line}" if line else "" for line in item.add]
        blocks.append("\n".join(lines))
    files = len(by_file(found))
    blocks.append(
        f"upgrade: {len(found)} items in {files} files, {guarded} of them guard paths. This script"
        " changed nothing: make the edits above, one change for the whole upgrade, and the owner"
        " approves each guard-path edit or makes it by hand. Then run the binding's `conformance`"
        ' entry point. CHANGELOG.md lists what no gate checks under "Worth doing, not checked".'
    )
    return "\n\n".join(blocks) + "\n"


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="upgrade.py")
    parser.add_argument("--root", type=Path, default=Path())
    parser.add_argument("--plugin", type=Path, default=plugin_pin.PLUGIN)
    options = parser.parse_args(argv[1:])
    root = options.root.resolve()
    pin = pin_finding(root, options.plugin)
    found = ([pin] if pin else []) + conformance.findings(root)
    found.sort(key=lambda finding: finding.path != str(gatelib.BINDING))
    print(render(root, options.plugin, found), end="")
    return 1 if found else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
