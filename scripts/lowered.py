#!/usr/bin/env python3
"""The ratchet's first rule as a gate: no threshold lowered, none missing.

Compares ``quality/thresholds.toml`` at head with the merge base.

Usage: ``lowered.py [--root DIR] [--base REF] [--file PATH]``. The base defaults to the merge
base of HEAD with ``origin/main`` (then ``main``), or ``HEAD~1`` on a clean main. A base
without the file passes: that is the first adoption.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
import tomllib
from pathlib import Path

MAIN_BRANCHES = ("origin/main", "main")


def git(root: Path, *args: str) -> str | None:
    result = subprocess.run(
        ["git", "-C", str(root), *args], capture_output=True, text=True, check=False
    )
    return result.stdout.strip() if result.returncode == 0 else None


def base_commit(root: Path) -> str | None:
    head = git(root, "rev-parse", "--abbrev-ref", "HEAD")
    for branch in MAIN_BRANCHES:
        base = git(root, "merge-base", "HEAD", branch)
        if base is None:
            continue
        if base == git(root, "rev-parse", "HEAD") or head in ("main", "master"):
            return git(root, "rev-parse", "HEAD~1")
        return base
    return git(root, "rev-parse", "HEAD~1")


def flatten(data: dict[str, object], prefix: str = "") -> dict[str, float]:
    flat: dict[str, float] = {}
    for key, value in data.items():
        name = f"{prefix}.{key}" if prefix else key
        if isinstance(value, dict):
            flat.update(flatten(value, name))
        elif isinstance(value, (int, float)) and not isinstance(value, bool):
            flat[name] = float(value)
    return flat


def lowered(before: dict[str, float], after: dict[str, float]) -> list[str]:
    problems = []
    for key, old in sorted(before.items()):
        if key not in after:
            problems.append(f"{key}: removed (was {old})")
        elif after[key] < old:
            problems.append(f"{key}: lowered from {old} to {after[key]}")
    return problems


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
    base = options.base or base_commit(root)
    if base is None:
        print(f"ratchet-lowered: no base commit yet; {options.file} accepted as is")
        return 0
    previous = git(root, "show", f"{base}:{options.file}")
    if previous is None:
        print(f"ratchet-lowered: {options.file} is new at {base[:12]}; accepted as is")
        return 0
    problems = lowered(
        flatten(tomllib.loads(previous)), flatten(tomllib.loads(current_path.read_text()))
    )
    for line in problems:
        print(line)
    print(f"ratchet-lowered: {'red' if problems else 'green'} against {base[:12]}")
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
