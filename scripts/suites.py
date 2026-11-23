from __future__ import annotations

import re
from dataclasses import dataclass

import gatelib
import markdown_text

SECTION = "## Tests"
COLUMNS = ("Suite", "Gate", "Files", "Run files")
IMPACT_COLUMNS = ("Sources", "Suite", "Tests")
NONE_PREFIX = "none:"
MIN_REASON = 10
PLACEHOLDER = (
    "the suites table still has the template's placeholder row; list each suite of this project,"
    " the gate that runs it whole, the globs of its test files and the recipe that runs given files"
)
NAME = re.compile(r"[A-Za-z][\w -]*")
COMMAND = re.compile(r"just [A-Za-z_][\w-]*")


@dataclass(frozen=True)
class Suite:
    name: str
    gate: str
    globs: tuple[str, ...]
    run: str
    reason: str


@dataclass(frozen=True)
class Impact:
    sources: tuple[str, ...]
    suite: str
    tests: tuple[str, ...]


def _row(row: tuple[str, ...], problems: list[str]) -> Suite | None:
    name, gate, files, run = (cell.strip() for cell in row)
    name = name.strip("`")
    if any(cell.startswith("<") for cell in row):
        problems.append(PLACEHOLDER)
        return None
    if name.lower().startswith(NONE_PREFIX):
        if len(name[len(NONE_PREFIX) :].strip()) < MIN_REASON:
            problems.append("a suites row says none with no reason: say why")
        return None
    return _suite(name, (gate, files, run), problems)


def _suite(name: str, cells: tuple[str, str, str], problems: list[str]) -> Suite | None:
    gate, files, run = cells
    commands = markdown_text.ticked(gate)
    globs = tuple(markdown_text.ticked(files))
    ways = [way for way in markdown_text.ticked(run) if COMMAND.fullmatch(way)]
    plain = run.strip("` ")
    reason = plain[len(NONE_PREFIX) :].strip() if plain.lower().startswith(NONE_PREFIX) else ""
    if (
        not NAME.fullmatch(name)
        or len(commands) != 1
        or not COMMAND.fullmatch(commands[0])
        or not globs
    ):
        problems.append(
            f"the suites row '{name}' needs a name, one `just <recipe>` gate and backticked globs"
        )
        return None
    if len(ways) > 1 or bool(ways) == bool(reason):
        which = "both" if ways else "neither"
        problems.append(
            f"the suites row '{name}' has {which} a `just <recipe>` that takes files and a"
            f" `{NONE_PREFIX} <reason>`: say exactly one"
        )
        return None
    if ways:
        return Suite(name, commands[0], globs, ways[0], "")
    if len(reason) < MIN_REASON:
        problems.append(f"the suites row '{name}' says none with no reason: say why")
        return None
    return Suite(name, commands[0], globs, "", reason)


def table(binding: str) -> markdown_text.Table | None:
    found = [t for t in markdown_text.tables(binding, SECTION) if t.header == COLUMNS]
    return found[0] if found else None


def parse(binding: str | None) -> tuple[list[Suite] | None, list[str]]:
    if binding is None or not markdown_text.has_section(binding, SECTION):
        return None, [f"has no '{SECTION}' section"]
    found = table(binding)
    if found is None:
        return None, [f"'{SECTION}' lacks its suites table with the columns {' | '.join(COLUMNS)}"]
    problems: list[str] = []
    suites = [suite for row in found.rows if (suite := _row(row, problems))]
    names = [suite.name for suite in suites]
    if len(set(names)) != len(names):
        problems.append("two suites rows have one name")
    return (None if problems else suites), problems


def split(binding: str) -> tuple[dict[str, str], str]:
    found = table(binding)
    if found is None:
        return {}, binding
    lines = binding.split("\n")
    rows = {row[0].strip().strip("`"): "|".join(cell.strip() for cell in row) for row in found.rows}
    kept = [*lines[: found.first], *lines[found.last :]]
    return rows, "\n".join(kept)


def owners(suites: list[Suite], path: str) -> list[Suite]:
    return [suite for suite in suites if gatelib.matches(path, list(suite.globs))]


def impact_table(binding: str) -> markdown_text.Table | None:
    found = [t for t in markdown_text.tables(binding, SECTION) if t.header == IMPACT_COLUMNS]
    return found[0] if found else None


def _impact_row(row: tuple[str, ...], known_names: set[str], problems: list[str]) -> Impact | None:
    sources_cell, suite_cell, tests_cell = (cell.strip() for cell in row)
    plain = sources_cell.strip("` ")
    if plain.startswith("<"):
        return None
    if plain.lower().startswith(NONE_PREFIX):
        if len(plain[len(NONE_PREFIX) :].strip()) < MIN_REASON:
            problems.append("an impact triggers row says none with no reason: say why")
        return None
    sources = tuple(markdown_text.ticked(sources_cell))
    suite_name = suite_cell.strip("` ")
    tests = tuple(markdown_text.ticked(tests_cell))
    if not sources or suite_name not in known_names or not tests:
        problems.append(
            f"the impact triggers row for '{suite_name}' needs backticked source globs,"
            " a known suite name and backticked test globs"
        )
        return None
    return Impact(sources, suite_name, tests)


def parse_impacts(binding: str | None, known: list[Suite]) -> tuple[list[Impact] | None, list[str]]:
    if binding is None or not markdown_text.has_section(binding, SECTION):
        return [], []
    found = impact_table(binding)
    if found is None:
        return [], []
    known_names = {suite.name for suite in known}
    problems: list[str] = []
    impacts = [impact for row in found.rows if (impact := _impact_row(row, known_names, problems))]
    return (None if problems else impacts), problems


def impacted(impacts: list[Impact], changed: list[str], tracked: list[str]) -> dict[str, list[str]]:
    selected: dict[str, set[str]] = {}
    for impact in impacts:
        sources = list(impact.sources)
        if any(gatelib.matches(path, sources) for path in changed):
            tests = list(impact.tests)
            selected.setdefault(impact.suite, set()).update(
                path for path in tracked if gatelib.matches(path, tests)
            )
    return {suite: sorted(paths) for suite, paths in selected.items() if paths}
