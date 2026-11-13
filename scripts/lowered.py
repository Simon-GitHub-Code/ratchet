#!/usr/bin/env python3
"""The ratchet's first rule as a gate: no threshold lowered, no ceiling raised, none missing.

Compares ``quality/thresholds.toml`` at head with the merge base. Every key only rises, except
the keys under ``ceilings`` (the speed gate's seconds per tier and gate, the change-size
ceiling), which only fall. A flag that is ``true`` (``absolute``) stays ``true``.

Relaxing a value is the owner's change with an ADR behind it, so a loosening passes when an ADR
under ``docs/adr/`` names it exactly, on a line of its own:
``Relaxes: <key> from <old> to <new>``, with ``removed`` as the new value of a dropped key. An
agent may add that ADR (ADR 0014), but ``quality/thresholds.toml`` is a guard path, so the
loosening itself is the owner's edit.

The owed ledger, ``quality/owed.toml``, is a ratchet too (ADR 0037): against the merge base an
entry may go, and none may be added or changed unless the same change moves the binding's plugin
pin to a newer version and the new entry carries that version. Otherwise a change could silence a
new red finding by writing an entry for it.

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
import owed

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


def _spelled(text: str) -> str:
    """A value as ``RELAXES`` spells it, in the form ``show`` prints."""
    if text in {"removed", "true", "false"}:
        return text
    return show(float(text))


def _named(value: Value | None) -> str:
    return "removed" if value is None else show(value)


def relaxations(root: Path) -> dict[tuple[str, str, str], str]:
    """Each loosening an ADR names, mapped to the ADR's path.

    Keyed on the shown values: ``1.0 == True`` in Python, so values would let a number pass a flag.
    """
    named: dict[tuple[str, str, str], str] = {}
    for adr in sorted((root / ADRS).glob("*.md")):
        for line in (gatelib.read_text(adr) or "").splitlines():
            if match := RELAXES.match(line):
                key = (match["key"], _spelled(match["old"]), _spelled(match["new"]))
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
    problems = owed_grown(root, base)
    previous = gatelib.show(root, base, options.file)
    if previous is None:
        print(f"ratchet-lowered: {options.file} is new at {base[:12]}; accepted as is")
        return gatelib.report("ratchet-lowered", problems, base) if problems else 0
    found = _loosenings(
        flatten(tomllib.loads(previous)), flatten(tomllib.loads(current_path.read_text()))
    )
    named = relaxations(root)
    for key, old, new, message in found:
        adr = named.get((key, show(old), _named(new)))
        if adr is None:
            problems.append(message)
        else:
            print(f"{message}, relaxed by {adr}")
    return gatelib.report("ratchet-lowered", problems, base)


def owed_grown(root: Path, base: str) -> list[str]:
    """What the owed ledger at head adds against ``base`` that no move of the pin allows."""
    binding = str(gatelib.BINDING)
    return owed.grown(
        owed.parse(gatelib.show(root, base, str(owed.LEDGER))),
        owed.read(root),
        binding=(gatelib.show(root, base, binding), gatelib.read_text(root / binding)),
    )


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
