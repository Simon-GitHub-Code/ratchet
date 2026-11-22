#!/usr/bin/env python3
from __future__ import annotations

import argparse
import secrets
import shlex
import signal
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

import changed_tests
import gatelib
import suites

MAX_SEED = (1 << 31) - 1
SEED_PLACEHOLDER = "{seed}"
FILES_PLACEHOLDER = "{files}"


@dataclass(frozen=True)
class Config:
    root: Path
    base: str | None
    runs: int
    seeds: tuple[int, ...]
    changed: bool
    suites: tuple[str, ...]
    command: tuple[str, ...]


def positive_int(raw: str) -> int:
    try:
        value = int(raw)
    except ValueError as error:
        message = f"expected a positive integer, got {raw!r}"
        raise argparse.ArgumentTypeError(message) from error
    if value <= 0:
        message = f"expected a positive integer, got {raw!r}"
        raise argparse.ArgumentTypeError(message)
    return value


def parse_args(argv: list[str]) -> Config:
    parser = argparse.ArgumentParser(
        prog="test_stability.py",
        usage=(
            "%(prog)s [--root DIR] [--base REF] [--runs N] [--seed SEED]..."
            " [--changed] [--suite NAME]... -- COMMAND..."
        ),
    )
    parser.add_argument("--root", type=Path, default=Path())
    parser.add_argument("--base")
    parser.add_argument("--runs", type=positive_int, default=3)
    parser.add_argument("--seed", action="append", type=positive_int, default=[])
    parser.add_argument("--changed", action="store_true")
    parser.add_argument("--suite", action="append", default=[])
    rest = argv[1:]
    split = rest.index("--") if "--" in rest else len(rest)
    options = parser.parse_args(rest[:split])
    command = rest[split + 1 :]
    if not command:
        parser.error("no command: name it after '--'")
    if not any(SEED_PLACEHOLDER in token for token in command):
        parser.error(
            f"command must include {SEED_PLACEHOLDER} so a failing run prints a replay command"
        )
    if options.suite and not options.changed:
        parser.error("--suite requires --changed")
    return Config(
        root=options.root,
        base=options.base,
        runs=options.runs,
        seeds=tuple(options.seed),
        changed=options.changed,
        suites=tuple(options.suite),
        command=tuple(command),
    )


def choose_seeds(runs: int, explicit: tuple[int, ...]) -> list[int]:
    if explicit:
        return list(explicit)
    chosen: list[int] = []
    seen: set[int] = set()
    while len(chosen) < runs:
        candidate = secrets.randbelow(MAX_SEED) + 1
        if candidate not in seen:
            seen.add(candidate)
            chosen.append(candidate)
    return chosen


def scope_suites(
    every: list[suites.Suite], requested: tuple[str, ...]
) -> list[suites.Suite] | None:
    if not requested:
        return every
    known = {suite.name for suite in every}
    unknown = [name for name in requested if name not in known]
    if unknown:
        print(f"test_stability: red: unknown suite(s): {', '.join(unknown)}")
        return None
    wanted = set(requested)
    return [suite for suite in every if suite.name in wanted]


def select_changed(
    root: Path, base_ref: str | None, requested: tuple[str, ...]
) -> tuple[list[str] | None, int]:
    binding = gatelib.read_text(root / gatelib.BINDING)
    every, problems = suites.parse(binding)
    if every is None:
        print(f"test_stability: red: {gatelib.BINDING}: {'; '.join(problems)}")
        return None, 1
    scoped = scope_suites(every, requested)
    if scoped is None:
        return None, 1
    base = base_ref or gatelib.base_commit(root)
    if base is None:
        print("test_stability: green: there is no commit to compare the tree with; nothing is run")
        return None, 0
    by_suite, clashes = changed_tests.sort(changed_tests.changed_files(root, base), scoped, every)
    if clashes:
        print(f"test_stability: red: {gatelib.BINDING}: {'; '.join(clashes)}")
        return None, 1
    selected = sorted({path for suite in scoped for path in by_suite[suite.name]})
    if not selected:
        names = ", ".join(suite.name for suite in scoped) or "none"
        print(f"test_stability: green: no test file of the suites in scope ({names}) changed")
        return None, 0
    return selected, 0


def build_argv(command: tuple[str, ...], seed: int, files: list[str]) -> list[str]:
    rendered = [token.replace(SEED_PLACEHOLDER, str(seed)) for token in command]
    if not files:
        return rendered
    if FILES_PLACEHOLDER in rendered:
        expanded: list[str] = []
        for token in rendered:
            if token == FILES_PLACEHOLDER:
                expanded.extend(files)
            else:
                expanded.append(token)
        return expanded
    return [*rendered, *files]


def run_once(root: Path, argv: list[str], *, index: int, total: int, seed: int) -> int:
    print(f"test_stability: run {index}/{total} seed {seed}", flush=True)
    try:
        status = subprocess.run(argv, cwd=root, check=False).returncode
    except OSError as error:
        print(f"test_stability: cannot run {argv[0]}: {error}")
        print(f"replay: {shlex.join(argv)}")
        return 127
    if status == 0:
        return 0
    if status < 0:
        print(f"test_stability: killed by {signal.Signals(-status).name}")
        code = 128 - status
    else:
        code = status
    print(f"replay: {shlex.join(argv)}")
    print(f"test_stability: red: failed on run {index}/{total} with seed {seed}")
    return code


def main(argv: list[str]) -> int:
    config = parse_args(argv)
    root = config.root.resolve()
    files: list[str] = []
    if config.changed:
        selected, status = select_changed(root, config.base, config.suites)
        if selected is None:
            return status
        files = selected
    seeds = choose_seeds(config.runs, config.seeds)
    total = len(seeds)
    for index, seed in enumerate(seeds, start=1):
        code = run_once(
            root,
            build_argv(config.command, seed, files),
            index=index,
            total=total,
            seed=seed,
        )
        if code != 0:
            return code
    print(f"test_stability: green: {total} shuffled runs passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
