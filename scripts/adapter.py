#!/usr/bin/env python3
"""Wire the Ratchet guard into a harness that does not load the plugin's hooks by itself.

Usage: ``adapter.py <claude|grok|codex|pi|opencode> [--plugin-root DIR] [--home DIR] [--apply]``.
Without ``--apply`` it prints the steps for that harness. With it, ``codex`` merges PreToolUse
and SessionStart entries into ``~/.codex/hooks.json`` and ``opencode`` writes
``~/.config/opencode/plugins/ratchet-guard.ts`` from ``adapters/opencode/``; the other three
need no file written. Those targets are locked guard paths, so the owner runs this in their own
shell. ``docs/harness/<name>.md`` explains each harness.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

HARNESSES = ("claude", "grok", "codex", "pi", "opencode")
PLUGIN_ROOT = Path(__file__).resolve().parents[1]
MATCHER = "Edit|Write|MultiEdit|NotebookEdit|Bash|apply_patch"
TIMEOUT_SECONDS = 10
PLACEHOLDER = "__RATCHET_PLUGIN_ROOT__"
CODEX_HOOKS = Path(".codex/hooks.json")
OPENCODE_PLUGIN = Path(".config/opencode/plugins/ratchet-guard.ts")
OPENCODE_TEMPLATE = Path("adapters/opencode/ratchet-guard.ts")


def hook_entry(command: str, matcher: str | None = None) -> dict[str, object]:
    entry: dict[str, object] = {
        "hooks": [{"type": "command", "command": command, "timeout": TIMEOUT_SECONDS}]
    }
    if matcher is not None:
        entry["matcher"] = matcher
    return entry


def _commands(entry: object) -> set[object]:
    """The command strings inside one matcher-group entry, or none when it has no shape."""
    if not isinstance(entry, dict) or not isinstance(entry.get("hooks"), list):
        return set()
    return {hook.get("command") for hook in entry["hooks"] if isinstance(hook, dict)}


def codex_hooks(existing: dict[str, object], plugin_root: Path) -> dict[str, object]:
    """``existing`` with the guard's PreToolUse and SessionStart entries added once."""
    hooks = existing.get("hooks")
    table: dict[str, object] = dict(hooks) if isinstance(hooks, dict) else {}
    wanted = {
        "PreToolUse": hook_entry(f'python3 "{plugin_root}/hooks/guard.py"', MATCHER),
        "SessionStart": hook_entry(f'python3 "{plugin_root}/hooks/session.py"'),
    }
    for event, entry in wanted.items():
        current = table.get(event)
        entries: list[object] = list(current) if isinstance(current, list) else []
        if not any(_same_command(candidate, entry) for candidate in entries):
            entries.append(entry)
        table[event] = entries
    return {**existing, "hooks": table}


def _same_command(candidate: object, entry: dict[str, object]) -> bool:
    return bool(_commands(candidate) & _commands(entry))


def opencode_plugin(template: str, plugin_root: Path) -> str:
    """The OpenCode plugin with the plugin root filled in."""
    return template.replace(PLACEHOLDER, plugin_root.as_posix())


def steps(harness: str, plugin_root: Path, home: Path) -> list[str]:
    """What wires the guard in ``harness``, as lines for the owner."""
    doc = f"docs/harness/{harness}.md"
    if harness in {"claude", "grok"}:
        return [
            f"{harness}: the plugin's hooks/hooks.json is loaded as installed; nothing to write.",
            f"see {doc}",
        ]
    if harness == "pi":
        return [
            "pi: the package's extension adapters/pi/ratchet-guard.ts loads with the package.",
            f"see {doc}",
        ]
    if harness == "codex":
        return [
            "codex: the plugin manifest names hooks/hooks.json; if Codex does not load it,",
            f"--apply merges PreToolUse and SessionStart entries into {home / CODEX_HOOKS}",
            f"pointing at {plugin_root}; Codex asks you to trust the new hooks once.",
            f"see {doc}",
        ]
    return [
        f"opencode: --apply writes {home / OPENCODE_PLUGIN} from {OPENCODE_TEMPLATE}",
        f"with the plugin root {plugin_root} filled in.",
        f"see {doc}",
    ]


def apply(harness: str, plugin_root: Path, home: Path) -> list[str]:
    """Write the wiring for ``harness``; the lines say what changed."""
    if harness == "codex":
        path = home / CODEX_HOOKS
        existing = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(codex_hooks(existing, plugin_root), indent=2) + "\n")
        return [f"wrote {path}"]
    if harness == "opencode":
        path = home / OPENCODE_PLUGIN
        template = (plugin_root / OPENCODE_TEMPLATE).read_text(encoding="utf-8")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(opencode_plugin(template, plugin_root), encoding="utf-8")
        return [f"wrote {path}"]
    return [f"{harness}: nothing to write"]


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("harness", choices=HARNESSES)
    parser.add_argument("--plugin-root", default=str(PLUGIN_ROOT))
    parser.add_argument("--home", default=str(Path.home()))
    parser.add_argument("--apply", action="store_true")
    options = parser.parse_args(argv[1:])
    plugin_root, home = Path(options.plugin_root).resolve(), Path(options.home)
    lines = steps(options.harness, plugin_root, home)
    if options.apply:
        lines += apply(options.harness, plugin_root, home)
    for line in lines:
        print(line)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
