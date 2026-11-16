#!/usr/bin/env python3
"""Run the test files a change adds or edits, in every suite that can run them (ADR 0073).

The binding's suites table (``## Tests``) says which globs make a suite, the gate that runs it
whole, and the recipe that runs given files of it. This lists the test files the change adds or
edits against the base: tracked changes and the files git would add, never a deleted file. It sorts
them by suite and runs each suite's files through its recipe, so a role, a developer and CI run
a suite's files with the environment and the build of the gate itself.

By default it runs the suites whose whole run sits in a later tier than tier 1, since the tier's
own gates run the others whole; where none does, every suite. ``all`` runs every suite's changed
files. A suite that has no way to run files says why and is not run. A file that two suites
claim is red: which suite it belongs to is the binding's to say.

Usage: ``changed_tests.py [--root DIR] [--base COMMIT] [all]``. The base is the one every gate
over the change takes (``gatelib.base_commit``). Exits 0 when every run was green or no test file
of a suite in scope changed, 1 when a suite's run failed or the table does not read, and 127 when a
recipe cannot start.
"""

from __future__ import annotations

import argparse
import signal
import subprocess
import sys
from pathlib import Path

import gatelib
import project_bite
import suites

ALL = "all"
TIER_ZERO_ONE = ("0", "1")


def listed(root: Path, *args: str) -> list[str]:
    done = subprocess.run(
        ["git", "-C", str(root), *args], capture_output=True, check=True, text=True
    )
    return [name for name in done.stdout.split("\0") if name]


def changed_files(root: Path, base: str) -> list[str]:
    """The files the change adds or edits since ``base``, and the files git would add."""
    tracked = listed(root, "diff", "--name-only", "-z", "--no-renames", "--diff-filter=d", base)
    new = listed(root, "ls-files", "-z", "--others", "--exclude-standard")
    return sorted(name for name in {*tracked, *new} if (root / name).is_file())


def later(rows: list[project_bite.Row], suite: suites.Suite) -> bool:
    return any(row.command == suite.gate and row.tier not in TIER_ZERO_ONE for row in rows)


def in_scope(
    rows: list[project_bite.Row], listed_suites: list[suites.Suite], *, everything: bool
) -> list[suites.Suite]:
    deferred = [suite for suite in listed_suites if later(rows, suite)]
    return listed_suites if everything or not deferred else deferred


def sort(
    found: list[str], scoped: list[suites.Suite], every: list[suites.Suite]
) -> tuple[dict[str, list[str]], list[str]]:
    """Each in-scope suite's changed files, and a line for each file two suites claim."""
    by_suite: dict[str, list[str]] = {suite.name: [] for suite in scoped}
    clashes: list[str] = []
    for path in found:
        owners = suites.owners(every, path)
        if len(owners) > 1:
            names = ", ".join(f"'{suite.name}'" for suite in owners)
            clashes.append(f"{path} matches the suites {names}; a test file belongs to one suite")
        elif owners and owners[0].name in by_suite:
            by_suite[owners[0].name].append(path)
    return by_suite, clashes


def run_suite(root: Path, suite: suites.Suite, files: list[str]) -> int:
    count = f"{len(files)} changed file{'s' if len(files) != 1 else ''}"
    if not suite.run:
        print(
            f"changed_tests: {suite.name}: {count} not run, since the suite cannot run by file:"
            f" {suite.reason}; `{suite.gate}` runs it whole"
        )
        return 0
    print(f"changed_tests: {suite.name}: running {count} through `{suite.run}`")
    argv = [*suite.run.split(), *files]
    try:
        status = subprocess.run(argv, cwd=root, check=False).returncode
    except OSError as error:
        print(f"changed_tests: {suite.name}: cannot run {argv[0]}: {error}")
        return 127
    if status < 0:
        print(f"changed_tests: {suite.name}: killed by {signal.Signals(-status).name}")
        return 128 - status
    return status


def refuse(problems: list[str]) -> int:
    print(f"changed_tests: red: {gatelib.BINDING}: {'; '.join(problems)}")
    return 1


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="changed_tests.py")
    parser.add_argument("everything", nargs="?", choices=[ALL])
    parser.add_argument("--root", type=Path, default=Path())
    parser.add_argument("--base")
    options = parser.parse_args(argv[1:])
    root = options.root.resolve()
    binding = gatelib.read_text(root / gatelib.BINDING)
    every, problems = suites.parse(binding)
    if every is None:
        return refuse(problems)
    base = options.base or gatelib.base_commit(root)
    if base is None:
        print("changed_tests: green: there is no commit to compare the tree with; nothing is run")
        return 0
    rows = project_bite.gate_rows(binding or "")
    scoped = in_scope(rows, every, everything=options.everything == ALL)
    by_suite, clashes = sort(changed_files(root, base), scoped, every)
    if clashes:
        return refuse(clashes)
    status = 0
    ran = 0
    for suite in scoped:
        if files := by_suite[suite.name]:
            ran += 1
            result = run_suite(root, suite, files)
            status = status or result
    if not ran:
        names = ", ".join(suite.name for suite in scoped) or "none"
        print(f"changed_tests: green: no test file of the suites in scope ({names}) changed")
    verdict = "green" if status == 0 else "red"
    print(f"changed_tests: {verdict}: {ran} of {len(scoped)} suites had changed test files")
    return status


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
