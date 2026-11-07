#!/usr/bin/env python3
"""The ratchet's first rule as a gate: no threshold lowered, no ceiling raised, none missing.

Compares ``quality/thresholds.toml`` at head with the merge base. Every key only rises, except
the keys under ``ceilings`` (the speed gate's seconds per tier and gate, the change-size
ceiling), which only fall.

Usage: ``lowered.py [--root DIR] [--base REF] [--file PATH]``. The base is ``gatelib.base_commit``:
the merge base of HEAD with the default branch, or ``HEAD~1`` on the default branch itself. A
base without the file passes: that is the first adoption.
"""

from __future__ import annotations

import argparse
import sys
import tomllib
from pathlib import Path

import gatelib

CEILINGS = "ceilings."


def flatten(data: dict[str, object], prefix: str = "") -> dict[str, float]:
    flat: dict[str, float] = {}
    for key, value in data.items():
        name = f"{prefix}.{key}" if prefix else key
        if isinstance(value, dict):
            flat.update(flatten(value, name))
        elif isinstance(value, (int, float)) and not isinstance(value, bool):
            flat[name] = float(value)
    return flat


def loosened(before: dict[str, float], after: dict[str, float]) -> list[str]:
    """Every key that was removed, lowered (a threshold) or raised (a ceiling)."""
    problems = []
    for key, old in sorted(before.items()):
        if key not in after:
            problems.append(f"{key}: removed (was {old})")
        elif key.startswith(CEILINGS) and after[key] > old:
            problems.append(f"{key}: ceiling raised from {old} to {after[key]}")
        elif not key.startswith(CEILINGS) and after[key] < old:
            problems.append(f"{key}: lowered from {old} to {after[key]}")
    return problems


def lowered(before: dict[str, float], after: dict[str, float]) -> list[str]:
    return loosened(before, after)


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
    problems = lowered(
        flatten(tomllib.loads(previous)), flatten(tomllib.loads(current_path.read_text()))
    )
    return gatelib.report("ratchet-lowered", problems, base)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
