#!/usr/bin/env python3
"""Permission rules from the guard manifest: ``.claude/settings.json`` mirrors the manifest.

Every pattern in ``guards/manifest.toml`` becomes one ``Edit`` rule: ``deny`` in a group with
``edit = "deny"``, ``ask`` in the rest. A group with ``edit = "append"`` gets none: a permission
rule matches a path and cannot read the edit, so an ``ask`` there would prompt for the additions
the hook lets through, and the hook is the one layer on those paths (ADR 0014). A
repository-relative pattern becomes ``Edit(/pattern)``
(Claude Code reads a leading ``/`` as the project root) and a ``~/`` pattern stays as it is. The
command rules are fixed here: the gate bypasses, history rewrites and forge changes the hook
denies, and ``gh pr merge`` as an ask. ``gh label create`` is not denied, because the hook lets a
recorded label through and a settings deny would refuse that one too (ADR 0006).

A group's ``exclude`` has no permission-rule equivalent, so a manifest that uses it is reported:
list the paths the group should cover instead. The ``deny`` and ``ask`` lists are owned by this
tool in full; a project's own rules go in ``allow``.

Usage: ``settings_rules.py [--root DIR] [--check | --write]``. ``--check`` (the default) prints
one line per rule missing from or foreign to the lists and exits 1 on any. ``--write`` replaces
the two lists and keeps every other key; it refuses while the manifest cannot be mirrored.
"""

from __future__ import annotations

import argparse
import json
import sys
import tomllib
from pathlib import Path

MANIFEST = Path("guards/manifest.toml")
SETTINGS = Path(".claude/settings.json")
SCHEMA = "https://json.schemastore.org/claude-code-settings.json"
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


def read_settings(root: Path) -> dict[str, object]:
    path = root / SETTINGS
    if not path.is_file():
        return {"$schema": SCHEMA}
    try:
        loaded = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}
    return loaded if isinstance(loaded, dict) else {}


def listed(settings: dict[str, object], key: str) -> list[str]:
    permissions = settings.get("permissions")
    if not isinstance(permissions, dict):
        return []
    values = permissions.get(key)
    return [v for v in values if isinstance(v, str)] if isinstance(values, list) else []


def drift(root: Path) -> list[str]:
    """One line per manifest fault, missing rule or foreign rule; empty when settings mirror."""
    manifest = read_manifest(root)
    if manifest is None:
        return [f"{MANIFEST}: missing or not TOML, so no permission rule can be derived"]
    (deny, ask), problems = rules(manifest)
    settings = read_settings(root)
    for key, expected in (("deny", deny), ("ask", ask)):
        actual = listed(settings, key)
        problems.extend(
            f"{SETTINGS}: '{r}' is missing from {key}" for r in expected if r not in actual
        )
        problems.extend(
            f"{SETTINGS}: '{r}' in {key} mirrors no manifest pattern"
            for r in actual
            if r not in expected
        )
    return problems


def write(root: Path) -> list[str]:
    """Replace the deny and ask lists from the manifest; the faults that stopped it, if any."""
    manifest = read_manifest(root)
    if manifest is None:
        return [f"{MANIFEST}: missing or not TOML, so no permission rule can be derived"]
    (deny, ask), problems = rules(manifest)
    if problems:
        return problems
    settings = read_settings(root)
    permissions = settings.get("permissions")
    if not isinstance(permissions, dict):
        permissions = {}
    settings["permissions"] = {**permissions, "deny": deny, "ask": ask}
    path = root / SETTINGS
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(settings, indent=2) + "\n", encoding="utf-8")
    return []


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", default=".")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--check", action="store_true")
    mode.add_argument("--write", action="store_true")
    options = parser.parse_args(argv[1:])
    root = Path(options.root).resolve()
    problems = write(root) if options.write else drift(root)
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
