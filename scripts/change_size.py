#!/usr/bin/env python3
from __future__ import annotations

import argparse
import re
import sys
from dataclasses import dataclass
from pathlib import Path

import gatelib
import owed

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "hooks"))
from ratchet_guards import dependencies

ADOPTION = "chore(method): adopt"
SAFEGUARD = "chore(method): safeguard"
LATER_ROW = re.compile(r"^\|\s*([^|]+?)\s*\|\s*`?later:", re.MULTILINE | re.IGNORECASE)
GATE_ROW = re.compile(r"^\|\s*([^|]+?)\s*\|\s*([^|]+?)\s*\|", re.MULTILINE)
CEILINGS_TABLE = "[ceilings.change]"
RECORDS = [
    "docs/features/**/*.md",
    "docs/features/**/verdict.json",
    "docs/disputes/**/*.md",
    ".scratch/**/*.md",
    str(owed.LEDGER),
    "quality/snapshots/**",
    ".mise/locks/**",
]
SECTION = "## Test paths"
LABEL = "Tests"
BULLET = re.compile(r"^- ([^:\n]+):[ \t]*(.*?)[ \t]*$")
TICKED = re.compile(r"`([^`\n]+)`")
NONE_PREFIX = "none:"
MIN_REASON = 10
KEYS = ("product_files", "product_lines", "test_lines")
LEGACY = {"product_files": "files", "product_lines": "lines"}
NO_ROW = (
    "change-size: the binding has no filled `## Test paths` section, so every changed line counts"
    " as product; write the globs of this repository's test files there, or `none: <reason>`"
)


@dataclass(frozen=True)
class Size:
    product_files: int = 0
    product_lines: int = 0
    test_files: int = 0
    test_lines: int = 0


@dataclass(frozen=True)
class Ceilings:
    product_files: int | None = None
    product_lines: int | None = None
    test_lines: int | None = None

    def recorded(self) -> bool:
        found = (self.product_files, self.product_lines, self.test_lines)
        return any(value is not None for value in found)


def not_counted(path: str, contract: dependencies.Contract | None = None) -> bool:
    return gatelib.matches(path, RECORDS) or (contract is not None and contract.is_lockfile(path))


def test_paths(binding: str | None) -> tuple[list[str] | None, list[str]]:
    lines = (binding or "").split("\n")
    start = next((i for i, line in enumerate(lines) if line.rstrip() == SECTION), None)
    if start is None:
        return None, [f"has no '{SECTION}' section"]
    value = ""
    for line in lines[start + 1 :]:
        if line.startswith("## "):
            break
        if (match := BULLET.match(line)) and match.group(1).strip() == LABEL:
            value = match.group(2)
            break
    plain = value.strip("` ")
    if not value or value.startswith("<"):
        return None, [f"'- {LABEL}:' is still the template's: name globs, or `none: <reason>`"]
    if plain.lower().startswith(NONE_PREFIX):
        if len(plain[len(NONE_PREFIX) :].strip()) < MIN_REASON:
            return None, [f"'- {LABEL}: none:' needs the reason this stack has no tests"]
        return [], []
    found = [item.strip() for item in TICKED.findall(value) if item.strip()]
    if not found:
        return None, [f"'- {LABEL}:' names its globs in backticks, or says `none: <reason>`"]
    return found, []


def test_globs(root: Path, base: str) -> list[str] | None:
    return test_paths(gatelib.show(root, base, str(gatelib.BINDING)))[0]


def _changes(root: Path, base: str) -> list[tuple[str, str, str, str | None]]:
    out = gatelib.git(root, "diff", "--numstat", "--find-renames", "-z", base) or ""
    fields = iter(out.split("\0")[:-1])
    found = []
    for head in fields:
        added, deleted, path = head.split("\t", 2)
        old = None
        if not path:
            old, path = next(fields), next(fields)
        found.append((added, deleted, path, old))
    return found


def measure(root: Path, base: str) -> Size:
    tests = test_globs(root, base) or []
    product, test = [0, 0], [0, 0]
    contract = dependencies.parse(gatelib.show(root, base, str(gatelib.BINDING)))

    def skipped(path: str) -> bool:
        return not_counted(path, contract)

    def add(path: str, lines: int) -> None:
        kind = test if gatelib.matches(path, tests) else product
        kind[0] += 1
        kind[1] += lines

    for added, deleted, path, old in _changes(root, base):
        if skipped(path) and (old is None or skipped(old)):
            continue
        add(path, (int(added) if added != "-" else 0) + (int(deleted) if deleted != "-" else 0))
    untracked = gatelib.git(root, "ls-files", "--others", "--exclude-standard", "-z", ":/") or ""
    for path in (p for p in untracked.split("\0") if p and not skipped(p)):
        text = gatelib.read_text(root / path)
        add(path, len(text.splitlines()) if text is not None else 0)
    return Size(product[0], product[1], test[0], test[1])


def _limit(value: object) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else None


def _entry(table: dict[str, object], key: str) -> int | None:
    return _limit(table[key]) if key in table else _limit(table.get(LEGACY.get(key, "")))


def ceilings(root: Path) -> Ceilings:
    table = gatelib.table(gatelib.thresholds(root), "ceilings", "change")
    return Ceilings(*(_entry(table, key) for key in KEYS))


def malformed(root: Path) -> list[str]:
    table = gatelib.table(gatelib.thresholds(root), "ceilings", "change")
    return [
        f"ceilings.change.{name}: {table[name]!r} is not a non-negative integer"
        for name in (*KEYS, *LEGACY.values())
        if name in table and _limit(table[name]) is None
    ]


def over(size: Size, limits: Ceilings) -> list[str]:
    measured = (
        ("product files", size.product_files, limits.product_files),
        ("product lines", size.product_lines, limits.product_lines),
        ("test lines", size.test_lines, limits.test_lines),
    )
    return [
        f"{name}: {value} changed, ceiling {limit}"
        for name, value, limit in measured
        if limit is not None and value > limit
    ]


def is_adoption(root: Path, base: str) -> bool:
    found = gatelib.subjects(root, base)
    return bool(found) and all(s.startswith(ADOPTION) for s in found)


def bound_questions(text: str) -> set[str]:
    return {
        m.group(1).strip()
        for m in GATE_ROW.finditer(text)
        if (tool := m.group(2).strip(" `").lower()) and not tool.startswith(("later:", "none"))
    }


def is_safeguard(root: Path, base: str) -> bool:
    found = gatelib.subjects(root, base)
    if not found or not all(s.startswith(SAFEGUARD) for s in found):
        return False
    before = gatelib.show(root, base, str(gatelib.BINDING)) or ""
    now = gatelib.read_text(root / gatelib.BINDING) or ""
    later_at_base = {m.group(1).strip() for m in LATER_ROW.finditer(before)}
    return bool(later_at_base & bound_questions(now))


def merged_on_default(root: Path) -> bool:
    head = gatelib.git(root, "rev-parse", "--abbrev-ref", "HEAD")
    default = gatelib.default_branches(root)[0].removeprefix("origin/")
    clean = not gatelib.git(root, "status", "--porcelain")
    merge = gatelib.git(root, "rev-parse", "--verify", "--quiet", "HEAD^2") is not None
    return head == default and clean and merge


def exemption(root: Path, base: str) -> str | None:
    if merged_on_default(root):
        return "HEAD merges a change the owner already judged; size reported only"
    if is_adoption(root, base):
        return "the adoption commit is exempt; carry the size in the PR's Criticism"
    if is_safeguard(root, base):
        return "the safeguard binding is exempt; carry the size in the PR's Criticism"
    return None


def with_ceilings(text: str, ceilings: tuple[int, int, int]) -> str:
    files, lines, tests = ceilings
    body = (
        f"{CEILINGS_TABLE}\nproduct_files = {files}\nproduct_lines = {lines}\n"
        f"test_lines = {tests}\n"
    )
    pattern = re.compile(r"^\[ceilings\.change\]\n(?:[^\[\n][^\n]*\n|\n)*", re.MULTILINE)
    if pattern.search(text):
        return pattern.sub(body + "\n", text).rstrip("\n") + "\n"
    return (text.rstrip("\n") + "\n\n" + body) if text.strip() else body


def record(root: Path, ceilings: tuple[int, int, int]) -> int:
    files, lines, tests = ceilings
    path = root / gatelib.THRESHOLDS
    path.parent.mkdir(parents=True, exist_ok=True)
    current = path.read_text(encoding="utf-8") if path.is_file() else ""
    path.write_text(with_ceilings(current, ceilings), encoding="utf-8")
    print(
        f"change-size: ceilings recorded, {files} product files, {lines} product lines,"
        f" {tests} test lines"
    )
    return 0


def describe(size: Size, limits: Ceilings) -> str:
    def of(limit: int | None) -> str:
        return "no ceiling" if limit is None else f"ceiling {limit}"

    return (
        f"change-size: {size.product_files} product files ({of(limits.product_files)}),"
        f" {size.product_lines} product lines ({of(limits.product_lines)}),"
        f" {size.test_lines} test lines ({of(limits.test_lines)}) changed"
    )


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", default=".")
    parser.add_argument("--base")
    parser.add_argument("--set", action="store_true")
    parser.add_argument("--product-files", type=int)
    parser.add_argument("--product-lines", type=int)
    parser.add_argument("--test-lines", type=int)
    options = parser.parse_args(argv[1:])
    root = Path(options.root).resolve()
    if options.set:
        given = (options.product_files, options.product_lines, options.test_lines)
        if any(value is None for value in given):
            parser.error("--set needs --product-files N, --product-lines N and --test-lines N")
        return record(root, (given[0], given[1], given[2]))
    if problems := malformed(root):
        return gatelib.report("change-size", problems)
    base = gatelib.base_commit(root, options.base)
    if base is None:
        print("change-size: no base commit yet")
        return 0
    size = measure(root, base)
    limits = ceilings(root)
    print(describe(size, limits))
    if test_globs(root, base) is None:
        print(NO_ROW)
    if not limits.recorded():
        print(
            "change-size: no ceiling recorded; `--set --product-files N --product-lines N"
            " --test-lines N` records them"
        )
        return 0
    if reason := exemption(root, base):
        print(f"change-size: {reason}")
        return 0
    return gatelib.report("change-size", over(size, limits), base)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
