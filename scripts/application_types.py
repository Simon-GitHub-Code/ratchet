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

Usage: ``application_types.py`` prints the types. ``application_types.py TYPE...`` prints every
question the catalogue narrows with ``applies`` or the ``none: not a <type>`` cell to write, so
adoption starts from the rows these types leave out. Exits 2 on a type the catalogue lacks.
"""

from __future__ import annotations

import re
import sys
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Iterator, Mapping, Sequence
    from collections.abc import Set as AbstractSet

CATALOGUE = Path(__file__).resolve().parents[1] / "skills/method/application-types.toml"
DECLARATION = re.compile(r"^- Application types:(.*)$", re.MULTILINE)
NONE = re.compile(r"^none\b[\s,:;-]*(.*)$")
ADR = re.compile(r"\bADR[ -]?(\d{4})\b")
MIN_REASON = 10


@dataclass(frozen=True)
class Catalogue:
    """The shipped types, the questions narrowed to some of them, and the collection gates."""

    types: Mapping[str, str]
    narrowed: Mapping[str, frozenset[str]]
    collections: frozenset[str]

    @property
    def questions(self) -> frozenset[str]:
        """Every question the catalogue says something about; its row is never dropped."""
        return self.collections | frozenset(self.narrowed)

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
            yield f"the Gates table has no '{question}' row; a row is bound or none, never dropped"
        for row in rows:
            problem = self.row_problem(row, frozenset(kinds), adrs)
            if problem:
                yield problem


def load(path: Path = CATALOGUE) -> Catalogue:
    data = tomllib.loads(path.read_text(encoding="utf-8"))
    questions = data["question"]
    return Catalogue(
        types=dict(data["types"]),
        narrowed={q["name"]: frozenset(q["applies-to"]) for q in questions if "applies-to" in q},
        collections=frozenset(q["name"] for q in questions if q.get("collection")),
    )


def declared(binding: str) -> list[str] | None:
    """The types a binding's ``- Application types:`` line names; ``None`` without the line."""
    match = DECLARATION.search(binding)
    if match is None:
        return None
    return [name.strip(" `") for name in match.group(1).split(",") if name.strip(" `")]


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
