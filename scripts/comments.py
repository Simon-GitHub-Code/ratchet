#!/usr/bin/env python3
from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import TYPE_CHECKING

import application_types
import architecture
import gatelib
import markdown_text
import owed
import suppressions

if TYPE_CHECKING:
    from collections.abc import Iterable, Sequence

RULE = "comments"
LIBRARY = "library"
PUBLISHED = "Published interface"
FIELDS = ("path", "line", "kind", "text")


class Kind(StrEnum):
    COMMENT = "comment"
    DOC = "doc"


KINDS = frozenset(kind.value for kind in Kind)


@dataclass(frozen=True)
class Record:
    path: str
    line: int
    kind: Kind
    text: str


@dataclass(frozen=True)
class Allowed:
    patterns: tuple[str, ...]
    published: tuple[str, ...]


@dataclass(frozen=True)
class Verdict:
    problems: list[str]
    owed: int


class RefusedError(ValueError):
    pass


def key(record: Record) -> owed.Key:
    return (RULE, record.path, record.text.strip())


def parsed(output: str, judged: frozenset[str]) -> list[Record]:
    records = []
    for line in output.split("\n"):
        if not line.strip():
            continue
        fields = line.split("\t", len(FIELDS) - 1)
        if len(fields) != len(FIELDS) or not fields[1].isdecimal() or fields[2] not in KINDS:
            names = f"{', '.join(FIELDS[:-1])} and {FIELDS[-1]}"
            message = f"the lister prints a line that is not a record of {names}: {line[:80]!r}"
            raise RefusedError(message)
        path, number, kind, text = fields
        if path not in judged:
            message = f"the lister reports {path}, a file it was not given"
            raise RefusedError(message)
        records.append(Record(path, int(number), Kind(kind), text))
    return records


def shown(root: Path, records: Sequence[Record]) -> None:
    files: dict[str, list[str] | None] = {}
    for record in records:
        if record.path not in files:
            text = gatelib.read_text(root / record.path)
            files[record.path] = None if text is None else text.removesuffix("\n").split("\n")
        lines = files[record.path]
        if lines is None:
            message = f"the lister reports {record.path}, which is not a text file here"
            raise RefusedError(message)
        if not 0 < record.line <= len(lines):
            message = (
                f"the lister reports line {record.line} of {record.path}, which has {len(lines)}"
            )
            raise RefusedError(message)
        if record.text not in lines[record.line - 1]:
            message = (
                f"the lister reports {record.text!r} at {record.path}:{record.line},"
                " which that line does not show"
            )
            raise RefusedError(message)


def allowed(binding: str | None) -> Allowed:
    spec = suppressions.parse(binding)
    patterns = tuple(kind.pattern for kind in spec.kinds) if spec else ()
    text = binding or ""
    if LIBRARY not in (application_types.declared(text) or []):
        return Allowed(patterns, ())
    lines = markdown_text.bullets(markdown_text.section(text, architecture.SECTION))
    return Allowed(patterns, tuple(markdown_text.ticked(lines.get(PUBLISHED, ""))))


def directives(root: Path, paths: Iterable[str], patterns: Sequence[str]) -> set[tuple[str, int]]:
    searched = sorted(set(paths))
    if not searched or not patterns:
        return set()
    expressions = [part for pattern in patterns for part in ("-e", pattern)]
    out = gatelib.git(
        root,
        *("--literal-pathspecs", "grep", "--no-index", "-I", "-n", "-z", "-E"),
        *expressions,
        *("--", *searched),
    )
    found = set()
    for line in filter(None, (out or "").split("\n")):
        path, number, _ = line.split("\0", 2)
        found.add((path, int(number)))
    return found


def findings(root: Path, records: Sequence[Record], rules: Allowed) -> list[Record]:
    matched = directives(root, (record.path for record in records), rules.patterns)
    return [
        record
        for record in records
        if (record.path, record.line) not in matched
        and not (record.kind is Kind.DOC and gatelib.matches(record.path, list(rules.published)))
    ]


def finding_line(record: Record) -> str:
    what = (
        "a doc comment outside the published interface of a library"
        if record.kind is Kind.DOC
        else "a comment no tool reads"
    )
    return f"{record.path}:{record.line}: {what}: {record.text.strip()!r}"


def stale_line(path: str, text: str, count: int) -> str:
    times = "" if count == 1 else f" ({count} entries)"
    return (
        f"{owed.LEDGER}: an entry owes the comment {text!r} in {path}{times}, which is no longer"
        " found; drop it"
    )


def judge(root: Path, output: str, *, judged: frozenset[str], binding: str | None) -> Verdict:
    try:
        records = parsed(output, judged)
        shown(root, records)
    except RefusedError as reason:
        return Verdict([f"{RULE}: refused: {reason}"], 0)
    found = findings(root, records, allowed(binding))
    ledger = owed.read(root)
    entries = [entry for entry in ledger.entries if entry.rule == RULE]
    hits, left = owed.excused([key(record) for record in found], entries)
    problems = [f"{owed.LEDGER}: {problem}" for problem in ledger.problems]
    problems += [finding_line(r) for r, hit in zip(found, hits, strict=True) if not hit]
    problems += [
        stale_line(path, text, count)
        for (_, path, text), count in sorted(left.items())
        if path in judged or not (root / path).is_file()
    ]
    return Verdict(problems, sum(hits))


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="comments.py")
    parser.add_argument("--root", type=Path, default=Path())
    parser.add_argument("--base")
    parser.add_argument("paths", nargs="*")
    options = parser.parse_args(argv[1:])
    root = options.root.resolve()
    base = gatelib.base_commit(root, options.base)
    binding = (gatelib.show(root, base, str(gatelib.BINDING)) if base else None) or (
        gatelib.read_text(root / gatelib.BINDING)
    )
    verdict = judge(root, sys.stdin.read(), judged=frozenset(options.paths), binding=binding)
    if verdict.owed:
        print(f"{RULE}: {verdict.owed} owed in {owed.LEDGER}")
    return gatelib.report(RULE, verdict.problems, base)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
