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

A tool's own report becomes these lines in a gate tool of a few lines, which the repository
writes, guards and bites like any other. For ``cargo mutants``, whose ``mutants.out/outcomes.json``
gives every outcome with both spans::

    import json, sys

    STATUS = {"CaughtMutant": "killed", "MissedMutant": "survived", "Timeout": "timeout",
              "Unviable": "unviable"}
    for outcome in json.load(sys.stdin)["outcomes"]:
        if outcome["scenario"] == "Baseline":
            continue
        mutant = outcome["scenario"]["Mutant"]
        span, unit = mutant["span"], (mutant.get("function") or {}).get("span")
        record = {
            "file": mutant["file"],
            "mutator": mutant["genre"],
            "replacement": mutant["replacement"],
            "status": STATUS.get(outcome["summary"], "skipped"),
            "start": [span["start"]["line"], span["start"]["column"]],
            "end": [span["end"]["line"], span["end"]["column"]],
        }
        if unit:
            record["unit"] = [unit["start"]["line"], unit["end"]["line"]]
        print(json.dumps(record))

Two tools need no converter. ``--tool stryker`` reads the json reporter's file and turns each
mutant into such a record, with the unit found as above in the ``source`` the report carries.
``--tool mutmut`` reads ``mutmut results --all``: mutmut prints a name and no position, so its
mutants keep that name as their identity and only their statuses are translated (``no tests`` is
``uncovered``, ``not checked`` is ``undecided``).

An entry excuses one mutant. Entries and live mutants match one to one, so a register with fewer
entries than live mutants is red and names the unmatched ones, and an entry listed twice is stale
the second time.

The identity is the mutant's name as the tool prints it (mutmut), or
``<file>:<mutator>:<replacement>@<l>:<c>-<l>:<c>`` for every other tool, since those number
their mutants per run. The suffix is the mutant's start and end inside its unit, lines counted
from the unit's first line (1), so an edit elsewhere in the file leaves the entry valid, and an
edit inside the unit changes the hash. An entry without the suffix is the old identity: it is
red, named, and lists the live mutants it matched. The function hash is the SHA-256 of the
enclosing function's AST (mutmut; the same recipe mutmut uses for its own cache), or of the
unit's text with trailing space cut (every other tool). For every unregistered survivor the gate
prints the entry to paste, hash included.

A partial run judges only its scope: for mutmut, the ``--only`` patterns the run was given
(``fnmatch`` over mutant names, as ``mutmut run`` takes them); for every other tool, the files
its mutants are in. A run that made no mutant is red, with one exception: a run that says which
files it was given (``--only``), all of which exist, read them and found nothing to mutate.
``--changed`` prints the scope of a changed-code run for the merge base: mutant-name patterns
for the changed source modules (mutmut); the changed files that the stryker config's ``mutate``
globs keep, comma-separated, so ``--mutate`` on the command line does not drop the config's
exclusions; for any other tool, every changed file that still exists, one per line, for the
recipe to filter and hand to its tool.

Usage::

    mutation_survivors.py [--root DIR] --tool NAME --mutants FILE [--only FILE...]
    mutation_survivors.py [--root DIR] --tool mutmut --results FILE [--only PATTERN...]
    mutation_survivors.py [--root DIR] --tool stryker [--report FILE] [--only FILE...]
    mutation_survivors.py [--root DIR] --tool NAME --changed [--base REF]

``--mutants -`` and ``--results -`` read stdin; ``--report`` defaults to the json reporter's
``reports/mutation/mutation.json``.
"""

from __future__ import annotations

import argparse
import ast
import datetime as dt
import fnmatch
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
STRYKER_REPORT = Path("reports/mutation/mutation.json")
STRYKER_CONFIG = Path("stryker.config.json")
PYPROJECT = Path("pyproject.toml")
MIN_REASON = 20
MUTMUT, STRYKER = "mutmut", "stryker"
# How a run ended for one mutant, in the words of the tool-neutral input.
STATES = ("killed", "survived", "uncovered", "timeout", "undecided", "unviable", "skipped")
KILLED, SURVIVED, UNCOVERED, TIMEOUT, UNDECIDED, UNVIABLE, SKIPPED = STATES
LIVE = frozenset({SURVIVED, UNCOVERED})
# A mutant a test met: only these say anything about the tests.
TESTED = LIVE | {KILLED, TIMEOUT}
MUTMUT_STATES = {
    "killed": KILLED,
    "survived": SURVIVED,
    "no tests": UNCOVERED,
    "timeout": TIMEOUT,
    "not checked": UNDECIDED,
}
STRYKER_STATES = {
    "Killed": KILLED,
    "Survived": SURVIVED,
    "NoCoverage": UNCOVERED,
    "Timeout": TIMEOUT,
    "Pending": UNDECIDED,
    "CompileError": UNVIABLE,
    "RuntimeError": UNVIABLE,
}
POSITION = re.compile(r"@(?:\d+:\d+-\d+:\d+|\?)$")
MUTMUT_LINE = re.compile(
    r"^\s*(?P<name>\S.*?): "
    r"(?P<status>killed|survived|no tests|timeout|suspicious|skipped|not checked)$"
)
CLASS_SEPARATOR = "ǁ"
DEFAULT_STRYKER_MUTATE = ["src/**/*.ts", "src/**/*.tsx", "!src/**/*.test.ts", "!src/**/*.test.tsx"]
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


# --- mutmut -------------------------------------------------------------------------------------


def mutmut_mutants(text: str, root: Path) -> list[Mutant]:
    """Every mutant ``mutmut results --all`` printed, hashed through its enclosing function."""
    hashes: dict[tuple[str, str], str] = {}
    found = []
    for line in text.splitlines():
        match = MUTMUT_LINE.match(line)
        if match is None:
            continue
        name = match.group("name")
        module, key = split_mutant_name(name)
        if (module, key) not in hashes:
            hashes[module, key] = python_function_hash(root, module, key)
        status = match.group("status")
        state = MUTMUT_STATES.get(status, SKIPPED)
        found.append(Mutant(name, status, hashes[module, key], state))
    return found


def split_mutant_name(name: str) -> tuple[str, str]:
    """``pkg.mod.x_f__mutmut_3`` -> (``pkg.mod``, ``x_f``); the key may name a class."""
    mangled = name.partition("__mutmut_")[0]
    parts = mangled.split(".")
    for index in range(len(parts) - 1, -1, -1):
        if parts[index].startswith(("x_", f"x{CLASS_SEPARATOR}")):
            return ".".join(parts[:index]), ".".join(parts[index:])
    return mangled.rpartition(".")[0], mangled.rpartition(".")[2]


def module_file(root: Path, module: str) -> Path | None:
    """The source file a mutmut module name came from (``src.`` stripped, ``__init__`` folded)."""
    relative = module.replace(".", "/")
    for prefix in ("", "src/"):
        for suffix in (".py", "/__init__.py"):
            candidate = root / f"{prefix}{relative}{suffix}"
            if candidate.is_file():
                return candidate
    return None


def python_function_hash(root: Path, module: str, key: str) -> str:
    """``sha256:`` of the function's AST, or ``unknown`` when the file or function is gone."""
    path = module_file(root, module)
    if path is None:
        return "unknown"
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except (SyntaxError, UnicodeDecodeError):
        return "unknown"
    function, class_path = _function_and_class(key)
    node = _find_function(tree.body, class_path, function)
    if node is None:
        return "unknown"
    return "sha256:" + hashlib.sha256(ast.dump(node, annotate_fields=False).encode()).hexdigest()


def _function_and_class(key: str) -> tuple[str, list[str]]:
    if CLASS_SEPARATOR in key:
        _, class_name, function = key.split(CLASS_SEPARATOR)
        return function, class_name.split(".")
    return key.removeprefix("x_"), []


def _find_function(
    body: list[ast.stmt], class_path: list[str], function: str
) -> ast.FunctionDef | ast.AsyncFunctionDef | None:
    for name in class_path:
        found = next((n for n in body if isinstance(n, ast.ClassDef) and n.name == name), None)
        if found is None:
            return None
        body = found.body
    for node in body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == function:
            return node
    return None


def mutmut_changed(root: Path, base: str | None) -> list[str]:
    """Mutant-name patterns for the source modules changed since the merge base."""
    source_paths, excluded = mutmut_config(root)
    before = gatelib.base_commit(root, base)
    changed = gatelib.changed_paths(root, before) if before else gatelib.tracked_paths(root)
    patterns = []
    for path in changed:
        if not path.endswith(".py") or not any(path.startswith(s) for s in source_paths):
            continue
        if gatelib.matches(path, excluded):
            continue
        module = path.removesuffix(".py").removesuffix("/__init__").replace("/", ".")
        patterns.append(f"{module.removeprefix('src.')}.*")
    return sorted(set(patterns))


def mutmut_config(root: Path) -> tuple[list[str], list[str]]:
    """``[tool.mutmut]`` source paths (normalised, trailing slash) and ``do_not_mutate`` globs."""
    data = _toml(root / PYPROJECT)
    table = gatelib.table(data, "tool", "mutmut")
    sources = _strings(table.get("source_paths") or table.get("paths_to_mutate")) or ["src/"]
    excluded = _strings(table.get("do_not_mutate"))
    return [s.rstrip("/") + "/" for s in sources], [
        g.rstrip("*") + "**" if g.endswith("/*") else g for g in excluded
    ]


def _strings(value: object) -> list[str]:
    return [v for v in value if isinstance(v, str)] if isinstance(value, list) else []


def _toml(path: Path) -> dict[str, object]:
    return tomllib.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}


# --- stryker ------------------------------------------------------------------------------------


def stryker_mutants(report: dict[str, object]) -> list[Mutant]:
    """Every mutant of the json report, as a record placed in the source the report carries."""
    found = []
    files = report.get("files")
    for path, result in files.items() if isinstance(files, dict) else []:
        if not isinstance(result, dict):
            continue
        source = str(result.get("source", "")).splitlines()
        for mutant in result.get("mutants", []):
            if not isinstance(mutant, dict):
                continue
            status = str(mutant.get("status", ""))
            record = Record(
                file=path,
                mutator=str(mutant.get("mutatorName", "?")),
                replacement=str(mutant.get("replacement", "")),
                status=status,
                state=STRYKER_STATES.get(status, SKIPPED),
                line=_start_line(mutant),
                span=_span(mutant),
            )
            found.append(placed(record, source))
    return found


def _start_line(mutant: dict[str, object]) -> int:
    start = _point(mutant, "start")
    line = start.get("line")
    return line if isinstance(line, int) and line > 0 else 1


def _point(mutant: dict[str, object], end: str) -> dict[str, object]:
    """The ``start`` or ``end`` position the report gives for a mutant; empty when it gives none."""
    location = mutant.get("location")
    point = location.get(end) if isinstance(location, dict) else None
    return point if isinstance(point, dict) else {}


def _span(mutant: dict[str, object]) -> tuple[int, int, int, int] | None:
    """Start line, start column, end line and end column, or ``None`` when the report lacks one."""
    given = [_point(mutant, end).get(key) for end in ("start", "end") for key in ("line", "column")]
    numbers = [n for n in given if isinstance(n, int)]
    if len(numbers) != len(given):
        return None
    start_line, start_column, end_line, end_column = numbers
    return start_line, start_column, end_line, end_column


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


def stryker_patterns(root: Path) -> list[str]:
    """The stryker config's ``mutate`` globs, or stryker's default."""
    globs = _json(root / STRYKER_CONFIG).get("mutate")
    if isinstance(globs, list):
        return [g for g in globs if isinstance(g, str)]
    return list(DEFAULT_STRYKER_MUTATE)


def changed_files(root: Path, base: str | None) -> list[str]:
    """Every file changed since the merge base that still exists; the whole tree with no base."""
    before = gatelib.base_commit(root, base)
    changed = gatelib.changed_paths(root, before) if before else gatelib.tracked_paths(root)
    return [path for path in changed if (root / path).is_file()]


def stryker_changed(root: Path, base: str | None) -> list[str]:
    """Changed files the stryker config's ``mutate`` globs keep, for ``--mutate``."""
    patterns = stryker_patterns(root)
    return [path for path in changed_files(root, base) if mutated_by(path, patterns)]


def nothing_to_mutate(root: Path, report: dict[str, object], scope: list[str]) -> bool:
    """Whether stryker ran and had nothing to mutate: types or re-exports only, or no code yet.

    The report lists no file. A scoped run was given files that exist, so stryker read them and
    found no mutant. A full run passes only when the globs' includes reach tracked files and
    its exclusions drop every one, as a skeleton's re-exporting index; an include that reaches
    no tracked file is a glob that misses the code, and still fails as an empty run.
    """
    ran = "schemaVersion" in report and report.get("files") == {}
    if scope:
        return ran and all((root / path).is_file() for path in scope)
    patterns = stryker_patterns(root)
    includes = [p for p in patterns if not p.startswith("!")]
    tracked = gatelib.tracked_paths(root)
    reached = [path for path in tracked if mutated_by(path, includes)]
    return ran and bool(reached) and not any(mutated_by(path, patterns) for path in reached)


def mutated_by(path: str, patterns: list[str]) -> bool:
    """Whether the globs, in order with ``!`` exclusions, keep the path."""
    kept = False
    for pattern in patterns:
        negated = pattern.startswith("!")
        if gatelib.matches(path, expand_braces(pattern.lstrip("!"))):
            kept = not negated
    return kept


def expand_braces(pattern: str) -> list[str]:
    """``a.{ts,tsx}`` -> ``a.ts``, ``a.tsx``; one level, as stryker's globs use it."""
    match = re.search(r"\{([^{}]*)\}", pattern)
    if match is None:
        return [pattern]
    head, tail = pattern[: match.start()], pattern[match.end() :]
    return [e for option in match.group(1).split(",") for e in expand_braces(head + option + tail)]


def _json(path: Path) -> dict[str, object]:
    if not path.is_file():
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    return data if isinstance(data, dict) else {}


# --- the gate -----------------------------------------------------------------------------------


def judge(tool: str, mutants: list[Mutant], found: list[Entry], *, scope: list[str]) -> list[str]:
    """Problems: unregistered survivors, stale entries, undecided mutants, an empty run."""
    if not mutants:
        return [f"{tool}: no mutant reported; the run did not happen or its results were not read"]
    problems = []
    live = [m for m in mutants if m.state in LIVE]
    mine = [
        e
        for e in found
        if e.tool == tool and in_scope(e.identity, tool, scope=scope, mutants=mutants)
    ]
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
    problems.extend(_stale(tool, entry, live) for entry in mine if _claim(unclaimed, entry))
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


def _stale(tool: str, entry: Entry, live: list[Mutant]) -> str:
    """Why an entry that no mutant claimed is red: the old identity form, or stale as ever."""
    if tool != MUTMUT and POSITION.search(entry.identity) is None:
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


def in_scope(identity: str, tool: str, *, scope: list[str], mutants: list[Mutant]) -> bool:
    """Whether a register entry is judged by this run: mutmut by pattern, the rest by file."""
    if tool == MUTMUT:
        return not scope or any(fnmatch.fnmatchcase(identity, p) for p in scope)
    files = {m.identity.split(":", 1)[0] for m in mutants}
    return identity.split(":", 1)[0] in files


# What a reader hands the judge: the run's mutants, or ``None`` when there is nothing to judge,
# and what is wrong with the input.
Run = tuple[list[Mutant] | None, list[str]]


def check(options: argparse.Namespace, root: Path) -> list[str]:
    found, problems = entries(root)
    if options.mutants is not None:
        mutants, unread = records_run(options, root)
    elif options.tool == MUTMUT:
        mutants, unread = mutmut_run(options, root)
    elif options.tool == STRYKER:
        mutants, unread = stryker_run(options, root)
    else:
        needed = f"--mutants is required for {options.tool}: JSON lines, one object per mutant"
        mutants, unread = None, [needed]
    problems += unread
    if mutants is None:
        return problems
    return problems + judge(options.tool, mutants, found, scope=options.only or [])


def given_files(options: argparse.Namespace) -> list[str]:
    """The files a scoped run says it was given: ``--only``, split at commas."""
    return [name for files in options.only or [] for name in files.split(",") if name]


def records_run(options: argparse.Namespace, root: Path) -> Run:
    if options.tool == MUTMUT:
        return None, ["--mutants takes another tool name: mutmut names its mutants"]
    text = sys.stdin.read() if options.mutants == "-" else _read(root, options.mutants)
    if text is None:
        return None, [f"{options.mutants}: missing; convert the tool's report to it first"]
    mutants, unread = neutral_mutants(text, root)
    given = given_files(options)
    if not mutants and not unread and given and all((root / name).is_file() for name in given):
        print(f"{options.tool}: no mutant in the files it was given; nothing to mutate yet")
        return None, []
    return mutants, unread


def mutmut_run(options: argparse.Namespace, root: Path) -> Run:
    if options.results is None:
        return None, ["--results is required for mutmut (`mutmut results --all`, or `-`)"]
    text = sys.stdin.read() if options.results == "-" else _read(root, options.results)
    if text is None:
        return None, [f"{options.results}: missing"]
    scope = options.only or []
    mutants = mutmut_mutants(text, root)
    return [m for m in mutants if in_scope(m.identity, MUTMUT, scope=scope, mutants=[])], []


def stryker_run(options: argparse.Namespace, root: Path) -> Run:
    path = _resolve(root, options.report or str(STRYKER_REPORT))
    if not path.is_file():
        named = options.report or STRYKER_REPORT
        return None, [f"{named}: missing; run stryker with the json reporter first"]
    report = _json(path)
    if nothing_to_mutate(root, report, given_files(options)):
        print("stryker: no mutant in the files it was given; nothing to mutate yet")
        return None, []
    return stryker_mutants(report), []


def _resolve(root: Path, given: str) -> Path:
    path = Path(given)
    return path if path.is_absolute() else root / path


def _read(root: Path, given: str) -> str | None:
    return gatelib.read_text(_resolve(root, given))


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", default=".")
    parser.add_argument("--tool", required=True, help="the register's `tool` key")
    parser.add_argument("--mutants", help="any tool: JSON lines, one per mutant, `-` for stdin")
    parser.add_argument("--results", help="mutmut: `mutmut results --all` output, `-` for stdin")
    parser.add_argument("--report", help="stryker: the json reporter's file")
    parser.add_argument(
        "--only", nargs="+", help="the run's scope: mutmut patterns, or the files it was given"
    )
    parser.add_argument(
        "--changed", action="store_true", help="print the scope of a changed-code run"
    )
    parser.add_argument("--base", help="the merge base to compare with")
    options = parser.parse_args(argv[1:])
    root = Path(options.root).resolve()
    if options.changed:
        if options.tool == MUTMUT:
            scope = "\n".join(mutmut_changed(root, options.base))
        elif options.tool == STRYKER:
            scope = ",".join(stryker_changed(root, options.base))
        else:
            scope = "\n".join(changed_files(root, options.base))
        if scope:
            print(scope)
        return 0
    return gatelib.report("mutation", check(options, root))


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
