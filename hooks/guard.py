#!/usr/bin/env python3
"""Claude Code PreToolUse hook of the Ratchet plugin: delegates to ``ratchet_guards.hook``.

Standard library only and run on the system ``python3`` (3.13 or later). The repository
root is the hook input's ``cwd``, else ``$CLAUDE_PROJECT_DIR``, else the working directory.
If the guard cannot even be loaded, the answer is ``ask``, so a broken guard stops at a
human instead of letting the tool call through.
"""

import io
import json
import os
import sys
from collections.abc import Mapping
from pathlib import Path

HOOKS = Path(__file__).resolve().parent
MINIMUM_PYTHON = (3, 13)


def fallback(problem: str) -> str:
    """An ``ask`` decision for when the guard itself cannot run."""
    output = {
        "hookEventName": "PreToolUse",
        "permissionDecision": "ask",
        "permissionDecisionReason": f"Ratchet guard could not run: {problem}.",
    }
    return json.dumps({"hookSpecificOutput": output})


def root_of(text: str, environ: Mapping[str, str], default: Path) -> Path:
    """The root: the payload's ``cwd``, else ``$CLAUDE_PROJECT_DIR``, else ``default``."""
    try:
        payload = json.loads(text)
    except ValueError:
        payload = None
    cwd = payload.get("cwd") if isinstance(payload, dict) else None
    if isinstance(cwd, str) and cwd:
        return Path(cwd)
    project = environ.get("CLAUDE_PROJECT_DIR")
    return Path(project) if project else default


def main() -> int:
    """Load the guard and answer one hook call."""
    if sys.version_info < MINIMUM_PYTHON:
        sys.stdout.write(fallback(f"python3 is {sys.version.split()[0]}, 3.13+ needed") + "\n")
        return 0
    text = sys.stdin.read()
    root = root_of(text, os.environ, Path.cwd())
    sys.path.insert(0, str(HOOKS))
    try:
        from ratchet_guards.hook import main as guard  # noqa: PLC0415 # -- after the path set-up above
    except Exception as error:  # noqa: BLE001 # -- fail closed: a broken guard must reach a human
        sys.stdout.write(fallback(f"{type(error).__name__}: {error}") + "\n")
        return 0
    return guard(root, io.StringIO(text))


if __name__ == "__main__":
    raise SystemExit(main())
