#!/usr/bin/env python3
"""The markers gate: ``TODO``, ``FIXME``, ``HACK`` and ``XXX`` never rise.

Counts each marker as a whole word over the tracked (and, in the work tree, untracked) text
files at the merge base and at head, and fails when any count rose. The count is relative, so
a repository adopts the gate with the markers it has and may only pay them down.

Usage: ``markers.py [--root DIR] [--base REF]``.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import gatelib

MARKERS = ("TODO", "FIXME", "HACK", "XXX")


def grep_count(
    root: Path, pattern: str, ref: str | None, *, word: bool = True, outside: str | None = None
) -> int:
    """Lines matching the extended regex ``pattern`` at ``ref`` (the work tree when ``None``).

    ``outside`` is a path left out of the count: a file that quotes the pattern, as a binding does.

    ``word`` holds the match to whole words, which suits a marker; a pattern that starts or ends
    in punctuation (``#[allow(``, ``.skip(``) can never match that way, so it passes ``False``.
    """
    args = ["grep", "-I", "-c", "-E", *(["-w"] if word else [])]
    args += ([] if ref else ["--untracked"]) + [pattern]
    scope = [".", f":(exclude){outside}"] if outside else ["."]
    out = gatelib.git(root, *args, *([ref] if ref else []), "--", *scope)
    return sum(int(line.rsplit(":", 1)[1]) for line in out.splitlines()) if out else 0


def counts(root: Path, ref: str | None) -> dict[str, int]:
    return {marker: grep_count(root, marker, ref) for marker in MARKERS}


def risen(before: dict[str, int], after: dict[str, int]) -> list[str]:
    """One line per marker whose count rose."""
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
