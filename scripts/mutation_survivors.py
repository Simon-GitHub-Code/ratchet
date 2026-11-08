#!/usr/bin/env python3
"""The mutation gate: zero unregistered survivors, and no stale entry in the register.

The mutation tool's score is a trend, not a gate; this gate is the register. It reads what the
tool found and ``quality/mutation-register.toml``, and goes red on:

- a live mutant (``survived`` or ``no tests`` for mutmut, ``Survived`` or ``NoCoverage`` for
  stryker) with no register entry matching its tool, identity and function hash;
- a register entry no live mutant matches: the mutant is killed, gone, or its function changed,
  so the equivalence claim is stale and is reviewed again;
- a mutant the run did not decide (``not checked``, ``Pending``), or a run that reported no
  mutant at all: a cache that hid the run is a run that did not happen;
- an entry without the tool, the identity, a ``sha256:`` hash, a reason of twenty characters or
  more and a date.

The identity is the mutant's name as the tool prints it (mutmut), or
``<file>:<mutator>:<replacement>`` where the tool numbers mutants per run (stryker). The
function hash is the SHA-256 of the enclosing function's AST (mutmut; the same recipe mutmut
uses for its own cache), or of the enclosing top-level declaration's text (stryker, from the
``source`` the report carries). For every unregistered survivor the gate prints the entry to
paste, hash included.

A partial run judges only its scope: for mutmut, the ``--only`` patterns the run was given
(``fnmatch`` over mutant names, as ``mutmut run`` takes them); for stryker, the files in the
report. ``--changed`` prints that scope for the merge base: mutant-name patterns for the changed
source modules (mutmut), or the changed files that the stryker config's ``mutate`` globs keep,
comma-separated, so ``--mutate`` on the command line does not drop the config's exclusions.

Usage::

    mutation_survivors.py [--root DIR] --tool mutmut --results FILE [--only PATTERN...]
    mutation_survivors.py [--root DIR] --tool stryker [--report FILE]
    mutation_survivors.py [--root DIR] --tool mutmut|stryker --changed [--base REF]

``--results -`` reads ``mutmut results --all`` from stdin; ``--report`` defaults to the json
reporter's ``reports/mutation/mutation.json``.
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
from dataclasses import dataclass
from pathlib import Path

import gatelib

REGISTER = Path("quality/mutation-register.toml")
STRYKER_REPORT = Path("reports/mutation/mutation.json")
STRYKER_CONFIG = Path("stryker.config.json")
PYPROJECT = Path("pyproject.toml")
MIN_REASON = 20
LIVE = {"mutmut": {"survived", "no tests"}, "stryker": {"Survived", "NoCoverage"}}
UNDECIDED = {"mutmut": {"not checked"}, "stryker": {"Pending"}}
KILLED = {"mutmut": {"killed", "timeout"}, "stryker": {"Killed", "Timeout"}}
TOOLS = tuple(LIVE)
MUTMUT_LINE = re.compile(
    r"^\s*(?P<name>\S.*?): "
    r"(?P<status>killed|survived|no tests|timeout|suspicious|skipped|not checked)$"
)
CLASS_SEPARATOR = "ǁ"
DEFAULT_STRYKER_MUTATE = ["src/**/*.ts", "src/**/*.tsx", "!src/**/*.test.ts", "!src/**/*.test.tsx"]
REPLACEMENT_WIDTH = 60


@dataclass(frozen=True)
class Mutant:
    """One mutant as the tool reported it, with the identity and hash the register uses."""

    identity: str
    status: str
    function_hash: str


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
    if strings.get("tool", TOOLS[0]) not in TOOLS:
        faults.append(f"`tool` is not one of {', '.join(TOOLS)}")
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
        found.append(Mutant(name, match.group("status"), hashes[module, key]))
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
    """Every mutant of the json report, hashed through its enclosing top-level declaration."""
    found = []
    files = report.get("files")
    for path, result in files.items() if isinstance(files, dict) else []:
        if not isinstance(result, dict):
            continue
        source = str(result.get("source", "")).splitlines()
        for mutant in result.get("mutants", []):
            if not isinstance(mutant, dict):
                continue
            replacement = " ".join(str(mutant.get("replacement", "")).split())[:REPLACEMENT_WIDTH]
            identity = f"{path}:{mutant.get('mutatorName', '?')}:{replacement}"
            line = _start_line(mutant)
            found.append(Mutant(identity, str(mutant.get("status", "")), unit_hash(source, line)))
    return found


def _start_line(mutant: dict[str, object]) -> int:
    location = mutant.get("location")
    start = location.get("start") if isinstance(location, dict) else None
    line = start.get("line") if isinstance(start, dict) else None
    return line if isinstance(line, int) and line > 0 else 1


def unit_hash(lines: list[str], line: int) -> str:
    """``sha256:`` of the top-level declaration enclosing ``line`` (1-based), trailing space cut."""
    if not lines:
        return "unknown"
    index = min(line, len(lines)) - 1
    start = index
    while start > 0 and not _opens_unit(lines[start]):
        start -= 1
    end = start + 1
    while end < len(lines) and not _opens_unit(lines[end]) and not _closes_unit(lines[end]):
        end += 1
    if end < len(lines) and _closes_unit(lines[end]):
        end += 1
    text = "\n".join(line.rstrip() for line in lines[start:end])
    return "sha256:" + hashlib.sha256(text.encode()).hexdigest()


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


def stryker_changed(root: Path, base: str | None) -> list[str]:
    """Changed files the stryker config's ``mutate`` globs keep, for ``--mutate``."""
    patterns = stryker_patterns(root)
    before = gatelib.base_commit(root, base)
    changed = gatelib.changed_paths(root, before) if before else gatelib.tracked_paths(root)
    return [path for path in changed if mutated_by(path, patterns) and (root / path).is_file()]


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
    live = {(m.identity, m.function_hash) for m in mutants if m.status in LIVE[tool]}
    mine = [
        e
        for e in found
        if e.tool == tool and in_scope(e.identity, tool, scope=scope, mutants=mutants)
    ]
    registered = {(e.identity, e.function_hash) for e in mine}
    for mutant in mutants:
        if mutant.status in UNDECIDED[tool]:
            problems.append(f"{mutant.identity}: {mutant.status}; the run did not decide it")
        elif (
            mutant.status in LIVE[tool]
            and (mutant.identity, mutant.function_hash) not in registered
        ):
            problems.append(
                f"{mutant.identity}: {mutant.status} and not in {REGISTER}; to register:\n"
                f"{stanza(tool, mutant)}"
            )
    problems.extend(
        f"{entry.identity}: register entry is stale; the mutant is killed, gone or its function"
        " changed"
        for entry in mine
        if (entry.identity, entry.function_hash) not in live
    )
    killed = sum(m.status in KILLED[tool] for m in mutants)
    print(
        f"{tool}: {killed}/{len(mutants)} killed, {len(live)} live, {len(mine)} registered in scope"
    )
    return problems


def in_scope(identity: str, tool: str, *, scope: list[str], mutants: list[Mutant]) -> bool:
    """Whether a register entry is judged by this run: mutmut by pattern, stryker by file."""
    if tool == "mutmut":
        return not scope or any(fnmatch.fnmatchcase(identity, p) for p in scope)
    files = {m.identity.split(":", 1)[0] for m in mutants}
    return identity.split(":", 1)[0] in files


def check(options: argparse.Namespace, root: Path) -> list[str]:
    found, problems = entries(root)
    if options.tool == "mutmut":
        if options.results is None:
            return [*problems, "--results is required for mutmut (`mutmut results --all`, or `-`)"]
        text = sys.stdin.read() if options.results == "-" else _read(root, options.results)
        if text is None:
            return [*problems, f"{options.results}: missing"]
        scope = options.only or []
        mutants = [
            m
            for m in mutmut_mutants(text, root)
            if in_scope(m.identity, "mutmut", scope=scope, mutants=[])
        ]
    else:
        path = _resolve(root, options.report or str(STRYKER_REPORT))
        if not path.is_file():
            given = options.report or STRYKER_REPORT
            return [*problems, f"{given}: missing; run stryker with the json reporter first"]
        report = _json(path)
        given = [f for files in options.only or [] for f in files.split(",") if f]
        if nothing_to_mutate(root, report, given):
            print("stryker: no mutant in the files it was given; nothing to mutate yet")
            return problems
        mutants = stryker_mutants(report)
    return problems + judge(options.tool, mutants, found, scope=options.only or [])


def _resolve(root: Path, given: str) -> Path:
    path = Path(given)
    return path if path.is_absolute() else root / path


def _read(root: Path, given: str) -> str | None:
    return gatelib.read_text(_resolve(root, given))


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", default=".")
    parser.add_argument("--tool", choices=TOOLS, required=True)
    parser.add_argument("--results", help="mutmut: `mutmut results --all` output, `-` for stdin")
    parser.add_argument("--report", help="stryker: the json reporter's file")
    parser.add_argument(
        "--only", nargs="+", help="the run's scope: mutmut patterns, or stryker's `--mutate` files"
    )
    parser.add_argument(
        "--changed", action="store_true", help="print the scope of a changed-code run"
    )
    parser.add_argument("--base", help="the merge base to compare with")
    options = parser.parse_args(argv[1:])
    root = Path(options.root).resolve()
    if options.changed:
        if options.tool == "mutmut":
            scope = "\n".join(mutmut_changed(root, options.base))
        else:
            scope = ",".join(stryker_changed(root, options.base))
        if scope:
            print(scope)
        return 0
    return gatelib.report("mutation", check(options, root))


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
