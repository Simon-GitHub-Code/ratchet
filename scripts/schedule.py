from __future__ import annotations

import functools
import shlex
from collections.abc import Hashable
from dataclasses import dataclass, field
from itertools import combinations
from typing import TYPE_CHECKING, Generic, TypeVar

import gatelib
import justfile_text
import markdown_text

if TYPE_CHECKING:
    import re
    from collections.abc import Callable, Iterable
    from pathlib import Path

SPEED = "## Speed"
APART = "Apart"
ALONE = "Alone"
WIDE = "Wide"
KINDS = (APART, ALONE, WIDE)
FLAGS = {APART: "--serial", ALONE: "--alone", WIDE: "--wide"}
KIND = {flag: kind for kind, flag in FLAGS.items()}
VALUED = frozenset(("--runner", "--jobs", "--root", "--report"))
RUNNER = "parallel.py"
UNREAD = "\0"
SHELL = frozenset("();<>|&")

Groups = tuple[tuple[str, ...], ...]
Task = TypeVar("Task", bound=Hashable)


def names(groups: Groups) -> frozenset[str]:
    return frozenset(name for group in groups for name in group)


def distinct(groups: Iterable[tuple[str, ...]]) -> Groups:
    found: dict[frozenset[str], tuple[str, ...]] = {}
    for group in groups:
        found.setdefault(frozenset(group), group)
    return tuple(found.values())


def spans(groups: Groups) -> str:
    return " ".join(f"`{' '.join(group)}`" for group in groups) or "none"


@dataclass(frozen=True)
class Schedule:
    groups: dict[str, Groups] = field(default_factory=dict)

    @property
    def apart(self) -> Groups:
        return self.groups.get(APART, ())

    @property
    def alone(self) -> frozenset[str]:
        return names(self.groups.get(ALONE, ()))

    @property
    def wide(self) -> frozenset[str]:
        return names(self.groups.get(WIDE, ()))

    def beside(self, one: str, other: str) -> bool:
        if one in self.alone or other in self.alone:
            return False
        return not any(one in group and other in group for group in self.apart)

    def among(self, gates: Iterable[str]) -> Schedule:
        present = frozenset(gates)
        kept = {
            kind: (tuple(name for name in group if name in present) for group in groups)
            for kind, groups in self.groups.items()
        }
        return Schedule({kind: tuple(filter(None, groups)) for kind, groups in kept.items()})

    def joined(self, other: Schedule) -> Schedule:
        kinds = dict.fromkeys([*self.groups, *other.groups])
        return Schedule(
            {
                kind: distinct([*self.groups.get(kind, ()), *other.groups.get(kind, ())])
                for kind in kinds
            }
        )

    def unstated(self, given: Schedule) -> Schedule:
        return Schedule(
            {
                APART: tuple(
                    group
                    for group in given.apart
                    if any(self.beside(*pair) for pair in combinations(group, 2))
                ),
                ALONE: singles(given.groups.get(ALONE, ()), self.alone),
                WIDE: singles(given.groups.get(WIDE, ()), self.wide),
            }
        )


def singles(groups: Groups, known: frozenset[str]) -> Groups:
    return tuple((name,) for group in groups for name in group if name not in known)


def stated(binding: str) -> Schedule:
    bullets = markdown_text.bullets(markdown_text.section(binding, SPEED))
    return Schedule(
        {
            kind: tuple(tuple(span.split()) for span in markdown_text.ticked(line))
            for kind, line in bullets.items()
            if kind in KINDS and not line.startswith("<")
        }
    )


def value(justfile: str, expression: str, seen: frozenset[str]) -> str:
    words = justfile_text.words(expression)
    if len(words) % 2 == 0 or any(word != "+" for word in words[1::2]):
        return UNREAD
    return "".join(operand(justfile, word, seen) for word in words[::2])


def operand(justfile: str, word: str, seen: frozenset[str]) -> str:
    literal = justfile_text.STRING.fullmatch(word)
    if literal and "\\" not in word:
        return literal.group(1) if literal.group(1) is not None else literal.group(2)
    defined = justfile_text.variables(justfile).get(word)
    if literal or defined is None or word in seen:
        return UNREAD
    return value(justfile, defined[: justfile_text.comment_at(defined)], seen | {word})


def interpolated(justfile: str, params: frozenset[str], found: re.Match[str]) -> str:
    return value(justfile, found[1], params)


def passed(line: str) -> tuple[Schedule, bool]:
    lexer = shlex.shlex(line, posix=True, punctuation_chars=True)
    lexer.whitespace_split = True
    try:
        words = list(lexer)
    except ValueError:
        return Schedule(), True
    named = (at for at, word in enumerate(words) if word.rsplit("/", 1)[-1] == RUNNER)
    start = next(named, len(words))
    found: dict[str, list[list[str]]] = {kind: [] for kind in KINDS}
    unread = False
    kind: str | None = None
    valued = False
    for word in words[start + 1 :]:
        flag, equals, rest = word.partition("=")
        if valued:
            valued = False
        elif word == "--" or set(word) <= SHELL:
            break
        elif UNREAD in word:
            unread, kind = True, None
        elif flag in KIND:
            kind = KIND[flag]
            found[kind].append([rest] if equals else [])
        elif word.startswith("-"):
            kind, valued = None, word in VALUED
        elif kind is not None:
            found[kind][-1].append(word)
    groups = {kind: tuple(tuple(group) for group in found[kind] if group) for kind in KINDS}
    each = {kind: singles(groups[kind], frozenset()) for kind in (ALONE, WIDE)}
    return Schedule({APART: groups[APART], **each}), unread


def recipes(justfile: str) -> tuple[Schedule, list[str]]:
    lines = justfile.split("\n")
    kept = Schedule()
    unread: list[str] = []
    for block in justfile_text.blocks(lines):
        if block.parsed is None:
            continue
        body = "\n".join(lines[block.head + 1 : block.end]).replace("\\\n", " ")
        text = justfile_text.INTERPOLATION.sub(
            functools.partial(interpolated, justfile, frozenset(block.parsed.params)),
            justfile_text.code_of(body),
        ).replace("{{", UNREAD)
        for line in (line for line in text.splitlines() if RUNNER in line):
            given, missed = passed(line)
            kept = kept.joined(given)
            unread += [block.name] if missed and block.name not in unread else []
    return kept, unread


def justfile(root: Path) -> str:
    texts: list[str] = []
    waiting, seen = [root / "justfile"], set()
    while waiting:
        path = waiting.pop(0)
        text = gatelib.read_text(path)
        if text is None or path.resolve() in seen:
            continue
        seen.add(path.resolve())
        texts.append(text)
        waiting += [path.parent / name for name in justfile_text.imports(text)]
    return "\n".join(texts)


class Board(Generic[Task]):
    def __init__(
        self, tasks: Iterable[Task], schedule: Schedule, gate: Callable[[Task], str]
    ) -> None:
        self.schedule = schedule
        self.gate = gate
        self.pending = sorted(tasks, key=lambda task: gate(task) in schedule.alone)
        self.running: list[Task] = []
        self.met: dict[Task, set[str]] = {}

    def take(self) -> Task | None:
        for task in self.pending:
            name = self.gate(task)
            if all(self.schedule.beside(name, self.gate(other)) for other in self.running):
                self.pending.remove(task)
                for other in self.running:
                    self.met[other].add(name)
                self.met[task] = {self.gate(other) for other in self.running}
                self.running.append(task)
                return task
        return None

    def done(self, task: Task) -> tuple[str, ...]:
        self.running.remove(task)
        return tuple(sorted(self.met.pop(task)))
