#!/usr/bin/env python3
import io
import json
import os
import sys
from collections.abc import Mapping
from pathlib import Path

HOOKS = Path(__file__).resolve().parent
MINIMUM_PYTHON = (3, 11)
ANTIGRAVITY_CALL = "toolCall"


def payload_of(text: str) -> dict[str, object]:
    try:
        payload = json.loads(text)
    except ValueError:
        return {}
    return payload if isinstance(payload, dict) else {}


def fallback(problem: str, *, antigravity: bool = False) -> str:
    because = f"Ratchet guard could not run: {problem}."
    if antigravity:
        return json.dumps({"decision": "force_ask", "reason": because})
    output = {
        "hookEventName": "PreToolUse",
        "permissionDecision": "ask",
        "permissionDecisionReason": because,
    }
    return json.dumps({"hookSpecificOutput": output})


def root_of(text: str, environ: Mapping[str, str], default: Path) -> Path:
    cwd = payload_of(text).get("cwd")
    if isinstance(cwd, str) and cwd:
        return Path(cwd)
    project = environ.get("CLAUDE_PROJECT_DIR")
    return Path(project) if project else default


def main() -> int:
    text = sys.stdin.read()
    antigravity = ANTIGRAVITY_CALL in payload_of(text)
    if sys.version_info < MINIMUM_PYTHON:
        problem = f"python3 is {sys.version.split()[0]}, 3.11+ needed"
        sys.stdout.write(fallback(problem, antigravity=antigravity) + "\n")
        return 0
    root = root_of(text, os.environ, Path.cwd())
    sys.path.insert(0, str(HOOKS))
    try:
        from ratchet_guards.hook import main as guard  # noqa: PLC0415 # -- after the path set-up above
    except Exception as error:  # noqa: BLE001 # -- fail closed: a broken guard must reach a human
        problem = f"{type(error).__name__}: {error}"
        sys.stdout.write(fallback(problem, antigravity=antigravity) + "\n")
        return 0
    project = os.environ.get("CLAUDE_PROJECT_DIR")
    return guard(root, io.StringIO(text), project=Path(project) if project else None)


if __name__ == "__main__":
    raise SystemExit(main())
