#!/usr/bin/env python3
"""Application types: which questions the method asks of which kind of software.

``skills/method/application-types.toml`` names six types (service, web-ui, app, cli, job,
library) and, per question, the types it is asked of. A type decides which questions apply and
never which tool answers one. A binding declares its types on one line::

    - Application types: service, web-ui

The conformance gate calls ``Catalogue.failures`` with the binding's Gates rows. A row that
applies to a declared type names a tool, or ``none`` with a reason and the ADR that accepts the
gap. A row that applies to none of them may say ``none: not a <type>``, naming a type the
question is asked of, and needs no ADR. A row the catalogue names is never dropped, and an empty
Tool cell is red.

A question may also require named proofs (``Catalogue.proofs``): faults specific to what it asks,
each described under ``[proofs]``. ``project_bite.py`` holds a bound row to them.

Every question also carries a trigger (ADR 0028): ``always``, ``code:<kind>`` or ``life:<event>``,
or parts of the last two joined by `` and ``, which fires when every part has. Until it fires, a
row's Tool cell may say ``later: <trigger>``, naming exactly the question's trigger, and judges
nothing. ``Catalogue.kinds`` and ``Catalogue.events`` describe what a trigger can name.

Usage: ``application_types.py`` prints the types. ``application_types.py TYPE...`` prints every
question the catalogue narrows with ``applies`` or the ``none: not a <type>`` cell to write, so
adoption starts from the rows these types leave out. Exits 2 on a type the catalogue lacks.
"""

from __future__ import annotations

import re
import sys
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

import gatelib

if TYPE_CHECKING:
    from collections.abc import Iterator, Mapping, Sequence
    from collections.abc import Set as AbstractSet

CATALOGUE = Path(__file__).resolve().parents[1] / "skills/method/application-types.toml"
DECLARATION = re.compile(r"^- Application types:(.*)$", re.MULTILINE)
EVENTS = re.compile(r"^- Life events:(.*)$", re.MULTILINE)
NONE = re.compile(r"^none\b[\s,:;-]*(.*)$")
# Why: ADR 0028. `later: <trigger>` is a whole cell, so the shipped trigger gate reads it as
# data; prose after the trigger would be a second grammar. Backticks are allowed, as in any cell.
LATER = re.compile(r"^`?later:(.*?)`?$")
ALWAYS = "always"
CODE = "code:"
LIFE = "life:"
# Why: a question some projects never meet even once deployed (a static site stores nothing) waits
# for the event and the kind of code together, so a trigger may join parts with this.
JOIN = " and "
ADR = re.compile(r"\bADR[ -]?(\d{4})\b")
MIN_REASON = 10


def parts(trigger: str) -> list[str]:
    """The parts of a trigger: one, or those a compound joins with `` and ``."""
    return [part.strip() for part in trigger.split(JOIN)]


def code_kinds(trigger: str) -> list[str]:
    """The kinds of code a trigger's ``code:`` parts name, in order."""
    return [part.removeprefix(CODE) for part in parts(trigger) if part.startswith(CODE)]


@dataclass(frozen=True)
class Event:
    """A life event: what it means and the globs whose presence makes the route propose it."""

    meaning: str
    signs: tuple[str, ...]


@dataclass(frozen=True)
class Catalogue:
    """The shipped types, the questions narrowed to some of them, and what their gates prove.

    ``proofs`` maps a question to the named proofs it requires, and ``meanings`` a proof to the
    fault it plants. ``triggers`` maps every question to when it is asked (ADR 0028), ``kinds`` a
    kind of code to what marks it, and ``events`` a life event to its meaning and signs.
    """

    types: Mapping[str, str]
    narrowed: Mapping[str, frozenset[str]]
    collections: frozenset[str]
    proofs: Mapping[str, tuple[str, ...]] = field(default_factory=dict)
    meanings: Mapping[str, str] = field(default_factory=dict)
    triggers: Mapping[str, str] = field(default_factory=dict)
    kinds: Mapping[str, str] = field(default_factory=dict)
    events: Mapping[str, Event] = field(default_factory=dict)

    @property
    def questions(self) -> frozenset[str]:
        """Every question the catalogue says something about; its row is never dropped."""
        named = self.collections | frozenset(self.narrowed) | frozenset(self.proofs)
        return named | frozenset(self.triggers)

    def trigger_of(self, question: str) -> str:
        """When a question is asked: ``always`` unless the catalogue names a trigger."""
        return self.triggers.get(question, ALWAYS)

    def trigger_problem(self, trigger: str) -> str | None:
        """Why a trigger is not one the catalogue can fire, or ``None`` when it is."""
        if trigger == ALWAYS:
            return None
        if all(self.part_fires_here(part) for part in parts(trigger)):
            return None
        return (
            f"'{trigger}' is not `always`, `code:<kind>` or `life:<event>` of this catalogue,"
            " or such parts joined by ` and `"
        )

    def part_fires_here(self, part: str) -> bool:
        """Whether a part of a trigger names a kind or an event of this catalogue."""
        return (part.startswith(CODE) and part.removeprefix(CODE) in self.kinds) or (
            part.startswith(LIFE) and part.removeprefix(LIFE) in self.events
        )

    def owed_by(self, event: str) -> tuple[str, ...]:
        """The questions whose trigger names ``event``, in the catalogue's order (ADR 0028).

        A question that joins the event to a kind of code is in the list: it waits for the kind too.
        """
        return tuple(
            question
            for question, trigger in self.triggers.items()
            if f"{LIFE}{event}" in parts(trigger)
        )

    def kinds_asked(self, types: AbstractSet[str]) -> frozenset[str]:
        """The kinds of code a repository of these types must say how to find (ADR 0028)."""
        return frozenset(
            kind
            for question in self.questions
            if self.asked_of(question) & types
            for kind in code_kinds(self.trigger_of(question))
        )

    def asked_of(self, question: str) -> frozenset[str]:
        """The types a question is asked of: every type unless the catalogue narrows it."""
        return self.narrowed.get(question, frozenset(self.types))

    def none_cell(self, question: str) -> str:
        """The Tool cell of a row that applies to none of the declared types."""
        *head, last = [a(kind) for kind in self.types if kind in self.asked_of(question)]
        return "none: not " + (f"{', '.join(head)} or {last}" if head else last)

    def row_problem(
        self, row: tuple[str, str], kinds: AbstractSet[str], adrs: AbstractSet[int]
    ) -> str | None:
        """What is wrong with one Gates row for a repository of these types."""
        question, tool = row
        if not tool:
            return f"gate '{question}' names no tool: an empty Tool cell is an unfinished binding"
        later = LATER.match(tool)
        if later is not None:
            return self.later_problem(question, later.group(1).strip())
        match = NONE.match(tool)
        if match is None:
            return None
        asked_of = self.asked_of(question)
        applies = bool(asked_of & kinds)
        if not applies and says_not_a(match.group(1), asked_of):
            return None
        problem = unaccepted(match.group(1), adrs)
        if problem is None:
            return None
        cell = self.none_cell(question)
        hint = "" if applies else f"; it is not asked of these types, so write `{cell}`"
        return f"gate '{question}' {problem}{hint}"

    def later_problem(self, question: str, said: str) -> str | None:
        """What is wrong with a ``later: <trigger>`` cell: it must be the question's own trigger."""
        trigger = self.trigger_of(question)
        if trigger == ALWAYS:
            return (
                f"gate '{question}' says `later: {said}`, but it is asked from the first commit:"
                " bind it"
            )
        if said != trigger:
            return (
                f"gate '{question}' says `later: {said}`, but its trigger is `{trigger}`: "
                f"write `later: {trigger}`"
            )
        return None

    def failures(
        self, binding: str, rows: Sequence[tuple[str, str]], adrs: AbstractSet[int]
    ) -> Iterator[str]:
        """What a binding's types line and Gates rows get wrong; ``rows`` are (question, tool)."""
        kinds = declared(binding)
        known = ", ".join(self.types)
        if kinds is None:
            yield f"has no '- Application types:' line; name one or more of {known}"
            return
        unknown = [kind for kind in kinds if kind not in self.types]
        if unknown or not kinds:
            named = f"'{unknown[0]}'" if unknown else "nothing"
            yield f"'- Application types:' names {named}; name one or more of {known}"
            return
        asked = {question for question, _ in rows}
        for question in sorted(self.questions - asked):
            yield dropped(question)
        for row in rows:
            problem = self.row_problem(row, frozenset(kinds), adrs)
            if problem:
                yield problem


def dropped(question: str) -> str:
    """The failure for a catalogue question with no row in the binding's Gates table."""
    return f"the Gates table has no '{question}' row; a row is bound or none, never dropped"


def load(path: Path = CATALOGUE) -> Catalogue:
    data = tomllib.loads(path.read_text(encoding="utf-8"))
    questions = data["question"]
    return Catalogue(
        types=dict(data["types"]),
        narrowed={q["name"]: frozenset(q["applies-to"]) for q in questions if "applies-to" in q},
        collections=frozenset(q["name"] for q in questions if q.get("collection")),
        proofs={q["name"]: tuple(q["proofs"]) for q in questions if "proofs" in q},
        meanings=dict(data.get("proofs", {})),
        triggers={q["name"]: q.get("trigger", ALWAYS) for q in questions},
        kinds=dict(data.get("kinds", {})),
        events={
            name: Event(event["meaning"], tuple(event["signs"]))
            for name, event in data.get("events", {}).items()
        },
    )


def declared(binding: str) -> list[str] | None:
    """The types a binding's ``- Application types:`` line names; ``None`` without the line."""
    match = DECLARATION.search(binding)
    if match is None:
        return None
    return [name.strip(" `") for name in match.group(1).split(",") if name.strip(" `")]


def life_events(binding: str) -> frozenset[str]:
    """The life events a binding's ``- Life events:`` line declares (ADR 0028).

    No line, or ``none``, declares none: a project is assumed young until its owner says otherwise.
    """
    match = EVENTS.search(binding)
    if match is None:
        return frozenset()
    named = (name.strip(" `") for name in match.group(1).split(","))
    return frozenset(name for name in named if name and name != "none")


def signs_seen(root: Path) -> dict[str, dict[str, str]]:
    """Per undeclared life event, each sign glob the tree matches and the first path it matched."""
    binding = gatelib.read_text(root / gatelib.BINDING)
    if binding is None:
        return {}
    declared = life_events(binding)
    paths = gatelib.tracked_paths(root)
    seen: dict[str, dict[str, str]] = {}
    for name, event in load().events.items():
        if name in declared:
            continue
        for sign in event.signs:
            match = next((path for path in paths if gatelib.matches(path, [sign])), None)
            if match is not None:
                seen.setdefault(name, {})[sign] = match
    return seen


def declared_or_all(binding: str) -> frozenset[str]:
    """The life events a judge may rely on: every event when the binding has no events line.

    Why: ``life_events`` reads a missing line as "no events", right for a trigger waiting on an
    owner's word, wrong for a judge that switches protection off. A 0.10 adoption that upgrades
    has the line missing while it may already be deployed, so for it a missing line fails closed;
    an explicit ``none`` is the owner saying the project is young.
    """
    if EVENTS.search(binding) is None:
        return frozenset(load().events)
    return life_events(binding)


def a(kind: str) -> str:
    return f"{'an' if kind[0] in 'aeiou' else 'a'} {kind}"


def says_not_a(reason: str, asked_of: AbstractSet[str]) -> bool:
    """Whether a ``none`` cell's reason names a type the question is asked of: 'not a service'."""
    return any(re.search(rf"\bnot {re.escape(a(kind))}\b", reason) for kind in asked_of)


def unaccepted(reason: str, adrs: AbstractSet[int]) -> str | None:
    """Why a ``none`` is not an accepted gap: no reason, no ADR, or an ADR that is not there."""
    if len(reason) < MIN_REASON:
        return f"is bound to none without a reason of {MIN_REASON}+ characters"
    cited = [int(number) for number in ADR.findall(reason)]
    if not cited:
        return "is bound to none and names no ADR that accepts the gap"
    missing = [number for number in cited if number not in adrs]
    if missing:
        return f"is bound to none under ADR {missing[0]:04d}, which docs/adr does not hold"
    return None


def main(argv: list[str]) -> int:
    catalogue = load()
    kinds = argv[1:]
    unknown = [kind for kind in kinds if kind not in catalogue.types]
    if unknown:
        print(f"application_types: '{unknown[0]}' is not one of {', '.join(catalogue.types)}")
        return 2
    if not kinds:
        for kind, meaning in catalogue.types.items():
            print(f"{kind}: {meaning}")
        return 0
    for question, asked_of in catalogue.narrowed.items():
        print(
            f"{question}: {'applies' if asked_of & set(kinds) else catalogue.none_cell(question)}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
