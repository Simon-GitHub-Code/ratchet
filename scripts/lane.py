#!/usr/bin/env python3
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
    "CHANGELOG.md": (TESTS.format("release"),),
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
