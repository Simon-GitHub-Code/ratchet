#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
import tomllib
from enum import Enum
from pathlib import Path
from typing import TYPE_CHECKING, NamedTuple

import plugin_pin

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "hooks"))
from ratchet_guards import events

if TYPE_CHECKING:
    from collections.abc import Callable

    import gatelib

MANIFEST = Path("guards/manifest.toml")
SETTINGS = Path(".claude/settings.json")
SCHEMA = "https://json.schemastore.org/claude-code-settings.json"
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
    ".git/ratchet-plugin",
    ".git/info/grafts",
    ".git/shallow",
    ".git/objects/info",
    ".git/objects/info/**",
    f".git/{events.LOG_NAME}",
    "~/.claude/settings.json",
    "~/.claude/settings.local.json",
    "~/.claude/plugins/**",
)


def edit_rule(pattern: str) -> str:
    return f"Edit({pattern})" if pattern.startswith("~/") else f"Edit(/{pattern})"


DENY = [*map(edit_rule, TAMPER), *COMMAND_DENIES]
ASK: list[str] = []
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
    MANIFEST = "manifest"
    MISSING = "missing"
    MISPLACED = "misplaced"
    OWN = "own"


class Drift(NamedTuple):
    line: str
    fault: Fault


def drifts(root: Path) -> list[Drift]:
    manifest = read_manifest(root)
    if manifest is None:
        line = f"{MANIFEST}: missing or not TOML, so no permission rule can be derived"
        return [Drift(line, Fault.MANIFEST)]
    return drifts_of(manifest, read_settings(root))


def stricter(settings: dict[str, object], manifest: dict[str, object]) -> list[str]:
    writes = plugin_writer(manifest)
    return [
        rule
        for rule in dict.fromkeys(listed(settings, "deny"))
        if rule not in DENY and writes(rule)
    ]


def drifts_of(manifest: dict[str, object], settings: dict[str, object]) -> list[Drift]:
    kept = stricter(settings, manifest)
    writes = plugin_writer(manifest)
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
                Fault.MISPLACED if writes(r) else Fault.OWN,
            )
            for r in actual
            if r not in expected
        )
    return found


def drift(root: Path) -> list[str]:
    return [found.line for found in drifts(root)]


def plugin_writer(manifest: dict[str, object]) -> Callable[[str], bool]:
    groups = manifest.get("group")
    mirrored = {
        edit_rule(pattern)
        for group in (groups if isinstance(groups, list) else [])
        if isinstance(group, dict) and isinstance(group.get("paths"), list)
        for pattern in group["paths"]
        if isinstance(pattern, str)
    }
    return lambda rule: rule in DENY or rule in EARLIER_ASKS or rule in mirrored


def plugin_writes(rule: str, manifest: dict[str, object]) -> bool:
    return plugin_writer(manifest)(rule)


def own_rules(settings: dict[str, object], manifest: dict[str, object]) -> list[tuple[str, str]]:
    writes = plugin_writer(manifest)
    return [
        (key, rule) for key in ("deny", "ask") for rule in listed(settings, key) if not writes(rule)
    ]


def regenerated(manifest: dict[str, object]) -> gatelib.Want:
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
    path = root / SETTINGS
    path.parent.mkdir(parents=True, exist_ok=True)
    want = regenerated(read_manifest(root) or {})
    path.write_text(want(settings_text(root)), encoding="utf-8")


def write_problems(root: Path, plugin: Path) -> list[str]:
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
