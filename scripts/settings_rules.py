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
(Claude Code reads a leading ``/`` as the project root) and a ``~/`` pattern stays as it is. The
``deny`` and ``ask`` lists are owned by this tool in full; a project's own rules go in ``allow``.

Usage: ``settings_rules.py [--root DIR] [--check | --write]``. ``--check`` (the default) prints
one line per rule missing from or foreign to the lists and exits 1 on any. ``--write`` replaces
the two lists and keeps every other key.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# Why: the conformance gate checks the settings only in a repository that has adopted the method.
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
    """One line per missing or foreign rule; empty when the settings hold the fixed lists."""
    settings = read_settings(root)
    problems: list[str] = []
    for key, expected in (("deny", DENY), ("ask", ASK)):
        actual = listed(settings, key)
        problems.extend(
            f"{SETTINGS}: '{r}' is missing from {key}" for r in expected if r not in actual
        )
        problems.extend(
            f"{SETTINGS}: '{r}' in {key} is not a rule this tool writes"
            for r in actual
            if r not in expected
        )
    return problems


def write(root: Path) -> None:
    """Replace the deny and ask lists with the fixed ones and keep every other key."""
    settings = read_settings(root)
    permissions = settings.get("permissions")
    if not isinstance(permissions, dict):
        permissions = {}
    settings["permissions"] = {**permissions, "deny": DENY, "ask": ASK}
    path = root / SETTINGS
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(settings, indent=2) + "\n", encoding="utf-8")


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", default=".")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--check", action="store_true")
    mode.add_argument("--write", action="store_true")
    options = parser.parse_args(argv[1:])
    root = Path(options.root).resolve()
    if options.write:
        write(root)
        print(f"settings-check: {SETTINGS} written")
        return 0
    problems = drift(root)
    for line in problems:
        print(line)
    print("settings-check: red" if problems else "settings-check: green")
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
