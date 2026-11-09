#!/usr/bin/env python3
"""The change-size gate: a change stays within the files and lines ceiling.

Measures the files and the added-plus-deleted lines changed against the merge base, untracked
files included, and compares them with ``[ceilings.change]`` in ``quality/thresholds.toml``
(``files`` and ``lines``; ceilings only fall, which the lowered gate enforces). Without a
recorded ceiling the gate is green and says so; ``--set --files N --lines N`` records one.

The adoption change is large by construction: it lands the templates, the binding and the
records in one commit. When every commit in the range is ``chore(method): adopt``, the gate
reports the size and passes, and the pull request's Criticism section carries the number.

Usage: ``change_size.py [--root DIR] [--base REF] [--set --files N --lines N]``.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

import gatelib

ADOPTION = "chore(method): adopt"
CEILINGS_TABLE = "[ceilings.change]"


def measure(root: Path, base: str) -> tuple[int, int]:
    """Changed files and changed lines (added plus deleted) since ``base``."""
    files, lines = 0, 0
    for row in (gatelib.git(root, "diff", "--numstat", "--no-renames", base) or "").splitlines():
        added, deleted, _ = row.split("\t", 2)
        files += 1
        lines += (int(added) if added != "-" else 0) + (int(deleted) if deleted != "-" else 0)
    untracked = gatelib.git(root, "ls-files", "--others", "--exclude-standard", "-z", ":/") or ""
    for path in (p for p in untracked.split("\0") if p):
        text = gatelib.read_text(root / path)
        files += 1
        lines += len(text.splitlines()) if text is not None else 0
    return files, lines


def _limit(value: object) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else None


def ceilings(root: Path) -> tuple[int | None, int | None]:
    table = gatelib.table(gatelib.thresholds(root), "ceilings", "change")
    return _limit(table.get("files")), _limit(table.get("lines"))


def malformed(root: Path) -> list[str]:
    """Each ``files`` or ``lines`` ceiling that is present but not a non-negative integer."""
    table = gatelib.table(gatelib.thresholds(root), "ceilings", "change")
    return [
        f"ceilings.change.{name}: {table[name]!r} is not a non-negative integer"
        for name in ("files", "lines")
        if name in table and _limit(table[name]) is None
    ]


def over(measured: tuple[int, int], limits: tuple[int | None, int | None]) -> list[str]:
    problems = []
    for name, value, limit in zip(("files", "lines"), measured, limits, strict=True):
        if limit is not None and value > limit:
            problems.append(f"{name}: {value} changed, ceiling {limit}")
    return problems


def is_adoption(root: Path, base: str) -> bool:
    found = gatelib.subjects(root, base)
    return bool(found) and all(s.startswith(ADOPTION) for s in found)


def merged_on_default(root: Path) -> bool:
    """A clean default branch whose HEAD is a merge: the change was judged before its merge."""
    head = gatelib.git(root, "rev-parse", "--abbrev-ref", "HEAD")
    default = gatelib.default_branches(root)[0].removeprefix("origin/")
    clean = not gatelib.git(root, "status", "--porcelain")
    merge = gatelib.git(root, "rev-parse", "--verify", "--quiet", "HEAD^2") is not None
    return head == default and clean and merge


def exemption(root: Path, base: str) -> str | None:
    """Why the size is reported only, or ``None`` when it is judged."""
    if merged_on_default(root):
        return "HEAD merges a change the owner already judged; size reported only"
    if is_adoption(root, base):
        return "the adoption commit is exempt; carry the size in the PR's Criticism"
    return None


def with_ceilings(text: str, files: int, lines: int) -> str:
    """The thresholds text with ``[ceilings.change]`` set to ``files`` and ``lines``."""
    body = f"{CEILINGS_TABLE}\nfiles = {files}\nlines = {lines}\n"
    pattern = re.compile(r"^\[ceilings\.change\]\n(?:[^\[\n][^\n]*\n|\n)*", re.MULTILINE)
    if pattern.search(text):
        return pattern.sub(body + "\n", text).rstrip("\n") + "\n"
    return (text.rstrip("\n") + "\n\n" + body) if text.strip() else body


def record(root: Path, files: int, lines: int) -> int:
    path = root / gatelib.THRESHOLDS
    path.parent.mkdir(parents=True, exist_ok=True)
    current = path.read_text(encoding="utf-8") if path.is_file() else ""
    path.write_text(with_ceilings(current, files, lines), encoding="utf-8")
    print(f"change-size: ceiling recorded, {files} files and {lines} lines")
    return 0


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", default=".")
    parser.add_argument("--base")
    parser.add_argument("--set", action="store_true")
    parser.add_argument("--files", type=int)
    parser.add_argument("--lines", type=int)
    options = parser.parse_args(argv[1:])
    root = Path(options.root).resolve()
    if options.set:
        if options.files is None or options.lines is None:
            parser.error("--set needs --files N and --lines N")
        return record(root, options.files, options.lines)
    if problems := malformed(root):
        return gatelib.report("change-size", problems)
    base = gatelib.base_commit(root, options.base)
    if base is None:
        print("change-size: no base commit yet")
        return 0
    files, lines = measure(root, base)
    limits = ceilings(root)
    print(f"change-size: {files} files, {lines} lines changed")
    if limits == (None, None):
        print("change-size: no ceiling recorded; `--set --files N --lines N` records one")
        return 0
    if reason := exemption(root, base):
        print(f"change-size: {reason}")
        return 0
    return gatelib.report("change-size", over((files, lines), limits), base)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
