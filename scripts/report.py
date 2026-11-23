#!/usr/bin/env python3
from __future__ import annotations

import argparse
import fcntl
import json
import os
import re
import shlex
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

import gatelib
import markdown_text
import runrecord

SECTION = "## Shared reports"
SCRIPT = "scripts/report.py"
COLUMNS = ("Report", "Producer", "Paths", "Readers", "Judged by")
NONE_PREFIX = "none:"
MIN_REASON = 10
MIN_READERS = 2
PLACEHOLDER = (
    "the shared reports table still has the template's placeholder row; bind `suite-report` first"
    " to run the suite once, then bind the `test`, `coverage`, `diff-coverage` and `test-counts`"
    " gates as readers of the shared suite report"
)
RUN = runrecord.RUN
FOLDER = gatelib.STATE / "reports"
NAME = re.compile(r"[A-Za-z][\w-]*")
COMMAND = re.compile(r"just ([A-Za-z_][\w-]*)(?: .*)?")
content_id = runrecord.content_id


@dataclass(frozen=True)
class Report:
    name: str
    producer: str
    paths: tuple[str, ...]
    readers: tuple[str, ...]
    judge: str


def recipe_of(command: str) -> str:
    match = COMMAND.fullmatch(command)
    return match.group(1) if match else ""


def _row(row: tuple[str, ...], problems: list[str]) -> Report | None:
    name, producer, paths, readers, judged = (cell.strip() for cell in row)
    name = name.strip("`")
    if any(cell.startswith("<") for cell in row):
        problems.append(PLACEHOLDER)
        return None
    if name.lower().startswith(NONE_PREFIX):
        if len(name[len(NONE_PREFIX) :].strip()) < MIN_REASON:
            problems.append("a shared reports row says none with no reason: say why")
        return None
    commands = markdown_text.ticked(producer)
    listed = tuple(markdown_text.ticked(readers))
    files = tuple(markdown_text.ticked(paths))
    if (
        not NAME.fullmatch(name)
        or len(commands) != 1
        or not recipe_of(commands[0])
        or not files
        or not listed
        or not all(recipe_of(reader) for reader in listed)
    ):
        problems.append(
            f"the shared reports row '{name}' needs a name, one `just <recipe>` producer,"
            " backticked paths and backticked `just <recipe>` readers"
        )
        return None
    judges = markdown_text.ticked(judged)
    if len(judges) != 1 or judges[0] not in listed:
        problems.append(
            f"the shared reports row '{name}' needs exactly one judge in `Judged by`,"
            " and it is one of the row's readers"
        )
        return None
    if len(listed) < MIN_READERS:
        problems.append(
            f"the shared reports row '{name}' has fewer than two readers, so it is not shared:"
            " say `none: <reason>` in its Report cell"
        )
        return None
    return Report(name, commands[0], files, listed, judges[0])


def parse(binding: str | None) -> tuple[list[Report] | None, list[str]]:
    if binding is None or not markdown_text.has_section(binding, SECTION):
        return None, [f"has no '{SECTION}' section"]
    found = [t for t in markdown_text.tables(binding, SECTION) if t.header == COLUMNS]
    if not found:
        return None, [f"'{SECTION}' lacks its table with the columns {' | '.join(COLUMNS)}"]
    problems: list[str] = []
    reports = [report for row in found[0].rows if (report := _row(row, problems))]
    names = [report.name for report in reports]
    if len(set(names)) != len(names):
        problems.append("two shared reports rows have one name")
    return (None if problems else reports), problems


def producers(binding: str) -> list[tuple[str, str]]:
    found = [t for t in markdown_text.tables(binding, SECTION) if t.header == COLUMNS]
    return [
        (row[0].strip("` "), recipe_of(command))
        for row in (found[0].rows if found else ())
        for command in markdown_text.ticked(row[1])[:1]
        if not row[0].lower().startswith(NONE_PREFIX) and recipe_of(command)
    ]


def held(record: Path) -> tuple[str, str, int] | None:
    try:
        found = json.loads(record.read_text(encoding="utf-8"))
        return str(found["run"]), str(found["content"]), int(found["status"])
    except (OSError, ValueError, KeyError, TypeError):
        return None


def obtain(root: Path, report: Report) -> tuple[int, Path]:
    shard = os.environ.get("SHARD", "1")
    shards = os.environ.get("SHARDS", "1")
    folder = root / FOLDER
    folder.mkdir(parents=True, exist_ok=True)
    key = f"{report.name}-{shard}of{shards}"
    with (folder / f"{key}.lock").open("w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        run = os.environ.get(RUN, "")
        content = content_id(root)
        record = folder / f"{key}.json"
        log = folder / f"{key}.log"
        stored = held(record)
        if run and stored and stored[:2] == (run, content):
            return stored[2], log
        if run and stored and stored[0] == run:
            print(
                f"report {report.name}: produced again, because the tree's content changed since"
                " this run produced it",
                file=sys.stderr,
            )
        record.unlink(missing_ok=True)
        with log.open("wb") as output:
            status = subprocess.run(
                shlex.split(report.producer),
                cwd=root,
                stdout=output,
                stderr=subprocess.STDOUT,
                pass_fds=(lock.fileno(),),
                check=False,
            ).returncode
        record.write_text(
            json.dumps({"run": run, "content": content, "status": status}), encoding="utf-8"
        )
        if run:
            runrecord.append(
                root / runrecord.TIMING,
                {
                    "kind": runrecord.PRODUCER,
                    "report": report.name,
                    "producer": recipe_of(report.producer),
                    **runrecord.stamp(root, content),
                },
                root=root,
                who="report",
            )
        return status, log


def choose(root: Path, args: argparse.Namespace) -> tuple[Report | None, int]:
    reports, problems = parse(gatelib.read_text(root / gatelib.BINDING))
    if reports is None:
        print(f"report: {gatelib.BINDING}: {'; '.join(problems)}", file=sys.stderr)
        return None, 1
    report = next((found for found in reports if found.name == args.report), None)
    if report is None:
        print(f"report: '{SECTION}' names no report '{args.report}'", file=sys.stderr)
        return None, 1
    if args.reader not in map(recipe_of, report.readers):
        print(
            f"report {report.name}: '{args.reader}' is not one of its readers in '{SECTION}'",
            file=sys.stderr,
        )
        return None, 2
    if args.path is not None and args.path not in report.paths:
        print(
            f"report {report.name}: '{args.path}' is not a path the {report.name} row of"
            f" {SECTION} in {gatelib.BINDING} lists",
            file=sys.stderr,
        )
        return None, 2
    return report, 0


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="report.py")
    parser.add_argument("report")
    parser.add_argument("--reader", required=True)
    parser.add_argument("--path")
    parser.add_argument("--root", type=Path, default=Path())
    args = parser.parse_args(argv)
    root = args.root.resolve()
    report, refused = choose(root, args)
    if report is None:
        return refused
    wanted = report.paths if args.path is None else (args.path,)
    status, log = obtain(root, report)
    if status:
        judge = recipe_of(report.judge)
        if args.reader == judge:
            print(log.read_text(encoding="utf-8", errors="replace"), end="", file=sys.stderr)
        print(
            f"report {report.name}: `{report.producer}` exited {status}; `{judge}` judges the"
            " failure",
            file=sys.stderr,
        )
        return 1
    missing = [path for path in wanted if not (root / path).exists()]
    if missing:
        print(
            f"report {report.name}: `{report.producer}` wrote no {', '.join(missing)}",
            file=sys.stderr,
        )
        return 1
    print("\n".join(wanted))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
