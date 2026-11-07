#!/usr/bin/env python3
"""Claude Code SessionStart and SubagentStart hook of the Ratchet plugin.

Delegates to ``ratchet_guards.session``. Standard library only and run on the system
``python3``. The repository root is the hook input's ``cwd``, else ``$CLAUDE_PROJECT_DIR``,
else the working directory. The hook only adds context, so any failure prints nothing.
"""

import json
import os
import sys
from pathlib import Path

from guard import MINIMUM_PYTHON, root_of

HOOKS = Path(__file__).resolve().parent


def event_of(text: str) -> str:
    """The hook event name in the payload, or empty."""
    try:
        payload = json.loads(text)
    except ValueError:
        return ""
    event = payload.get("hook_event_name") if isinstance(payload, dict) else None
    return event if isinstance(event, str) else ""


def main() -> int:
    """Load the session module and print its context for this checkout, if any."""
    if sys.version_info < MINIMUM_PYTHON:
        return 0
    text = sys.stdin.read()
    root = root_of(text, os.environ, Path.cwd())
    sys.path.insert(0, str(HOOKS))
    try:
        from ratchet_guards.session import main as session  # noqa: PLC0415 # -- after the path set-up above

        return session(root, event_of(text))
    except Exception:  # noqa: BLE001 # -- context only: a broken hook says nothing
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
