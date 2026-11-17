#!/usr/bin/env python3
from __future__ import annotations

import argparse
import re
import subprocess
import sys
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path

import gatelib
import suppressions

FULL = 100.0
CONDITION = re.compile(r"\((\d+)/(\d+)\)")
QUESTION = "coverage, absolute and ratcheted"
EXCLUDED_FROM_SCAN = (".xml", ".toml", ".md", ".json", ".lock")


@dataclass
class Group:
    key: str
    paths: list[str]
    absolute: bool
    line: float | None
    branch: float | None


@dataclass
class Tally:
    lines: int = 0
    lines_hit: int = 0
    branches: int = 0
    branches_hit: int = 0
    files: list[str] = field(default_factory=list)

    @property
    def measured(self) -> bool:
        return bool(self.files) or self.lines > 0 or self.branches > 0

    def rate(self, kind: str) -> float:
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


class UnreadableError(ValueError): ...


def _number(value: object) -> float | None:
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else None


def measured(report: Path, root: Path, tallies: dict[str, Tally] | None = None) -> dict[str, Tally]:
    tallies = {} if tallies is None else tallies
    if report.read_bytes().lstrip().startswith(b"<"):
        cobertura(report, root, tallies)
    elif not lcov(report.read_text(encoding="utf-8", errors="replace"), root, tallies):
        message = f"{report}: neither a Cobertura report nor an LCOV tracefile naming a source file"
        raise UnreadableError(message)
    return tallies


def cobertura(report: Path, root: Path, tallies: dict[str, Tally]) -> None:
    tree = ET.parse(report)  # noqa: S314 # -- see the import
    sources = [s.text.strip() for s in tree.iter("source") if s.text and s.text.strip()]
    for cls in tree.iter("class"):
        path = relative(cls.get("filename", ""), sources, root)
        tally = tallies.setdefault(path, Tally())
        tally.files = [path]
        for line in cls.findall("lines/line"):
            tally.lines += 1
            tally.lines_hit += int(line.get("hits", "0")) > 0
            if match := CONDITION.search(line.get("condition-coverage", "")):
                tally.branches_hit += int(match.group(1))
                tally.branches += int(match.group(2))


def lcov(text: str, root: Path, tallies: dict[str, Tally]) -> int:
    files: dict[str, dict[tuple[str, str], bool]] = {}
    marks: dict[tuple[str, str], bool] = {}
    for record in text.splitlines():
        kind, _, value = record.strip().partition(":")
        if kind == "SF":
            marks = files.setdefault(relative(value, [], root), {})
        elif kind == "DA":
            line, hits = value.split(",")[:2]
            marks[kind, line] = marks.get((kind, line), False) or int(hits) > 0
        elif kind == "BRDA":
            where, _, taken = value.rpartition(",")
            marks[kind, where] = marks.get((kind, where), False) or taken not in {"-", "0"}
    for path, found in files.items():
        tally = tallies.setdefault(path, Tally())
        tally.files = [path]
        lines = [hit for (kind, _), hit in found.items() if kind == "DA"]
        branches = [hit for (kind, _), hit in found.items() if kind == "BRDA"]
        tally.lines += len(lines)
        tally.lines_hit += sum(lines)
        tally.branches += len(branches)
        tally.branches_hit += sum(branches)
    return len(files)


def relative(filename: str, sources: list[str], root: Path) -> str:
    fallback = None
    for source in [*sources, ""]:
        candidate = Path(source) / filename if source else Path(filename)
        if candidate.is_absolute():
            try:
                path = candidate.resolve().relative_to(root.resolve()).as_posix()
            except ValueError:
                continue
            if (root / path).is_file():
                return path
            fallback = fallback or path
        elif (root / candidate).is_file():
            return candidate.as_posix()
    return fallback or filename


def tally_groups(found: list[Group], files: dict[str, Tally]) -> tuple[dict[str, Tally], list[str]]:
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


def exclusion_patterns(root: Path) -> list[str] | None:
    base = gatelib.base_commit(root)
    for text in (
        gatelib.show(root, base, str(gatelib.BINDING)) if base else None,
        gatelib.read_text(root / gatelib.BINDING),
    ):
        spec = suppressions.parse(text)
        if spec is not None and QUESTION in spec.silenced:
            names = spec.silenced[QUESTION]
            return [kind.pattern for kind in spec.kinds if kind.name in names]
    return None


def exclusions(root: Path, files: list[str], patterns: list[str]) -> list[str]:
    scanned = [path for path in files if not path.endswith(EXCLUDED_FROM_SCAN)]
    if not scanned or not patterns:
        return []
    wanted = [arg for pattern in patterns for arg in ("-e", pattern)]
    found = subprocess.run(
        ["git", "grep", "--no-index", "-I", "-n", "-E", *wanted, "--", *scanned],
        capture_output=True,
        text=True,
        check=False,
        cwd=root,
    )
    hits = [line.split(":", 2)[:2] for line in found.stdout.splitlines()]
    return [f"{path}:{number}: a coverage exclusion on an absolute path" for path, number in hits]


def judge(group: Group, tally: Tally, root: Path, *, patterns: list[str] | None) -> list[str]:
    if not tally.measured:
        return []
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
    if group.absolute and patterns is not None:
        problems += exclusions(root, tally.files, patterns)
    return problems


def raised(text: str, group: Group, tally: Tally) -> str:
    block = re.compile(rf"(?m)^{re.escape(f'[{group.key}]')}\n(?P<body>(?:(?!\[)[^\n]*\n)*)")
    if not tally.measured:
        return text
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


def problems(root: Path, reports: list[Path], *, write_back: bool = False) -> list[str]:
    missing = [
        f"{r}: missing; run the suite with a Cobertura or LCOV report first"
        for r in reports
        if not r.is_file()
    ]
    if missing:
        return missing
    found = groups(gatelib.thresholds(root))
    if not found:
        return [f"{gatelib.THRESHOLDS}: no [<language>.coverage.<group>] table with paths"]
    files: dict[str, Tally] = {}
    try:
        for report in reports:
            measured(report, root, files)
    except UnreadableError as unreadable:
        return [str(unreadable)]
    totals, orphans = tally_groups(found, files)
    if write_back:
        write(root, found, totals)
        found = groups(gatelib.thresholds(root))
    faults = [f"{path}: measured but in no coverage group" for path in orphans]
    patterns = exclusion_patterns(root)
    if patterns is None and any(group.absolute for group in found):
        print(
            "coverage: no exclusion is searched for on absolute paths: the binding's Suppressions"
            f" section says no kinds silence '{QUESTION}' (ADR 0050)"
        )
    for group in found:
        tally = totals[group.key]
        if tally.measured:
            print(f"{group.key}: {tally.rate('line')}% lines, {tally.rate('branch')}% branches")
        else:
            print(f"{group.key}: not measured; no file in the report falls in it")
        faults += judge(group, tally, root, patterns=patterns)
    return faults


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", default=".")
    parser.add_argument("--report", action="append", default=None, help="repeatable")
    parser.add_argument("--write", action="store_true")
    options = parser.parse_args(argv[1:])
    root = Path(options.root).resolve()
    reports = [Path(r) for r in options.report or ["coverage.xml"]]
    reports = [r if r.is_absolute() else root / r for r in reports]
    return gatelib.report("coverage", problems(root, reports, write_back=options.write))


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
