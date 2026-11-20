#!/usr/bin/env python3
from __future__ import annotations

import argparse
import sys
from collections import Counter
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import TYPE_CHECKING

import application_types
import architecture
import gatelib
import justfile_text
import markdown_text
import owed
import pins
import plugin_pin
import suppressions

if TYPE_CHECKING:
    from collections.abc import Iterable, Sequence

RULE = owed.COMMENTS
LIBRARY = "library"
PUBLISHED = "Published interface"
NOT_A_LIBRARY = "none: not a library"
FIELDS = ("path", "line", "kind", "text")
SECTION = "## Comments"
COLUMNS = ("Files", "Comments")
LISTED = "listed"
LISTER = "comments-list"
JUSTFILE = "justfile"
NONE_PREFIX = "none:"
MIN_REASON = 10
DOCUMENTS = (".md", ".markdown")
SNIFFED = 8000
RECORD_REFUSED = (
    f"{RULE}: refused: only the adoption and the change that first fills the lister record what"
    " the gate finds; a later comment is red until it is gone"
)
RECORD_UNPINNED = (
    f"{RULE}: refused: the binding pins no plugin version for an entry to be owed since"
)


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
    red: tuple[Record, ...] = ()
    found: tuple[Record, ...] = ()


@dataclass(frozen=True)
class Row:
    globs: tuple[str, ...]
    listed: bool


@dataclass(frozen=True)
class Table:
    rows: tuple[Row, ...]
    problems: tuple[str, ...]


def row_of(rules: Table, path: str) -> Row | None:
    return next((row for row in rules.rows if gatelib.matches(path, list(row.globs))), None)


def listed(rules: Table, path: str) -> bool:
    row = row_of(rules, path)
    return row is not None and row.listed


@dataclass(frozen=True)
class Scope:
    paths: tuple[str, ...]
    binding: str | None
    whole: bool
    note: str = ""


class RefusedError(ValueError):
    pass


def table(binding: str | None) -> Table | None:
    if binding is None or not markdown_text.has_section(binding, SECTION):
        return None
    found = markdown_text.table(binding, SECTION)
    if found is None or found.header != COLUMNS:
        return Table((), (f"the section lacks its table with the columns {' | '.join(COLUMNS)}",))
    rows: list[Row] = []
    problems: list[str] = []
    for files, comments in found.rows:
        globs = tuple(markdown_text.ticked(files))
        said = comments.strip("` ")
        if files.startswith("<") or comments.startswith("<"):
            problems.append("the Comments table still has the template's placeholder row")
        elif not globs:
            problems.append(f"the Comments row '{files}' gives no glob in backticks")
        elif said == LISTED:
            rows.append(Row(globs, listed=True))
        elif said.startswith(NONE_PREFIX) and len(said[len(NONE_PREFIX) :].strip()) >= MIN_REASON:
            rows.append(Row(globs, listed=False))
        else:
            problems.append(
                f"the Comments row {files} says '{comments}': write `listed`, or"
                " `none: <reason>` for code nobody writes by hand"
            )
    if not rows and not problems:
        problems.append("the Comments table has no row: one row per kind of tracked file")
    return Table(tuple(rows), tuple(problems))


def placeholder(justfile: str | None) -> bool:
    if justfile is None or not justfile_text.defines_recipe(justfile, LISTER):
        return False
    return justfile_text.BINDS in justfile_text.code_of(
        "\n".join(justfile_text.definition(justfile, LISTER))
    )


def bound(binding: str | None, justfile: str | None) -> bool:
    found = table(binding)
    return found is not None and not found.problems and not placeholder(justfile)


def bound_at(root: Path, base: str | None) -> bool:
    if base is None:
        return bound(gatelib.read_text(root / gatelib.BINDING), gatelib.read_text(root / JUSTFILE))
    return bound(gatelib.show(root, base, str(gatelib.BINDING)), gatelib.show(root, base, JUSTFILE))


def first_listed(root: Path, base: str | None) -> str | None:
    if not bound_at(root, None) or (base is not None and bound_at(root, base)):
        return None
    pin = plugin_pin.pinned(gatelib.read_text(root / gatelib.BINDING))
    return pin.version if pin else None


def judgeable(root: Path, path: str) -> bool:
    if path.lower().endswith(DOCUMENTS):
        return False
    try:
        with (root / path).open("rb") as handle:
            return b"\0" not in handle.read(SNIFFED)
    except OSError:
        return False


def tracked_text(root: Path) -> list[str]:
    return [path for path in gatelib.tracked_paths(root) if judgeable(root, path)]


def scope(root: Path, base: str | None) -> Scope:
    binding = gatelib.read_text(root / gatelib.BINDING)
    if not bound_at(root, None):
        note = (
            f"{RULE}: green: the lister is not bound yet, so nothing is judged; the conformance"
            f" gate holds the '{SECTION}' section until it is filled"
        )
        return Scope((), binding, whole=False, note=note)
    then = gatelib.show(root, base, str(gatelib.BINDING)) if base is not None else None
    if base is not None and bound_at(root, base):
        rules = table(then) or Table((), ())
        changed = [p for p in gatelib.changed_paths(root, base) if judgeable(root, p)]
        return Scope(tuple(path for path in changed if listed(rules, path)), then, whole=False)
    rules = table(binding) or Table((), ())
    paths = tuple(path for path in tracked_text(root) if listed(rules, path))
    note = (
        f"{RULE}: the merge base has no bound lister, so this change judges every file a"
        " `listed` row of its own Comments table matches"
    )
    return Scope(paths, then or binding, whole=True, note=note)


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


def interface_problems(binding: str) -> list[str]:
    line = f"'- {PUBLISHED}:'"
    value = markdown_text.bullets(markdown_text.section(binding, architecture.SECTION)).get(
        PUBLISHED
    )
    if value is None:
        missing = (
            f"the Architecture section has no {line} line: the globs of the files whose doc"
            f" comments are a library's promise, each in backticks, or `{NOT_A_LIBRARY}`"
            " (ADR 0063)"
        )
        return [missing]
    if value.startswith("<"):
        return [f"the Architecture section's {line} line is still the template's placeholder"]
    if value.strip("` ") == NOT_A_LIBRARY:
        library = LIBRARY in (application_types.declared(binding) or [])
        return (
            [f"the types declare `{LIBRARY}`, and {line} says none: name the globs of its files"]
            if library
            else []
        )
    if not markdown_text.ticked(value):
        return [f"the Architecture section's {line} line names no glob in backticks"]
    return []


def directives(root: Path, paths: Iterable[str], patterns: Sequence[str]) -> set[tuple[str, int]]:
    searched = sorted(set(paths))
    if not searched or not patterns:
        return set()
    expressions = [part for pattern in patterns for part in ("-e", pattern)]
    out = gatelib.git(
        root,
        *("grep", "--no-index", "-I", "-n", "-z", "-E"),
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

    def allowed_record(record: Record) -> bool:
        if record.kind is Kind.DOC:
            return gatelib.matches(record.path, list(rules.published))
        return (record.path, record.line) in matched or pinned_note(root, record)

    return [record for record in records if not allowed_record(record)]


def pinned_note(root: Path, record: Record) -> bool:
    if not pins.is_workflow(record.path):
        return False
    lines = (gatelib.read_text(root / record.path) or "").split("\n")
    line = lines[record.line - 1] if record.line <= len(lines) else ""
    uses = pins.USES.match(line)
    return bool(uses and pins.SHA.search(uses.group(1)) and pins.VERSION_COMMENT.search(line))


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
    red = tuple(record for record, hit in zip(found, hits, strict=True) if not hit)
    problems += [finding_line(record) for record in red]
    problems += [
        stale_line(path, text, count)
        for (_, path, text), count in sorted(left.items())
        if path in judged or not (root / path).is_file()
    ]
    return Verdict(problems, sum(hits), red, tuple(found))


def record(root: Path, verdict: Verdict, *, since: str, base: str | None) -> int:
    added = [owed.Entry(r.path, RULE, r.text.strip(), since) for r in verdict.red if r.text.strip()]
    path = root / owed.LEDGER
    text = gatelib.read_text(path)
    held = owed.parse(gatelib.show(root, base, str(owed.LEDGER)) if base else None).entries
    renamed = gatelib.renamed(root, base) if base else {}
    found = Counter(key(r) for r in verdict.found)
    mine = []
    for entry in sorted(owed.brought(held, owed.parse(text).entries, renamed).elements()):
        if entry.rule == RULE and found[entry.key] > 0:
            found[entry.key] -= 1
            mine.append(entry)
    redated = [owed.Entry(e.path, e.rule, e.problem, since) for e in mine]
    made = owed.recorded([*redated, *added], mine)(text)
    if made != (text or ""):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(made, encoding="utf-8")
    return len(added)


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="comments.py")
    parser.add_argument("--root", type=Path, default=Path())
    parser.add_argument("--base")
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument("--files", action="store_true")
    modes.add_argument("--changed", action="store_true")
    parser.add_argument("--record", action="store_true")
    parser.add_argument("paths", nargs="*")
    options = parser.parse_args(argv[1:])
    root = options.root.resolve()
    base = gatelib.base_commit(root, options.base)
    if options.files or options.changed:
        judged = scope(root, base)
        if options.files:
            sys.stdout.write("".join(f"{path}\0" for path in judged.paths))
            return 0
        paths, binding = frozenset(judged.paths), judged.binding
        if judged.note:
            print(judged.note)
    else:
        judged = None
        paths = frozenset(options.paths)
        binding = (gatelib.show(root, base, str(gatelib.BINDING)) if base else None) or (
            gatelib.read_text(root / gatelib.BINDING)
        )
    output = sys.stdin.read()
    verdict = judge(root, output, judged=paths, binding=binding)
    if options.record:
        if judged is None or not judged.whole:
            return gatelib.report(RULE, [RECORD_REFUSED], base)
        pin = plugin_pin.pinned(gatelib.read_text(root / gatelib.BINDING))
        if pin is None:
            return gatelib.report(RULE, [RECORD_UNPINNED], base)
        added = record(root, verdict, since=pin.version, base=owed.next_base(root, options.base))
        print(f"{RULE}: recorded {added} owed since {pin.version} in {owed.LEDGER}")
        verdict = judge(root, output, judged=paths, binding=binding)
    if verdict.owed:
        print(f"{RULE}: {verdict.owed} owed in {owed.LEDGER}")
    return gatelib.report(RULE, verdict.problems, base)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
