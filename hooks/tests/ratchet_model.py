"""A pure model of the threshold ratchet: what the default branch can hold after any history.

The state is one threshold key that only rises and one ceiling key that only falls, each over
three ordered values or absent, the relaxation records the default branch holds, what each key
last held there, and two branch slots, each open branch with the tree it forked from and the tree
it has made. A branch lands the way ``merge.attempt`` lands one: merged with the default branch's
tip, judged by the gate on the merge result, and the default branch moved forward only when the
merge is clean and the gate is green. ``graph()`` is every reachable state and step, ``RULES``
what a reader expects to stay true over any history. Every rule is a predicate over a step:
``(before, action, after)``.

The gate is the one ADR 0047 describes. A record excuses a loosening only in the change that adds
it, and a key the base lacks is judged against the value it last held. A flag and a number are
different values, so a number is left behind through a flag only by two changes, each with a
record of its own.

Bounds keep the graph small and closed: a branch changes one key or deletes the file and adds at
most one record, the default branch takes ``LANDINGS`` changes, and no more branches are open
than can still land.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, replace
from enum import Enum
from typing import TYPE_CHECKING, NamedTuple

from tests import model_tla

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator

LANDINGS = 2


class Key(Enum):
    FLOOR = "gate.floor"
    CEILING = "ceilings.gate.seconds"


class Value(Enum):
    """A key's value as the file spells it; a flag is a value of its own, never a number."""

    ONE = "1"
    TWO = "2"
    THREE = "3"
    ON = "true"


Cell = Value | None
NUMBERS = (Value.ONE, Value.TWO, Value.THREE)
VALUES: dict[Key, tuple[Value, ...]] = {Key.FLOOR: (*NUMBERS, Value.ON), Key.CEILING: NUMBERS}


class Slot(Enum):
    A = 0
    B = 1


@dataclass(frozen=True)
class Step:
    """One ``Relaxes:`` line: leave for ``key`` to go from ``old`` to ``new``, ``None`` removed."""

    key: Key
    old: Value
    new: Cell

    @property
    def line(self) -> str:
        new = "removed" if self.new is None else self.new.value
        return f"Relaxes: {self.key.value} from {self.old.value} to {new}"


@dataclass(frozen=True)
class Record:
    """A file under ``docs/adr/`` holding one ``Relaxes:`` line; ``copy`` tells two alike apart."""

    step: Step
    copy: int = 0


def names(records: frozenset[Record], step: Step) -> bool:
    return any(record.step == step for record in records)


Held = tuple[Cell, Cell]
"""Per key, the value the default branch last held: the value it holds, or held before removal."""


@dataclass(frozen=True)
class Tree:
    """What one commit holds: both keys, the records under ``docs/adr/`` and the file itself."""

    floor: Cell
    ceiling: Cell
    records: frozenset[Record] = frozenset()
    file: bool = True
    """Whether ``quality/thresholds.toml`` is there; without it both keys are absent."""

    def cell(self, key: Key) -> Cell:
        return self.floor if key is Key.FLOOR else self.ceiling

    def setting(self, key: Key, value: Cell) -> Tree:
        return replace(self, floor=value) if key is Key.FLOOR else replace(self, ceiling=value)

    def values(self) -> tuple[Cell, Cell]:
        return self.floor, self.ceiling


@dataclass(frozen=True)
class Branch:
    """An open branch: the tree it forked from, what the keys last held then, and its tree now."""

    fork: Tree
    head: Tree
    held: Held

    @property
    def own_records(self) -> frozenset[Record]:
        return self.head.records - self.fork.records

    @property
    def changed(self) -> bool:
        return self.head.values() != self.fork.values() or not self.head.file


@dataclass(frozen=True)
class State:
    """The default branch, the open branches, and per key the value the default branch last held.

    ``last`` is no file of any commit: it is the default branch's history, as the gate reads it.
    ``left`` is how many more changes the default branch takes, the bound on the graph.
    """

    default: Tree
    branches: tuple[Branch | None, Branch | None]
    last: Held
    left: int = LANDINGS

    def branch(self, slot: Slot) -> Branch | None:
        return self.branches[slot.value]

    def with_branch(self, slot: Slot, branch: Branch | None) -> State:
        branches = list(self.branches)
        branches[slot.value] = branch
        return replace(self, branches=(branches[0], branches[1]))


class Kind(Enum):
    OPEN = "opens from the default tip"
    ABANDON = "is abandoned"
    RAISE = "raises"
    LOWER = "lowers"
    REMOVE = "removes"
    RETYPE = "retypes"
    ADD = "adds"
    DELETE = "deletes quality/thresholds.toml"
    RECORD_OWN = "records its own loosening"
    RECORD_OTHER = "records a loosening it does not make"
    RECORD_STRAY = "records a line that matches no loosening"
    LAND = "tries to land"


EDITS = frozenset({Kind.RAISE, Kind.LOWER, Kind.REMOVE, Kind.RETYPE, Kind.ADD})
RECORDS = frozenset({Kind.RECORD_OWN, Kind.RECORD_OTHER, Kind.RECORD_STRAY})


@dataclass(frozen=True)
class Action:
    kind: Kind
    slot: Slot
    key: Key | None = None
    value: Cell = None
    record: Record | None = None

    @property
    def name(self) -> str:
        who = f"branch {self.slot.name.lower()} {self.kind.value}"
        if self.kind in EDITS and self.key is not None:
            to = "" if self.value is None else f" to {self.value.value}"
            return f"{who} {self.key.value}{to}"
        if self.record is not None:
            return f"{who}: '{self.record.step.line}'"
        return who


Move = tuple[Action, State]
STRAY = Step(Key.FLOOR, Value.ONE, Value.THREE)
NEVER: Held = (None, None)
"""No memory: what a rule passes when it asks only about keys present before and after."""
INITIAL = State(
    default=Tree(floor=Value.TWO, ceiling=Value.THREE),
    branches=(None, None),
    last=(Value.TWO, Value.THREE),
)


def rank(value: Value) -> int:
    return NUMBERS.index(value)


def loosens(key: Key, old: Cell, new: Cell) -> bool:
    """Whether ``old`` to ``new`` loosens ``key``: removed, retyped, lowered or raised."""
    if old is None:
        return False
    if new is None or (old is Value.ON) != (new is Value.ON):
        return True
    if old is Value.ON:
        return False
    return rank(new) > rank(old) if key is Key.CEILING else rank(new) < rank(old)


def loosenings(before: Tree, after: Tree, held: Held) -> list[Step]:
    """Each key ``after`` loosens against ``before``; a key that comes back, against ``held``."""
    found = []
    for index, key in enumerate(Key):
        old, new = before.cell(key), after.cell(key)
        if old is None and new is not None:
            old = held[index]
        if old is not None and loosens(key, old, new):
            found.append(Step(key, old, new))
    return found


def green(base: Tree, head: Tree, held: Held) -> bool:
    """Whether the gate passes ``head`` against ``base``.

    It asks for the file, and for every loosening a record that ``head`` adds. ``held`` is what
    each key last held in the history ``base`` ends.
    """
    added = head.records - base.records
    return head.file and all(names(added, step) for step in loosenings(base, head, held))


def merge(fork: Tree, tip: Tree, head: Tree) -> Tree | None:
    """The three-way merge of ``head`` into ``tip`` from ``fork``, or ``None`` when it conflicts.

    Each key is a line of its own with unchanged lines between, and each record a file named by
    its line, so git merges key by key and the records as a union.
    """
    records = tip.records | head.records
    if not head.file:
        deleted = Tree(floor=None, ceiling=None, records=records, file=False)
        return deleted if tip.values() == fork.values() else None
    merged = replace(tip, records=records)
    for key in Key:
        base, ours, theirs = fork.cell(key), tip.cell(key), head.cell(key)
        if theirs not in (base, ours):
            if ours != base:
                return None
            merged = merged.setting(key, theirs)
    return merged


def edit_kind(old: Cell, new: Cell) -> Kind:
    if new is None:
        return Kind.REMOVE
    if old is None:
        return Kind.ADD
    if (old is Value.ON) != (new is Value.ON):
        return Kind.RETYPE
    return Kind.RAISE if rank(new) > rank(old) else Kind.LOWER


def notch_looser(key: Key, value: Cell) -> Step | None:
    """The loosening one notch past ``value``: the next number the ratchet forbids, or removal."""
    if value is None:
        return None
    if value is Value.ON:
        return Step(key, value, None)
    index = rank(value) + (1 if key is Key.CEILING else -1)
    return Step(key, value, NUMBERS[index] if 0 <= index < len(NUMBERS) else None)


def _open(state: State) -> Iterator[Move]:
    if sum(branch is not None for branch in state.branches) >= state.left:
        return
    for slot in Slot:
        if state.branch(slot) is None:
            branch = Branch(state.default, state.default, state.last)
            yield Action(Kind.OPEN, slot), state.with_branch(slot, branch)


def _abandon(state: State) -> Iterator[Move]:
    for slot in Slot:
        if state.branch(slot) is not None:
            yield Action(Kind.ABANDON, slot), state.with_branch(slot, None)


def _unchanged(state: State) -> Iterator[tuple[Slot, Branch]]:
    for slot in Slot:
        branch = state.branch(slot)
        if branch is not None and not branch.changed and not branch.own_records:
            yield slot, branch


def _edit(state: State) -> Iterator[Move]:
    for slot, branch in _unchanged(state):
        for key in Key:
            current = branch.head.cell(key)
            for value in (None, *VALUES[key]):
                if value != current:
                    head = branch.head.setting(key, value)
                    action = Action(edit_kind(current, value), slot, key, value)
                    yield action, state.with_branch(slot, replace(branch, head=head))


def _delete(state: State) -> Iterator[Move]:
    for slot, branch in _unchanged(state):
        head = Tree(floor=None, ceiling=None, records=branch.head.records, file=False)
        yield Action(Kind.DELETE, slot), state.with_branch(slot, replace(branch, head=head))


def _offers(branch: Branch) -> Iterator[tuple[Kind, Step]]:
    own = loosenings(branch.fork, branch.head, branch.held)
    for step in own:
        yield Kind.RECORD_OWN, step
    edited = [key for key in Key if branch.head.cell(key) != branch.fork.cell(key)]
    for key in edited or list(Key):
        other = notch_looser(key, branch.head.cell(key))
        if other is not None and other not in own:
            yield Kind.RECORD_OTHER, other
    yield Kind.RECORD_STRAY, STRAY


def _record(state: State) -> Iterator[Move]:
    for slot in Slot:
        branch = state.branch(slot)
        if branch is None or branch.own_records:
            continue
        for kind, step in _offers(branch):
            alike = sum(record.step == step for record in branch.head.records)
            record = Record(step, copy=alike)
            head = replace(branch.head, records=branch.head.records | {record})
            yield (
                Action(kind, slot, record=record),
                state.with_branch(slot, replace(branch, head=head)),
            )


def land(state: State, slot: Slot) -> State:
    """The state after ``slot`` tries to land: unchanged unless the merge is clean and green."""
    branch = state.branch(slot)
    assert branch is not None
    merged = merge(branch.fork, state.default, branch.head)
    if merged is None or not green(state.default, merged, state.last):
        return state
    return replace(
        state.with_branch(slot, None),
        default=merged,
        last=remember(state.last, merged),
        left=state.left - 1,
    )


def remember(last: Held, tree: Tree) -> Held:
    """``last`` after the default branch comes to hold ``tree``: each value it holds now."""
    floor, ceiling = (
        last[i] if tree.cell(key) is None else tree.cell(key) for i, key in enumerate(Key)
    )
    return floor, ceiling


def _land(state: State) -> Iterator[Move]:
    for slot in Slot:
        if state.branch(slot) is not None:
            yield Action(Kind.LAND, slot), land(state, slot)


ACTIONS: dict[str, Callable[[State], Iterator[Move]]] = {
    "open a branch": _open,
    "abandon a branch": _abandon,
    "change a key on a branch": _edit,
    "delete the thresholds file on a branch": _delete,
    "add a relaxation record on a branch": _record,
    "land a branch": _land,
}


def moves(state: State) -> list[Move]:
    """Every action open to the actors in ``state``; none once the default branch took its bound."""
    if not state.left:
        return []
    return [move for actions in ACTIONS.values() for move in actions(state)]


Edge = tuple[State, Action, State]


class Graph(NamedTuple):
    """Every state reachable from ``INITIAL`` in breadth-first order, and every step."""

    states: tuple[State, ...]
    edges: tuple[Edge, ...]


def graph() -> Graph:
    """Every state reachable from ``INITIAL`` and every step between them."""
    seen = {INITIAL: None}
    queue = deque([INITIAL])
    edges: list[Edge] = []
    while queue:
        state = queue.popleft()
        for action, after in moves(state):
            edges.append((state, action, after))
            if after not in seen:
                seen[after] = None
                queue.append(after)
    return Graph(tuple(seen), tuple(edges))


def landed(before: State, after: State) -> Branch | None:
    """The branch whose landing is the step from ``before`` to ``after``, if one landed."""
    if after.left != before.left - 1:
        return None
    return next(
        branch
        for branch, now in zip(before.branches, after.branches, strict=True)
        if branch is not None and now is None
    )


def named(before: State, _: Action, after: State) -> bool:
    """A value on the default branch never loosens unless a relaxation record in that tree names exactly that step."""  # noqa: E501
    steps = loosenings(before.default, after.default, before.last)
    return all(names(after.default.records, step) for step in steps)


def brought(before: State, _: Action, after: State) -> bool:
    """Every loosening that lands brings its own relaxation record in the same change."""
    added = after.default.records - before.default.records
    return all(names(added, step) for step in loosenings(before.default, after.default, NEVER))


def not_late(before: State, _: Action, after: State) -> bool:
    """A branch that was green when it forked cannot loosen the default branch by landing late."""
    branch = landed(before, after)
    if branch is None or not green(branch.fork, branch.head, branch.held):
        return True
    own = loosenings(branch.fork, branch.head, branch.held)
    return all(step in own for step in loosenings(before.default, after.default, before.last))


def remembered(before: State, _: Action, after: State) -> bool:
    """A key that was removed comes back looser than the value it last held only with its own relaxation record in the same change, and the thresholds file never goes."""  # noqa: E501
    if not after.default.file:
        return False
    added = after.default.records - before.default.records
    for index, key in enumerate(Key):
        old, new, last = before.default.cell(key), after.default.cell(key), before.last[index]
        returns = old is None and new is not None and last is not None
        if returns and loosens(key, last, new) and not names(added, Step(key, last, new)):
            return False
    return True


RULES: dict[str, Callable[[State, Action, State], bool]] = {
    "1. a loosening on the default branch is named by a record in its tree": named,
    "2. a loosening that lands brings its own record": brought,
    "3. a branch green at its fork does not loosen by landing late": not_late,
    "4. a removed key comes back no looser without a record, and the file stays": remembered,
}


# The rules by the names the TLA+ module gives them.
NAMES = dict(zip(("Named", "Brought", "NotLate", "Remembered"), RULES, strict=True))


def spelled(value: Cell) -> str:
    return "absent" if value is None else value.value


def tla_action(action: Action) -> str:
    """The action of the module a step is: its kind, whichever branch, key or record it takes."""
    return model_tla.identifier(f"branch {action.kind.value}")


def export() -> model_tla.Export:
    """The reachable graph as a TLA+ module: what the default branch holds, and the four rules.

    Every rule is about a step, so each is a ``model_tla.Step``. None breaks, so the module has
    the graph's states and steps and no more.
    """
    return model_tla.export(
        "Ratchet",
        graph(),
        {
            name: model_tla.Step((RULES[rule].__doc__ or "").strip(), RULES[rule])
            for name, rule in NAMES.items()
        },
        fields=(),
        derived={
            "floor": lambda state: spelled(state.default.floor),
            "ceiling": lambda state: spelled(state.default.ceiling),
            "file": lambda state: state.default.file,
            "records": lambda state: frozenset(
                f"{record.step.line} ({record.copy + 1})" for record in state.default.records
            ),
            "held": lambda state: tuple(spelled(value) for value in state.last),
            "left": lambda state: state.left,
        },
        action=tla_action,
        deadlock=False,
    )


class Default(NamedTuple):
    """What the default branch holds, as the charts show it: each key, and what its records name."""

    floor: str
    ceiling: str
    relaxes: frozenset[str]
    """The loosenings its records name, each as ``key from old to new``, and which copy."""


def default(state: State) -> Default:
    relaxes = frozenset(
        f"{r.step.key.value} from {r.step.old.value} to {spelled(r.step.new)}"
        + (f", copy {r.copy + 1}" if r.copy else "")
        for r in state.default.records
    )
    return Default(spelled(state.default.floor), spelled(state.default.ceiling), relaxes)


def summary() -> model_tla.Export:
    """The graph seen through ``default``, small enough for the statechart tool to draw whole."""
    seen = model_tla.summary(graph(), default, tla_action)
    return model_tla.export("RatchetDefault", seen, {}, fields=Default._fields, deadlock=False)
