#!/usr/bin/env python3
r"""Triggers gate: a safeguard that is owed is bound before a second instance of what it guards.

ADR 0028: a question is asked once its trigger has fired. Until then its Gates row says
``later: code:<kind>`` or ``later: life:<event>`` and judges nothing. This gate watches the two
ways a trigger fires.

``code:<kind>``: the binding's ``## Triggers`` table says what marks an instance of each kind of
code in this stack::

    | Kind | Paths | Pattern |
    | --- | --- | --- |
    | outbound-call | `src/**/*.py` | `requests\.(get\|post)\(` |
    | listener | `src/server.py` | |
    | stored-format | none: nothing is stored | |

``Paths`` are comma-separated globs over tracked files. Without a ``Pattern`` each matching file
is one instance; with one, each matching line is. ``none: <reason>`` says this repository never
has the kind. A literal ``|`` in a pattern is written ``\|``.

``life:<event>``: the binding's ``- Life events:`` line names the events its owner declared.

A trigger may join parts with `` and `` (``life:deployed and code:stored-format``): it fires when
every part has. Such a row belongs to its code kind: a second instance of that kind while the row
is owed is red, and the event alone never is.

A row is **owed** when its trigger has fired and it is still ``later:``. The first instance of a
kind is free: the change that brings it prints a note and nothing more. A later change that adds
another instance while a row it owes is still ``later:`` is red, and says to run
``/ratchet:safeguard``, which chooses the tool, writes the recipe and the fault and binds the row.
A change that binds the row itself is green. A declared life event owes its rows too, and is never
red: the route puts binding them first.

The Triggers table, the Gates rows and the life events that judge are read at the merge base
(ADR 0028): a change cannot loosen its own judge. Instances are counted at the merge base and at
head, the work tree's tracked files. On the adoption (the base holds no binding) nothing is judged.

Other scripts import ``owed(root)`` and ``instances(root, ref)``. ``--json`` prints ``owed`` for
the route as a list of ``{"question", "trigger", "name", "count"}`` objects. ``--count`` prints
each kind of the work tree's table with its instances and the table's problems, so whoever writes
a row sees at once what it finds; it judges nothing.

Usage: ``triggers.py [--root DIR] [--base REF] [--json | --count]``. Exits 1 when red.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import TYPE_CHECKING

import application_types
import gatelib
import project_bite

if TYPE_CHECKING:
    from collections.abc import Collection, Mapping, Sequence

SECTION = "\n## Triggers\n"
NEVER = "none:"
SHOWN = 5
CELL_BREAK = re.compile(r"(?<!\\)\|")


@dataclass(frozen=True)
class Kind:
    """One row of the Triggers table: how a kind of code is found in this repository."""

    name: str
    paths: tuple[str, ...]
    pattern: re.Pattern[str] | None = None
    never: str = ""

    def finds(self, path: str) -> bool:
        """Whether ``path`` can hold an instance of this kind."""
        return gatelib.matches(path, list(self.paths))


@dataclass(frozen=True)
class Owed:
    """A Gates row whose trigger has fired and is still ``later:``.

    ``name`` is the kind of code or the life event (the code part of a compound), ``count`` its
    instances (0 for an event).
    """

    question: str
    trigger: str
    name: str
    count: int


def unwrap(cell: str) -> str:
    """A cell less the backticks around it."""
    return cell.strip().removeprefix("`").removesuffix("`").strip()


def table(text: str) -> list[list[str]]:
    """The rows of the Triggers table under its header, each a list of stripped cells."""
    _, _, body = text.partition(SECTION)
    lines = [line.strip() for line in body.split("\n## ")[0].splitlines() if line.startswith("|")]
    cells = [[cell.strip() for cell in CELL_BREAK.split(line.strip("|"))] for line in lines]
    rows = [row for row in cells if not set("".join(row)) <= set("-: ")]
    return rows[1:]


def parse(text: str) -> tuple[dict[str, Kind], list[str]]:
    """The kinds a binding's Triggers table describes, and what is wrong with its rows.

    A row with a problem is left out of the kinds. The conformance gate prints the problems; this
    gate reads what it can.
    """
    kinds: dict[str, Kind] = {}
    problems: list[str] = []
    for row in table(text):
        first, second, third = (*row, "", "")[:3]
        name, paths_cell = unwrap(first), unwrap(second)
        pattern_cell = unwrap(third).replace("\\|", "|")
        if name in kinds:
            problems.append(f"the Triggers table names '{name}' twice")
        elif paths_cell.lower().startswith(NEVER):
            kinds[name] = Kind(name, (), None, paths_cell[len(NEVER) :].strip())
        elif not paths_cell:
            problems.append(f"Triggers row '{name}' has no Paths: write globs, or `none: <reason>`")
        else:
            paths = tuple(unwrap(glob) for glob in paths_cell.split(",") if unwrap(glob))
            try:
                pattern = re.compile(pattern_cell) if pattern_cell else None
            except re.error as error:
                problems.append(f"Triggers row '{name}' has a Pattern that is not a regex: {error}")
                continue
            kinds[name] = Kind(name, paths, pattern)
    return kinds, problems


def binding_at(root: Path, ref: str | None) -> str:
    """The binding at ``ref``, or the work tree's when ``ref`` is ``None``; empty when absent."""
    if ref is None:
        return gatelib.read_text(root / gatelib.BINDING) or ""
    return gatelib.show(root, ref, str(gatelib.BINDING)) or ""


def file_text(root: Path, ref: str | None, path: str) -> str:
    """A tracked file's text, empty when it is gone or is not text."""
    try:
        if ref is None:
            return (root / path).read_text(encoding="utf-8")
        return gatelib.show(root, ref, path) or ""
    except (OSError, UnicodeDecodeError):
        return ""


def instances(
    root: Path, ref: str | None = None, kinds: Mapping[str, Kind] | None = None
) -> dict[str, list[str]]:
    """The instances of each kind at ``ref`` (the work tree when ``None``), by kind.

    Each is a path, or ``path:line`` for a kind with a Pattern. ``kinds`` defaults to the Triggers
    table of the binding at ``ref``; the gate passes the merge base's, so both counts are made
    with one judge. Every kind of the table is a key, with no instances when it has none.
    """
    described = parse(binding_at(root, ref))[0] if kinds is None else kinds
    paths = gatelib.tracked_paths(root, ref)
    found: dict[str, list[str]] = {}
    for name, kind in described.items():
        found[name] = []
        for path in (p for p in paths if kind.finds(p)):
            if kind.pattern is None:
                found[name].append(path)
                continue
            lines = file_text(root, ref, path).splitlines()
            found[name] += [
                f"{path}:{n}" for n, line in enumerate(lines, 1) if kind.pattern.search(line)
            ]
    return found


def later_trigger(tool: str) -> str | None:
    """The trigger a ``later: <trigger>`` Tool cell names; ``None`` for any other cell."""
    match = application_types.LATER.match(tool)
    return match.group(1).strip() if match else None


def owed_rows(
    rows: Sequence[project_bite.Row], found: Mapping[str, Collection[str]], events: Collection[str]
) -> list[Owed]:
    """The ``later:`` rows of ``rows`` whose trigger has fired, given instances and events."""
    owing: list[Owed] = []
    for row in rows:
        trigger = later_trigger(row.tool)
        if trigger is None:
            continue
        if not all(fired(part, found, events) for part in application_types.parts(trigger)):
            continue
        kinds = application_types.code_kinds(trigger)
        if kinds:
            owing.append(Owed(row.question, trigger, kinds[0], len(found[kinds[0]])))
        else:
            name = trigger.removeprefix(application_types.LIFE)
            owing.append(Owed(row.question, trigger, name, 0))
    return owing


def fired(part: str, found: Mapping[str, Collection[str]], events: Collection[str]) -> bool:
    """Whether one part of a trigger has fired: the kind has an instance, the event is declared."""
    if part.startswith(application_types.CODE):
        return bool(found.get(part.removeprefix(application_types.CODE)))
    return (
        part.startswith(application_types.LIFE)
        and part.removeprefix(application_types.LIFE) in events
    )


def owed(root: Path) -> list[Owed]:
    """The rows head owes: still ``later:``, with the kind present or the event declared."""
    binding = binding_at(root, None)
    kinds, _ = parse(binding)
    return owed_rows(
        project_bite.gate_rows(binding),
        instances(root, None, kinds),
        application_types.life_events(binding),
    )


def listing(labels: Sequence[str]) -> str:
    """A few instances, then how many more."""
    more = len(labels) - SHOWN
    return ", ".join(labels[:SHOWN]) + (f" and {more} more" if more > 0 else "")


def names(rows: Sequence[Owed]) -> str:
    return ", ".join(row.question for row in rows)


def judge(root: Path, base: str) -> tuple[list[str], list[str]]:
    """The problems and the notes of the change since ``base``."""
    before = binding_at(root, base)
    kinds, _ = parse(before)
    bound_at_head = {
        row.question
        for row in project_bite.gate_rows(binding_at(root, None))
        if later_trigger(row.tool) is None
    }
    at_base, at_head = instances(root, base, kinds), instances(root, None, kinds)
    owing = [
        row
        for row in owed_rows(
            project_bite.gate_rows(before), at_head, application_types.life_events(before)
        )
        if row.question not in bound_at_head
    ]
    problems: list[str] = []
    notes: list[str] = []
    for kind in kinds:
        rows = [row for row in owing if application_types.code_kinds(row.trigger)[:1] == [kind]]
        before_count, now = len(at_base[kind]), len(at_head[kind])
        if not rows or now <= before_count:
            continue
        if before_count == 0:
            notes.append(
                f"triggers: note: '{kind}' has arrived ({listing(at_head[kind])}) and owes"
                f" {names(rows)}; this first one is free. Run /ratchet:safeguard next to bind"
                " them before another is added (ADR 0028)"
            )
            continue
        added = [label for label in at_head[kind] if label not in set(at_base[kind])]
        problems.append(
            f"triggers: '{kind}' gained {len(added)} instance(s) ({listing(added)}) while the"
            f" base already held {before_count} and these rows are still `later:`:"
            f" {names(rows)}. Run /ratchet:safeguard to bind them before adding more (ADR 0028)"
        )
    for event in sorted(
        {row.name for row in owing if not application_types.code_kinds(row.trigger)}
    ):
        rows = [row for row in owing if row.trigger == application_types.LIFE + event]
        notes.append(
            f"triggers: note: life event '{event}' is declared and owes {names(rows)};"
            " run /ratchet:safeguard to bind them (ADR 0028)"
        )
    return problems, notes


def count(root: Path) -> None:
    """Print each kind of the work tree's Triggers table with what it finds, and its problems."""
    kinds, problems = parse(binding_at(root, None))
    for problem in problems:
        print(f"triggers: {problem}")
    for name, found in instances(root, None, kinds).items():
        if kinds[name].never:
            print(f"{name}: none: {kinds[name].never}")
        else:
            print(f"{name}: {len(found)}" + (f" ({listing(found)})" if found else ""))


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="triggers.py")
    parser.add_argument("--root", type=Path, default=Path())
    parser.add_argument("--base")
    parser.add_argument("--json", action="store_true", help="print the rows head owes, as JSON")
    parser.add_argument("--count", action="store_true", help="print each kind's instances")
    options = parser.parse_args(argv[1:])
    root = options.root.resolve()
    if options.count:
        count(root)
        return 0
    if options.json:
        print(json.dumps([asdict(row) for row in owed(root)]))
        return 0
    base = gatelib.base_commit(root, options.base)
    if base is None:
        print("triggers: green: there is no commit to compare the tree with; nothing is judged")
        return 0
    if gatelib.adoption(root, base):
        print(
            f"triggers: green: the base {base[:9]} holds no {gatelib.BINDING}, so this change is"
            " the adoption and nothing is judged; the route lists what is owed"
        )
        return 0
    problems, notes = judge(root, base)
    for note in notes:
        print(note)
    return gatelib.report("triggers", problems, base)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
