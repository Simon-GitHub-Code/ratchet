#!/usr/bin/env python3
from __future__ import annotations

import functools
import re
import sys
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

import gatelib
import markdown_text

if TYPE_CHECKING:
    from collections.abc import Iterator, Mapping, Sequence
    from collections.abc import Set as AbstractSet

CATALOGUE = Path(__file__).resolve().parents[1] / "skills/method/application-types.toml"
DECLARATION = re.compile(r"^- Application types:(.*)$", re.MULTILINE)
EVENTS = re.compile(r"^- Life events:(.*)$", re.MULTILINE)
STAKES = re.compile(r"^- Stakes:(.*)$", re.MULTILINE)
STANDARD = "standard"
HIGH_CONSEQUENCE = "high-consequence"
KNOWN_STAKES = (STANDARD, HIGH_CONSEQUENCE)
STANDARD_STAKES_REASON = re.compile(r"\b(?:standard stakes|not high-consequence)\b")
NONE = re.compile(r"^none\b[\s,:;-]*(.*)$")
LATER = re.compile(r"^`?later:(.*?)`?$")
ALWAYS = "always"
CODE = "code:"
LIFE = "life:"
JOIN = " and "
ADR = re.compile(r"\bADR[ -]?(\d{4})\b")
MIN_REASON = 10


def parts(trigger: str) -> list[str]:
    return [part.strip() for part in trigger.split(JOIN)]


def code_kinds(trigger: str) -> list[str]:
    return [part.removeprefix(CODE) for part in parts(trigger) if part.startswith(CODE)]


@dataclass(frozen=True)
class Event:
    meaning: str
    signs: tuple[str, ...]
    in_binding: bool = False


@dataclass(frozen=True)
class Catalogue:
    types: Mapping[str, str]
    narrowed: Mapping[str, frozenset[str]]
    collections: frozenset[str]
    proofs: Mapping[str, tuple[str, ...]] = field(default_factory=dict)
    meanings: Mapping[str, str] = field(default_factory=dict)
    triggers: Mapping[str, str] = field(default_factory=dict)
    kinds: Mapping[str, str] = field(default_factory=dict)
    events: Mapping[str, Event] = field(default_factory=dict)
    directed: frozenset[str] = frozenset()
    architecture: Mapping[str, str] = field(default_factory=dict)
    promoted: Mapping[str, str] = field(default_factory=dict)
    high_stakes: frozenset[str] = frozenset()

    @property
    def questions(self) -> frozenset[str]:
        named = self.collections | frozenset(self.narrowed) | frozenset(self.proofs)
        return named | frozenset(self.triggers)

    def required_questions(self, stakes: str = STANDARD) -> frozenset[str]:
        return self.questions if stakes == HIGH_CONSEQUENCE else self.questions - self.high_stakes

    def trigger_of(self, question: str, stakes: str = STANDARD) -> str:
        if stakes == HIGH_CONSEQUENCE and question in self.promoted:
            return self.promoted[question]
        return self.triggers.get(question, ALWAYS)

    def trigger_problem(self, trigger: str) -> str | None:
        if trigger == ALWAYS:
            return None
        if all(self.part_fires_here(part) for part in parts(trigger)):
            return None
        return (
            f"'{trigger}' is not `always`, `code:<kind>` or `life:<event>` of this catalogue,"
            " or such parts joined by ` and `"
        )

    def part_fires_here(self, part: str) -> bool:
        return (part.startswith(CODE) and part.removeprefix(CODE) in self.kinds) or (
            part.startswith(LIFE) and part.removeprefix(LIFE) in self.events
        )

    def owed_by(self, event: str) -> tuple[str, ...]:
        return tuple(
            question
            for question, trigger in self.triggers.items()
            if f"{LIFE}{event}" in parts(trigger)
        )

    def kinds_asked(self, types: AbstractSet[str], stakes: str = STANDARD) -> frozenset[str]:
        return frozenset(
            kind
            for question in self.required_questions(stakes)
            if self.asked_of(question) & types
            for kind in code_kinds(self.trigger_of(question, stakes))
        )

    def kinds_required(self, types: AbstractSet[str], stakes: str = STANDARD) -> frozenset[str]:
        if not types:
            return frozenset()
        reopen = (code_kinds(trigger) for trigger in self.architecture)
        return self.kinds_asked(types, stakes) | frozenset(
            kind for kinds in reopen for kind in kinds
        )

    def asked_of(self, question: str) -> frozenset[str]:
        return self.narrowed.get(question, frozenset(self.types))

    def none_cell(self, question: str) -> str:
        *head, last = [a(kind) for kind in self.types if kind in self.asked_of(question)]
        return "none: not " + (f"{', '.join(head)} or {last}" if head else last)

    def exempt_none(
        self, question: str, reason: str, kinds: AbstractSet[str], *, stakes: str
    ) -> bool:
        asked_of = self.asked_of(question)
        if not (asked_of & kinds) and says_not_a(reason, asked_of):
            return True
        return (
            stakes != HIGH_CONSEQUENCE
            and question in self.high_stakes
            and bool(STANDARD_STAKES_REASON.search(reason))
        )

    def none_hint(self, question: str, kinds: AbstractSet[str], *, stakes: str) -> str:
        if not (self.asked_of(question) & kinds):
            return f"; it is not asked of these types, so write `{self.none_cell(question)}`"
        if stakes != HIGH_CONSEQUENCE and question in self.high_stakes:
            return (
                "; it is asked only under high-consequence stakes, so write `none: standard stakes`"
            )
        return ""

    def row_problem(
        self,
        row: tuple[str, str],
        kinds: AbstractSet[str] | None,
        adrs: AbstractSet[int],
        *,
        stakes: str = STANDARD,
    ) -> str | None:
        question, tool = row
        if not tool:
            return f"gate '{question}' names no tool: an empty Tool cell is an unfinished binding"
        later = LATER.match(tool)
        if later is not None:
            return self.later_problem(question, later.group(1).strip(), stakes)
        match = NONE.match(tool)
        if match is None or kinds is None:
            return None
        if self.exempt_none(question, match.group(1), kinds, stakes=stakes):
            return None
        problem = unaccepted(match.group(1), adrs)
        if problem is None:
            return None
        return f"gate '{question}' {problem}{self.none_hint(question, kinds, stakes=stakes)}"

    def later_problem(self, question: str, said: str, stakes: str = STANDARD) -> str | None:
        trigger = self.trigger_of(question, stakes)
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

    def types_problem(self, binding: str) -> str | None:
        kinds = declared(binding)
        known = ", ".join(self.types)
        if kinds is None:
            return f"has no '- Application types:' line; name one or more of {known}"
        unknown = [kind for kind in kinds if kind not in self.types]
        if unknown or not kinds:
            named = f"'{unknown[0]}'" if unknown else "nothing"
            return f"'- Application types:' names {named}; name one or more of {known}"
        return None

    def failures(
        self, binding: str, rows: Sequence[tuple[str, str]], adrs: AbstractSet[int]
    ) -> Iterator[str]:
        problem = self.types_problem(binding)
        if problem:
            yield problem
        if stakes_error := stakes_problem(binding):
            yield stakes_error
        stakes = stakes_of(binding)
        kinds = None if problem else frozenset(declared(binding) or ())
        asked = {question for question, _ in rows}
        for question in sorted(self.required_questions(stakes) - asked):
            yield dropped(question)
        for row in rows:
            row_problem = self.row_problem(row, kinds, adrs, stakes=stakes)
            if row_problem:
                yield row_problem


def dropped(question: str) -> str:
    return f"the Gates table has no '{question}' row; a row is bound or none, never dropped"


def load(path: Path = CATALOGUE) -> Catalogue:
    return parsed(path.read_text(encoding="utf-8"))


@functools.lru_cache(maxsize=8)
def parsed(text: str) -> Catalogue:
    data = tomllib.loads(text)
    questions = data["question"]
    return Catalogue(
        types=dict(data["types"]),
        narrowed={q["name"]: frozenset(q["applies-to"]) for q in questions if "applies-to" in q},
        collections=frozenset(q["name"] for q in questions if q.get("collection")),
        proofs={q["name"]: tuple(q["proofs"]) for q in questions if "proofs" in q},
        meanings=dict(data.get("proofs", {})),
        triggers={q["name"]: q.get("trigger", ALWAYS) for q in questions},
        directed=frozenset(q["name"] for q in questions if q.get("directives")),
        kinds=dict(data.get("kinds", {})),
        events={
            name: Event(
                event["meaning"], tuple(event.get("signs", ())), bool(event.get("in-binding"))
            )
            for name, event in data.get("events", {}).items()
        },
        architecture=dict(data.get("architecture", {})),
        promoted={
            q["name"]: q["high-consequence-trigger"]
            for q in questions
            if "high-consequence-trigger" in q
        },
        high_stakes=frozenset(q["name"] for q in questions if q.get("stakes") == HIGH_CONSEQUENCE),
    )


def stakes_of(binding: str | None) -> str:
    match = STAKES.search(binding or "")
    if match is None:
        return STANDARD
    value = match.group(1).strip(" `")
    return HIGH_CONSEQUENCE if value == HIGH_CONSEQUENCE else STANDARD


def stakes_problem(binding: str) -> str | None:
    match = STAKES.search(binding)
    if match is None:
        return None
    value = match.group(1).strip(" `")
    if value in KNOWN_STAKES:
        return None
    named = f"'{value}'" if value else "nothing"
    return f"'- Stakes:' names {named}; write one of {', '.join(KNOWN_STAKES)}"


def declared(binding: str) -> list[str] | None:
    match = DECLARATION.search(binding)
    if match is None:
        return None
    return [name.strip(" `") for name in match.group(1).split(",") if name.strip(" `")]


def life_events(binding: str) -> frozenset[str]:
    match = EVENTS.search(binding)
    if match is None:
        return frozenset()
    named = (name.strip(" `") for name in match.group(1).split(","))
    return frozenset(name for name in named if name and name != "none")


SIGNS_SECTION = "## Life event signs"
SIGNS_COLUMNS = ("Event", "Paths")
MIN_REASON_LENGTH = 10


@dataclass(frozen=True)
class Signs:
    globs: Mapping[str, tuple[str, ...]]
    problems: tuple[str, ...]


def signs_in(binding: str | None) -> Signs | None:
    if binding is None or not markdown_text.has_section(binding, SIGNS_SECTION):
        return None
    events = load().events
    needed = [name for name, event in events.items() if event.in_binding]
    allowed = [name for name, event in events.items() if event.in_binding or event.signs]
    tables = [t for t in markdown_text.tables(binding, SIGNS_SECTION) if t.header == SIGNS_COLUMNS]
    if not tables:
        columns = " | ".join(SIGNS_COLUMNS)
        return Signs({}, (f"the section lacks its table with the columns {columns}",))
    globs: dict[str, tuple[str, ...]] = {}
    problems: list[str] = []
    for label, cell in tables[0].rows:
        name = label.strip("` ")
        plain = cell.strip("` ")
        if name.startswith("<") or cell.startswith("<"):
            problems.append("the table still has the template's placeholder row")
        elif name not in allowed:
            known = ", ".join(allowed)
            problems.append(f"the row '{name}' is no event whose signs the binding holds: {known}")
        elif name in globs:
            problems.append(f"the event '{name}' is listed twice")
        elif plain.lower().startswith("none:"):
            if len(plain[len("none:") :].strip()) < MIN_REASON_LENGTH:
                problems.append(f"'{name}' says none with no reason: say why no file marks it")
            globs[name] = ()
        elif listed := [g.strip() for item in markdown_text.ticked(cell) for g in item.split(",")]:
            globs[name] = tuple(glob for glob in listed if glob)
        else:
            problems.append(
                f"'{name}' names no globs: write them in backticks, or `none: <reason>`"
            )
    problems += [f"the event '{name}' has no row" for name in needed if name not in globs]
    return Signs(globs, tuple(problems))


def signs_seen(root: Path) -> dict[str, dict[str, str]]:
    binding = gatelib.read_text(root / gatelib.BINDING)
    if binding is None:
        return {}
    declared = life_events(binding)
    table = signs_in(binding)
    paths = gatelib.tracked_paths(root)
    seen: dict[str, dict[str, str]] = {}
    for name, event in load().events.items():
        if name in declared:
            continue
        for sign in (*event.signs, *(table.globs.get(name, ()) if table else ())):
            match = next((path for path in paths if gatelib.matches(path, [sign])), None)
            if match is not None:
                seen.setdefault(name, {})[sign] = match
    return seen


def declared_or_all(binding: str) -> frozenset[str]:
    if EVENTS.search(binding) is None:
        return frozenset(load().events)
    return life_events(binding)


def a(kind: str) -> str:
    return f"{'an' if kind[0] in 'aeiou' else 'a'} {kind}"


def says_not_a(reason: str, asked_of: AbstractSet[str]) -> bool:
    return any(re.search(rf"\bnot {re.escape(a(kind))}\b", reason) for kind in asked_of)


def unaccepted(reason: str, adrs: AbstractSet[int]) -> str | None:
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
