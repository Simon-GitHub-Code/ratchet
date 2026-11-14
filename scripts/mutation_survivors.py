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

A partial run judges only its scope: the files its mutants are in. A run that made no mutant is
red, with one exception: a run that says which files it was given (``--only``), all of which
exist, read them and found nothing to mutate. ``--changed`` prints every changed file that still
exists, one per line, for the recipe to filter and hand to its tool.

Usage::

    mutation_survivors.py [--root DIR] --tool NAME --mutants FILE [--only FILE...]
    mutation_survivors.py [--root DIR] --tool NAME --changed [--base REF]

``--mutants -`` reads stdin.
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import re
import sys
import tomllib
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

import gatelib

REGISTER = Path("quality/mutation-register.toml")
MIN_REASON = 20
# How a run ended for one mutant, in the words of the tool-neutral input.
STATES = ("killed", "survived", "uncovered", "timeout", "undecided", "unviable", "skipped")
KILLED, SURVIVED, UNCOVERED, TIMEOUT, UNDECIDED, UNVIABLE, SKIPPED = STATES
LIVE = frozenset({SURVIVED, UNCOVERED})
# A mutant a test met: only these say anything about the tests.
TESTED = LIVE | {KILLED, TIMEOUT}
POSITION = re.compile(r"@(?:\d+:\d+-\d+:\d+|\?)$")
REPLACEMENT_WIDTH = 60
TEXTS = ("file", "mutator", "replacement", "status")


@dataclass(frozen=True)
class Mutant:
    """One mutant with the identity and hash the register uses.

    ``status`` is the tool's own word, for the messages; ``state`` is the neutral one, for the
    verdict.
    """

    identity: str
    status: str
    function_hash: str
    state: str


@dataclass(frozen=True)
class Record:
    """One mutant in the tool-neutral form: where it is, what it does, how its run ended."""

    file: str
    mutator: str
    replacement: str
    status: str
    state: str
    line: int
    span: tuple[int, int, int, int] | None
    unit: tuple[int, int] | None = None


@dataclass(frozen=True)
class Entry:
    """One ``[[survivor]]`` of the register."""

    tool: str
    identity: str
    function_hash: str


# --- the register ------------------------------------------------------------------------------


def entries(root: Path) -> tuple[list[Entry], list[str]]:
    """The register's entries and the problems with the malformed ones."""
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
    """The register entry to paste for an unregistered survivor."""
    return (
        f"[[survivor]]\ntool = {json.dumps(tool)}\nidentity = {json.dumps(mutant.identity)}\n"
        f'function_hash = "{mutant.function_hash}"\n'
        'reason = "<why this mutant is equivalent, twenty characters or more>"\n'
        f"date = {dt.datetime.now(tz=dt.UTC).date().isoformat()}"
    )


def unit_bounds(lines: list[str], line: int) -> tuple[int, int]:
    """Zero-based ``[start, end)`` rows of the top-level declaration enclosing 1-based ``line``."""
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
    """``sha256:`` of the top-level declaration enclosing ``line`` (1-based), trailing space cut."""
    if not lines:
        return "unknown"
    start, end = unit_bounds(lines, line)
    text = "\n".join(line.rstrip() for line in lines[start:end])
    return "sha256:" + hashlib.sha256(text.encode()).hexdigest()


def placed(record: Record, source: list[str]) -> Mutant:
    """The identity and hash ADR 0018 gives a mutant whose tool numbers mutants per run.

    The unit is the record's own when it names one, else the enclosing top-level declaration.
    """
    start, end = (
        (record.unit[0] - 1, record.unit[1]) if record.unit else unit_bounds(source, record.line)
    )
    place = "?"
    if record.span is not None:
        start_line, start_column, end_line, end_column = record.span
        place = f"{start_line - start}:{start_column}-{end_line - start}:{end_column}"
    replacement = " ".join(record.replacement.split())[:REPLACEMENT_WIDTH]
    text = "\n".join(line.rstrip() for line in source[start:end])
    digest = "sha256:" + hashlib.sha256(text.encode()).hexdigest() if source else "unknown"
    identity = f"{record.file}:{record.mutator}:{replacement}@{place}"
    return Mutant(identity, record.status, digest, record.state)


# --- the tool-neutral input ---------------------------------------------------------------------


def neutral_mutants(text: str, root: Path) -> tuple[list[Mutant], list[str]]:
    """Every mutant of the JSON lines, placed in the file it names, and the lines that hold none."""
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
    """One object of the input as a record, or what is wrong with it."""
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
    )


def _pair(value: object) -> tuple[int, int] | None:
    """``[line, column]`` or ``[first, last]`` as two whole numbers, the first from 1."""
    if not isinstance(value, list) or [type(item) for item in value] != [int, int]:
        return None
    first, second = value
    return (first, second) if first > 0 else None


def _opens_unit(line: str) -> bool:
    return bool(line.strip()) and line[0] not in " \t})]" and not line.startswith("//")


def _closes_unit(line: str) -> bool:
    return bool(line.strip()) and line[0] in "})]"


def changed_files(root: Path, base: str | None) -> list[str]:
    """Every file changed since the merge base that still exists; the whole tree with no base."""
    before = gatelib.base_commit(root, base)
    changed = gatelib.changed_paths(root, before) if before else gatelib.tracked_paths(root)
    return [path for path in changed if (root / path).is_file()]


# --- the gate -----------------------------------------------------------------------------------


def judge(tool: str, mutants: list[Mutant], found: list[Entry]) -> list[str]:
    """Problems: unregistered survivors, stale entries, undecided mutants, an empty run."""
    if not mutants:
        return [f"{tool}: no mutant reported; the run did not happen or its results were not read"]
    problems = []
    live = [m for m in mutants if m.state in LIVE]
    mine = [e for e in found if e.tool == tool and in_scope(e.identity, mutants=mutants)]
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
    return problems


def _key(item: Mutant | Entry) -> tuple[str, str]:
    return item.identity, item.function_hash


def _claim(unclaimed: Counter[tuple[str, str]], item: Mutant | Entry) -> bool:
    """Take one register entry for ``item`` if one is left: an entry excuses one mutant."""
    if unclaimed[_key(item)] <= 0:
        return False
    unclaimed[_key(item)] -= 1
    return True


def _stale(entry: Entry, live: list[Mutant]) -> str:
    """Why an entry that no mutant claimed is red: the old identity form, or stale as ever."""
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


def in_scope(identity: str, *, mutants: list[Mutant]) -> bool:
    """Whether a register entry is judged by this run: the files the run's mutants are in."""
    files = {m.identity.split(":", 1)[0] for m in mutants}
    return identity.split(":", 1)[0] in files


# What a reader hands the judge: the run's mutants, or ``None`` when there is nothing to judge,
# and what is wrong with the input.
Run = tuple[list[Mutant] | None, list[str]]


def check(options: argparse.Namespace, root: Path) -> list[str]:
    found, problems = entries(root)
    if options.mutants is None:
        needed = f"--mutants is required for {options.tool}: JSON lines, one object per mutant"
        return [*problems, needed]
    mutants, unread = records_run(options, root)
    problems += unread
    if mutants is None:
        return problems
    return problems + judge(options.tool, mutants, found)


def given_files(options: argparse.Namespace) -> list[str]:
    """The files a scoped run says it was given: ``--only``, split at commas."""
    return [name for files in options.only or [] for name in files.split(",") if name]


def records_run(options: argparse.Namespace, root: Path) -> Run:
    text = sys.stdin.read() if options.mutants == "-" else _read(root, options.mutants)
    if text is None:
        return None, [f"{options.mutants}: missing; convert the tool's report to it first"]
    mutants, unread = neutral_mutants(text, root)
    given = given_files(options)
    if not mutants and not unread and given and all((root / name).is_file() for name in given):
        print(f"{options.tool}: no mutant in the files it was given; nothing to mutate yet")
        return None, []
    return mutants, unread


def _resolve(root: Path, given: str) -> Path:
    path = Path(given)
    return path if path.is_absolute() else root / path


def _read(root: Path, given: str) -> str | None:
    return gatelib.read_text(_resolve(root, given))


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", default=".")
    parser.add_argument("--tool", required=True, help="the register's `tool` key")
    parser.add_argument("--mutants", help="the run's mutants: JSON lines, `-` for stdin")
    parser.add_argument("--only", nargs="+", help="the files the run was given")
    parser.add_argument(
        "--changed", action="store_true", help="print the scope of a changed-code run"
    )
    parser.add_argument("--base", help="the merge base to compare with")
    options = parser.parse_args(argv[1:])
    root = Path(options.root).resolve()
    if options.changed:
        scope = "\n".join(changed_files(root, options.base))
        if scope:
            print(scope)
        return 0
    return gatelib.report("mutation", check(options, root))


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
