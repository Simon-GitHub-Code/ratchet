#!/usr/bin/env python3
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
    tracked = listed(root, "diff", "--name-only", "-z", "--no-renames", "--diff-filter=d", base)
    new = listed(root, "ls-files", "-z", "--others", "--exclude-standard")
    return sorted(name for name in {*tracked, *new} if (root / name).is_file())


def tracked_files(root: Path) -> list[str]:
    found = listed(root, "ls-files", "-z", "--cached", "--others", "--exclude-standard")
    return sorted({name for name in found if (root / name).is_file()})


def later(rows: list[project_bite.Row], suite: suites.Suite) -> bool:
    return any(row.command == suite.gate and row.tier not in TIER_ZERO_ONE for row in rows)


def in_scope(
    rows: list[project_bite.Row], listed_suites: list[suites.Suite], *, everything: bool
) -> list[suites.Suite]:
    deferred = [suite for suite in listed_suites if later(rows, suite)]
    return listed_suites if everything or not deferred else deferred


def _clash(path: str, owners: list[suites.Suite]) -> str:
    names = ", ".join(f"'{suite.name}'" for suite in owners)
    return f"{path} matches the suites {names}; a test file belongs to one suite"


def sort(
    found: list[str], scoped: list[suites.Suite], every: list[suites.Suite]
) -> tuple[dict[str, list[str]], list[str]]:
    by_suite: dict[str, list[str]] = {suite.name: [] for suite in scoped}
    clashes: list[str] = []
    for path in found:
        owners = suites.owners(every, path)
        if len(owners) > 1:
            clashes.append(_clash(path, owners))
        elif owners and owners[0].name in by_suite:
            by_suite[owners[0].name].append(path)
    return by_suite, clashes


def merge_impacts(
    by_suite: dict[str, list[str]],
    selected: dict[str, list[str]],
    every: list[suites.Suite],
) -> tuple[dict[str, int], list[str]]:
    added: dict[str, int] = {}
    clashes: list[str] = []
    for suite_name, paths in selected.items():
        if suite_name not in by_suite:
            continue
        direct = set(by_suite[suite_name])
        merged = set(direct)
        for path in paths:
            owners = suites.owners(every, path)
            if len(owners) > 1:
                clashes.append(_clash(path, owners))
            elif owners and owners[0].name == suite_name:
                merged.add(path)
        by_suite[suite_name] = sorted(merged)
        added[suite_name] = len(merged) - len(direct)
    return added, clashes


def _file_count(total: int, impacted_count: int) -> str:
    plural = "s" if total != 1 else ""
    if not impacted_count:
        return f"{total} changed file{plural}"
    return f"{total} selected file{plural} ({impacted_count} by impact)"


def run_suite(root: Path, suite: suites.Suite, files: list[str], *, impacted_count: int = 0) -> int:
    count = _file_count(len(files), impacted_count)
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
    impacts, impact_problems = suites.parse_impacts(binding, every)
    if impacts is None:
        return refuse(impact_problems)
    base = options.base or gatelib.base_commit(root)
    if base is None:
        print("changed_tests: green: there is no commit to compare the tree with; nothing is run")
        return 0
    rows = project_bite.gate_rows(binding or "")
    scoped = in_scope(rows, every, everything=options.everything == ALL)
    changed = changed_files(root, base)
    by_suite, clashes = sort(changed, scoped, every)
    tracked = tracked_files(root) if impacts else []
    impact_counts, impact_clashes = merge_impacts(
        by_suite, suites.impacted(impacts, changed, tracked), every
    )
    if clashes or impact_clashes:
        return refuse([*clashes, *impact_clashes])
    status = 0
    ran = 0
    for suite in scoped:
        if files := by_suite[suite.name]:
            ran += 1
            result = run_suite(root, suite, files, impacted_count=impact_counts.get(suite.name, 0))
            status = status or result
    if not ran:
        names = ", ".join(suite.name for suite in scoped) or "none"
        print(f"changed_tests: green: no test file of the suites in scope ({names}) changed")
    verdict = "green" if status == 0 else "red"
    print(f"changed_tests: {verdict}: {ran} of {len(scoped)} suites had changed test files")
    return status


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
