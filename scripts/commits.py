#!/usr/bin/env python3
"""The commits gate: every subject since the merge base follows the convention.

A subject is ``type(scope): summary`` or ``type: summary``, with a type from the conventional
set, a lower-case scope, a summary that starts with a non-space character, no trailing full
stop, and at most 72 characters. Merge commits are skipped.

Usage: ``commits.py [--root DIR] [--base REF]``.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

import gatelib

TYPES = (
    "build",
    "chore",
    "ci",
    "docs",
    "feat",
    "fix",
    "perf",
    "refactor",
    "revert",
    "style",
    "test",
)
SUBJECT = re.compile(rf"^(?:{'|'.join(TYPES)})(?:\([a-z0-9][a-z0-9/._-]*\))?!?: \S.*$")
MAX_SUBJECT = 72


def faults(subject: str) -> list[str]:
    found = []
    if not SUBJECT.match(subject):
        found.append(f"'{subject}': not `type(scope): summary` with a type in {', '.join(TYPES)}")
    if len(subject) > MAX_SUBJECT:
        found.append(f"'{subject[:30]}...': {len(subject)} characters, at most {MAX_SUBJECT}")
    if subject.endswith("."):
        found.append(f"'{subject}': ends with a full stop")
    return found


def problems(subjects: list[str]) -> list[str]:
    return [fault for subject in subjects for fault in faults(subject)]


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", default=".")
    parser.add_argument("--base")
    options = parser.parse_args(argv[1:])
    root = Path(options.root).resolve()
    base = gatelib.base_commit(root, options.base)
    if base is None:
        head = gatelib.git(root, "log", "-1", "--format=%s") or ""
        return gatelib.report("commits", problems([head] if head else []))
    return gatelib.report("commits", problems(gatelib.subjects(root, base)), base)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
