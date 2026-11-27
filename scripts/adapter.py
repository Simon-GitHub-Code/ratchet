#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

HARNESSES = ("claude", "grok", "codex", "pi", "opencode")
PLUGIN_ROOT = Path(__file__).resolve().parents[1]
MATCHER = "Edit|Write|MultiEdit|NotebookEdit|Bash|apply_patch"
TIMEOUT_SECONDS = 10
INTERPRETER = "python3 -E -s"
PLACEHOLDER = "__RATCHET_PLUGIN_ROOT__"
CODEX_HOOKS = Path(".codex/hooks.json")
CODEX_MARKETPLACE_MARKER = ".codex-marketplace-install.json"
RATCHET_HOOK_RE = re.compile(r'^python3(?:\s+-E\s+-s)?\s+"[^"]+/hooks/(guard|session)\.py"$')
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
    if not isinstance(entry, dict) or not isinstance(entry.get("hooks"), list):
        return set()
    return {hook.get("command") for hook in entry["hooks"] if isinstance(hook, dict)}


def _ratchet_script(command: object) -> str | None:
    match = RATCHET_HOOK_RE.match(command) if isinstance(command, str) else None
    return match.group(1) if match else None


def codex_hooks(existing: dict[str, object], plugin_root: Path) -> dict[str, object]:
    hooks = existing.get("hooks")
    table: dict[str, object] = dict(hooks) if isinstance(hooks, dict) else {}
    wanted = {
        "PreToolUse": hook_entry(f'{INTERPRETER} "{plugin_root}/hooks/guard.py"', MATCHER),
        "SessionStart": hook_entry(f'{INTERPRETER} "{plugin_root}/hooks/session.py"'),
    }
    for event, entry in wanted.items():
        current = table.get(event)
        entries: list[object] = _upgraded(current, plugin_root) if isinstance(current, list) else []
        if not any(_same_command(candidate, entry) for candidate in entries):
            entries.append(entry)
        table[event] = entries
    return {**existing, "hooks": table}


def _upgraded(entries: list[object], plugin_root: Path) -> list[object]:
    def upgraded_hook(hook: object) -> object:
        if not isinstance(hook, dict):
            return hook
        script = _ratchet_script(hook.get("command"))
        if script is None:
            return hook
        return {**hook, "command": f'{INTERPRETER} "{plugin_root}/hooks/{script}.py"'}

    def upgraded(entry: object) -> object:
        if not isinstance(entry, dict) or not isinstance(entry.get("hooks"), list):
            return entry
        return {**entry, "hooks": [upgraded_hook(hook) for hook in entry["hooks"]]}

    return [upgraded(entry) for entry in entries]


def _strip_ratchet(entries: list[object]) -> list[object]:
    kept: list[object] = []
    for entry in entries:
        if not isinstance(entry, dict) or not isinstance(entry.get("hooks"), list):
            kept.append(entry)
            continue
        remaining = [
            hook
            for hook in entry["hooks"]
            if not (isinstance(hook, dict) and _ratchet_script(hook.get("command")))
        ]
        if remaining:
            kept.append({**entry, "hooks": remaining})
    return kept


def codex_hooks_without_ratchet(existing: dict[str, object]) -> dict[str, object]:
    hooks = existing.get("hooks")
    if not isinstance(hooks, dict):
        return dict(existing)
    table = dict(hooks)
    for event in ("PreToolUse", "SessionStart"):
        current = table.get(event)
        if not isinstance(current, list):
            continue
        cleaned = _strip_ratchet(current)
        if cleaned:
            table[event] = cleaned
        else:
            table.pop(event, None)
    return {**existing, "hooks": table}


def _same_command(candidate: object, entry: dict[str, object]) -> bool:
    return bool(_commands(candidate) & _commands(entry))


def opencode_plugin(template: str, plugin_root: Path) -> str:
    return template.replace(PLACEHOLDER, plugin_root.as_posix())


def marketplace_refusal(harness: str, plugin_root: Path) -> str | None:
    if harness == "codex" and (plugin_root / CODEX_MARKETPLACE_MARKER).is_file():
        return (
            f"codex: refusing to point {CODEX_HOOKS} at marketplace clone {plugin_root};"
            " Codex loads hooks/hooks.json from the installed plugin cache natively,"
            " or pass --plugin-root pointing at"
            " ~/.codex/plugins/cache/<marketplace>/ratchet/<version>"
        )
    return None


def steps(harness: str, plugin_root: Path, home: Path) -> list[str]:
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
            "codex: Codex loads the installed plugin's hooks/hooks.json natively.",
            f"Only on older Codex builds, --apply merges entries into {home / CODEX_HOOKS}",
            f"pointing at {plugin_root}; --remove strips legacy Ratchet entries from it.",
            f"see {doc}",
        ]
    return [
        f"opencode: --apply writes {home / OPENCODE_PLUGIN} from {OPENCODE_TEMPLATE}",
        f"with the plugin root {plugin_root} filled in.",
        f"see {doc}",
    ]


def apply(harness: str, plugin_root: Path, home: Path) -> list[str]:
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


def remove(harness: str, home: Path) -> list[str]:
    if harness != "codex":
        return [f"{harness}: nothing to remove"]
    path = home / CODEX_HOOKS
    if not path.is_file():
        return [f"{path}: absent, nothing to remove"]
    existing = json.loads(path.read_text(encoding="utf-8"))
    path.write_text(json.dumps(codex_hooks_without_ratchet(existing), indent=2) + "\n")
    return [f"updated {path}"]


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("harness", choices=HARNESSES)
    parser.add_argument("--plugin-root", default=str(PLUGIN_ROOT))
    parser.add_argument("--home", default=str(Path.home()))
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--apply", action="store_true")
    mode.add_argument("--remove", action="store_true")
    options = parser.parse_args(argv[1:])
    plugin_root, home = Path(options.plugin_root).resolve(), Path(options.home)
    if options.remove:
        for line in remove(options.harness, home):
            print(line)
        return 0
    lines = steps(options.harness, plugin_root, home)
    if options.apply:
        if (refusal := marketplace_refusal(options.harness, plugin_root)) is not None:
            print(refusal)
            return 1
        lines += apply(options.harness, plugin_root, home)
    for line in lines:
        print(line)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
