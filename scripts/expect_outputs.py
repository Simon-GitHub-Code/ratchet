#!/usr/bin/env python3
from __future__ import annotations

import argparse
import difflib
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

import gatelib
import markdown_text

if TYPE_CHECKING:
    from collections.abc import Sequence

SECTION = "## Recorded outputs"
NONE = "none:"
EMPTY_PATTERNS = frozenset({"", "-", "—", "none"})


@dataclass(frozen=True)
class Rule:
    paths: tuple[str, ...]
    pattern: re.Pattern[str] | None = None


@dataclass(frozen=True)
class Listing:
    added: tuple[str, ...]
    altered: tuple[str, ...]


@dataclass(frozen=True)
class _Scope:
    root: Path
    base: str
    head: str | None = None


def _unwrap(cell: str) -> str:
    return cell.strip().removeprefix("`").removesuffix("`").strip()


def _pattern(cell: str) -> tuple[bool, re.Pattern[str] | None]:
    raw = cell.strip()
    text = _unwrap(cell).replace("\\|", "|")
    lower = text.lower()
    if (
        raw.startswith("<")
        or text.startswith("<")
        or lower in EMPTY_PATTERNS
        or lower.startswith(NONE)
    ):
        return True, None
    try:
        return True, re.compile(text)
    except re.error:
        return False, None


def parse(text: str) -> tuple[Rule, ...]:
    found = markdown_text.table(text, SECTION)
    if found is None:
        return ()
    rules: list[Rule] = []
    for row in found.rows:
        first, second = (*row, "")[:2]
        paths_cell = _unwrap(first)
        if (
            not paths_cell
            or first.strip().startswith("<")
            or paths_cell.startswith("<")
            or paths_cell.lower().startswith(NONE)
        ):
            continue
        paths = tuple(_unwrap(glob) for glob in paths_cell.split(",") if _unwrap(glob))
        ok, pattern = _pattern(second)
        if paths and ok:
            rules.append(Rule(paths, pattern))
    return tuple(rules)


def declaration(root: Path, base: str) -> tuple[Rule, ...]:
    text = gatelib.show(root, base, str(gatelib.BINDING))
    return () if text is None else parse(text)


def _changed(root: Path, base: str, head: str | None) -> list[str]:
    if head is None:
        return gatelib.changed_paths(root, base)
    listed = gatelib.git(root, "diff", "--name-only", "--no-renames", "-z", base, head) or ""
    return sorted(path for path in listed.split("\0") if path)


def _lines(root: Path, ref: str | None, path: str) -> list[str]:
    text = (
        gatelib.read_text(root / path)
        if ref is None
        else gatelib.git_raw(root, "show", f"{ref}:{path}")
    )
    return [] if text is None else text.splitlines()


def _classify_inline(scope: _Scope, path: str, patterns: Sequence[re.Pattern[str]]) -> str | None:
    def has_match(lines: Sequence[str]) -> bool:
        return any(pattern.search(line) is not None for pattern in patterns for line in lines)

    after = _lines(scope.root, scope.head, path)
    if gatelib.git(scope.root, "cat-file", "-e", f"{scope.base}:{path}") is None:
        return "added" if has_match(after) else None
    before = _lines(scope.root, scope.base, path)
    opcodes = difflib.SequenceMatcher(None, before, after).get_opcodes()
    if any(
        tag in ("replace", "delete") and has_match(before[i1:i2]) for tag, i1, i2, _, _ in opcodes
    ):
        return "altered"
    if any(
        tag in ("insert", "replace") and has_match(after[j1:j2]) for tag, _, _, j1, j2 in opcodes
    ):
        return "added"
    return None


def _classify_path(scope: _Scope, path: str, matching: Sequence[Rule]) -> str | None:
    patterns = [rule.pattern for rule in matching if rule.pattern is not None]
    if len(patterns) < len(matching):
        existed = gatelib.git(scope.root, "cat-file", "-e", f"{scope.base}:{path}") is not None
        return "altered" if existed else "added"
    return _classify_inline(scope, path, patterns)


def listing(root: Path, base: str, head: str | None = None) -> Listing:
    rules = declaration(root, base)
    if not rules:
        return Listing((), ())
    scope = _Scope(root, base, head)
    added: list[str] = []
    altered_paths: list[str] = []
    for path in _changed(root, base, head):
        matching = [rule for rule in rules if gatelib.matches(path, list(rule.paths))]
        if not matching:
            continue
        kind = _classify_path(scope, path, matching)
        if kind == "added":
            added.append(path)
        elif kind == "altered":
            altered_paths.append(path)
    return Listing(tuple(added), tuple(altered_paths))


def altered(root: Path, base: str, head: str | None = None) -> list[str]:
    return list(listing(root, base, head).altered)


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="expect_outputs.py")
    parser.add_argument("--root", type=Path, default=Path())
    parser.add_argument("--base")
    parser.add_argument("--json", action="store_true")
    options = parser.parse_args(argv[1:])
    root = options.root.resolve()
    base = gatelib.base_commit(root, options.base)
    if base is None:
        if options.json:
            print(json.dumps({"added": [], "altered": []}))
        else:
            print("expect-outputs: none: there is no commit to compare the tree with")
        return 0
    found = listing(root, base)
    if options.json:
        print(json.dumps({"added": list(found.added), "altered": list(found.altered)}))
        return 0
    if not found.added and not found.altered:
        print("expect-outputs: none")
        return 0
    for path in found.added:
        print(f"added\t{path}")
    for path in found.altered:
        print(f"altered\t{path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
