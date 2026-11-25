#!/usr/bin/env python3
from __future__ import annotations

import argparse
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

import gatelib
import markdown_text
from markers import CHUNK, diff_paths, grep_count, grep_count_paths, staged_paths

if TYPE_CHECKING:
    from collections.abc import Sequence

SECTION = "## Suppressions"
KIND = "Kind"
QUESTION = "Question"
EXCLUDE = "Exclude"
BINDING = str(gatelib.BINDING)
RISES = "rises"
NEVER = "never"
KEPT = "kept"
RULES = (RISES, NEVER, KEPT)
NONE_PREFIX = "none:"
MIN_REASON = 10
BROAD_EXCLUDES = frozenset({"*", "**", "**/*", "."})
COLUMNS = {KIND: (KIND, "Pattern", "Rule"), QUESTION: (QUESTION, "Kinds")}


@dataclass(frozen=True)
class Kind:
    name: str
    pattern: str
    rule: str


@dataclass(frozen=True)
class Spec:
    kinds: tuple[Kind, ...]
    silenced: dict[str, tuple[str, ...]]
    problems: tuple[str, ...]
    excludes: tuple[str, ...] = ()


def valid(pattern: str) -> bool:
    with tempfile.TemporaryDirectory(prefix="ratchet-pattern-") as scratch:
        (Path(scratch) / "empty").touch()
        done = subprocess.run(
            ["git", "grep", "--no-index", "-E", "-q", "-e", pattern, "--", "empty"],
            capture_output=True,
            check=False,
            cwd=scratch,
        )
    return done.returncode in {0, 1}


def _pattern(cell: str) -> str:
    ticked = markdown_text.ticked(cell)
    return ticked[0].replace("\\|", "|") if len(ticked) == 1 else ""


def _kinds(rows: tuple[tuple[str, ...], ...], problems: list[str]) -> tuple[Kind, ...]:
    found: dict[str, Kind] = {}
    for label, cell, rule in rows:
        name = label.strip("` ")
        pattern = _pattern(cell)
        if name.startswith("<") or cell.startswith("<"):
            problems.append(f"the kind '{name}' is still the template's placeholder row")
        elif name in found:
            problems.append(f"the kind '{name}' is listed twice")
        elif not pattern or not valid(pattern):
            problems.append(f"the kind '{name}' needs one backticked extended regex as Pattern")
        elif rule.strip("` ") not in RULES:
            problems.append(
                f"the kind '{name}' has the Rule '{rule}': write `rises`, `never` or `kept`"
            )
        else:
            found[name] = Kind(name, pattern, rule.strip("` "))
    return tuple(found.values())


def _silenced(
    rows: tuple[tuple[str, ...], ...], kinds: tuple[Kind, ...], problems: list[str]
) -> dict[str, tuple[str, ...]]:
    known = {kind.name for kind in kinds}
    found: dict[str, tuple[str, ...]] = {}
    for question, cell in rows:
        if question.startswith("<") or cell.startswith("<"):
            problems.append("the question table still has the template's placeholder row")
        elif cell.lower().startswith(NONE_PREFIX):
            if len(cell[len(NONE_PREFIX) :].strip()) < MIN_REASON:
                problems.append(f"'{question}' says none with no reason: say why no directive")
            found[question] = ()
        else:
            names = tuple(markdown_text.ticked(cell))
            if unknown := [name for name in names if name not in known]:
                problems.append(f"'{question}' names the kind '{unknown[0]}', which has no row")
            elif not names:
                problems.append(f"'{question}' names no kind: write kinds in backticks or `none:`")
            else:
                found[question] = names
    return found


def _bad_exclude(pattern: str) -> bool:
    return (
        not pattern
        or pattern.startswith("/")
        or ".." in pattern.split("/")
        or pattern in BROAD_EXCLUDES
    )


def _excludes(binding: str, problems: list[str]) -> tuple[str, ...]:
    raw = markdown_text.bullets(markdown_text.section(binding, SECTION)).get(EXCLUDE)
    if raw is None or raw.startswith("<"):
        return ()
    plain = raw.strip("` ")
    if plain.lower().startswith(NONE_PREFIX):
        if len(plain[len(NONE_PREFIX) :].strip()) < MIN_REASON:
            problems.append("'- Exclude: none:' needs the reason no path is excluded")
        return ()
    found = tuple(item.strip() for item in markdown_text.ticked(raw) if item.strip())
    if not found:
        problems.append("'- Exclude:' names its globs in backticks, or says `none: <reason>`")
        return ()
    bad = [item for item in found if _bad_exclude(item)]
    if bad:
        problems.append(
            f"'- Exclude:' pattern '{bad[0]}' must be a specific relative glob without `..`"
        )
        return ()
    return found


def parse(binding: str | None) -> Spec | None:
    if binding is None or not markdown_text.has_section(binding, SECTION):
        return None
    problems: list[str] = []
    tables = {t.header[0]: t for t in markdown_text.tables(binding, SECTION)}
    for first, header in COLUMNS.items():
        if first not in tables or tables[first].header != header:
            problems.append(f"the section lacks its table with the columns {' | '.join(header)}")
    kinds = _kinds(tables[KIND].rows, problems) if KIND in tables else ()
    covered = tables.get(QUESTION)
    silenced = _silenced(covered.rows, kinds, problems) if covered else {}
    excludes = _excludes(binding, problems)
    return Spec(kinds, silenced, tuple(problems), excludes)


def counts(
    root: Path, ref: str | None, kinds: tuple[Kind, ...], *, excludes: Sequence[str] = ()
) -> dict[str, int]:
    outside: str | tuple[str, ...] = (BINDING, *excludes) if excludes else BINDING
    return {
        k.name: grep_count(root, k.pattern, ref, word=False, outside=outside)
        for k in kinds
        if k.rule == RISES
    }


def counts_staged(
    root: Path, kinds: tuple[Kind, ...], paths: Sequence[str], *, ref: str | None = None
) -> dict[str, int]:
    return {
        k.name: grep_count_paths(root, k.pattern, paths, ref=ref, word=False)
        for k in kinds
        if k.rule == RISES
    }


def risen(before: dict[str, int], after: dict[str, int]) -> list[str]:
    return [
        f"{kind}: rose from {before.get(kind, 0)} to {count}"
        for kind, count in after.items()
        if count > before.get(kind, 0)
    ]


def focused(root: Path, kinds: tuple[Kind, ...], *, excludes: Sequence[str] = ()) -> list[str]:
    scope = [".", f":(exclude){BINDING}", *(f":(glob,exclude){glob}" for glob in excludes)]
    found: list[str] = []
    for kind in kinds:
        if kind.rule == NEVER:
            out = gatelib.git(
                root,
                *("grep", "-I", "-n", "-E", "--untracked", "-e", kind.pattern),
                *("--", *scope),
            )
            found += [f"{kind.name} at {line}" for line in (out or "").splitlines()]
    return found


def _filter_paths(paths: Sequence[str], excludes: Sequence[str]) -> list[str]:
    if not excludes:
        return list(paths)
    patterns = list(excludes)
    return [path for path in paths if not gatelib.matches(path, patterns)]


def focused_staged(root: Path, kinds: tuple[Kind, ...], paths: Sequence[str]) -> list[str]:
    if not paths:
        return []
    found: list[str] = []
    for kind in kinds:
        for start in range(0, len(paths), CHUNK):
            out = gatelib.git(
                root,
                *("--literal-pathspecs", "grep", "--cached", "-I", "-n", "-E", "-e", kind.pattern),
                *("--", *paths[start : start + CHUNK]),
            )
            found += [f"{kind.name} at {line}" for line in (out or "").splitlines()]
    return found


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", default=".")
    parser.add_argument("--base")
    parser.add_argument("--staged", action="store_true")
    options = parser.parse_args(argv[1:])
    root = Path(options.root).resolve()
    base = gatelib.base_commit(root, options.base)
    now = parse(
        gatelib.show(root, "", BINDING)
        if options.staged
        else gatelib.read_text(root / gatelib.BINDING)
    )
    then = parse(gatelib.show(root, base, str(gatelib.BINDING))) if base else None
    excludes = then.excludes if then is not None else (now.excludes if base is None and now else ())
    never = tuple(
        {k.name: k for spec in (then, now) if spec for k in spec.kinds if k.rule == NEVER}.values()
    )
    if options.staged:
        raw_index = (
            staged_paths(root, outside=BINDING)
            if base is None
            else diff_paths(root, base, in_index=True, outside=BINDING)
        )
        index_paths = _filter_paths(raw_index, excludes)
        problems = focused_staged(root, never, index_paths)
    else:
        index_paths = []
        problems = focused(root, never, excludes=excludes)
    if base is None or then is None:
        if base is not None:
            print(
                "suppressions: the merge base's binding has no Suppressions section: none counted"
            )
    elif options.staged:
        base_paths = _filter_paths(
            diff_paths(root, base, in_index=False, outside=BINDING), excludes
        )
        problems += risen(
            counts_staged(root, then.kinds, base_paths, ref=base),
            counts_staged(root, then.kinds, index_paths),
        )
    else:
        problems += risen(
            counts(root, base, then.kinds, excludes=excludes),
            counts(root, None, then.kinds, excludes=excludes),
        )
    return gatelib.report("suppressions", problems, base)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
