#!/usr/bin/env python3
"""Say which lane CI runs a pull request in, and the tests it owes.

A change whose every path is a decision record or a page has no code for the full suite to judge.
It runs only the tests that read the pages it changed, and the job that reports under each
required name stays: it runs that short list instead of the full path.

``PAGES`` is the one mapping, an allow-list: each glob is a page CI knows, with the test files
that read it, none for a page no test reads. ``test_skills`` reads the README, every page under
``docs/`` but the records, and ADR 0034. Each path changed against the merge base with the
base, a rename or deletion counted by both its paths, must match one or more; the change then owes
the union of their tests. A path that matches none (a new directory under ``docs/``, a new
model page, a file that is not a page), an empty diff, a git failure and an output that cannot be
read each send the change to ``full``. The script, the justfile and ``.github`` match nothing,
so a change to any of them is always ``full``. Skill pages stay out: ``test_skills``,
``test_conformance``, ``test_templates``, ``test_owed`` and ``test_upgrade`` read them, along with
the templates under them, and a skills change would owe all of those.

Usage: ``lane.py [BASE]``. Prints ``full``, or ``pages`` followed by the test files; no ``BASE`` is
``full``. Exits 0.
"""

from __future__ import annotations

import re
import subprocess
import sys

TESTS = "hooks/tests/test_{}.py"
PROSE = TESTS.format("skills")
PAGES: dict[str, tuple[str, ...]] = {
    "docs/adr/*.md": (),
    "docs/adr/0034-*.md": (PROSE,),
    "docs/*.md": (PROSE,),
    "docs/harness/*.md": (PROSE,),
    "docs/research/*.md": (PROSE,),
    "docs/model/README.md": (PROSE,),
    "docs/acceptance.md": (TESTS.format("acceptance_record"),),
    "docs/model/drain.md": (PROSE, TESTS.format("drain_model")),
    "docs/model/pipeline.md": (PROSE, TESTS.format("pipeline_model")),
    "docs/model/ratchet.md": (PROSE, TESTS.format("ratchet_model")),
    "docs/model/route.md": (PROSE, TESTS.format("route_model")),
    "docs/model/safeguard.md": (PROSE, TESTS.format("safeguard_model")),
    "README.md": (PROSE,),
    "CHANGELOG.md": (),
}
FULL_LANE = "full"
PAGES_LANE = "pages"


def pattern(glob: str) -> re.Pattern[str]:
    return re.compile("".join("[^/\\0]*" if char == "*" else re.escape(char) for char in glob))


RULES = tuple((pattern(glob), tests) for glob, tests in PAGES.items())


def owed(paths: list[str]) -> list[str] | None:
    tests: set[str] = set()
    for path in paths:
        hits = [rule_tests for rule, rule_tests in RULES if rule.fullmatch(path)]
        if not hits:
            return None
        tests.update(*hits)
    return sorted(tests) if paths else None


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
    tests = owed(paths) if paths is not None else None
    return FULL_LANE if tests is None else " ".join([PAGES_LANE, *tests])


def main(argv: list[str]) -> int:
    print(lane_for(argv[1] if len(argv) > 1 else ""))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
