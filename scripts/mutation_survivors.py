#!/usr/bin/env python3
"""The mutation gate: zero unregistered survivors, and no stale entry in the register.

The mutation tool's score is a trend, not a gate; this gate is the register. It reads what the
tool found and ``quality/mutation-register.toml``, and goes red on:

- a live mutant (``survived`` or ``uncovered``) with no register entry matching its tool,
  identity and function hash;
- a register entry no live mutant matches: the mutant is killed, gone, or its function changed,
  so the equivalence claim is stale and is reviewed again;
- a mutant the run did not decide (``undecided``), or a run that reported no mutant at all: a
  cache that hid the run is a run that did not happen;
- a mutant that timed out and has a register entry: the run could not tell it from a survivor.
  A timeout nobody registered counts as a kill, as mutation tools count it;
- a run in which every decided mutant was ``unviable``: none met a test. An unviable mutant is
  never a kill; the gate counts them apart and prints the count on every run that has one;
- an entry without the tool, the identity, a ``sha256:`` hash, a reason of twenty characters or
  more and a date.

Any mutation tool feeds the gate through one input, ``--tool NAME --mutants FILE``: JSON lines,
one object per mutant the run made. ``NAME`` is the register's ``tool`` key and is yours to choose.

    {"file": "src/lib.rs", "mutator": "BinaryOperator", "replacement": "-",
     "status": "survived", "start": [18, 11], "end": [18, 12], "unit": [17, 19]}

- ``file``: the path from the repository root; the gate reads the file to hash the unit.
- ``mutator``, ``replacement``: what the tool calls the change and what it wrote.
- ``status``: ``killed``, ``survived``, ``uncovered`` (no test reaches it), ``timeout``,
  ``undecided`` (the run did not finish it), ``unviable`` (the mutant did not build or load, so
  no test met it) or ``skipped`` (not run on purpose: the tool was told to leave it).
- ``start``, ``end``: the mutated text's ``[line, column]``, lines from 1 as the file counts
  them; ``end`` defaults to ``start``.
- ``unit``: the ``[first, last]`` line of the function or declaration the mutant sits in, when
  the tool says. Without it the gate takes the enclosing top-level declaration: from the nearest
  line above that starts in column one to the next such line or closing bracket.
- ``duration``: optional finite non-negative seconds spent testing the mutant; when present
  with positive total time, the gate prints a slowest-10% and top-files profile line.
- ``tests_completed``: optional finite non-negative count of tests completed before deciding
  the mutant; when present, the gate prints the mean tests per mutant.

A tool's own report becomes these lines in a gate tool of a few lines, which the repository's
agent writes for the tool it chose, guards and bites like any other: the plugin reads no tool's
report and names no tool (ADR 0050). The recipe of the binding's mutation rows runs the tool,
converts its outcomes, and hands the lines to this gate.

An entry excuses one mutant. Entries and live mutants match one to one, so a register with fewer
entries than live mutants is red and names the unmatched ones, and an entry listed twice is stale
the second time.

The identity is ``<file>:<mutator>:<replacement>@<l>:<c>-<l>:<c>``, since tools number their
mutants per run. The suffix is the mutant's start and end inside its unit, lines counted from the
unit's first line (1), so an edit elsewhere in the file leaves the entry valid, and an edit inside
the unit changes the hash. An entry without the suffix is the old identity: it is red, named, and
lists the live mutants it matched. The function hash is the SHA-256 of the unit's text with
trailing space cut. For every unregistered survivor the gate prints the entry to paste, hash
included.

A partial run judges only its scope: the files its mutants are in, or the files and line
ranges passed to ``--only``. When ``--only`` gives line ranges (``FILE:START-END``), an
unchanged register entry whose unit does not overlap any range is out of scope, while an
entry whose function hash no longer matches any unit in that file stays in scope. A run
that made no mutant is red, with one exception: a run that says which files or ranges it
was given (``--only``), all of whose files exist, read them and found nothing to mutate
(and has no stale register entry in scope). ``--changed`` prints every changed file that
still exists, one per line, for the recipe to filter and hand to its tool; ``--changed
--ranges`` prints 1-based inclusive ``FILE:START-END`` added or edited line ranges instead.

Usage::

    mutation_survivors.py [--root DIR] --tool NAME --mutants FILE [--only FILE[:START-END]...]
    mutation_survivors.py [--root DIR] --tool NAME --changed [--ranges] [--base REF]

``--mutants -`` reads stdin.
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import math
import re
import sys
import tomllib
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

import gatelib

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

REGISTER = Path("quality/mutation-register.toml")
MIN_REASON = 20
STATES = ("killed", "survived", "uncovered", "timeout", "undecided", "unviable", "skipped")
KILLED, SURVIVED, UNCOVERED, TIMEOUT, UNDECIDED, UNVIABLE, SKIPPED = STATES
LIVE = frozenset({SURVIVED, UNCOVERED})
TESTED = LIVE | {KILLED, TIMEOUT}
POSITION = re.compile(r"@(?:\d+:\d+-\d+:\d+|\?)$")
HUNK = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,(\d+))? @@")
RANGE_SUFFIX = re.compile(r"^(.+):(\d+)-(\d+)$")
BAD_RANGE_SUFFIX = re.compile(r":(?:[^/]*-[^/]*|\d*)$")
REPLACEMENT_WIDTH = 60
SLOWEST_SHARE = 0.1
SLOWEST_FILES = 3
TEXTS = ("file", "mutator", "replacement", "status")
Range = tuple[int, int]
ScopeMap = dict[str, list[Range] | None]


@dataclass(frozen=True)
class Mutant:
    identity: str
    status: str
    function_hash: str
    state: str
    duration: float | None = None
    tests_completed: float | None = None


@dataclass(frozen=True)
class Record:
    file: str
    mutator: str
    replacement: str
    status: str
    state: str
    line: int
    span: tuple[int, int, int, int]
    unit: tuple[int, int] | None = None
    duration: float | None = None
    tests_completed: float | None = None


@dataclass(frozen=True)
class Entry:
    tool: str
    identity: str
    function_hash: str


def entries(root: Path) -> tuple[list[Entry], list[str]]:
    path = root / REGISTER
    if not path.is_file():
        return [], []
    data = tomllib.loads(path.read_text(encoding="utf-8"))
    found, problems = [], []
    survivors = data.get("survivor", [])
    for number, table in enumerate(survivors if isinstance(survivors, list) else [], 1):
        entry, faults = _entry(table)
        if entry is None:
            problems.append(f"{REGISTER}: survivor {number}: {'; '.join(faults)}")
        else:
            found.append(entry)
    return found, problems


def _entry(table: object) -> tuple[Entry | None, list[str]]:
    if not isinstance(table, dict):
        return None, ["not a table"]
    strings = {
        k: v
        for k in ("tool", "identity", "function_hash", "reason")
        if isinstance(v := table.get(k), str) and v
    }
    faults = [
        f"`{key}` missing"
        for key in ("tool", "identity", "function_hash", "reason")
        if key not in strings
    ]
    if not strings.get("function_hash", "sha256:").startswith("sha256:"):
        faults.append("`function_hash` does not start with `sha256:`")
    if len(strings.get("reason", "x" * MIN_REASON)) < MIN_REASON:
        faults.append(f"`reason` shorter than {MIN_REASON} characters")
    if not isinstance(table.get("date"), dt.date):
        faults.append("`date` missing or not a TOML date")
    if faults:
        return None, faults
    return Entry(strings["tool"], strings["identity"], strings["function_hash"]), []


def stanza(tool: str, mutant: Mutant) -> str:
    return (
        f"[[survivor]]\ntool = {json.dumps(tool)}\nidentity = {json.dumps(mutant.identity)}\n"
        f'function_hash = "{mutant.function_hash}"\n'
        'reason = "<why this mutant is equivalent, twenty characters or more>"\n'
        f"date = {dt.datetime.now(tz=dt.UTC).date().isoformat()}"
    )


def unit_bounds(lines: list[str], line: int) -> tuple[int, int]:
    if not lines:
        return 0, 0
    start = min(line, len(lines)) - 1
    while start > 0 and not _opens_unit(lines[start]):
        start -= 1
    end = start + 1
    while end < len(lines) and not _opens_unit(lines[end]) and not _closes_unit(lines[end]):
        end += 1
    if end < len(lines) and _closes_unit(lines[end]):
        end += 1
    return start, end


def unit_hash(lines: list[str], line: int) -> str:
    if not lines:
        return "unknown"
    start, end = unit_bounds(lines, line)
    text = "\n".join(line.rstrip() for line in lines[start:end])
    return "sha256:" + hashlib.sha256(text.encode()).hexdigest()


def placed(record: Record, source: list[str]) -> Mutant:
    start, end = (
        (record.unit[0] - 1, record.unit[1]) if record.unit else unit_bounds(source, record.line)
    )
    start_line, start_column, end_line, end_column = record.span
    place = f"{start_line - start}:{start_column}-{end_line - start}:{end_column}"
    replacement = " ".join(record.replacement.split())[:REPLACEMENT_WIDTH]
    text = "\n".join(line.rstrip() for line in source[start:end])
    digest = "sha256:" + hashlib.sha256(text.encode()).hexdigest() if source else "unknown"
    identity = f"{record.file}:{record.mutator}:{replacement}@{place}"
    return Mutant(
        identity,
        record.status,
        digest,
        record.state,
        duration=record.duration,
        tests_completed=record.tests_completed,
    )


def neutral_mutants(text: str, root: Path) -> tuple[list[Mutant], list[str]]:
    found, problems = [], []
    sources: dict[str, list[str]] = {}
    for number, line in enumerate(text.splitlines(), 1):
        if not line.strip():
            continue
        try:
            raw: object = json.loads(line)
        except ValueError:
            raw = None
        record = record_of(raw)
        if isinstance(record, str):
            problems.append(f"mutants line {number}: {record}")
            continue
        if record.file not in sources:
            sources[record.file] = (gatelib.read_text(root / record.file) or "").splitlines()
        found.append(placed(record, sources[record.file]))
    return found, problems


def record_of(raw: object) -> Record | str:
    if not isinstance(raw, dict):
        return "not a JSON object"
    texts = {key: raw.get(key, "" if key == "replacement" else None) for key in TEXTS}
    faults = [f"`{key}` is not text" for key, value in texts.items() if not isinstance(value, str)]
    faults += [f"`{key}` is empty" for key in ("file", "mutator") if texts[key] == ""]
    if texts["status"] not in STATES:
        faults.append(f"`status` is not one of {', '.join(STATES)}")
    start, end = _pair(raw.get("start")), _pair(raw.get("end", raw.get("start")))
    if start is None or end is None:
        faults.append("`start` and `end` are [line, column], whole numbers, lines from 1")
    unit = _pair(raw.get("unit"))
    if "unit" in raw and (
        unit is None or (start is not None and not unit[0] <= start[0] <= unit[1])
    ):
        faults.append("`unit` is the [first, last] line of the declaration the mutant sits in")
    if faults or start is None or end is None:
        return "; ".join(faults)
    status = str(texts["status"])
    return Record(
        file=str(texts["file"]),
        mutator=str(texts["mutator"]),
        replacement=str(texts["replacement"]),
        status=status,
        state=status,
        line=start[0],
        span=(*start, *end),
        unit=unit,
        duration=_non_negative_number(raw.get("duration")),
        tests_completed=_non_negative_number(raw.get("tests_completed")),
    )


def _non_negative_number(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    number = float(value)
    return number if math.isfinite(number) and number >= 0 else None


def _pair(value: object) -> tuple[int, int] | None:
    if not isinstance(value, list) or [type(item) for item in value] != [int, int]:
        return None
    first, second = value
    return (first, second) if first > 0 else None


def _opens_unit(line: str) -> bool:
    return bool(line.strip()) and line[0] not in " \t})]" and not line.startswith("//")


def _closes_unit(line: str) -> bool:
    return bool(line.strip()) and line[0] in "})]"


def changed_files(root: Path, base: str | None) -> list[str]:
    before = gatelib.base_commit(root, base)
    changed = gatelib.changed_paths(root, before) if before else gatelib.tracked_paths(root)
    return [path for path in changed if (root / path).is_file()]


def _merge_ranges(ranges: list[Range]) -> list[Range]:
    merged: list[Range] = []
    for start, end in sorted(ranges):
        if merged and start <= merged[-1][1] + 1:
            prev_start, prev_end = merged[-1]
            merged[-1] = (prev_start, max(prev_end, end))
        else:
            merged.append((start, end))
    return merged


def _whole_file_ranges(root: Path, paths: list[str]) -> dict[str, list[Range]]:
    found: dict[str, list[Range]] = {}
    for path in paths:
        if text := gatelib.read_text(root / path):
            found[path] = [(1, len(text.splitlines()))]
    return found


def _parse_diff_ranges(diff: str) -> dict[str, list[Range]]:
    found: dict[str, list[Range]] = {}
    current: str | None = None
    in_header = False
    for line in diff.splitlines():
        if line.startswith("diff --git "):
            current, in_header = None, True
        elif in_header and line.startswith("+++ b/"):
            current = line[6:].removesuffix("\t")
        elif match := HUNK.match(line):
            in_header = False
            new_start = int(match.group(1))
            new_count = int(match.group(2)) if match.group(2) is not None else 1
            if current and new_count > 0:
                found.setdefault(current, []).append((new_start, new_start + new_count - 1))
    return found


def changed_ranges(root: Path, base: str | None) -> list[str]:
    before = gatelib.base_commit(root, base)
    if before is None:
        by_file = _whole_file_ranges(root, gatelib.tracked_paths(root))
    else:
        diff = gatelib.git(
            root,
            "-c",
            "core.quotepath=false",
            "diff",
            "-U0",
            "--no-renames",
            "--diff-filter=AMCR",
            before,
        )
        by_file = _parse_diff_ranges(diff or "")
        untracked_raw = (
            gatelib.git(
                root,
                "ls-files",
                "--others",
                "--exclude-standard",
                "--full-name",
                "-z",
                ":/",
            )
            or ""
        )
        untracked = [path for path in untracked_raw.split("\0") if path]
        for path, ranges in _whole_file_ranges(root, untracked).items():
            by_file.setdefault(path, []).extend(ranges)
    return [
        f"{path}:{start}-{end}"
        for path in sorted(by_file)
        if (root / path).is_file()
        for start, end in _merge_ranges(by_file[path])
    ]


def judge(
    tool: str,
    mutants: list[Mutant],
    found: list[Entry],
    *,
    scopes: ScopeMap | None = None,
    root: Path = Path(),
) -> list[str]:
    if not mutants:
        return [f"{tool}: no mutant reported; the run did not happen or its results were not read"]
    problems = []
    live = [m for m in mutants if m.state in LIVE]
    mine = _in_scope_entries(tool, mutants, found, scopes=scopes, root=root)
    unclaimed = Counter(_key(e) for e in mine)
    for mutant in mutants:
        if mutant.state == UNDECIDED:
            problems.append(f"{mutant.identity}: {mutant.status}; the run did not decide it")
        elif mutant.state in LIVE and not _claim(unclaimed, mutant):
            problems.append(
                f"{mutant.identity}: {mutant.status} and not in {REGISTER}; to register:\n"
                f"{stanza(tool, mutant)}"
            )
    problems.extend(
        f"{mutant.identity}: {mutant.status}; the run could not tell it from a survivor, and the"
        " register has an entry for it; rerun it with less load, and if it times out again on its"
        " own, remove the entry"
        for mutant in mutants
        if mutant.state == TIMEOUT and _claim(unclaimed, mutant)
    )
    problems.extend(_stale(entry, live) for entry in mine if _claim(unclaimed, entry))
    killed = sum(m.state in {KILLED, TIMEOUT} for m in mutants)
    unviable = sum(m.state == UNVIABLE for m in mutants)
    if unviable and not any(m.state in TESTED for m in mutants):
        problems.append(
            f"{tool}: all {unviable} mutants the run built were unviable, so no test met one; a"
            " warning the mutation build treats as an error is the usual cause"
        )
    apart = f"; {unviable} unviable, which no test met" if unviable else ""
    print(
        f"{tool}: {killed}/{len(mutants)} killed, {len(live)} live, {len(mine)} registered in"
        f" scope{apart}"
    )
    if profile := _profile_line(mutants):
        print(profile)
    if (mean_tests := _mean_tests_completed(mutants)) is not None:
        formatted = f"{mean_tests:.2f}" if mean_tests > 0 else "0"
        print(f"ratchet-work: tests-per-mutant {formatted}")
    return problems


def _mean_tests_completed(mutants: list[Mutant]) -> float | None:
    tests = [m.tests_completed for m in mutants if m.tests_completed is not None]
    return sum(tests) / len(tests) if tests else None


def _duration_parts(mutants: list[Mutant]) -> list[str]:
    timed: list[float] = []
    by_file: dict[str, float] = {}
    for mutant in mutants:
        if mutant.duration is None:
            continue
        timed.append(mutant.duration)
        if mutant.duration > 0:
            file = mutant.identity.split(":", 1)[0]
            by_file[file] = by_file.get(file, 0.0) + mutant.duration
    total = sum(timed)
    if total <= 0:
        return []
    top_count = max(1, math.ceil(len(timed) * SLOWEST_SHARE))
    slowest_sum = sum(sorted(timed, reverse=True)[:top_count])
    slowest_pct = round(100 * slowest_sum / total)
    top_files = sorted(by_file.items(), key=lambda item: (-item[1], item[0]))[:SLOWEST_FILES]
    files_text = ", ".join(
        f"{file} {secs:.1f}s ({round(100 * secs / total)}%)" for file, secs in top_files
    )
    return [
        f"slowest 10% of mutants took {slowest_pct}% of the time",
        f"slowest files: {files_text}",
    ]


def _profile_line(mutants: list[Mutant]) -> str | None:
    parts = _duration_parts(mutants)
    if (mean_tests := _mean_tests_completed(mutants)) is not None:
        parts.append(f"mean {mean_tests:.2f} tests per mutant")
    return f"mutation: profile: {'; '.join(parts)}" if parts else None


def _key(item: Mutant | Entry) -> tuple[str, str]:
    return item.identity, item.function_hash


def _claim(unclaimed: Counter[tuple[str, str]], item: Mutant | Entry) -> bool:
    if unclaimed[_key(item)] <= 0:
        return False
    unclaimed[_key(item)] -= 1
    return True


def _stale(entry: Entry, live: list[Mutant]) -> str:
    if POSITION.search(entry.identity) is None:
        matched = [
            m.identity
            for m in live
            if POSITION.sub("", m.identity) == entry.identity
            and m.function_hash == entry.function_hash
        ]
        outcome = (
            f"it matched {', '.join(matched)}; replace it by one entry for each of those you"
            " judge equivalent"
            if matched
            else "it matched nothing live; remove it"
        )
        return (
            f"{entry.identity}: register entry has the old identity, without a position, so it"
            f" cannot say which mutant it excuses; {outcome}"
        )
    return (
        f"{entry.identity}: register entry is stale; the mutant is killed, gone, its function"
        " changed, or the register lists it more than once"
    )


def _file_units(lines: list[str]) -> list[tuple[int, int, str]]:
    units: list[tuple[int, int, str]] = []
    seen: set[tuple[int, int]] = set()
    for line_no in range(1, len(lines) + 1):
        start, end = unit_bounds(lines, line_no)
        if (start, end) in seen:
            continue
        seen.add((start, end))
        text = "\n".join(line.rstrip() for line in lines[start:end])
        digest = "sha256:" + hashlib.sha256(text.encode()).hexdigest()
        units.append((start + 1, end, digest))
    return units


def _entry_in_scope(
    entry: Entry,
    *,
    mutant_files: set[str],
    mutant_keys: set[tuple[str, str]],
    scopes: ScopeMap | None,
    units_for: Callable[[str], list[tuple[int, int, str]]],
) -> bool:
    file = entry.identity.split(":", 1)[0]
    if not scopes or file not in scopes:
        return file in mutant_files
    ranges = scopes[file]
    if ranges is None or _key(entry) in mutant_keys:
        return True
    matching = [
        (start, end) for start, end, digest in units_for(file) if digest == entry.function_hash
    ]
    if not matching:
        return True
    return any(
        u_start <= r_end and r_start <= u_end
        for u_start, u_end in matching
        for r_start, r_end in ranges
    )


def _in_scope_entries(
    tool: str,
    mutants: list[Mutant],
    registered: Sequence[Entry],
    *,
    scopes: ScopeMap | None,
    root: Path,
) -> list[Entry]:
    mutant_files = {m.identity.split(":", 1)[0] for m in mutants}
    mutant_keys = {_key(m) for m in mutants}
    unit_cache: dict[str, list[tuple[int, int, str]]] = {}

    def units_for(file: str) -> list[tuple[int, int, str]]:
        if file not in unit_cache:
            unit_cache[file] = _file_units((gatelib.read_text(root / file) or "").splitlines())
        return unit_cache[file]

    return [
        entry
        for entry in registered
        if entry.tool == tool
        and _entry_in_scope(
            entry,
            mutant_files=mutant_files,
            mutant_keys=mutant_keys,
            scopes=scopes,
            units_for=units_for,
        )
    ]


def parse_scopes(targets: list[str]) -> tuple[ScopeMap, list[str]]:
    scopes: ScopeMap = {}
    problems: list[str] = []
    for target in targets:
        if match := RANGE_SUFFIX.match(target):
            path, start, end = match.group(1), int(match.group(2)), int(match.group(3))
            if start <= 0 or end < start:
                problems.append(
                    f"{target}: invalid line range; expected PATH:START-END with 1 <= START <= END"
                )
            elif path not in scopes:
                scopes[path] = [(start, end)]
            elif (existing := scopes[path]) is not None:
                existing.append((start, end))
        elif BAD_RANGE_SUFFIX.search(target):
            problems.append(
                f"{target}: invalid line range; expected PATH:START-END with 1 <= START <= END"
            )
        else:
            scopes[target] = None
    merged = {
        path: None if ranges is None else _merge_ranges(ranges) for path, ranges in scopes.items()
    }
    return merged, problems


Run = tuple[list[Mutant] | None, list[str]]


def check(options: argparse.Namespace, root: Path) -> list[str]:
    found, problems = entries(root)
    scopes, scope_problems = parse_scopes(given_files(options))
    problems += scope_problems
    if options.mutants is None:
        needed = f"--mutants is required for {options.tool}: JSON lines, one object per mutant"
        return [*problems, needed]
    if scope_problems:
        return problems
    mutants, unread = records_run(options, root, scopes=scopes, registered=found)
    problems += unread
    if mutants is None:
        return problems
    return problems + judge(options.tool, mutants, found, scopes=scopes, root=root)


def given_files(options: argparse.Namespace) -> list[str]:
    return [name for files in options.only or [] for name in files.split(",") if name]


def records_run(
    options: argparse.Namespace,
    root: Path,
    *,
    scopes: ScopeMap | None = None,
    registered: Sequence[Entry] = (),
) -> Run:
    text = sys.stdin.read() if options.mutants == "-" else _read(root, options.mutants)
    if text is None:
        return None, [f"{options.mutants}: missing; convert the tool's report to it first"]
    mutants, unread = neutral_mutants(text, root)
    if not mutants and not unread and scopes and all((root / path).is_file() for path in scopes):
        stale = [
            _stale(entry, [])
            for entry in _in_scope_entries(options.tool, [], registered, scopes=scopes, root=root)
        ]
        if stale:
            return None, stale
        print(f"{options.tool}: no mutant in the files it was given; nothing to mutate yet")
        return None, []
    return mutants, unread


def _resolve(root: Path, given: str) -> Path:
    path = Path(given)
    return path if path.is_absolute() else root / path


def _read(root: Path, given: str) -> str | None:
    return gatelib.read_text(_resolve(root, given))


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--root", default=".")
    parser.add_argument("--tool", required=True, help="the register's `tool` key")
    parser.add_argument("--mutants", help="the run's mutants: JSON lines, `-` for stdin")
    parser.add_argument(
        "--only", nargs="+", help="the files or FILE:START-END ranges the run was given"
    )
    parser.add_argument(
        "--changed", action="store_true", help="print the scope of a changed-code run"
    )
    parser.add_argument(
        "--ranges",
        action="store_true",
        help="with --changed, print 1-based inclusive PATH:START-END line ranges",
    )
    parser.add_argument("--base", help="the merge base to compare with")
    options = parser.parse_args(argv[1:])
    if options.ranges and not options.changed:
        parser.error("--ranges requires --changed")
    root = Path(options.root).resolve()
    if options.changed:
        items = (
            changed_ranges(root, options.base)
            if options.ranges
            else changed_files(root, options.base)
        )
        scope = "\n".join(items)
        if scope:
            print(scope)
        return 0
    return gatelib.report("mutation", check(options, root))


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
