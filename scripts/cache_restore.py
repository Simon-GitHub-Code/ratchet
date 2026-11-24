#!/usr/bin/env python3
from __future__ import annotations

import argparse
import contextlib
import os
import stat
import sys
from pathlib import Path
from typing import TYPE_CHECKING

import gatelib
import runrecord

if TYPE_CHECKING:
    from collections.abc import Iterable, Sequence

HIT_VALUES = {"true": True, "false": False}


def path_bytes(path: Path) -> int:
    try:
        info = path.lstat()
    except OSError:
        return 0
    if stat.S_ISREG(info.st_mode):
        return info.st_size
    if not stat.S_ISDIR(info.st_mode):
        return 0
    total = 0
    for folder, _, names in os.walk(path):
        for name in names:
            with contextlib.suppress(OSError):
                entry = Path(folder, name).lstat()
                if stat.S_ISREG(entry.st_mode):
                    total += entry.st_size
    return total


def resolved_paths(root: Path, raw: str) -> Iterable[Path]:
    place = root / Path(raw).expanduser()
    anchor = Path(place.anchor)
    return anchor.glob(str(place.relative_to(anchor)))


def measure(root: Path, patterns: Sequence[str]) -> int:
    seen: set[Path] = set()
    total = 0
    for raw in patterns:
        for matched in resolved_paths(root, raw):
            if matched not in seen:
                seen.add(matched)
                total += path_bytes(matched)
    return total


def parse(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="cache_restore.py")
    parser.add_argument("--root", default=".")
    parser.add_argument("--report")
    parser.add_argument("--hit")
    parser.add_argument("paths", nargs="*")
    return parser.parse_args(argv[1:])


def main(argv: list[str]) -> int:
    options = parse(argv)
    root = Path(options.root).resolve()
    patterns = list(options.paths)
    if not patterns:
        binding = gatelib.read_text(root / gatelib.BINDING) or ""
        patterns = runrecord.ci_cache_paths(binding)
    if not patterns:
        print("ratchet-cache: no cache paths configured", flush=True)
        return 0
    total = measure(root, patterns)
    joined = ", ".join(patterns)
    if total == 0:
        why = "cold miss" if options.hit == "" else "nothing restored"
        detail = f"{joined}: {why}"
    else:
        detail = joined
    print(f"ratchet-cache: restored {total} bytes ({detail})", flush=True)
    if options.report:
        record: dict[str, object] = {
            "kind": runrecord.CACHE_RESTORE,
            "bytes": total,
            "paths": patterns,
            **runrecord.stamp(root),
        }
        if options.hit in HIT_VALUES:
            record["hit"] = HIT_VALUES[options.hit]
        elif options.hit == "":
            record["miss"] = True
        runrecord.append(root / options.report, record, root=root, who="ratchet-cache")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
