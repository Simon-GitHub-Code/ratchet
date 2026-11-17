#!/usr/bin/env python3
from __future__ import annotations

import argparse
import functools
import json
import re
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import TYPE_CHECKING

import application_types
import gatelib
import markdown_text
import project_bite

if TYPE_CHECKING:
    from collections.abc import Callable, Collection, Mapping, Sequence

SECTION = "\n## Triggers\n"
NEVER = "none:"
SHOWN = 5
CELL_BREAK = re.compile(r"(?<!\\)\|")
REVIEW_QUESTION = "architecture reviewed when its shape changes"
REVIEW = "architecture review"
ARCHITECTURE = "/ratchet:architecture"
ADRS = "docs/adr/"
REVIEW_LINE = re.compile(r"^- Architecture review:(.*)$", re.MULTILINE)
OUTCOME_LINE = re.compile(r"^- Outcome:\s*`?(\w+)`?\s*$", re.MULTILINE)
DECIDED_LINE = re.compile(r"^- Decided:\s*\d{4}-\d{2}-\d{2}\s*$", re.MULTILINE)
HOLDS, CHANGES = "holds", "changes"
DECISION, REFUTATION, OPTIONS = "## Decision", "## Refutation criterion", "## Considered options"
OPTION = re.compile(r"^(?:[-*]|\d+\.) \S", re.MULTILINE)
MIN_OPTIONS = 2


@dataclass(frozen=True)
class Kind:
    name: str
    paths: tuple[str, ...]
    pattern: re.Pattern[str] | None = None
    never: str = ""

    def finds(self, path: str) -> bool:
        return gatelib.matches(path, list(self.paths))


@dataclass(frozen=True)
class Owed:
    question: str
    trigger: str
    name: str
    count: int


def unwrap(cell: str) -> str:
    return cell.strip().removeprefix("`").removesuffix("`").strip()


def table(text: str) -> list[list[str]]:
    _, _, body = text.partition(SECTION)
    lines = [line.strip() for line in body.split("\n## ")[0].splitlines() if line.startswith("|")]
    cells = [[cell.strip() for cell in CELL_BREAK.split(line.strip("|"))] for line in lines]
    rows = [row for row in cells if not set("".join(row)) <= set("-: ")]
    return rows[1:]


def parse(text: str) -> tuple[dict[str, Kind], list[str]]:
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
    if ref is None:
        return gatelib.read_text(root / gatelib.BINDING) or ""
    return gatelib.show(root, ref, str(gatelib.BINDING)) or ""


def file_text(root: Path, ref: str | None, path: str) -> str:
    try:
        if ref is None:
            return (root / path).read_text(encoding="utf-8")
        return gatelib.show(root, ref, path) or ""
    except (OSError, UnicodeDecodeError):
        return ""


def instances(
    root: Path, ref: str | None = None, kinds: Mapping[str, Kind] | None = None
) -> dict[str, list[str]]:
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


def held_before(root: Path, base: str, kind: Kind) -> str | None:
    for commit, names_text in touched(root, base):
        if (
            any(kind.finds(path) for path in names_text.split("\n") if path)
            and instances(root, commit, {kind.name: kind})[kind.name]
        ):
            return commit
    return None


@functools.lru_cache(maxsize=16)
def touched(root: Path, base: str) -> tuple[tuple[str, str], ...]:
    listed = gatelib.git(
        root, "log", "-m", "--first-parent", "--no-renames", "--name-only", "--format=%x00%H", base
    )
    blocks = (block.partition("\n") for block in (listed or "").split("\0")[1:])
    return tuple((commit, names_text) for commit, _, names_text in blocks)


def later_trigger(tool: str) -> str | None:
    match = application_types.LATER.match(tool)
    return match.group(1).strip() if match else None


def owed_rows(
    rows: Sequence[project_bite.Row], found: Mapping[str, Collection[str]], events: Collection[str]
) -> list[Owed]:
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
    if part.startswith(application_types.CODE):
        return bool(found.get(part.removeprefix(application_types.CODE)))
    return (
        part.startswith(application_types.LIFE)
        and part.removeprefix(application_types.LIFE) in events
    )


def owed(root: Path) -> list[Owed]:
    binding = binding_at(root, None)
    kinds, _ = parse(binding)
    return owed_rows(
        project_bite.gate_rows(binding),
        instances(root, None, kinds),
        application_types.life_events(binding),
    )


@dataclass(frozen=True)
class Review:
    path: str
    keys: tuple[str, ...]
    problems: tuple[str, ...]


def review_of(path: str, text: str) -> Review | None:
    match = REVIEW_LINE.search(text)
    if match is None:
        return None
    keys = tuple(unwrap(key) for key in match.group(1).split(",") if unwrap(key))
    problems: list[str] = []
    outcome = OUTCOME_LINE.search(text)
    said = outcome.group(1) if outcome else None
    if said not in (HOLDS, CHANGES):
        problems.append(f"has no '- Outcome: {HOLDS}' or '- Outcome: {CHANGES}' line")
    if DECIDED_LINE.search(text) is None:
        problems.append("has no '- Decided: YYYY-MM-DD' line")
    problems += [
        f"has no '{heading}' section that says something"
        for heading in (DECISION, REFUTATION)
        if markdown_text.empty_body(text, heading)
    ]
    options = len(OPTION.findall(markdown_text.section(text, OPTIONS)))
    if said == CHANGES and options < MIN_OPTIONS:
        problems.append(
            f"changes the architecture and lists {options} option(s) under '{OPTIONS}';"
            f" it lists {MIN_OPTIONS} or more, with what each costs to undo"
        )
    return Review(path, keys, tuple(problems))


def reviews_at(root: Path, ref: str | None) -> list[Review]:
    if ref is None:
        paths = [p for p in gatelib.tracked_paths(root) if p.startswith(ADRS) and p.endswith(".md")]
    else:
        listed = gatelib.git(root, "grep", "-l", "-E", REVIEW_LINE.pattern, ref, "--", ADRS)
        paths = [line.removeprefix(f"{ref}:") for line in (listed or "").splitlines()]
    found = []
    for path in paths:
        review = review_of(path, file_text(root, ref, path))
        if review is not None:
            found.append(review)
    return found


def first_held(root: Path, ref: str, kind: Kind) -> int | None:
    log = touched(root, ref)
    for place in range(len(log) - 1, -1, -1):
        commit, names_text = log[place]
        if (
            any(kind.finds(path) for path in names_text.split("\n") if path)
            and instances(root, commit, {kind.name: kind})[kind.name]
        ):
            return place
    return None


def added_at(root: Path, ref: str, path: str) -> int | None:
    listed = gatelib.git(
        root, "log", "-m", "--first-parent", "--diff-filter=A", "--format=%H", ref, "--", path
    )
    added = (listed or "").split()
    places = {commit: place for place, (commit, _) in enumerate(touched(root, ref))}
    return places.get(added[-1]) if added else None


def settled(
    root: Path, ref: str, reviews: Sequence[Review], *, key: str, kind: Kind | None
) -> bool:
    counting = [review for review in reviews if not review.problems and key in review.keys]
    if kind is None:
        return bool(counting)
    arrival = first_held(root, ref, kind)
    if arrival is None:
        return False
    return any((at := added_at(root, ref, r.path)) is None or at < arrival for r in counting)


def why_not(reviews: Sequence[Review], key: str) -> str:
    said = [
        f"{review.path} {'; '.join(review.problems)}" for review in reviews if key in review.keys
    ]
    return "; ".join(said) if said else f"no decision record names `{key}`"


def reviews_bound(binding: str) -> bool:
    return any(
        row.question == REVIEW_QUESTION and project_bite.SHIPPED.fullmatch(row.tool)
        for row in project_bite.gate_rows(binding)
    )


def reviews_start(root: Path, ref: str) -> str | None:
    listed = gatelib.git(
        root,
        "log",
        "-m",
        "--first-parent",
        "--reverse",
        "--format=%H",
        f"-G{REVIEW_QUESTION}",
        ref,
        "--",
        str(gatelib.BINDING),
    )
    for commit in dict.fromkeys((listed or "").split()):
        if reviews_bound(binding_at(root, commit)):
            return commit
    return None


@dataclass
class Start:
    root: Path
    ref: str

    @functools.cached_property
    def commit(self) -> str | None:
        return reviews_start(self.root, self.ref)

    def had(self, kind: Kind) -> bool:
        return self.commit is None or held_before(self.root, self.commit, kind) is not None

    def declared(self, event: str) -> bool:
        if self.commit is None:
            return True
        return event in application_types.life_events(binding_at(self.root, self.commit))


def reviews_owed(root: Path) -> list[Owed]:
    binding = binding_at(root, None)
    if not reviews_bound(binding):
        return []
    kinds, _ = parse(binding)
    reopening = application_types.load().architecture
    named = {kind for key in reopening for kind in application_types.code_kinds(key)}
    found = instances(root, None, {name: kinds[name] for name in named if name in kinds})
    events = application_types.life_events(binding)
    reviews = reviews_at(root, None)
    head = gatelib.git(root, "rev-parse", "--verify", "--quiet", "HEAD")
    if head is None:
        return []
    start = Start(root, head)
    owing: list[Owed] = []
    for key in reopening:
        code = application_types.code_kinds(key)
        if code:
            kind = code[0]
            arrived = found.get(kind) and not start.had(kinds[kind])
            if arrived and not settled(root, head, reviews, key=key, kind=kinds[kind]):
                owing.append(Owed(REVIEW, key, kind, len(found[kind])))
            continue
        event = key.removeprefix(application_types.LIFE)
        if (
            event in events
            and not start.declared(event)
            and not settled(root, head, reviews, key=key, kind=None)
        ):
            owing.append(Owed(REVIEW, key, event, 0))
    return owing


def listing(labels: Sequence[str]) -> str:
    more = len(labels) - SHOWN
    return ", ".join(labels[:SHOWN]) + (f" and {more} more" if more > 0 else "")


def names(rows: Sequence[Owed]) -> str:
    return ", ".join(row.question for row in rows)


def judge(root: Path, base: str) -> tuple[list[str], list[str]]:
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
    history = functools.cache(lambda kind: held_before(root, base, kinds[kind]))
    for kind in kinds:
        rows = [row for row in owing if application_types.code_kinds(row.trigger)[:1] == [kind]]
        before_count, now = len(at_base[kind]), len(at_head[kind])
        if not rows or now <= before_count:
            continue
        earlier = history(kind) if before_count == 0 else None
        if before_count == 0 and earlier is None:
            notes.append(
                f"triggers: note: '{kind}' has arrived ({listing(at_head[kind])}) and owes"
                f" {names(rows)}; the change that brings a kind is free. Run /ratchet:safeguard"
                " next to bind them before another is added (ADR 0030)"
            )
            continue
        added = [label for label in at_head[kind] if label not in set(at_base[kind])]
        held = (
            f"the base already held {before_count}"
            if earlier is None
            else f"the base holds none but {earlier[:9]} did, so it is returning, not arriving,"
        )
        adr = "0028" if earlier is None else "0048"
        problems.append(
            f"triggers: '{kind}' gained {len(added)} instance(s) ({listing(added)}) while"
            f" {held} and these rows are still `later:`: {names(rows)}."
            f" Run /ratchet:safeguard to bind them before adding more (ADR {adr})"
        )
    for event in sorted(
        {row.name for row in owing if not application_types.code_kinds(row.trigger)}
    ):
        rows = [row for row in owing if row.trigger == application_types.LIFE + event]
        notes.append(
            f"triggers: note: life event '{event}' is declared and owes {names(rows)};"
            " run /ratchet:safeguard to bind them (ADR 0028)"
        )
    if reviews_bound(before):
        reviewing = judge_reviews(Judged(root, base, before, kinds, at_base, at_head), history)
        problems += reviewing[0]
        notes += reviewing[1]
    return problems, notes


@dataclass(frozen=True)
class Judged:
    root: Path
    base: str
    before: str
    kinds: Mapping[str, Kind]
    at_base: Mapping[str, list[str]]
    at_head: Mapping[str, list[str]]


def judge_reviews(
    judged: Judged, history: Callable[[str], str | None]
) -> tuple[list[str], list[str]]:
    reopening = application_types.load().architecture
    grown = [
        kind
        for kind in judged.kinds
        if application_types.CODE + kind in reopening
        and len(judged.at_head[kind]) > len(judged.at_base[kind])
    ]
    events = [
        event
        for event in sorted(application_types.life_events(judged.before))
        if application_types.LIFE + event in reopening
    ]
    if not grown and not events:
        return [], []
    found = reviews_at(judged.root, judged.base)
    start = Start(judged.root, judged.base)
    problems: list[str] = []
    notes: list[str] = []
    for kind in grown:
        key = application_types.CODE + kind
        before_count = len(judged.at_base[kind])
        earlier = history(kind) if before_count == 0 else None
        if (before_count or earlier) and settled(
            judged.root, judged.base, found, key=key, kind=judged.kinds[kind]
        ):
            continue
        if before_count == 0 and earlier is None:
            notes.append(
                f"triggers: note: '{kind}' has arrived ({listing(judged.at_head[kind])}) and"
                f" reopens the architecture ({reopening[key]}); the change that brings a kind is"
                f" free. Run {ARCHITECTURE} next to review it before another is added (ADR 0052)"
            )
            continue
        if start.had(judged.kinds[kind]):
            continue
        added = [label for label in judged.at_head[kind] if label not in set(judged.at_base[kind])]
        held = (
            f"the base already held {before_count}"
            if earlier is None
            else f"the base holds none but {earlier[:9]} did, so it is returning, not arriving,"
        )
        problems.append(
            f"triggers: architecture review owed: '{kind}' gained {len(added)} instance(s)"
            f" ({listing(added)}) while {held} and the review it reopens has not landed:"
            f" {why_not(found, key)}. Run {ARCHITECTURE} and land its decision record before"
            " adding more (ADR 0052)"
        )
    for event in events:
        key = application_types.LIFE + event
        if not settled(judged.root, judged.base, found, key=key, kind=None) and not start.declared(
            event
        ):
            notes.append(
                f"triggers: note: life event '{event}' is declared and reopens the architecture"
                f" ({reopening[key]}); run {ARCHITECTURE} to review it (ADR 0052)"
            )
    return problems, notes


def count(root: Path) -> None:
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
    parser.add_argument(
        "--reviews", action="store_true", help="print the architecture reviews head owes, as JSON"
    )
    options = parser.parse_args(argv[1:])
    root = options.root.resolve()
    if options.count:
        count(root)
        return 0
    if options.json:
        print(json.dumps([asdict(row) for row in owed(root)]))
        return 0
    if options.reviews:
        reopening = application_types.load().architecture
        owing = [{**asdict(row), "reopens": reopening[row.trigger]} for row in reviews_owed(root)]
        print(json.dumps(owing))
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
