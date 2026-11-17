#!/usr/bin/env python3
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
        checked = [] if subject is None or merging(root) else [subject]
        return gatelib.report("commits", problems(checked))
    base = gatelib.base_commit(root, options.base)
    if base is None:
        head = gatelib.git(root, "log", "-1", "--format=%s") or ""
        return gatelib.report("commits", problems([head] if head else []))
    return gatelib.report("commits", problems(gatelib.subjects(root, base)), base)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
