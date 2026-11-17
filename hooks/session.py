#!/usr/bin/env python3
import importlib
import os
import sys
from pathlib import Path

from guard import MINIMUM_PYTHON, payload_of, root_of

HOOKS = Path(__file__).resolve().parent
ANTIGRAVITY_EVENT = "hookEventName"


def event_of(text: str) -> str:
    payload = payload_of(text)
    event = payload.get("hook_event_name", payload.get(ANTIGRAVITY_EVENT))
    return event if isinstance(event, str) else ""


def main() -> int:
    if sys.version_info < MINIMUM_PYTHON:
        return 0
    text = sys.stdin.read()
    root = root_of(text, os.environ, Path.cwd())
    sys.path.insert(0, str(HOOKS))
    try:
        module = importlib.import_module("ratchet_guards.session")
        module.record_plugin(root, HOOKS.parent)
        return module.main(root, event_of(text), antigravity=ANTIGRAVITY_EVENT in payload_of(text))
    except Exception:  # noqa: BLE001 # -- context only: a broken hook says nothing
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
