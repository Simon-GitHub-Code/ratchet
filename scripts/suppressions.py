#!/usr/bin/env python3
"""The suppressions gate: no kind of suppression rises, and no focus marker exists.

Counts each kind of tool directive that silences a check (``noqa``, ``type: ignore``,
``pragma: no cover``, ``eslint-disable``, ``@ts-ignore`` and their peers) at the merge base and
at head, and fails when any count rose. A focus marker (``.only(``, ``fit(``, ``fdescribe(``)
turns a suite into the one test somebody was looking at, so one at head fails outright.

Usage: ``suppressions.py [--root DIR] [--base REF]``.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import gatelib
from markers import grep_count

KINDS = {
    "noqa": r"#\s*noqa",
    "type-ignore": r"#\s*type:\s*ignore",
    "no-cover": r"pragma:\s*no\s*cover",
    "no-mutate": r"pragma:\s*no\s*mutate",
    "pylint-disable": r"pylint:\s*disable",
    "eslint-disable": r"eslint-disable",
    "ts-ignore": r"@ts-(ignore|expect-error|nocheck)",
    "coverage-ignore": r"(istanbul|c8|v8)\s+ignore",
    "skipped-test": (
        r"(\b(it|test|describe|context|suite|bench)\.skip\(|mark\.skip|@skip\b"
        r"|\bxit\(|\bxdescribe\(|\bxtest\(|#\[ignore|\b[tb]\.Skip\()"
    ),
    "nolint": r"\bnolint\b",
    "allow": r"#!?\[(allow|expect)\(",
    "nosec": r"\b(nosec|NOSONAR)\b",
}
FOCUS = r"\.only\(|\bfit\(|\bfdescribe\(|\bfcontext\("


def counts(root: Path, ref: str | None) -> dict[str, int]:
    return {kind: grep_count(root, pattern, ref, word=False) for kind, pattern in KINDS.items()}


def risen(before: dict[str, int], after: dict[str, int]) -> list[str]:
    return [
        f"{kind}: rose from {before[kind]} to {after[kind]}"
        for kind in KINDS
        if after[kind] > before[kind]
    ]


def focused(root: Path) -> list[str]:
    """Each focus marker in the work tree, as ``path:line: text``."""
    out = gatelib.git(root, "grep", "-I", "-n", "-E", "--untracked", FOCUS, "--", ".")
    return [f"focus marker at {line}" for line in (out or "").splitlines()]


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", default=".")
    parser.add_argument("--base")
    options = parser.parse_args(argv[1:])
    root = Path(options.root).resolve()
    base = gatelib.base_commit(root, options.base)
    problems = focused(root)
    if base is not None:
        problems += risen(counts(root, base), counts(root, None))
    return gatelib.report("suppressions", problems, base)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
