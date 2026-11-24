from __future__ import annotations

import functools
import re
import shlex
from collections.abc import Hashable
from dataclasses import dataclass, field
from itertools import combinations
from typing import TYPE_CHECKING, Generic, TypeVar

import gatelib
import justfile_text
import markdown_text

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable
    from pathlib import Path

SPEED = "## Speed"
APART = "Apart"
ALONE = "Alone"
WIDE = "Wide"
KINDS = (APART, ALONE, WIDE)
FLAGS = {APART: "--serial", ALONE: "--alone", WIDE: "--wide"}
KIND = {flag: kind for kind, flag in FLAGS.items()}
JOBS = "--jobs"
VALUED = frozenset(("--runner", JOBS, "--root", "--report", "--budget"))
RUNNER = "parallel.py"
UNREAD = "\0"
SUBSTITUTED = "\1"
UNKNOWN = "\2"
SHELL = frozenset("();<>|&")
SPLITS = frozenset("*?[{")
PARAMETER = re.compile(r"[A-Za-z_][A-Za-z0-9_]*|[0-9#?$!*-]")
HIDDEN = re.compile(f"([{UNREAD}{SUBSTITUTED}{UNKNOWN}])([0-9a-f]*)\\1")

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


def hidden(text: str, mark: str = UNREAD) -> str:
    return f"{mark}{text.encode().hex()}{mark}"


def shown(text: str) -> str:
    return HIDDEN.sub(lambda found: bytes.fromhex(found[2]).decode(), text)


@dataclass(frozen=True)
class Unread:
    recipe: str
    words: tuple[str, ...]
    gates: tuple[str, ...] | None


@dataclass(frozen=True)
class Assumed:
    recipe: str
    words: tuple[str, ...]


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
        return hidden(f"{{{{ {expression} }}}}")
    return "".join(operand(justfile, word, seen) for word in words[::2])


def operand(justfile: str, word: str, seen: frozenset[str]) -> str:
    literal = justfile_text.STRING.fullmatch(word)
    if literal and "\\" not in word:
        return literal.group(1) if literal.group(1) is not None else literal.group(2)
    defined = justfile_text.variables(justfile).get(word)
    if literal or defined is None or word in seen:
        return hidden(f"{{{{ {word} }}}}")
    return value(justfile, defined[: justfile_text.comment_at(defined)], seen | {word})


def interpolated(justfile: str, params: frozenset[str], found: re.Match[str]) -> str:
    return value(justfile, found[1], params)


def closing(line: str, at: int, pair: str) -> int:
    depth = 0
    for end in range(at, len(line)):
        depth += (line[end] == pair[0]) - (line[end] == pair[1])
        if depth == 0:
            return end + 1
    return len(line)


def expansion(line: str, at: int, *, quoted: bool) -> tuple[int, str]:
    following = line[at + 1 : at + 2]
    if following == "(":
        return closing(line, at + 1, "()"), SUBSTITUTED
    if following == "{":
        end = closing(line, at + 1, "{}")
        one = line[end - 1] == "}" and not re.search(r"\$\(|`|@", line[at:end])
        return end, UNKNOWN if quoted and one else SUBSTITUTED
    name = PARAMETER.match(line, at + 1)
    if name is None:
        return at + 1, SUBSTITUTED
    return name.end(), UNKNOWN if quoted else SUBSTITUTED


def marked(line: str) -> str:
    out: list[str] = []
    at, quote = 0, ""
    while at < len(line):
        char, end, mark = line[at], at + 1, ""
        if quote == "'":
            quote = "" if char == "'" else quote
        elif char == "\\":
            end = at + 2
        elif char == '"' or (char == "'" and not quote):
            quote = "" if quote else char
        elif char == "`":
            found = line.find("`", at + 1)
            end, mark = (found + 1 if found > at else len(line)), SUBSTITUTED
        elif char == "$":
            end, mark = expansion(line, at, quoted=bool(quote))
        elif not quote and char in "<>(" and "(" in line[at : at + 2]:
            end, mark = closing(line, line.index("(", at), "()"), SUBSTITUTED
        elif not quote and char in SPLITS:
            mark = SUBSTITUTED
        out.append(hidden(line[at:end], mark) if mark else line[at:end])
        at = end
    return "".join(out)


def unreadable(word: str, *, valued: bool = False) -> bool:
    return SUBSTITUTED in word or (not valued and (UNREAD in word or UNKNOWN in word))


def flagged(words: list[str]) -> tuple[Schedule, list[str], list[str]]:
    found: dict[str, list[list[str]]] = {kind: [] for kind in KINDS}
    missed: list[str] = []
    assumed: list[str] = []
    kind: str | None = None
    waiting = ""
    for word in words:
        flag, equals, rest = word.partition("=")
        if waiting or (equals and flag in VALUED):
            option, value = (waiting, word) if waiting else (flag, rest)
            if unreadable(value, valued=True):
                missed.append(shown(word))
            elif UNKNOWN in value and option == JOBS:
                assumed.append(shown(f"{option} {value}" if waiting else word))
            kind, waiting = None, ""
        elif unreadable(word):
            missed.append(shown(word))
            kind = None
        elif flag in KIND:
            kind = KIND[flag]
            found[kind].append([rest] if equals else [])
        elif word.startswith("-"):
            kind, waiting = None, word if word in VALUED else ""
        elif kind is not None:
            found[kind][-1].append(word)
    groups = {kind: tuple(tuple(group) for group in found[kind] if group) for kind in KINDS}
    each = {kind: singles(groups[kind], frozenset()) for kind in (ALONE, WIDE)}
    return Schedule({APART: groups[APART], **each}), missed, assumed


def passed(
    line: str,
) -> tuple[Schedule, tuple[str, ...], tuple[str, ...] | None, tuple[str, ...]]:
    lexer = shlex.shlex(marked(line), posix=True, punctuation_chars=True)
    lexer.whitespace_split = True
    try:
        words = list(lexer)
    except ValueError:
        return Schedule(), (shown(line.strip()),), None, ()
    named = (at for at, word in enumerate(words) if word.rsplit("/", 1)[-1] == RUNNER)
    after = words[next(named, len(words)) + 1 :]
    ends = (at for at, word in enumerate(after) if set(word) <= SHELL)
    command = after[: next(ends, len(after))]
    split = command.index("--") if "--" in command else len(command)
    given, missed, assumed = flagged(command[:split])
    gates = command[split + 1 :]
    known = split < len(command) and not any(map(unreadable, gates))
    return given, tuple(missed), tuple(gates) if known else None, tuple(assumed)


def recipes(justfile: str) -> tuple[Schedule, list[Unread], list[Assumed]]:
    lines = justfile.split("\n")
    kept = Schedule()
    unread: list[Unread] = []
    assumed: list[Assumed] = []
    for block in justfile_text.blocks(lines):
        if block.parsed is None:
            continue
        body = "\n".join(lines[block.head + 1 : block.end]).replace("\\\n", " ")
        text = justfile_text.INTERPOLATION.sub(
            functools.partial(interpolated, justfile, frozenset(block.parsed.params)),
            justfile_text.code_of(body),
        ).replace("{{", hidden("{{"))
        for line in (line for line in text.splitlines() if RUNNER in line):
            given, missed, gates, read_as = passed(line)
            kept = kept.joined(given)
            unread += [Unread(block.name, missed, gates)] if missed else []
            assumed += [Assumed(block.name, read_as)] if read_as and not missed else []
    return kept, unread, assumed


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
        self,
        tasks: Iterable[Task],
        schedule: Schedule,
        gate: Callable[[Task], str],
        *,
        alone: Callable[[Task], bool] | None = None,
    ) -> None:
        self.schedule = schedule
        self.gate = gate
        self.alone = alone
        self.pending = sorted(tasks, key=self._is_alone)
        self.running: list[Task] = []
        self.met: dict[Task, set[str]] = {}

    def _is_alone(self, task: Task) -> bool:
        return self.gate(task) in self.schedule.alone or (
            self.alone is not None and self.alone(task)
        )

    def _can_run(self, task: Task) -> bool:
        if self._is_alone(task) or any(self._is_alone(other) for other in self.running):
            return not self.running
        name = self.gate(task)
        return all(self.schedule.beside(name, self.gate(other)) for other in self.running)

    def take(self) -> Task | None:
        for task in self.pending:
            if self._can_run(task):
                name = self.gate(task)
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
