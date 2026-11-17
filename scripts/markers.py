#!/usr/bin/env python3
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import gatelib

MARKERS = ("TODO", "FIXME", "HACK", "XXX")


def grep_count(
    root: Path, pattern: str, ref: str | None, *, word: bool = True, outside: str | None = None
) -> int:
    args = ["grep", "-I", "-c", "-E", *(["-w"] if word else [])]
    args += ([] if ref else ["--untracked"]) + [pattern]
    scope = [".", f":(exclude){outside}"] if outside else ["."]
    out = gatelib.git(root, *args, *([ref] if ref else []), "--", *scope)
    return sum(int(line.rsplit(":", 1)[1]) for line in out.splitlines()) if out else 0


def counts(root: Path, ref: str | None) -> dict[str, int]:
    return {marker: grep_count(root, marker, ref) for marker in MARKERS}


def risen(before: dict[str, int], after: dict[str, int]) -> list[str]:
    return [
        f"{marker}: rose from {before[marker]} to {after[marker]}"
        for marker in MARKERS
        if after[marker] > before[marker]
    ]


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", default=".")
    parser.add_argument("--base")
    options = parser.parse_args(argv[1:])
    root = Path(options.root).resolve()
    base = gatelib.base_commit(root, options.base)
    now = counts(root, None)
    if base is None:
        print(f"markers: no base commit yet; {sum(now.values())} marker(s) accepted as is")
        return 0
    return gatelib.report("markers", risen(counts(root, base), now), base)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
