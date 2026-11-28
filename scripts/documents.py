from __future__ import annotations

import argparse
import datetime as dt
import functools
import posixpath
import re
import sys
import tomllib
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

import feature_preflight as preflight
import gatelib
import markdown_text

if TYPE_CHECKING:
    from collections.abc import Callable, Collection, Iterable, Mapping, Sequence

CATALOGUE = Path(__file__).resolve().parents[1] / "skills/method/application-types.toml"
SECTION = "## Documents"
COLUMNS = ("Kind", "Paths", "Prune")
MARKDOWN = ".md"
STANDING = "standing"
STEERING = "steering"
NEVER = "never"
CUT = "pass"
ANSWERS = "## Answers"
OWNERS_WORDS = "## In the owner's words"
BRIEF = "docs/design-brief.md"
VERDICT = "verdict.json"
FEATURE_SPEC = re.compile(r"docs/features/[^/]+/spec\.md")
TICKETS = "issues"
DATE = re.compile(r"\d{4}-\d{2}-\d{2}")
MARKERS = ("<<<<<<<", "|||||||", "=======", ">>>>>>>")
PRUNE = re.compile(rf"`?({NEVER}|{CUT})`?(?:\s+keeping\s+(.+))?")
PRUNE_AFTER = "Prune after"
PRUNE_AFTER_DEFAULT = 10
DOCS_PRUNE = re.compile(r"docs\(prune\)!?: ")
PASSED = gatelib.STATE / "prune-pass"
CITED = re.compile(r"(?P<path>[\w.@+-]+(?:/[\w.@+-]+)+)(?::(?P<symbol>[A-Za-z_][\w.]*))?")
LINK = re.compile(r"\[[^\]\n]*\]\(([^)\s#]+)(?:#[^)\s]*)?\)")


@dataclass(frozen=True)
class Kind:
    name: str
    paths: tuple[str, ...]
    lifetime: str
    prune: str
    keeps: tuple[str, ...] = ()

    @property
    def steering(self) -> bool:
        return self.lifetime == STEERING

    def holds(self, path: str) -> bool:
        return gatelib.matches(path, list(self.paths))


@dataclass(frozen=True)
class Declared:
    kinds: tuple[Kind, ...]
    problems: tuple[str, ...]


def shipped(text: str | None = None) -> tuple[Kind, ...]:
    data = tomllib.loads(text if text is not None else CATALOGUE.read_text(encoding="utf-8"))
    return tuple(
        Kind(
            row["name"],
            tuple(row["paths"]),
            row["lifetime"],
            row["prune"],
            tuple(row.get("keeps", ())),
        )
        for row in data.get("documents", ())
    )


def declared(binding: str | None) -> Declared | None:
    if binding is None or not markdown_text.has_section(binding, SECTION):
        return None
    tables = [t for t in markdown_text.tables(binding, SECTION) if t.header == COLUMNS]
    after = after_problems(binding)
    if not tables:
        columns = " | ".join(COLUMNS)
        return Declared((), (f"the section lacks its table with the columns {columns}", *after))
    kinds: list[Kind] = []
    problems: list[str] = []
    for row in tables[0].rows:
        kind, problem = row_kind(row, {held.name for held in kinds})
        if problem:
            problems.append(problem)
        if kind:
            kinds.append(kind)
    return Declared(tuple(kinds), (*problems, *after))


def after_line(binding: str | None) -> str | None:
    return markdown_text.bullets(markdown_text.section(binding or "", SECTION)).get(PRUNE_AFTER)


def whole(value: str) -> bool:
    return value.isdecimal() and int(value) > 0


def after_problems(binding: str) -> tuple[str, ...]:
    value = after_line(binding)
    if value is None or whole(value):
        return ()
    return (f"`- {PRUNE_AFTER}:` is not a whole number of merges, one or more",)


def prune_after(binding: str | None) -> int:
    value = after_line(binding)
    return int(value) if value is not None and whole(value) else PRUNE_AFTER_DEFAULT


def row_kind(row: Sequence[str], held: set[str]) -> tuple[Kind | None, str | None]:
    name = row[0].strip("` ")
    if name.startswith("<") or row[1].startswith("<") or row[2].startswith("<"):
        return None, "the table still has the template's placeholder row"
    if name in held:
        return None, f"the kind '{name}' is listed twice"
    globs = tuple(glob.strip() for item in markdown_text.ticked(row[1]) for glob in item.split(","))
    if not globs:
        return None, f"'{name}' names no paths: write the globs in backticks"
    prune = PRUNE.fullmatch(row[2].strip())
    if prune is None:
        return None, f"'{name}' has a Prune of neither `{NEVER}` nor `{CUT}`"
    keeps = tuple(markdown_text.ticked(prune.group(2) or ""))
    if keeps and prune.group(1) == NEVER:
        return None, f"'{name}' is `{NEVER}` and keeps headings: only a `{CUT}` row keeps any"
    return Kind(name, globs, STANDING, prune.group(1), keeps), None


def all_kinds(binding: str | None, catalogue: str | None = None) -> tuple[Kind, ...]:
    own = declared(binding)
    return (*shipped(catalogue), *(own.kinds if own else ()))


def kind_of(path: str, kinds: Iterable[Kind]) -> Kind | None:
    return next((kind for kind in kinds if kind.holds(path)), None)


def unkinded(paths: Iterable[str], kinds: Sequence[Kind]) -> list[str]:
    return sorted(
        path for path in paths if path.endswith(MARKDOWN) and kind_of(path, kinds) is None
    )


def kinds_of(root: Path) -> tuple[Kind, ...]:
    return all_kinds(gatelib.read_text(root / gatelib.BINDING))


@functools.cache
def steering() -> tuple[Kind, ...]:
    return tuple(kind for kind in shipped() if kind.steering)


def is_steering(path: str) -> bool:
    return any(kind.holds(path) for kind in steering())


def is_ticket(path: str) -> bool:
    return is_steering(path) and posixpath.basename(posixpath.dirname(path)) == TICKETS


def unit_of(path: str) -> str:
    folder = posixpath.dirname(path)
    return posixpath.dirname(folder) if posixpath.basename(folder) == TICKETS else folder


def snapshot(paths: Iterable[str], read: Callable[[str], str | None]) -> dict[str, str]:
    held = {}
    for path in paths:
        if is_steering(path) or posixpath.basename(path) == VERDICT:
            text = read(path)
            if text is not None:
                held[path] = text
    return held


def work_tree(root: Path) -> dict[str, str]:
    return snapshot(gatelib.tracked_paths(root), lambda path: gatelib.read_text(root / path))


def parts(line: str) -> tuple[str | None, str]:
    text = line.strip().removeprefix("- ")
    head, colon, rest = text.partition(": ")
    if colon and DATE.fullmatch(head):
        return head, rest.strip()
    return None, text


def answers_of(text: str) -> list[str]:
    return [
        line.strip() for line in markdown_text.section(text, ANSWERS).split("\n") if line.strip()
    ]


def named(path: str, text: str, tickets: Collection[str]) -> frozenset[str]:
    ticket = preflight.local_ticket(path, text)
    folder = posixpath.dirname(path)
    refs = [posixpath.normpath(ticket.parent)] if ticket.parent else []
    found = set()
    for ref in ticket.blockers:
        if ref.endswith(".md"):
            refs.append(posixpath.normpath(posixpath.join(folder, ref)))
        else:
            number = int(ref.lstrip("#"))
            found |= {
                other
                for other in tickets
                if (lead := re.match(r"\d+", posixpath.basename(other)))
                and int(lead.group()) == number
            }
    return frozenset(found | set(refs))


def expired(
    tree: Mapping[str, str], closing: frozenset[str] = frozenset()
) -> dict[str, tuple[str, ...]]:
    units: dict[str, list[str]] = {}
    for path in sorted(tree):
        if is_steering(path):
            units.setdefault(unit_of(path), []).append(path)
    tickets = [path for path in tree if is_ticket(path)]
    waiting = {
        path: named(path, tree[path], tickets)
        for path in tickets
        if path not in closing and preflight.local_ticket(path, tree[path]).is_open
    }
    found = {}
    for key, files in units.items():
        mine = [path for path in files if is_ticket(path)]
        shut = bool(mine) and not any(path in waiting for path in mine)
        free = not any(
            unit_of(other) != key and names & set(files) for other, names in waiting.items()
        )
        if (shut and free) or (not mine and f"{key}/{VERDICT}" in tree):
            found[key] = tuple(files)
    return found


def listed(tree: Mapping[str, str]) -> frozenset[str]:
    return frozenset(path for files in expired(tree).values() for path in files)


def closes(tree: Mapping[str, str], path: str) -> bool:
    named = posixpath.normpath(path)
    return unit_of(named) in expired(tree, frozenset({named}))


def left(before: Mapping[str, str], after: Mapping[str, str]) -> list[str]:
    was = expired(before)
    return sorted(key for key in expired(after) if key not in was)


def default_tip(root: Path) -> str | None:
    return next(
        (
            ref
            for ref in gatelib.default_branches(root)
            if gatelib.git(root, "rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}")
        ),
        None,
    )


def added_specs(root: Path, tree: Mapping[str, str]) -> list[str]:
    tip = default_tip(root)
    added = gatelib.git(root, "diff", "--name-only", "--diff-filter=A", f"{tip}...HEAD")
    return sorted(
        path for path in (added or "").split("\n") if FEATURE_SPEC.fullmatch(path) and path in tree
    )


def with_lines(text: str, lines: Sequence[str]) -> str:
    rows = text.split("\n")
    start = next(
        i for i, row in enumerate(rows) if markdown_text.strip_comment(row) == OWNERS_WORDS
    )
    end = next(
        (i for i in range(start + 1, len(rows)) if rows[i].startswith(markdown_text.SECTION_START)),
        len(rows),
    )
    last = max((i for i in range(start + 1, end) if rows[i].strip()), default=start)
    tail = rows[last + 1 :]
    empty = last == start
    lead = [""] if empty else []
    gap = [""] if empty and tail and tail[0].strip() else []
    return "\n".join([*rows[: last + 1], *lead, *lines, *gap, *tail])


def fresh(
    files: Iterable[str], tree: Mapping[str, str], brief: str, *, today: dt.date
) -> list[str]:
    held = {parts(row)[1] for row in markdown_text.section(brief, OWNERS_WORDS).split("\n")}
    fresh_lines = []
    for path in files:
        for line in answers_of(tree[path]):
            date, body = parts(line)
            if body not in held:
                held.add(body)
                fresh_lines.append(f"- {date or today.isoformat()}: {body}")
    return fresh_lines


def prune(root: Path, path: Path) -> None:
    inside = len(path.relative_to(root).parts) - 1
    for parent in list(path.parents)[:inside]:
        try:
            parent.rmdir()
        except OSError:
            break


@dataclass(frozen=True)
class Expiry:
    deleted: tuple[str, ...]
    carried: tuple[str, ...]
    problem: str | None = None


def expire(root: Path, today: dt.date) -> Expiry:
    tree = work_tree(root)
    files = sorted({*listed(tree), *added_specs(root, tree)})
    brief = gatelib.read_text(root / BRIEF)
    new = fresh(files, tree, brief or "", today=today)
    if new and (brief is None or not markdown_text.has_section(brief, OWNERS_WORDS)):
        return Expiry(
            (),
            (),
            f"{BRIEF} has no `{OWNERS_WORDS}` section, so nothing expired was deleted: add the"
            " heading, then run `documents.py expire` again",
        )
    if new and brief is not None:
        (root / BRIEF).write_text(with_lines(brief, new), encoding="utf-8")
    for path in files:
        (root / path).unlink()
        prune(root, root / path)
    return Expiry(tuple(files), tuple(new))


def sections_of(text: str) -> tuple[list[str], list[str], list[str]] | None:
    rows = text.split("\n")
    starts = [i for i, row in enumerate(rows) if markdown_text.strip_comment(row) == OWNERS_WORDS]
    if not starts:
        return None
    end = next(
        (
            i
            for i in range(starts[0] + 1, len(rows))
            if rows[i].startswith(markdown_text.SECTION_START)
        ),
        len(rows),
    )
    return rows[: starts[0] + 1], rows[starts[0] + 1 : end], rows[end:]


def carried(before: str | None, after: str | None, answers: Collection[str]) -> bool:
    old = sections_of(before or "")
    new = sections_of(after or "")
    if old is None or new is None or old[::2] != new[::2]:
        return False
    was = Counter(row.strip() for row in old[1] if row.strip())
    now = Counter(row.strip() for row in new[1] if row.strip())
    return not was - now and all(
        parts(row)[0] is not None and parts(row)[1] in answers for row in (now - was).elements()
    )


def settled(text: str) -> str | None:
    found = sections_of(text)
    if found is None:
        return None
    head, body, tail = found
    if any(row.startswith(MARKERS) for row in [*head, *tail]):
        return None
    kept: list[str] = []
    skipping = False
    for row in body:
        if row.startswith("|||||||"):
            skipping = True
        elif row.startswith("======="):
            skipping = False
        elif not row.startswith(MARKERS) and not skipping and row.strip():
            kept.append(row)
    seen: set[str] = set()
    unique = []
    for row in kept:
        if parts(row)[1] not in seen:
            seen.add(parts(row)[1])
            unique.append(row)
    ordered = sorted(unique, key=lambda row: parts(row)[0] or "")
    return "\n".join([*head, "", *ordered, "", *tail])


@dataclass(frozen=True)
class Citation:
    path: str
    symbol: str | None


def citations(text: str, page: str) -> list[Citation]:
    found: dict[Citation, None] = {}
    for item in markdown_text.ticked(text):
        match = CITED.fullmatch(item)
        if match:
            found[Citation(match["path"], match["symbol"])] = None
    folder = posixpath.dirname(page)
    for target in LINK.findall(text):
        path = posixpath.normpath(posixpath.join(folder, target))
        if ":" not in target and path.split("/")[0] not in ("", ".", ".."):
            found[Citation(path, None)] = None
    return list(found)


def present(path: str, tree: Collection[str]) -> bool:
    return path in tree or any(held.startswith(f"{path}/") for held in tree)


def holds_symbol(root: Path, path: str, symbol: str) -> bool:
    text = gatelib.read_text(root / path)
    return text is not None and re.search(rf"(?<!\w){re.escape(symbol)}(?!\w)", text) is not None


def cited(root: Path, pages: Iterable[str]) -> list[str]:
    tree = set(gatelib.tracked_paths(root))
    findings = []
    for page in pages:
        for cite in citations(gatelib.read_text(root / page) or "", page):
            if not present(cite.path, tree):
                findings.append(f"cited: {page}: `{cite.path}` is not in the tree")
            elif cite.symbol and not holds_symbol(root, cite.path, cite.symbol):
                findings.append(
                    f"cited: {page}: `{cite.path}` does not hold `{cite.symbol}` as a whole word"
                )
    return findings


def prunable_pages(root: Path, kinds: Sequence[Kind]) -> list[str]:
    return sorted(
        path
        for path in gatelib.tracked_paths(root)
        if path.endswith(MARKDOWN) and (kind := kind_of(path, kinds)) and kind.prune == CUT
    )


def changed_after(root: Path, page: str, path: str) -> bool:
    last = gatelib.git(root, "log", "-1", "--format=%H", "--", page)
    if not last:
        return False
    later = gatelib.git(root, "rev-list", "--count", f"{last}..HEAD", "--", path)
    return int(later or 0) > 0


def stale(root: Path) -> list[tuple[str, str]]:
    tree = set(gatelib.tracked_paths(root))
    pairs = (
        (page, cite.path)
        for page in prunable_pages(root, kinds_of(root))
        for cite in citations(gatelib.read_text(root / page) or "", page)
        if cite.path in tree
    )
    return [pair for pair in dict.fromkeys(pairs) if changed_after(root, *pair)]


def landed_after(root: Path, ref: str, mark: str) -> int:
    line = (gatelib.git(root, "rev-list", "--first-parent", ref) or "").split()
    after = set((gatelib.git(root, "rev-list", "--ancestry-path", f"{mark}..{ref}") or "").split())
    return sum(commit in after for commit in line) - (mark not in line)


def count_after(root: Path, ref: str, commit: str) -> int | None:
    span = f"{commit}..{ref}" if commit else ref
    counted = gatelib.git(root, "rev-list", "--first-parent", "--count", span)
    return int(counted) if counted else None


def landed_since_pass(root: Path, ref: str) -> int | None:
    counts = []
    logged = (gatelib.git(root, "log", "--format=%H %s", ref) or "").splitlines()
    marks = (line.split(" ", 1) for line in logged)
    mark = next((pair[0] for pair in marks if DOCS_PRUNE.match(pair[-1])), None)
    if mark:
        counts.append(landed_after(root, ref, mark))
    recorded = (gatelib.read_text(root / PASSED) or "").strip()
    if recorded and (since := count_after(root, ref, recorded)) is not None:
        counts.append(since)
    if counts:
        return min(counts)
    return count_after(root, ref, "")


@dataclass(frozen=True)
class Due:
    merges: int
    page: str
    path: str


def due(root: Path, ref: str) -> Due | None:
    merges = landed_since_pass(root, ref)
    after = prune_after(gatelib.read_text(root / gatelib.BINDING))
    if merges is None or merges < after:
        return None
    found = stale(root)
    return Due(merges, *found[0]) if found else None


def passed(root: Path) -> str | None:
    tip = default_tip(root)
    commit = gatelib.git(root, "rev-parse", tip) if tip else None
    if not commit:
        return None
    (gatelib.state_dir(root) / PASSED.name).write_text(f"{commit}\n", encoding="utf-8")
    return commit


def filled(text: str) -> int:
    return sum(1 for row in text.split("\n") if row.strip())


def cuttable(text: str, keeps: Collection[str]) -> int:
    kept = sum(
        sum(markdown_text.strip_comment(row) == heading for row in text.split("\n"))
        + filled(markdown_text.section(text, heading))
        for heading in keeps
    )
    return filled(text) - kept


def prunable_counts(root: Path) -> list[tuple[str, int]]:
    kinds = kinds_of(root)
    held = prunable_pages(root, kinds)
    return [
        (
            kind.name,
            sum(
                cuttable(gatelib.read_text(root / page) or "", kind.keeps)
                for page in held
                if kind_of(page, kinds) is kind
            ),
        )
        for kind in kinds
        if kind.prune == CUT
    ]


def prunable_line(counts: Iterable[tuple[str, int]]) -> str:
    kinds = ", ".join(f"{name} {count}" for name, count in counts)
    return (
        f"prunable lines: {kinds} (a count, not a gate;"
        " /ratchet:prune pages cuts them when a pass is due)"
    )


def report_cited(root: Path, pages: Sequence[str]) -> int:
    findings = cited(root, pages)
    for finding in findings:
        print(finding)
    return 1 if findings else 0


def report_stale(root: Path) -> int:
    for page, path in stale(root):
        print(f"{page}\t{path}")
    return 0


def report_prunable(root: Path) -> int:
    print(prunable_line(prunable_counts(root)))
    return 0


def report_passed(root: Path) -> int:
    commit = passed(root)
    if commit is None:
        print("passed: the default branch has no commit to count from")
        return 1
    print(f"passed: merges are counted from {commit[:12]}")
    return 0


def report_expire(root: Path) -> int:
    done = expire(root, dt.datetime.now(tz=dt.UTC).date())
    if done.problem is not None:
        print(f"expire: {done.problem}")
        return 1
    for path in done.deleted:
        print(f"expire: deleted {path}")
    for line in done.carried:
        print(f"expire: carried {line} to {BRIEF}")
    if not done.deleted:
        print("expire: nothing has expired")
    return 0


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=["expire", "cited", "stale", "passed", "prunable"])
    parser.add_argument("pages", nargs="*")
    parser.add_argument("--root", default=".")
    options = parser.parse_intermixed_args(argv[1:])
    root = Path(options.root).resolve()
    if options.action == "cited":
        if not options.pages:
            parser.error("cited needs the files whose citations it reads")
        return report_cited(root, options.pages)
    actions: dict[str, Callable[[Path], int]] = {
        "stale": report_stale,
        "prunable": report_prunable,
        "passed": report_passed,
        "expire": report_expire,
    }
    return actions[options.action](root)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
