#!/usr/bin/env python3
"""The ratchet's first rule as a gate: no threshold lowered, no ceiling raised, none missing.

Compares ``quality/thresholds.toml`` at head with the merge base. Every key only rises, except
the keys under ``ceilings`` (the speed gate's seconds per tier and gate, the change-size
ceiling), which only fall. A flag that is ``true`` (``absolute``) stays ``true``.

Relaxing a value is the owner's change with an ADR behind it, so a loosening passes when an ADR
under ``docs/adr/`` (a guard path only the owner writes) names it exactly, on a line of its own:
``Relaxes: <key> from <old> to <new>``, with ``removed`` as the new value of a dropped key.

Usage: ``lowered.py [--root DIR] [--base REF] [--file PATH]``. The base is ``gatelib.base_commit``:
the merge base of HEAD with the default branch, or ``HEAD~1`` on the default branch itself. A
base without the file passes: that is the first adoption.
"""

from __future__ import annotations

import argparse
import math
import re
import sys
import tomllib
from pathlib import Path

import gatelib

CEILINGS = "ceilings."
ADRS = "docs/adr"
_VALUE = r"true|false|-?\d+(?:\.\d+)?"
RELAXES = re.compile(
    rf"^\W*Relaxes:\s*(?P<key>\S+)\s+from\s+(?P<old>{_VALUE})\s+to\s+(?P<new>{_VALUE}|removed)\s*$"
)
Value = float | bool
Loosening = tuple[str, Value, Value | None, str]


def flatten(data: dict[str, object], prefix: str = "") -> dict[str, Value]:
    flat: dict[str, Value] = {}
    for key, value in data.items():
        name = f"{prefix}.{key}" if prefix else key
        if isinstance(value, dict):
            flat.update(flatten(value, name))
        elif isinstance(value, bool):
            flat[name] = value
        elif isinstance(value, (int, float)):
            flat[name] = float(value)
    return flat


def show(value: Value) -> str:
    return str(value).lower() if isinstance(value, bool) else str(value)


def _loosenings(before: dict[str, Value], after: dict[str, Value]) -> list[Loosening]:
    found: list[Loosening] = []
    for key, old in sorted(before.items()):
        new = after.get(key)
        if new is None:
            found.append((key, old, None, f"{key}: removed (was {show(old)})"))
        elif isinstance(old, bool) != isinstance(new, bool):
            kinds = (
                ("a boolean", "a number") if isinstance(old, bool) else ("a number", "a boolean")
            )
            found.append((key, old, new, f"{key}: changed from {kinds[0]} to {kinds[1]}"))
        elif isinstance(old, bool):
            if old and not new:
                found.append((key, old, new, f"{key}: turned off"))
        elif not math.isfinite(new):
            found.append((key, old, new, f"{key}: {show(new)} is not a finite number"))
        elif key.startswith(CEILINGS) and new > old:
            found.append((key, old, new, f"{key}: ceiling raised from {show(old)} to {show(new)}"))
        elif not key.startswith(CEILINGS) and new < old:
            found.append((key, old, new, f"{key}: lowered from {show(old)} to {show(new)}"))
    return found


def loosened(before: dict[str, Value], after: dict[str, Value]) -> list[str]:
    """Every key that was removed, lowered (a threshold), raised (a ceiling) or turned off."""
    return [message for *_, message in _loosenings(before, after)]


def lowered(before: dict[str, Value], after: dict[str, Value]) -> list[str]:
    return loosened(before, after)


def _parse(text: str) -> Value | None:
    """A value as ``RELAXES`` spells it; ``None`` for ``removed``."""
    if text == "removed":
        return None
    return text == "true" if text in {"true", "false"} else float(text)


def relaxations(root: Path) -> dict[tuple[str, Value | None, Value | None], str]:
    """Each loosening an ADR names, mapped to the ADR's path."""
    named: dict[tuple[str, Value | None, Value | None], str] = {}
    for adr in sorted((root / ADRS).glob("*.md")):
        for line in (gatelib.read_text(adr) or "").splitlines():
            if match := RELAXES.match(line):
                key = (match["key"], _parse(match["old"]), _parse(match["new"]))
                named[key] = adr.relative_to(root).as_posix()
    return named


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", default=".")
    parser.add_argument("--base")
    parser.add_argument("--file", default="quality/thresholds.toml")
    options = parser.parse_args(argv[1:])
    root = Path(options.root).resolve()
    current_path = root / options.file
    if not current_path.is_file():
        print(f"{options.file}: missing")
        return 1
    base = gatelib.base_commit(root, options.base)
    if base is None:
        print(f"ratchet-lowered: no base commit yet; {options.file} accepted as is")
        return 0
    previous = gatelib.show(root, base, options.file)
    if previous is None:
        print(f"ratchet-lowered: {options.file} is new at {base[:12]}; accepted as is")
        return 0
    found = _loosenings(
        flatten(tomllib.loads(previous)), flatten(tomllib.loads(current_path.read_text()))
    )
    named = relaxations(root)
    problems = []
    for key, old, new, message in found:
        adr = named.get((key, old, new))
        if adr is None:
            problems.append(message)
        else:
            print(f"{message}, relaxed by {adr}")
    return gatelib.report("ratchet-lowered", problems, base)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
