from __future__ import annotations

from collections.abc import Hashable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Generic, TypeVar

import markdown_text

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable

SPEED = "## Speed"
APART = "Apart"
ALONE = "Alone"
WIDE = "Wide"
KINDS = (APART, ALONE, WIDE)

Groups = tuple[tuple[str, ...], ...]
Task = TypeVar("Task", bound=Hashable)


def names(groups: Groups) -> frozenset[str]:
    return frozenset(name for group in groups for name in group)


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

    def share(self, gate: str, *, cores: int, lanes: int) -> int:
        return cores if gate in self.alone else max(1, cores // lanes)

    def among(self, gates: Iterable[str]) -> Schedule:
        present = frozenset(gates)
        kept = {
            kind: (tuple(name for name in group if name in present) for group in groups)
            for kind, groups in self.groups.items()
        }
        return Schedule({kind: tuple(filter(None, groups)) for kind, groups in kept.items()})

    def over(self, given: Schedule) -> Schedule:
        return Schedule({**given.groups, **self.groups})


def stated(binding: str) -> Schedule:
    bullets = markdown_text.bullets(markdown_text.section(binding, SPEED))
    return Schedule(
        {
            kind: tuple(tuple(span.split()) for span in markdown_text.ticked(line))
            for kind, line in bullets.items()
            if kind in KINDS and not line.startswith("<")
        }
    )


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
