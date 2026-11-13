#!/usr/bin/env python3
"""Permission rules from the guard manifest: ``.claude/settings.json`` mirrors the manifest.

Every pattern in ``guards/manifest.toml`` becomes one ``Edit`` rule: ``deny`` in a group with
``edit = "deny"``, ``ask`` in the rest. A group with ``edit = "append"`` gets none: a permission
rule matches a path and cannot read the edit, so an ``ask`` there would prompt for the additions
the hook lets through, and the hook is the one layer on those paths (ADR 0014). A
repository-relative pattern becomes ``Edit(/pattern)``
(Claude Code reads a leading ``/`` as the project root) and a ``~/`` pattern stays as it is. The
command rules are fixed here: the gate bypasses, history rewrites and forge changes the hook
denies, the commands that write the default branch's ref in their plain spellings (ADR 0016),
and ``gh pr merge`` as an ask. ``gh label create`` is not denied, because the hook lets a
recorded label through and a settings deny would refuse that one too (ADR 0006).

A group's ``exclude`` has no permission-rule equivalent, so a manifest that uses it is reported:
list the paths the group should cover instead. The rules the plugin generates are the whole of
what it writes: a rule no version of it generates is the project's own, and ``regenerated`` keeps
it where it stood, so ``--check`` still reports it and no write removes it. A rule the plugin
generated under an earlier mapping, such as the ``ask`` an append group once had, is regenerated.

Usage: ``settings_rules.py [--root DIR] [--check | --write]``. ``--check`` (the default) prints
one line per rule missing from or foreign to the lists and exits 1 on any. ``--write`` regenerates
the two lists and keeps every other key and every rule of the project's own. It refuses while the
manifest cannot be mirrored, while ``settings.json`` is not a JSON object, and while the plugin in
use is not the one CI runs (``plugin_pin.write_refusal``).
"""

from __future__ import annotations

import argparse
import json
import sys
import tomllib
from enum import Enum
from pathlib import Path
from typing import TYPE_CHECKING, NamedTuple

import plugin_pin

if TYPE_CHECKING:
    import gatelib

MANIFEST = Path("guards/manifest.toml")
SETTINGS = Path(".claude/settings.json")
SCHEMA = "https://json.schemastore.org/claude-code-settings.json"
# Why: a permission rule matches a command's words, so it can name only the plain spelling of a
# command that writes a default branch, and only a command the hook refuses whole: `git branch`,
# `update-ref`, `checkout` and `switch` are denied here only for a default branch's name. The hook
# reads every spelling (ADR 0016); these rules are the second layer for the common ones.
DEFAULT_BRANCHES = ("main", "master")
DEFAULT_BRANCH_DENIES = (
    tuple(
        f"Bash(git {command} {name}:*)"
        for name in DEFAULT_BRANCHES
        for command in ("branch -f", "branch -d", "branch -D", "branch -m", "branch -M")
    )
    + tuple(
        f"Bash(git {command}{name}:*)"
        for name in DEFAULT_BRANCHES
        for command in ("update-ref refs/heads/", "update-ref -d refs/heads/")
    )
    + tuple(
        f"Bash(git {command} {name}:*)"
        for name in DEFAULT_BRANCHES
        for command in ("checkout -B", "switch -C")
    )
)
OTHER_REF_DENIES = (
    "Bash(git update-ref refs/remotes/*)",
    "Bash(git replace:*)",
    "Bash(git fast-import:*)",
    "Bash(git send-pack:*)",
    "Bash(git receive-pack:*)",
    "Bash(git remote add origin:*)",
    "Bash(git remote set-url:*)",
    "Bash(git remote set-head:*)",
    "Bash(git remote set-branches:*)",
    "Bash(git remote rename:*)",
    "Bash(git remote remove:*)",
    "Bash(git remote rm:*)",
)
COMMAND_DENIES = (
    "Bash(git push --force:*)",
    "Bash(git push * --force*)",
    "Bash(git push -f:*)",
    "Bash(git push * -f)",
    "Bash(git push * -f *)",
    "Bash(git push --no-verify:*)",
    "Bash(git push * --no-verify*)",
    "Bash(git commit --no-verify:*)",
    "Bash(git commit * --no-verify*)",
    "Bash(pre-commit uninstall:*)",
    "Bash(gh api *branches/*/protection*)",
    "Bash(gh api *rulesets*)",
    "Bash(gh repo edit:*)",
    "Bash(gh label edit:*)",
    "Bash(gh label delete:*)",
    "Bash(gh label clone:*)",
    "Bash(gh run delete:*)",
    *DEFAULT_BRANCH_DENIES,
    *OTHER_REF_DENIES,
)
COMMAND_ASKS = ("Bash(gh pr merge:*)",)
APPEND = "append"
Rules = tuple[list[str], list[str]]


def edit_rule(pattern: str) -> str:
    """The ``Edit`` permission rule that covers one manifest pattern."""
    return f"Edit({pattern})" if pattern.startswith("~/") else f"Edit(/{pattern})"


def rules(manifest: dict[str, object]) -> tuple[Rules, list[str]]:
    """The (deny, ask) lists the manifest asks for, and what in it cannot be mirrored."""
    deny: list[str] = []
    ask: list[str] = []
    problems: list[str] = []
    groups = manifest.get("group")
    for group in groups if isinstance(groups, list) else []:
        if not isinstance(group, dict):
            continue
        name = str(group.get("name", "<unnamed>"))
        if group.get("exclude"):
            problems.append(
                f"guards/manifest.toml: group '{name}' uses exclude, which no permission rule "
                f"can express; list the paths it should cover instead"
            )
        paths = group.get("paths")
        patterns = [p for p in paths if isinstance(p, str)] if isinstance(paths, list) else []
        if group.get("edit") == APPEND:
            continue
        target = deny if group.get("edit") == "deny" else ask
        target.extend(map(edit_rule, patterns))
    return (deny + list(COMMAND_DENIES), list(COMMAND_ASKS) + ask), problems


def read_manifest(root: Path) -> dict[str, object] | None:
    path = root / MANIFEST
    if not path.is_file():
        return None
    try:
        return tomllib.loads(path.read_text(encoding="utf-8"))
    except tomllib.TOMLDecodeError:
        return None


def parse_settings(text: str | None) -> dict[str, object] | None:
    """The settings file's JSON object; a new one for no file, ``None`` for text that is not one."""
    if text is None:
        return {"$schema": SCHEMA}
    try:
        loaded = json.loads(text)
    except json.JSONDecodeError:
        return None
    return loaded if isinstance(loaded, dict) else None


def settings_text(root: Path) -> str | None:
    path = root / SETTINGS
    return path.read_text(encoding="utf-8") if path.is_file() else None


def read_settings(root: Path) -> dict[str, object]:
    return parse_settings(settings_text(root)) or {}


def listed(settings: dict[str, object], key: str) -> list[str]:
    permissions = settings.get("permissions")
    if not isinstance(permissions, dict):
        return []
    values = permissions.get(key)
    return [v for v in values if isinstance(v, str)] if isinstance(values, list) else []


class Fault(Enum):
    """What kind of drift a line reports, which says who may settle it."""

    MANIFEST = "manifest"
    MISSING = "missing"
    MISPLACED = "misplaced"
    OWN = "own"


class Drift(NamedTuple):
    """One line ``--check`` prints, and the kind of fault it is."""

    line: str
    fault: Fault


def drifts(root: Path) -> list[Drift]:
    """Each manifest fault, missing rule and foreign rule, with its kind; empty when they mirror."""
    manifest = read_manifest(root)
    if manifest is None:
        line = f"{MANIFEST}: missing or not TOML, so no permission rule can be derived"
        return [Drift(line, Fault.MANIFEST)]
    return drifts_of(manifest, read_settings(root))


def drifts_of(manifest: dict[str, object], settings: dict[str, object]) -> list[Drift]:
    """The drift of ``settings`` from what ``manifest`` asks for."""
    (deny, ask), problems = rules(manifest)
    found = [Drift(problem, Fault.MANIFEST) for problem in problems]
    for key, expected in (("deny", deny), ("ask", ask)):
        actual = listed(settings, key)
        found.extend(
            Drift(f"{SETTINGS}: '{r}' is missing from {key}", Fault.MISSING)
            for r in expected
            if r not in actual
        )
        found.extend(
            Drift(
                f"{SETTINGS}: '{r}' in {key} mirrors no manifest pattern",
                Fault.MISPLACED if plugin_writes(r, manifest) else Fault.OWN,
            )
            for r in actual
            if r not in expected
        )
    return found


def drift(root: Path) -> list[str]:
    """One line per manifest fault, missing rule or foreign rule; empty when settings mirror."""
    return [found.line for found in drifts(root)]


def plugin_writes(rule: str, manifest: dict[str, object]) -> bool:
    """Whether some version of the plugin writes ``rule`` for this manifest, in either list."""
    groups = manifest.get("group")
    patterns = {
        edit_rule(pattern)
        for group in (groups if isinstance(groups, list) else [])
        if isinstance(group, dict) and isinstance(group.get("paths"), list)
        for pattern in group["paths"]
        if isinstance(pattern, str)
    }
    return rule in patterns or rule in COMMAND_DENIES or rule in COMMAND_ASKS


def own_rules(settings: dict[str, object], manifest: dict[str, object]) -> list[tuple[str, str]]:
    """The project's own rules, as (list, rule) in order: those no version of the plugin writes."""
    return [
        (key, rule)
        for key in ("deny", "ask")
        for rule in listed(settings, key)
        if not plugin_writes(rule, manifest)
    ]


def regenerated(manifest: dict[str, object]) -> gatelib.Want:
    """The want over ``settings.json`` text, which leaves text that is not a JSON object alone."""
    (deny, ask), _ = rules(manifest)
    ours = {"deny": deny, "ask": ask}

    def want(text: str | None) -> str:
        settings = parse_settings(text)
        if settings is None:
            return text or ""
        permissions = settings.get("permissions")
        held = permissions if isinstance(permissions, dict) else {}
        own = own_rules(settings, manifest)
        lists = {
            key: [*rules_, *(rule for k, rule in own if k == key)] for key, rules_ in ours.items()
        }
        settings["permissions"] = {**held, **lists}
        return json.dumps(settings, indent=2) + "\n"

    return want


def regenerate(root: Path) -> None:
    """Write ``.claude/settings.json`` as ``regenerated`` makes it from the root's manifest."""
    path = root / SETTINGS
    path.parent.mkdir(parents=True, exist_ok=True)
    want = regenerated(read_manifest(root) or {})
    path.write_text(want(settings_text(root)), encoding="utf-8")


def write_problems(root: Path, plugin: Path) -> list[str]:
    """What stops ``--write`` from regenerating the file under ``plugin``; empty when nothing."""
    manifest = read_manifest(root)
    if manifest is None:
        return [f"{MANIFEST}: missing or not TOML, so no permission rule can be derived"]
    _, problems = rules(manifest)
    refusal = plugin_pin.write_refusal(root, plugin)
    if refusal:
        problems.append(f"{SETTINGS}: not written, {refusal}")
    if parse_settings(settings_text(root)) is None:
        problems.append(f"{SETTINGS}: not a JSON object, so it is left as it is; fix or delete it")
    return problems


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", default=".")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--check", action="store_true")
    mode.add_argument("--write", action="store_true")
    options = parser.parse_args(argv[1:])
    root = Path(options.root).resolve()
    problems = write_problems(root, plugin_pin.PLUGIN) if options.write else drift(root)
    if options.write and not problems:
        regenerate(root)
    for line in problems:
        print(line)
    if problems:
        print("settings-check: red")
    elif options.write:
        print(f"settings-check: {SETTINGS} written from {MANIFEST}")
    else:
        print("settings-check: green")
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
