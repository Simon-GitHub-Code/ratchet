#!/usr/bin/env python3
"""The suppressions gate: no kind of suppression rises, and no focus marker exists.

The plugin names no directive of any tool (ADR 0040). The binding's ``## Suppressions`` section
lists, one row per kind, the directives of the tools this project bound, each as an extended
regular expression written by whoever bound the tool::

    | Kind | Pattern | Rule |
    | --- | --- | --- |
    | <kind> | `<regex>` | rises |

``rises`` fails when the kind's count (lines matched, at the merge base and at head) rose;
``never`` fails on any match at head, as a marker that narrows a suite to the part somebody was
looking at does. The merge base's table judges the change, so a change cannot loosen its own
judge; a ``never`` row of either side is held at head. The binding itself is not searched, since
it quotes every pattern. A second table says which kinds silence each
Gates question that a directive can silence, or ``none: <reason>``; conformance holds that table
to the Gates rows.

Usage: ``suppressions.py [--root DIR] [--base REF]``.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

import gatelib
import markdown_text
from markers import grep_count

SECTION = "## Suppressions"
KIND = "Kind"
QUESTION = "Question"
BINDING = str(gatelib.BINDING)
RISES = "rises"
NEVER = "never"
RULES = (RISES, NEVER)
NONE_PREFIX = "none:"
MIN_REASON = 10
COLUMNS = {KIND: (KIND, "Pattern", "Rule"), QUESTION: (QUESTION, "Kinds")}


@dataclass(frozen=True)
class Kind:
    """One row of the kinds table: a name, the regex that finds one, and what a match means."""

    name: str
    pattern: str
    rule: str


@dataclass(frozen=True)
class Spec:
    """A binding's suppressions: its kinds, the kinds that silence each question, its problems."""

    kinds: tuple[Kind, ...]
    silenced: dict[str, tuple[str, ...]]
    problems: tuple[str, ...]


def valid(pattern: str) -> bool:
    """Whether ``git grep -E`` takes the pattern: a pattern git rejects counts nothing, silently."""
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
            problems.append(f"the kind '{name}' has the Rule '{rule}': write `rises` or `never`")
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


def parse(binding: str | None) -> Spec | None:
    """The binding's Suppressions section, or ``None`` when it has none."""
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
    return Spec(kinds, silenced, tuple(problems))


def counts(root: Path, ref: str | None, kinds: tuple[Kind, ...]) -> dict[str, int]:
    return {
        k.name: grep_count(root, k.pattern, ref, word=False, outside=BINDING)
        for k in kinds
        if k.rule == RISES
    }


def risen(before: dict[str, int], after: dict[str, int]) -> list[str]:
    return [
        f"{kind}: rose from {before.get(kind, 0)} to {count}"
        for kind, count in after.items()
        if count > before.get(kind, 0)
    ]


def focused(root: Path, kinds: tuple[Kind, ...]) -> list[str]:
    """Each match of a ``never`` kind in the work tree, as ``path:line: text``."""
    found: list[str] = []
    for kind in kinds:
        if kind.rule == NEVER:
            out = gatelib.git(
                root,
                *("grep", "-I", "-n", "-E", "--untracked", "-e", kind.pattern),
                *("--", ".", f":(exclude){BINDING}"),
            )
            found += [f"{kind.name} at {line}" for line in (out or "").splitlines()]
    return found


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", default=".")
    parser.add_argument("--base")
    options = parser.parse_args(argv[1:])
    root = Path(options.root).resolve()
    base = gatelib.base_commit(root, options.base)
    now = parse(gatelib.read_text(root / gatelib.BINDING))
    then = parse(gatelib.show(root, base, str(gatelib.BINDING))) if base else None
    never = {k.name: k for spec in (then, now) if spec for k in spec.kinds if k.rule == NEVER}
    problems = focused(root, tuple(never.values()))
    if then is None:
        if base is not None:
            print(
                "suppressions: the merge base's binding has no Suppressions section: none counted"
            )
    else:
        problems += risen(counts(root, base, then.kinds), counts(root, None, then.kinds))
    return gatelib.report("suppressions", problems, base)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
