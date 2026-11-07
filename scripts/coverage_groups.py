#!/usr/bin/env python3
"""The grouped coverage gate: per-group line and branch coverage against the ratchet.

Reads a Cobertura report (``coverage.xml``, which coverage.py, c8, vitest, Jest and most tools
emit) and the coverage groups in ``quality/thresholds.toml``::

    [python.coverage.core]
    paths = ["src/core/**"]
    absolute = true           # held at 100% by this gate, not by a number in the file

    [python.coverage.app]
    paths = ["src/app/**"]
    line = 90.0
    branch = 80.0

Every table two levels under ``<language>.coverage`` is a group; its ``paths`` are globs over
repository-relative paths. An absolute group needs 100% lines and branches and may carry no
coverage exclusion in its files (``pragma: no cover``, ``istanbul ignore`` and peers). A measured
file that falls in no group fails: new code cannot land outside the rules. ``--write`` raises each
group's recorded line and branch to the measured value and never lowers one.

Usage: ``coverage_groups.py [--root DIR] [--report FILE] [--write]``.
"""

from __future__ import annotations

import argparse
import re
import sys
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path

import gatelib

FULL = 100.0
CONDITION = re.compile(r"\((\d+)/(\d+)\)")
EXCLUSION = re.compile(
    r"pragma:\s*no\s*cover|(?:istanbul|c8|v8)\s+ignore|coverage:\s*ignore|#\[coverage\(off\)\]"
)
EXCLUDED_FROM_SCAN = (".xml", ".toml", ".md", ".json", ".lock")


@dataclass
class Group:
    """One ``[<language>.coverage.<group>]`` table: its paths and what it must reach."""

    key: str
    paths: list[str]
    absolute: bool
    line: float | None
    branch: float | None


@dataclass
class Tally:
    """Lines and branches, total and hit, over one file or one group."""

    lines: int = 0
    lines_hit: int = 0
    branches: int = 0
    branches_hit: int = 0
    files: list[str] = field(default_factory=list)

    def rate(self, kind: str) -> float:
        """The percentage covered, ``100`` when nothing was measured."""
        total, hit = (
            (self.lines, self.lines_hit) if kind == "line" else (self.branches, self.branches_hit)
        )
        return FULL if total == 0 else round(hit / total * 100, 1)


def groups(thresholds: dict[str, object]) -> list[Group]:
    found = []
    for language, tables in thresholds.items():
        if language == "ceilings" or not isinstance(tables, dict):
            continue
        for name, table in gatelib.table(tables, "coverage").items():
            if not isinstance(table, dict):
                continue
            paths = table.get("paths")
            found.append(
                Group(
                    key=f"{language}.coverage.{name}",
                    paths=[p for p in paths if isinstance(p, str)]
                    if isinstance(paths, list)
                    else [],
                    absolute=table.get("absolute") is True,
                    line=_number(table.get("line")),
                    branch=_number(table.get("branch")),
                )
            )
    return found


def _number(value: object) -> float | None:
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else None


def measured(report: Path, root: Path) -> dict[str, Tally]:
    """Per-file tallies, keyed by the repository-relative path the report resolves to."""
    tree = ET.parse(report)  # noqa: S314 # -- see the import
    sources = [s.text.strip() for s in tree.iter("source") if s.text and s.text.strip()]
    tallies: dict[str, Tally] = {}
    for cls in tree.iter("class"):
        path = relative(cls.get("filename", ""), sources, root)
        tally = tallies.setdefault(path, Tally())
        for line in cls.iter("line"):
            tally.lines += 1
            tally.lines_hit += int(line.get("hits", "0")) > 0
            if match := CONDITION.search(line.get("condition-coverage", "")):
                tally.branches_hit += int(match.group(1))
                tally.branches += int(match.group(2))
    return tallies


def relative(filename: str, sources: list[str], root: Path) -> str:
    """The report's filename as a repository-relative path, through its ``<source>`` roots."""
    for source in [*sources, ""]:
        candidate = Path(source) / filename if source else Path(filename)
        if candidate.is_absolute():
            try:
                return candidate.resolve().relative_to(root.resolve()).as_posix()
            except ValueError:
                continue
        if (root / candidate).is_file():
            return candidate.as_posix()
    return filename


def tally_groups(found: list[Group], files: dict[str, Tally]) -> tuple[dict[str, Tally], list[str]]:
    """Each group's tally over its files, and the measured files no group claims."""
    totals = {group.key: Tally() for group in found}
    orphans = []
    for path, tally in sorted(files.items()):
        group = next((g for g in found if gatelib.matches(path, g.paths)), None)
        if group is None:
            orphans.append(path)
            continue
        total = totals[group.key]
        total.lines += tally.lines
        total.lines_hit += tally.lines_hit
        total.branches += tally.branches
        total.branches_hit += tally.branches_hit
        total.files.append(path)
    return totals, orphans


def exclusions(root: Path, files: list[str]) -> list[str]:
    hits = []
    for path in files:
        if path.endswith(EXCLUDED_FROM_SCAN):
            continue
        text = gatelib.read_text(root / path)
        for number, line in enumerate((text or "").splitlines(), 1):
            if EXCLUSION.search(line):
                hits.append(f"{path}:{number}: a coverage exclusion on an absolute path")
    return hits


def judge(group: Group, tally: Tally, root: Path) -> list[str]:
    problems = []
    for kind, recorded in (("line", group.line), ("branch", group.branch)):
        rate = tally.rate(kind)
        if group.absolute:
            if rate < FULL:
                problems.append(
                    f"{group.key}: {kind} coverage {rate}% on an absolute path, needs 100%"
                )
        elif recorded is None:
            problems.append(f"{group.key}: no {kind} threshold recorded; `--write` records {rate}%")
        elif rate < recorded:
            problems.append(f"{group.key}: {kind} coverage {rate}% below the ratchet {recorded}%")
    if group.absolute:
        problems += exclusions(root, tally.files)
    return problems


def raised(text: str, group: Group, tally: Tally) -> str:
    """The thresholds text with the group's line and branch raised to the measured values."""
    block = re.compile(rf"(?m)^{re.escape(f'[{group.key}]')}\n(?P<body>(?:(?!\[)[^\n]*\n)*)")
    for kind, recorded in (("line", group.line), ("branch", group.branch)):
        rate = tally.rate(kind)
        found = block.search(text)
        if found is None or group.absolute or (recorded is not None and rate <= recorded):
            continue
        body = found.group("body")
        key_line = re.compile(rf"(?m)^{kind} = [^\n]*$")
        if key_line.search(body):
            body = key_line.sub(f"{kind} = {rate}", body, count=1)
        else:
            core = body.rstrip("\n")
            body = core + ("\n" if core else "") + f"{kind} = {rate}\n" + body[len(core) + 1 :]
        text = text[: found.start("body")] + body + text[found.end("body") :]
    return text


def write(root: Path, found: list[Group], totals: dict[str, Tally]) -> None:
    path = root / gatelib.THRESHOLDS
    text = path.read_text(encoding="utf-8")
    for group in found:
        text = raised(text, group, totals[group.key])
    path.write_text(text, encoding="utf-8")


def problems(root: Path, report: Path, *, write_back: bool = False) -> list[str]:
    if not report.is_file():
        return [f"{report}: missing; run the suite with a Cobertura report first"]
    found = groups(gatelib.thresholds(root))
    if not found:
        return [f"{gatelib.THRESHOLDS}: no [<language>.coverage.<group>] table with paths"]
    totals, orphans = tally_groups(found, measured(report, root))
    if write_back:
        write(root, found, totals)
        found = groups(gatelib.thresholds(root))
    faults = [f"{path}: measured but in no coverage group" for path in orphans]
    for group in found:
        tally = totals[group.key]
        print(f"{group.key}: {tally.rate('line')}% lines, {tally.rate('branch')}% branches")
        faults += judge(group, tally, root)
    return faults


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", default=".")
    parser.add_argument("--report", default="coverage.xml")
    parser.add_argument("--write", action="store_true")
    options = parser.parse_args(argv[1:])
    root = Path(options.root).resolve()
    report = Path(options.report)
    report = report if report.is_absolute() else root / report
    return gatelib.report("coverage", problems(root, report, write_back=options.write))


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
