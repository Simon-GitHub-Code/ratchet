#!/usr/bin/env python3
"""The commits gate: every subject since the merge base follows the convention.

A subject is ``type(scope): summary`` or ``type: summary``, with a type from the conventional
set, a lower-case scope, a summary that starts with a non-space character, no trailing full
stop, and at most 72 characters. The `` (#12)`` a forge appends to a squash merge's subject is
not counted: the author wrote the rest, and the number is added after the last gate has run.
Merge commits are skipped.

With ``--message-file`` it checks the one message being written instead, as git's ``commit-msg``
hook: the history check at the ``pre-commit`` stage would judge the commit an amend replaces, and
cannot see the new subject at all.

Usage: ``commits.py [--root DIR] [--base REF]`` or ``commits.py --message-file PATH``.
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
# The pull request number a forge's squash merge writes after the title, at the very end.
SQUASH_NUMBER = re.compile(r" \(#\d+\)$")


def faults(subject: str) -> list[str]:
    found = []
    if not SUBJECT.match(subject):
        found.append(f"'{subject}': not `type(scope): summary` with a type in {', '.join(TYPES)}")
    written, number = SQUASH_NUMBER.sub("", subject), SQUASH_NUMBER.search(subject)
    if len(written) > MAX_SUBJECT:
        uncounted = f", not counting '{number.group()}'" if number else ""
        found.append(
            f"'{subject[:30]}...': {len(written)} characters, at most {MAX_SUBJECT}{uncounted}"
        )
    if subject.endswith("."):
        found.append(f"'{subject}': ends with a full stop")
    return found


def problems(subjects: list[str]) -> list[str]:
    return [fault for subject in subjects for fault in faults(subject)]


def subject_of(message: str) -> str | None:
    """The first line git keeps: comment lines and leading blank lines are stripped."""
    for line in message.splitlines():
        if line.strip() and not line.startswith("#"):
            return line.rstrip()
    return None


def merging(root: Path) -> bool:
    return gatelib.git(root, "rev-parse", "-q", "--verify", "MERGE_HEAD") is not None


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", default=".")
    parser.add_argument("--base")
    parser.add_argument("--message-file", type=Path)
    options = parser.parse_args(argv[1:])
    root = Path(options.root).resolve()
    if options.message_file is not None:
        subject = subject_of(options.message_file.read_text(encoding="utf-8", errors="replace"))
        # Why: the history check skips merge commits, so the message git writes for one is not
        # judged here either.
        checked = [] if subject is None or merging(root) else [subject]
        return gatelib.report("commits", problems(checked))
    base = gatelib.base_commit(root, options.base)
    if base is None:
        head = gatelib.git(root, "log", "-1", "--format=%s") or ""
        return gatelib.report("commits", problems([head] if head else []))
    return gatelib.report("commits", problems(gatelib.subjects(root, base)), base)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
