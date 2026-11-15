#!/usr/bin/env python3
"""Say which lane CI runs a pull request in: ``pages`` or ``full``.

A change whose every path is a decision record or a page has no code for the full suite to judge,
so it runs only the tests that read those files (``pages_tests`` in the justfile). The one job
that reports under each required name stays: it runs the light path instead of the full one.

Classification is an allow-list. Each path changed against the merge base with the base, a
rename or deletion counted by both its paths, must match ``PAGES``; one that does not, an empty
diff, a git failure and an output that cannot be read each send the change to ``full``. The
script, the justfile and ``.github`` match nothing, so a change to any of them is always ``full``.
Skill pages stay out: ``test_skills``, ``test_conformance``, ``test_templates``, ``test_owed`` and
``test_upgrade`` read them, along with the templates under them, and the light path does not run
that set.

Usage: ``lane.py [BASE]``. Prints ``pages`` or ``full``; no ``BASE`` is ``full``. Exits 0.
"""

from __future__ import annotations

import re
import subprocess
import sys

PAGES = (
    "docs/adr/*.md",
    "docs/**/*.md",
    "README.md",
    "CHANGELOG.md",
)
PAGES_LANE = "pages"
FULL_LANE = "full"


def pattern(glob: str) -> re.Pattern[str]:
    out = ""
    rest = glob
    while rest:
        if rest.startswith("**/"):
            out += "(?:[^/\\0]+/)*"
            rest = rest[3:]
        elif rest[0] == "*":
            out += "[^/\\0]*"
            rest = rest[1:]
        else:
            out += re.escape(rest[0])
            rest = rest[1:]
    return re.compile(out)


ALLOWED = tuple(pattern(glob) for glob in PAGES)


def lane_of(paths: list[str]) -> str:
    if paths and all(any(rule.fullmatch(path) for rule in ALLOWED) for path in paths):
        return PAGES_LANE
    return FULL_LANE


def git(*args: str) -> bytes | None:
    try:
        done = subprocess.run(["git", *args], capture_output=True, check=False)
    except OSError:
        return None
    return done.stdout if done.returncode == 0 else None


def changed(base: str) -> list[str] | None:
    merge_base = git("merge-base", base, "HEAD")
    if merge_base is None:
        return None
    start = merge_base.decode(errors="replace").strip()
    listing = git("diff", "--name-only", "--no-renames", "-z", start, "HEAD")
    if listing is None or (listing and not listing.endswith(b"\0")):
        return None
    try:
        return [raw.decode("utf-8") for raw in listing.split(b"\0")[:-1]]
    except UnicodeDecodeError:
        return None


def lane_for(base: str) -> str:
    paths = changed(base) if base else None
    return lane_of(paths) if paths is not None else FULL_LANE


def main(argv: list[str]) -> int:
    print(lane_for(argv[1] if len(argv) > 1 else ""))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
