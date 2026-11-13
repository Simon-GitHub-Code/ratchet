#!/usr/bin/env python3
"""The snapshots gate: every config the manifest lists is recorded, and the record matches.

The files in the manifest's ``tool-config`` group are the gate tools' configs it knows of; a
config the group does not list is neither copied nor checked, so loosening it shows nowhere.
``--write`` copies each into ``quality/snapshots/`` (the path's slashes become ``__``), which is a
ratchet path, so a config change shows up as a guard-path change with a diff a reviewer can read.
``--check`` regenerates the set and fails on any snapshot that differs, is missing or is stale.

Usage: ``snapshots.py [--root DIR] [--group NAME] (--write | --check)``.
"""

from __future__ import annotations

import argparse
import sys
import tomllib
from pathlib import Path

import gatelib

MANIFEST = Path("guards/manifest.toml")
SNAPSHOTS = Path("quality/snapshots")
GROUP = "tool-config"


def patterns(root: Path, group: str) -> list[str] | None:
    """The group's repository-relative patterns, or ``None`` when the manifest lacks the group."""
    path = root / MANIFEST
    if not path.is_file():
        return None
    data = tomllib.loads(path.read_text(encoding="utf-8"))
    groups = data.get("group")
    for table in groups if isinstance(groups, list) else []:
        if isinstance(table, dict) and table.get("name") == group:
            paths = table.get("paths")
            listed = paths if isinstance(paths, list) else []
            return [p for p in listed if isinstance(p, str) and not p.startswith("~/")]
    return None


def snapshot_name(path: str) -> str:
    return path.replace("/", "__")


def expected(root: Path, group: str) -> dict[str, str] | None:
    """Snapshot name to content for every tracked file the group covers."""
    found = patterns(root, group)
    if found is None:
        return None
    files = [p for p in gatelib.tracked_paths(root) if gatelib.matches(p, found)]
    return {snapshot_name(p): (root / p).read_bytes().decode("utf-8", "replace") for p in files}


def write(root: Path, group: str) -> list[str]:
    wanted = expected(root, group)
    if wanted is None:
        return [f"{MANIFEST}: no group named '{group}'"]
    target = root / SNAPSHOTS
    target.mkdir(parents=True, exist_ok=True)
    for stale in target.iterdir():
        if stale.is_file() and stale.name not in wanted:
            stale.unlink()
    for name, content in wanted.items():
        (target / name).write_text(content, encoding="utf-8")
    return []


def check(root: Path, group: str) -> list[str]:
    wanted = expected(root, group)
    if wanted is None:
        return [f"{MANIFEST}: no group named '{group}'"]
    target = root / SNAPSHOTS
    present = {p.name: p.read_text(encoding="utf-8") for p in target.glob("*") if p.is_file()}
    problems = [
        f"{SNAPSHOTS}/{n}: missing; run `just snapshots`" for n in wanted if n not in present
    ]
    problems += [f"{SNAPSHOTS}/{n}: stale; its config is gone" for n in present if n not in wanted]
    problems += [
        f"{SNAPSHOTS}/{n}: differs from its config; run `just snapshots` and review the diff"
        for n in wanted
        if n in present and present[n] != wanted[n]
    ]
    return sorted(problems)


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", default=".")
    parser.add_argument("--group", default=GROUP)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--write", action="store_true")
    mode.add_argument("--check", action="store_true")
    options = parser.parse_args(argv[1:])
    root = Path(options.root).resolve()
    if options.write:
        problems = write(root, options.group)
        return gatelib.report("snapshots", problems)
    return gatelib.report("snapshots-check", check(root, options.group))


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
