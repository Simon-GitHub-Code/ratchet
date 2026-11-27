#!/usr/bin/env python3
from __future__ import annotations

import argparse
import re
import sys
from dataclasses import dataclass
from pathlib import Path

import expect_outputs
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
    "**/*.md",
    "**/*.markdown",
    "docs/features/**/verdict.json",
    str(owed.LEDGER),
    "quality/snapshots/**",
    ".mise/locks/**",
]
SECTION = "## Test paths"
LABEL = "Tests"
GENERATED_LABEL = "Generated"
BROAD_EXCLUDES = frozenset({"*", "**", "**/*", "."})
BULLET = re.compile(r"^- ([^:\n]+):[ \t]*(.*?)[ \t]*$")
TICKED = re.compile(r"`([^`\n]+)`")
NONE_PREFIX = "none:"
MIN_REASON = 10
KEYS = (
    "product_files",
    "product_lines",
    "test_lines",
    "product_files_hard",
    "product_lines_hard",
)
LEGACY = {"product_files": "files", "product_lines": "lines"}
HARD_PAIRS = (
    ("product_files", "product_files_hard"),
    ("product_lines", "product_lines_hard"),
)
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
    product_files_hard: int | None = None
    product_lines_hard: int | None = None

    def recorded(self) -> bool:
        found = (
            self.product_files,
            self.product_lines,
            self.test_lines,
            self.product_files_hard,
            self.product_lines_hard,
        )
        return any(value is not None for value in found)

    @property
    def hard_product_files(self) -> int | None:
        return self.product_files if self.product_files_hard is None else self.product_files_hard

    @property
    def hard_product_lines(self) -> int | None:
        return self.product_lines if self.product_lines_hard is None else self.product_lines_hard


def not_counted(
    path: str,
    contract: dependencies.Contract | None = None,
    excluded: list[str] | None = None,
) -> bool:
    return (
        gatelib.matches(path, RECORDS)
        or (contract is not None and contract.is_lockfile(path))
        or gatelib.matches(path, excluded or [])
    )


def _section_lines(binding: str | None) -> list[str] | None:
    lines = (binding or "").split("\n")
    start = next((i for i, line in enumerate(lines) if line.rstrip() == SECTION), None)
    if start is None:
        return None
    body: list[str] = []
    for line in lines[start + 1 :]:
        if line.startswith("## "):
            break
        body.append(line)
    return body


def _bullet(body: list[str], label: str) -> str | None:
    for line in body:
        if (match := BULLET.match(line)) and match.group(1).strip() == label:
            return match.group(2)
    return None


def _bad_glob(pattern: str) -> bool:
    return (
        not pattern
        or pattern.startswith("/")
        or ".." in pattern.split("/")
        or pattern in BROAD_EXCLUDES
    )


def test_paths(binding: str | None) -> tuple[list[str] | None, list[str]]:
    body = _section_lines(binding)
    if body is None:
        return None, [f"has no '{SECTION}' section"]
    value = _bullet(body, LABEL) or ""
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


def generated_paths(binding: str | None) -> tuple[list[str], list[str]]:
    body = _section_lines(binding)
    value = None if body is None else _bullet(body, GENERATED_LABEL)
    if value is None or value.startswith("<"):
        return [], []
    plain = value.strip("` ")
    if plain.lower().startswith(NONE_PREFIX):
        if len(plain[len(NONE_PREFIX) :].strip()) < MIN_REASON:
            return [], [f"'- {GENERATED_LABEL}: none:' needs the reason why none are generated"]
        return [], []
    found = [item.strip() for item in TICKED.findall(value) if item.strip()]
    if not found:
        return [], [
            f"'- {GENERATED_LABEL}:' names its globs in backticks, or says `none: <reason>`"
        ]
    if bad := [item for item in found if _bad_glob(item)]:
        return [], [
            f"'- {GENERATED_LABEL}:' refuses the pattern `{bad[0]}`: write a narrower relative glob"
        ]
    return found, []


def recorded_output_globs(binding: str | None) -> list[str]:
    if not binding:
        return []
    return [
        glob
        for rule in expect_outputs.parse(binding)
        if rule.pattern is None
        for glob in rule.paths
    ]


def test_globs(root: Path, base: str) -> list[str] | None:
    return test_paths(gatelib.show(root, base, str(gatelib.BINDING)))[0]


def _changes(
    root: Path, base: str, head: str | None = None
) -> list[tuple[str, str, str, str | None]]:
    args = ["diff", "--numstat", "--find-renames", "-z", base]
    if head is not None:
        args.append(head)
    out = gatelib.git(root, *args) or ""
    fields = iter(out.split("\0")[:-1])
    found = []
    for entry in fields:
        added, deleted, path = entry.split("\t", 2)
        old = None
        if not path:
            old, path = next(fields), next(fields)
        found.append((added, deleted, path, old))
    return found


def measure(root: Path, base: str, head: str | None = None) -> Size:
    binding = gatelib.show(root, base, str(gatelib.BINDING))
    tests = test_paths(binding)[0] or []
    excluded = [*generated_paths(binding)[0], *recorded_output_globs(binding)]
    product, test = [0, 0], [0, 0]
    contract = dependencies.parse(binding)

    def skipped(path: str) -> bool:
        return not_counted(path, contract, excluded)

    def add(path: str, lines: int) -> None:
        kind = test if gatelib.matches(path, tests) else product
        kind[0] += 1
        kind[1] += lines

    for added, _deleted, path, old in _changes(root, base, head):
        if skipped(path) and (old is None or skipped(old)):
            continue
        add(path, int(added) if added != "-" else 0)
    if head is None:
        untracked = (
            gatelib.git(root, "ls-files", "--others", "--exclude-standard", "-z", ":/") or ""
        )
        for path in (p for p in untracked.split("\0") if p and not skipped(p)):
            text = gatelib.read_text(root / path)
            add(path, len(text.splitlines()) if text is not None else 0)
    return Size(product[0], product[1], test[0], test[1])


def _limit(value: object) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else None


def _entry(table: dict[str, object], key: str) -> int | None:
    return _limit(table[key]) if key in table else _limit(table.get(LEGACY.get(key, "")))


def ceilings_from(data: dict[str, object]) -> Ceilings:
    table = gatelib.table(data, "ceilings", "change")
    return Ceilings(*(_entry(table, key) for key in KEYS))


def ceilings(root: Path) -> Ceilings:
    return ceilings_from(gatelib.thresholds(root))


def malformed(root: Path) -> list[str]:
    table = gatelib.table(gatelib.thresholds(root), "ceilings", "change")
    problems = [
        f"ceilings.change.{name}: {table[name]!r} is not a non-negative integer"
        for name in (*KEYS, *LEGACY.values())
        if name in table and _limit(table[name]) is None
    ]
    for soft_key, hard_key in HARD_PAIRS:
        soft = _entry(table, soft_key)
        hard = _entry(table, hard_key)
        if soft is not None and hard is not None and hard < soft:
            problems.append(
                f"ceilings.change.{hard_key}: {hard} is below ceilings.change.{soft_key} ({soft})"
            )
    return problems


def over(size: Size, limits: Ceilings) -> list[str]:
    measured = (
        (
            "product files",
            "changed",
            size.product_files,
            limits.hard_product_files,
            limits.product_files_hard is not None,
        ),
        (
            "product lines",
            "added",
            size.product_lines,
            limits.hard_product_lines,
            limits.product_lines_hard is not None,
        ),
        ("test lines", "added", size.test_lines, limits.test_lines, False),
    )
    return [
        f"{name}: {value} {unit}, {'hard ' if is_hard else ''}ceiling {limit}"
        for name, unit, value, limit, is_hard in measured
        if limit is not None and value > limit
    ]


def over_autonomous(size: Size, limits: Ceilings) -> list[str]:
    measured = (
        (
            "product files",
            "changed",
            size.product_files,
            limits.product_files,
            limits.product_files_hard,
        ),
        (
            "product lines",
            "added",
            size.product_lines,
            limits.product_lines,
            limits.product_lines_hard,
        ),
    )
    items: list[str] = []
    for name, unit, value, soft, hard in measured:
        if soft is not None and value > soft:
            cap = f" (hard ceiling {hard})" if hard is not None else ""
            items.append(f"{name}: {value} {unit}, over autonomous ceiling {soft}{cap}")
    return items


def is_adoption(root: Path, base: str, head: str | None = None) -> bool:
    found = gatelib.subjects(root, base, head or "HEAD")
    return bool(found) and all(s.startswith(ADOPTION) for s in found)


def bound_questions(text: str) -> set[str]:
    return {
        m.group(1).strip()
        for m in GATE_ROW.finditer(text)
        if (tool := m.group(2).strip(" `").lower()) and not tool.startswith(("later:", "none"))
    }


def is_safeguard(root: Path, base: str, head: str | None = None) -> bool:
    found = gatelib.subjects(root, base, head or "HEAD")
    if not found or not all(s.startswith(SAFEGUARD) for s in found):
        return False
    before = gatelib.show(root, base, str(gatelib.BINDING)) or ""
    now = (
        gatelib.read_text(root / gatelib.BINDING)
        if head is None
        else gatelib.show(root, head, str(gatelib.BINDING))
    ) or ""
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


def with_ceilings(
    text: str,
    ceilings: tuple[int, int, int],
    *,
    hard: tuple[int | None, int | None] = (None, None),
) -> str:
    files, lines, tests = ceilings
    files_hard, lines_hard = hard
    rows = [CEILINGS_TABLE, f"product_files = {files}"]
    if files_hard is not None:
        rows.append(f"product_files_hard = {files_hard}")
    rows.append(f"product_lines = {lines}")
    if lines_hard is not None:
        rows.append(f"product_lines_hard = {lines_hard}")
    rows.append(f"test_lines = {tests}")
    body = "\n".join(rows) + "\n"
    pattern = re.compile(r"^\[ceilings\.change\]\n(?:[^\[\n][^\n]*\n|\n)*", re.MULTILINE)
    if pattern.search(text):
        return pattern.sub(body + "\n", text).rstrip("\n") + "\n"
    return (text.rstrip("\n") + "\n\n" + body) if text.strip() else body


def record(
    root: Path,
    ceilings: tuple[int, int, int],
    *,
    hard: tuple[int | None, int | None] = (None, None),
) -> int:
    files, lines, tests = ceilings
    files_hard, lines_hard = hard
    path = root / gatelib.THRESHOLDS
    path.parent.mkdir(parents=True, exist_ok=True)
    current = path.read_text(encoding="utf-8") if path.is_file() else ""
    path.write_text(with_ceilings(current, ceilings, hard=hard), encoding="utf-8")
    files_part = f"{files} product files" + (
        f" (hard {files_hard})" if files_hard is not None else ""
    )
    lines_part = f"{lines} product lines" + (
        f" (hard {lines_hard})" if lines_hard is not None else ""
    )
    print(f"change-size: ceilings recorded, {files_part}, {lines_part}, {tests} test lines")
    return 0


def _describe_limit(soft: int | None, hard: int | None) -> str:
    if soft is not None and hard is not None and hard > soft:
        return f"autonomous ceiling {soft}, hard ceiling {hard}"
    effective = soft if hard is None else hard
    return "no ceiling" if effective is None else f"ceiling {effective}"


def describe(size: Size, limits: Ceilings) -> str:
    test_limit = "no ceiling" if limits.test_lines is None else f"ceiling {limits.test_lines}"
    return (
        f"change-size: {size.product_files} product files"
        f" ({_describe_limit(limits.product_files, limits.product_files_hard)}),"
        f" {size.product_lines} product lines"
        f" ({_describe_limit(limits.product_lines, limits.product_lines_hard)}),"
        f" {size.test_lines} test lines ({test_limit}) added"
    )


def _set_ceilings(parser: argparse.ArgumentParser, root: Path, options: argparse.Namespace) -> int:
    given = (options.product_files, options.product_lines, options.test_lines)
    if any(value is None for value in given):
        parser.error("--set needs --product-files N, --product-lines N and --test-lines N")
    hard = (options.product_files_hard, options.product_lines_hard)
    if (hard[0] is not None and hard[0] < given[0]) or (hard[1] is not None and hard[1] < given[1]):
        parser.error("hard ceilings cannot be below autonomous ceilings")
    return record(root, (given[0], given[1], given[2]), hard=hard)


def _judge(root: Path, base: str, *, size: Size, limits: Ceilings) -> int:
    if reason := exemption(root, base):
        print(f"change-size: {reason}")
        return 0
    if reasons := over(size, limits):
        return gatelib.report("change-size", reasons, base)
    for warning in over_autonomous(size, limits):
        print(f"change-size: warning: {warning}; requires owner merge")
    return gatelib.report("change-size", [], base)


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", default=".")
    parser.add_argument("--base")
    parser.add_argument("--set", action="store_true")
    parser.add_argument("--product-files", type=int)
    parser.add_argument("--product-files-hard", type=int)
    parser.add_argument("--product-lines", type=int)
    parser.add_argument("--product-lines-hard", type=int)
    parser.add_argument("--test-lines", type=int)
    options = parser.parse_args(argv[1:])
    root = Path(options.root).resolve()
    if options.set:
        return _set_ceilings(parser, root, options)
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
    return _judge(root, base, size=size, limits=limits)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
