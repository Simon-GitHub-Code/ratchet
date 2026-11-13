#!/usr/bin/env python3
"""Permission rules: ``.claude/settings.json`` holds a fixed list of denies and asks nothing.

The ``deny`` list is the same in every repository and does not read the manifest. It names what
takes effect before any merge, as ``Edit`` rules: the harness's settings, hooks, workflows, agents,
skills and commands (Claude Code reads them from the checkout a session runs in), the git hooks,
config and the refs the guards read the default branch through (ADR 0016), and the user's settings
and installed plugins. Beside them are the command rules the hook also denies: the gate bypasses,
history rewrites and forge changes, and the commands that write the default branch's ref in their
plain spellings. ``gh label create`` is not denied, because the hook lets a recorded label through
and a settings deny would refuse that one too (ADR 0006).

The ``ask`` list is empty. An edit of any other guard path meets no rule here: on a branch the
merge judge reads it, and where it lands on the default branch the hook refuses it. The hook is
the one layer that asks, on a merge and on the agent-ready label, so its prompt is the one the
owner reads, with its reasons (ADR 0033). A repository-relative pattern becomes ``Edit(/pattern)``
(Claude Code reads a leading ``/`` as the project root) and a home pattern stays as it is.

The rules the plugin writes are the whole of what it owns. Earlier versions wrote one ``Edit`` rule
per manifest pattern and ``gh pr merge`` as an ask; a rule no version writes is the project's own,
and ``regenerated`` keeps it where it stood, so ``--check`` still reports it and no write removes
it. A rule some version writes that the project holds in ``deny`` and this one does not is
stricter than the plugin's: it stays in ``deny``, and neither list reports it. No regeneration
takes a rule out of ``deny`` (ADR 0029).

Usage: ``settings_rules.py [--root DIR] [--check | --write]``. ``--check`` (the default) prints
one line per rule missing from or foreign to the lists and exits 1 on any. ``--write`` regenerates
the two lists and keeps every other key and every rule of the project's own. It refuses while the
repository has no readable manifest, while ``settings.json`` is not a JSON object, and while the
plugin in use is not the one CI runs (``plugin_pin.write_refusal``).
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

# Why: the settings belong to a repository that has adopted the method, and the patterns an
# earlier version mirrored tell its rules from the project's own.
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
    "Bash(git config core.hooksPath:*)",
    "Bash(git -c core.hooksPath*)",
    *DEFAULT_BRANCH_DENIES,
    *OTHER_REF_DENIES,
)
# Why: each of these acts before a merge, so no merge judge reads a change to one.
TAMPER = (
    ".claude/settings.json",
    ".claude/settings.local.json",
    ".claude/hooks/**",
    ".claude/workflows/**",
    ".claude/agents/**",
    ".claude/skills/**",
    ".claude/commands/**",
    ".git/hooks/**",
    ".git/config",
    ".git/refs",
    ".git/refs/**",
    ".git/packed-refs",
    ".git/HEAD",
    ".git/info/grafts",
    ".git/shallow",
    ".git/objects/info",
    ".git/objects/info/**",
    "~/.claude/settings.json",
    "~/.claude/settings.local.json",
    "~/.claude/plugins/**",
)


def edit_rule(pattern: str) -> str:
    """The ``Edit`` permission rule that covers one pattern."""
    return f"Edit({pattern})" if pattern.startswith("~/") else f"Edit(/{pattern})"


DENY = [*map(edit_rule, TAMPER), *COMMAND_DENIES]
ASK: list[str] = []
# Why: an earlier version asked here; a repository that still holds it is regenerated.
EARLIER_ASKS = ("Bash(gh pr merge:*)",)


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
    """Each manifest fault, missing rule and foreign rule, with its kind; empty when they match."""
    manifest = read_manifest(root)
    if manifest is None:
        line = f"{MANIFEST}: missing or not TOML, so no permission rule can be derived"
        return [Drift(line, Fault.MANIFEST)]
    return drifts_of(manifest, read_settings(root))


def stricter(settings: dict[str, object], manifest: dict[str, object]) -> list[str]:
    """The rules the project denies that an earlier version wrote and this one does not: kept.

    Why: ``deny`` is as strict as a rule gets. A rule the plugin could write that the project
    holds there is the owner's choice, and taking it out of ``deny`` would loosen it.
    """
    return [
        rule
        for rule in listed(settings, "deny")
        if rule not in DENY and plugin_writes(rule, manifest)
    ]


def drifts_of(manifest: dict[str, object], settings: dict[str, object]) -> list[Drift]:
    """The drift of ``settings`` from the fixed lists, read against ``manifest``."""
    kept = stricter(settings, manifest)
    found: list[Drift] = []
    for key, expected in (("deny", [*DENY, *kept]), ("ask", ASK)):
        actual = listed(settings, key)
        found.extend(
            Drift(f"{SETTINGS}: '{r}' is missing from {key}", Fault.MISSING)
            for r in expected
            if r not in actual and r not in kept
        )
        found.extend(
            Drift(
                f"{SETTINGS}: '{r}' in {key} is not a rule this tool writes",
                Fault.MISPLACED if plugin_writes(r, manifest) else Fault.OWN,
            )
            for r in actual
            if r not in expected
        )
    return found


def drift(root: Path) -> list[str]:
    """One line per manifest fault, missing rule or foreign rule; empty when settings match."""
    return [found.line for found in drifts(root)]


def plugin_writes(rule: str, manifest: dict[str, object]) -> bool:
    """Whether some version of the plugin writes ``rule`` for this manifest, in either list."""
    groups = manifest.get("group")
    mirrored = {
        edit_rule(pattern)
        for group in (groups if isinstance(groups, list) else [])
        if isinstance(group, dict) and isinstance(group.get("paths"), list)
        for pattern in group["paths"]
        if isinstance(pattern, str)
    }
    return rule in DENY or rule in EARLIER_ASKS or rule in mirrored


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

    def want(text: str | None) -> str:
        settings = parse_settings(text)
        if settings is None:
            return text or ""
        permissions = settings.get("permissions")
        held = permissions if isinstance(permissions, dict) else {}
        own = own_rules(settings, manifest)
        kept = stricter(settings, manifest)
        ours = {"deny": [*DENY, *kept], "ask": [rule for rule in ASK if rule not in kept]}
        lists = {
            key: [*rules_, *(rule for k, rule in own if k == key)] for key, rules_ in ours.items()
        }
        settings["permissions"] = {**held, **lists}
        return json.dumps(settings, indent=2) + "\n"

    return want


def regenerate(root: Path) -> None:
    """Write ``.claude/settings.json`` as ``regenerated`` makes it for the root's manifest."""
    path = root / SETTINGS
    path.parent.mkdir(parents=True, exist_ok=True)
    want = regenerated(read_manifest(root) or {})
    path.write_text(want(settings_text(root)), encoding="utf-8")


def write_problems(root: Path, plugin: Path) -> list[str]:
    """What stops ``--write`` from regenerating the file under ``plugin``; empty when nothing."""
    if read_manifest(root) is None:
        return [f"{MANIFEST}: missing or not TOML, so no permission rule can be derived"]
    problems: list[str] = []
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
        print(f"settings-check: {SETTINGS} written")
    else:
        print("settings-check: green")
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
