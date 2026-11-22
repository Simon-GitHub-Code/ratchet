#!/usr/bin/env python3
from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import TYPE_CHECKING

import gatelib

if TYPE_CHECKING:
    from collections.abc import Sequence

MARKERS = ("TODO", "FIXME", "HACK", "XXX")
CHUNK = 200


def _sum_counts(out: str | None) -> int:
    return sum(int(line.rsplit(":", 1)[1]) for line in out.splitlines()) if out else 0


def diff_paths(root: Path, base: str, *, in_index: bool, outside: str | None = None) -> list[str]:
    filter_flag = "--diff-filter=d" if in_index else "--diff-filter=a"
    diff_args = ("diff", "--cached", "--name-only", "--no-renames", filter_flag, "-z", base)
    out = gatelib.git(root, *diff_args) or ""
    return [p for p in out.split("\0") if p and p != outside]


def staged_paths(root: Path, *, outside: str | None = None) -> list[str]:
    out = gatelib.git(root, "ls-files", "--cached", "-z") or ""
    return [p for p in out.split("\0") if p and p != outside]


def grep_count(
    root: Path, pattern: str, ref: str | None, *, word: bool = True, outside: str | None = None
) -> int:
    args = ["grep", "-I", "-c", "-E", *(["-w"] if word else [])]
    args += ([] if ref else ["--untracked"]) + [pattern]
    scope = [".", f":(exclude){outside}"] if outside else ["."]
    return _sum_counts(gatelib.git(root, *args, *([ref] if ref else []), "--", *scope))


def grep_count_paths(
    root: Path,
    pattern: str,
    paths: Sequence[str],
    *,
    ref: str | None = None,
    word: bool = True,
) -> int:
    if not paths:
        return 0
    target = ["--cached"] if ref is None else []
    tail = [ref] if ref is not None else []
    args = [
        "--literal-pathspecs",
        "grep",
        *target,
        "-I",
        "-c",
        "-E",
        *(["-w"] if word else []),
        "-e",
        pattern,
        *tail,
    ]
    return sum(
        _sum_counts(gatelib.git(root, *args, "--", *paths[start : start + CHUNK]))
        for start in range(0, len(paths), CHUNK)
    )


def counts(root: Path, ref: str | None) -> dict[str, int]:
    return {marker: grep_count(root, marker, ref) for marker in MARKERS}


def counts_paths(root: Path, paths: Sequence[str], *, ref: str | None = None) -> dict[str, int]:
    return {marker: grep_count_paths(root, marker, paths, ref=ref) for marker in MARKERS}


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
    parser.add_argument("--staged", action="store_true")
    options = parser.parse_args(argv[1:])
    root = Path(options.root).resolve()
    base = gatelib.base_commit(root, options.base)
    if base is None:
        now = counts_paths(root, staged_paths(root)) if options.staged else counts(root, None)
        print(f"markers: no base commit yet; {sum(now.values())} marker(s) accepted as is")
        return 0
    if options.staged:
        before = counts_paths(root, diff_paths(root, base, in_index=False), ref=base)
        after = counts_paths(root, diff_paths(root, base, in_index=True))
        return gatelib.report("markers", risen(before, after), base)
    return gatelib.report("markers", risen(counts(root, base), counts(root, None)), base)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
