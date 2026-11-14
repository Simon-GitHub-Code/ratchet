"""A pure model of the safeguard a project owes as it grows (ADR 0028, ADR 0030).

One kind of code and one life event in a repository with a default branch and one open branch.
The open branch forks from the default branch's tip with one change and may stay open while
other branches land, each forked from the tip and landed at once. Landing follows
``merge.attempt``: the branch is merged into the tip with ``--no-ff``, the triggers gate judges the
merge result against the tip, and the default branch moves to it only when the merge is clean and
the gate is green.

``judge`` is the gate as ``scripts/triggers.py`` documents it, with ADR 0048: a kind arrives free
once, and one that went back to none and returns is owed. With ADR 0052 the kind is also one whose
arrival reopens the architecture: once the binding asks for the review, another instance is red
until a decision record citing the kind lands, unless the kind was already held when the binding
first asked. The event is not one that reopens it, so it owes rows alone. ``RULES`` are promises
about whole histories. The tests check each on every reachable state and step of ``graph()``, and
check ``judge``, ``merge``, ``owed`` and ``reviews_owed`` against the real gate on real
repositories.

Some rules hold only with the merge judge behind the gate. The binding is a guard path
(``docs/process/**``, the template manifest's ``decisions`` group), so a change to it lands only
when the owner merges it (ADR 0021, ADR 0033). A rule named ``..., with the guard`` exempts such a
change; the same rule without the suffix is the gate alone.
"""

from __future__ import annotations

import enum
import functools
from collections import deque
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal, NamedTuple, TypeVar

from tests import model_tla

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator

Field = TypeVar("Field")
# Why: two instances are several, and a third id lets a branch move one of them. The model with
# four ids, three instances and every change free to wait on the open branch has 123,597 states
# and gives every rule the verdict this one gives; this one is small enough for every test run.
IDS = 3
WIDEST = 2


class Row(enum.StrEnum):
    """A Gates row's Tool cell: ``later: <trigger>``, a bound tool, or no row at all."""

    LATER = "later"
    BOUND = "bound"
    ABSENT = "absent"


class Table(enum.StrEnum):
    """Whether the kind's Triggers row finds its instances, or points where none are."""

    FINDS = "finds"
    BLIND = "blind"


class Event(enum.StrEnum):
    """Whether the binding's ``- Life events:`` line declares the event."""

    UNDECLARED = "undeclared"
    DECLARED = "declared"


class Start(enum.StrEnum):
    """Whether the binding has asked for the architecture review, and what it found then.

    The reviews start at the first commit of the default branch whose binding has the review's
    row. A kind held at or before it was there when the architecture was chosen, so it owes none.
    """

    NONE = "not asked"
    FRESH = "asked before the kind"
    HELD = "asked with the kind held"


class Debt(enum.StrEnum):
    """A row that can be owed: the kind's, or the event's."""

    KIND = "kind"
    EVENT = "event"


class Tree(NamedTuple):
    """What one commit holds that the gate reads: the kind's instances, the binding, the review.

    ``review`` is the binding's architecture review row, ``BOUND`` to the plugin's gate or
    ``ABSENT``; ``reviewed`` is whether a decision record citing the kind is held.
    """

    instances: frozenset[int] = frozenset()
    row: Row = Row.LATER
    table: Table = Table.FINDS
    event: Event = Event.UNDECLARED
    event_row: Row = Row.LATER
    review: Row = Row.ABSENT
    reviewed: bool = False

    def found(self, table: Table) -> frozenset[int]:
        """The instances a Triggers row as ``table`` says finds."""
        return self.instances if table is Table.FINDS else frozenset()

    @property
    def binding(self) -> tuple[Row, Table, Event, Row, Row]:
        return (self.row, self.table, self.event, self.event_row, self.review)


class Change(enum.StrEnum):
    """The one change a branch makes."""

    ADD_ONE = "add one instance"
    ADD_SEVERAL = "add several instances"
    MOVE = "move an instance"
    REMOVE_SOME = "remove some instances"
    REMOVE_ALL = "remove every instance"
    BIND = "bind the row"
    UNBIND = "put the bound row back to later"
    DROP = "drop the row"
    RESTORE = "put the dropped row back as later"
    RETARGET = "point the Triggers row elsewhere"
    DECLARE = "declare the event"
    BIND_EVENT = "bind the event's row"
    ASK_REVIEW = "ask for the architecture review"
    REVIEW = "land the architecture review"
    ADD_REVIEWED = "add one instance with its review"


# Why: these changes land at once and never wait on an open branch. The event is never red and
# its lines merge apart from the kind's; the others change the binding or add a decision record,
# which only the owner merges, and a record beside an instance is judged at the tip as on a
# branch. Letting them wait, and letting the event's and the review's changes land while a branch
# is open, takes 8,994 states to 62,300 and changes no rule's verdict.
EVENT_CHANGES = frozenset({Change.DECLARE, Change.BIND_EVENT})
BINDING_CHANGES = frozenset({Change.UNBIND, Change.DROP, Change.RESTORE, Change.RETARGET})
REVIEW_CHANGES = frozenset({Change.ASK_REVIEW, Change.REVIEW, Change.ADD_REVIEWED})
LANDED_AT_ONCE = EVENT_CHANGES | BINDING_CHANGES | REVIEW_CHANGES
NO_BRANCH_OPEN = EVENT_CHANGES | REVIEW_CHANGES


class Branch(NamedTuple):
    """The open branch: the tip it forked from, its change, what it holds, and the fork's past.

    ``held`` is whether any commit of the default branch up to the fork held an instance;
    ``start`` is where the reviews stood at the fork.
    """

    fork: Tree
    change: Change
    head: Tree
    held: bool
    start: Start = Start.NONE


class State(NamedTuple):
    """The default branch, the open branch, and two variables of history.

    ``allowance`` is how many instances the kind had when its row came to be owed, kept while the
    row is ``later:``. No commit holds it; rule 5 is about it. ``held`` is whether any commit of
    the default branch up to its tip held an instance, which the gate reads from the log, and
    ``start`` is where the reviews stood when they started, which it reads from the log too.
    """

    main: Tree
    allowance: int | None
    branch: Branch | None
    held: bool = False
    start: Start = Start.NONE


INITIAL = State(Tree(), None, None)


@dataclass(frozen=True)
class Verdict:
    """The gate's answer on one change: red, and the three notes it may print."""

    red: bool
    arrived: bool
    event_owes: bool
    reopens: bool = False


@functools.cache
def judge(
    base: Tree,
    head: Tree,
    held: bool,  # noqa: FBT001
    *,
    start: Start = Start.NONE,
) -> Verdict:
    """The triggers gate on the change from ``base`` to ``head``.

    The rows, the Triggers table, the events, the review row and its records are read at
    ``base``; a row bound at ``head`` is owed no longer, and a record ``head`` brings counts not.
    The kind is red when its count rose while its row or its review is owed, unless it rose from
    none and no commit of the default branch up to ``base`` held an instance (``held``). The
    review is owed while the base asks for it, holds no record, and the kind came after ``start``.
    """
    before, now = base.found(base.table), head.found(base.table)
    rose = len(now) > len(before)
    owes = base.row is Row.LATER and bool(now) and head.row is not Row.BOUND
    grew = owes and rose
    reviewing = base.review is Row.BOUND and not base.reviewed and rose
    event = (
        base.event is Event.DECLARED
        and base.event_row is Row.LATER
        and head.event_row is not Row.BOUND
    )
    first = not before and not held
    unreviewed = reviewing and not first and start is not Start.HELD
    return Verdict(
        red=(grew and not first) or unreviewed,
        arrived=grew and first,
        event_owes=event,
        reopens=reviewing and first,
    )


def owed(tree: Tree) -> dict[Debt, int]:
    """The rows ``tree`` owes with each one's count, as ``triggers.owed`` reads them."""
    debts: dict[Debt, int] = {}
    if tree.row is Row.LATER and tree.found(tree.table):
        debts[Debt.KIND] = len(tree.instances)
    if tree.event is Event.DECLARED and tree.event_row is Row.LATER:
        debts[Debt.EVENT] = 0
    return debts


def owes_kind(tree: Tree) -> bool:
    return Debt.KIND in owed(tree)


def reviews_owed(state: State) -> int | None:
    """The kind's count when the default branch owes its review, as ``triggers.reviews_owed``."""
    main = state.main
    found = main.found(main.table)
    asked = main.review is Row.BOUND and not main.reviewed
    return len(found) if asked and found and state.start is not Start.HELD else None


def review_owed(tree: Tree, start: Start) -> bool:
    """Whether a change to ``tree`` that raises the count is judged against an owed review."""
    return tree.review is Row.BOUND and not tree.reviewed and start is not Start.HELD


def three_way(fork: Field, ours: Field, theirs: Field) -> tuple[Field] | None:
    """One line's merge: the side that changed it, or ``None`` when both changed it apart."""
    if theirs in (fork, ours):
        return (ours,)
    if ours == fork:
        return (theirs,)
    return None


@functools.cache
def merge(fork: Tree, tip: Tree, head: Tree) -> Tree | None:
    """``git merge`` of ``head`` into ``tip``; ``None`` when it conflicts.

    Each instance is a file of its own and each field of the binding a line apart from the
    others, so only one field changed two ways conflicts.
    """
    removed, added = fork.instances - head.instances, head.instances - fork.instances
    fields = [
        three_way(*sides) for sides in zip(fork.binding, tip.binding, head.binding, strict=True)
    ]
    reviewed = three_way(fork.reviewed, tip.reviewed, head.reviewed)
    if any(field is None for field in fields) or reviewed is None:
        return None
    row, table, event, event_row, review = (field[0] for field in fields if field is not None)
    instances = (tip.instances - removed) | added
    return Tree(instances, row, table, event, event_row, review, reviewed[0])


@dataclass(frozen=True)
class Landing:
    """One attempt to land a branch: what the gate saw, and whether the default branch moved."""

    tip: Tree
    fork: Tree
    head: Tree
    merged: Tree | None
    landed: bool
    branch_green: bool
    tip_held: bool = False
    fork_held: bool = False
    tip_start: Start = Start.NONE
    fork_start: Start = Start.NONE

    @property
    def touches_binding(self) -> bool:
        """Whether the change edits the binding, which only the owner merges."""
        return self.fork.binding != self.head.binding

    @property
    def count_rose(self) -> bool:
        """Whether the merge holds more instances than the tip, as the tip's table counts."""
        table = self.tip.table
        if self.merged is None:
            return False
        return len(self.merged.found(table)) > len(self.tip.found(table))


def attempt(
    tip: Tree,
    fork: Tree,
    head: Tree,
    *,
    held: tuple[bool, bool],
    start: tuple[Start, Start],
) -> Landing:
    """``merge.attempt`` of a branch forked at ``fork`` holding ``head`` into ``tip``.

    ``held`` says whether the default branch up to the tip and up to the fork ever held an
    instance, and ``start`` where the reviews stood at each.
    """
    merged = merge(fork, tip, head)
    green = merged is not None and not judge(tip, merged, held[0], start=start[0]).red
    on_branch = not judge(fork, head, held[1], start=start[1]).red
    return Landing(tip, fork, head, merged, green, on_branch, *held, *start)


def allowance_after(allowance: int | None, tree: Tree) -> int | None:
    """The history variable once the default branch holds ``tree``.

    Why: a kind whose row is bound or dropped is not owed, so what it gains then is no growth of
    an owed kind; the owner putting the row back to ``later:`` starts the count again.
    """
    if tree.row is not Row.LATER:
        return None
    if allowance is None and tree.instances:
        return len(tree.instances)
    return allowance


def trees(state: State) -> list[Tree]:
    branch = state.branch
    return [state.main] + ([branch.fork, branch.head] if branch else [])


def places(state: State) -> dict[int, int]:
    """Which trees of ``state`` hold each instance, as bits in the order of ``trees``."""
    bits: dict[int, int] = {}
    for n, tree in enumerate(trees(state)):
        for instance in tree.instances:
            bits[instance] = bits.get(instance, 0) | 1 << n
    return bits


def settled(state: State) -> bool:
    """Whether the review can never be owed again: its record is held, or it began held."""
    return state.start is Start.HELD or (state.start is Start.FRESH and state.main.reviewed)


def canonical(state: State) -> State:
    """``state`` with its instances numbered by where they are, so an id carries no history.

    Two instances the same trees hold are interchangeable, so numbering them in the order of
    the trees that hold them makes one state of every way to name them. Once the review is
    settled the gate never reads its record or its start again, so every settled state is
    numbered as one that began held and holds no record.
    """
    bits = places(state)
    order = sorted(bits, key=lambda n: (bits[n], n))
    names = {old: new for new, old in enumerate(order)}
    calm = settled(state)
    if order == list(range(len(order))) and not calm:
        return state

    def renamed(tree: Tree) -> Tree:
        instances = frozenset(names[n] for n in tree.instances)
        return tree._replace(instances=instances, reviewed=tree.reviewed and not calm)

    branch = state.branch
    if branch is not None:
        branch = branch._replace(fork=renamed(branch.fork), head=renamed(branch.head))
        branch = branch._replace(start=Start.HELD) if calm else branch
    start = Start.HELD if calm else state.start
    return state._replace(main=renamed(state.main), branch=branch, start=start)


def fresh(state: State, count: int) -> frozenset[int] | None:
    """``count`` ids above every id ``state`` holds, or ``None`` when ``IDS`` would be passed."""
    used = places(state)
    if len(used) + count > IDS:
        return None
    start = max(used, default=-1) + 1
    return frozenset(range(start, start + count))


def choices(state: State) -> list[int]:
    """One instance of the tip from each set of instances the same trees hold."""
    bits = places(state)
    picked: dict[int, int] = {}
    for instance in sorted(state.main.instances):
        picked.setdefault(bits[instance], instance)
    return list(picked.values())


def heads(state: State, change: Change) -> Iterator[Tree]:
    """Each tree ``change`` can make of the default branch's tip; none when it cannot be made."""
    tip = state.main
    held_now = tip.instances
    new = fresh(state, 2 if change is Change.ADD_SEVERAL else 1)
    if change in {Change.ADD_ONE, Change.ADD_SEVERAL}:
        if new is not None and len(held_now | new) <= WIDEST:
            yield tip._replace(instances=held_now | new)
    elif change is Change.MOVE:
        for gone in choices(state) if new is not None else []:
            yield tip._replace(instances=(held_now - {gone}) | new)
    elif change is Change.REMOVE_SOME:
        for gone in choices(state) if len(held_now) > 1 else []:
            yield tip._replace(instances=held_now - {gone})
    elif change is Change.REMOVE_ALL:
        if held_now:
            yield tip._replace(instances=frozenset())
    else:
        edited = edit(state, change, new)
        if edited is not None:
            yield edited


def edit(state: State, change: Change, new: frozenset[int] | None) -> Tree | None:
    """The tip with ``change`` made to its binding or its records, or ``None`` when it cannot be."""
    if change is Change.REVIEW:
        return record(state, frozenset())
    if change is Change.ADD_REVIEWED:
        return record(state, new)
    return binding_edit(state.main, change)


def record(state: State, new: frozenset[int] | None) -> Tree | None:
    """The tip with the review's record landed beside ``new`` instances; ``None`` when it cannot.

    Why: a record matters only while the review is owed; one landed after it settled is one the
    gate never reads.
    """
    tip = state.main
    if state.start is not Start.FRESH or tip.reviewed or new is None:
        return None
    if len(tip.instances | new) > WIDEST:
        return None
    return tip._replace(instances=tip.instances | new, reviewed=True)


def binding_edit(tip: Tree, change: Change) -> Tree | None:
    """The tip with ``change`` made to its binding, or ``None`` when it cannot be made there."""
    edits = {
        Change.BIND: (tip.row is Row.LATER, {"row": Row.BOUND}),
        Change.UNBIND: (tip.row is Row.BOUND, {"row": Row.LATER}),
        Change.DROP: (tip.row is not Row.ABSENT, {"row": Row.ABSENT}),
        Change.RESTORE: (tip.row is Row.ABSENT, {"row": Row.LATER}),
        Change.RETARGET: (tip.table is Table.FINDS, {"table": Table.BLIND}),
        Change.DECLARE: (tip.event is Event.UNDECLARED, {"event": Event.DECLARED}),
        Change.BIND_EVENT: (
            tip.event is Event.DECLARED and tip.event_row is Row.LATER,
            {"event_row": Row.BOUND},
        ),
        Change.ASK_REVIEW: (tip.review is Row.ABSENT, {"review": Row.BOUND}),
    }
    possible, fields = edits[change]
    return tip._replace(**fields) if possible else None


class Verb(enum.StrEnum):
    """What an action does with a branch."""

    FORK = "fork"
    LAND = "land"
    ABANDON = "abandon"
    LAND_AT_ONCE = "land at once"


class Step(NamedTuple):
    """A named action: its verb, and the change a fork or a branch landed at once makes."""

    verb: Verb
    change: Change | None = None


def forks(tip: Tree) -> bool:
    """Whether a branch forked from ``tip`` waits open, or lands at once.

    Why: a branch that waits matters only where the gate's verdict can change while it waits.
    Once the tip's row is gone or its Triggers row finds nothing, the kind is never red again,
    and a branch then lands as it would at once.
    """
    return tip.row is not Row.ABSENT and tip.table is Table.FINDS


def landed(state: State, result: Landing) -> State:
    """``state`` after ``result``: the default branch at the merge when it landed."""
    if not result.landed or result.merged is None:
        return state
    held = state.held or bool(result.merged.instances)
    allowance = allowance_after(state.allowance, result.merged)
    start = state.start
    if start is Start.NONE and result.merged.review is Row.BOUND:
        start = Start.HELD if held else Start.FRESH
    return State(result.merged, allowance, state.branch, held, start)


def outcomes(step: Step, state: State) -> Iterator[tuple[State, Landing | None]]:
    """Each state ``step`` can lead to from ``state``, with the landing it attempts, if any.

    Ids are kept as given and a new instance takes an id above them all, so the world can take
    the same step on a repository whose ids name files.
    """
    branch = state.branch
    if step.verb is Verb.LAND and branch is not None:
        result = attempt(
            state.main,
            branch.fork,
            branch.head,
            held=(state.held, branch.held),
            start=(state.start, branch.start),
        )
        after = landed(state, result)
        yield (after._replace(branch=None) if result.landed else after), result
    elif step.verb is Verb.ABANDON and branch is not None:
        yield state._replace(branch=None), None
    elif step.verb is Verb.FORK and branch is None and forks(state.main):
        assert step.change is not None
        for head in heads(state, step.change):
            branch = Branch(state.main, step.change, head, state.held, state.start)
            yield state._replace(branch=branch), None
    elif step.verb is Verb.LAND_AT_ONCE and (branch is None or step.change not in NO_BRANCH_OPEN):
        assert step.change is not None
        for head in heads(state, step.change):
            result = attempt(
                state.main,
                state.main,
                head,
                held=(state.held, state.held),
                start=(state.start, state.start),
            )
            yield landed(state, result), result


STEPS: dict[str, Step] = {
    **{f"fork: {c.value}": Step(Verb.FORK, c) for c in Change if c not in LANDED_AT_ONCE},
    "land the branch": Step(Verb.LAND),
    "abandon the branch": Step(Verb.ABANDON),
    **{
        f"another branch lands: {change.value}": Step(Verb.LAND_AT_ONCE, change)
        for change in Change
    },
}


def successors(step: Step, state: State) -> Iterator[State]:
    """The states ``step`` leads to from ``state``, each numbered by ``canonical``."""
    yield from dict.fromkeys(canonical(after) for after, _ in outcomes(step, state))


ACTIONS: dict[str, Callable[[State], Iterator[State]]] = {
    name: functools.partial(successors, step) for name, step in STEPS.items()
}

Edge = tuple[State, str, State]


class Graph(NamedTuple):
    """Every state reachable from ``INITIAL`` in breadth-first order, and every step."""

    states: tuple[State, ...]
    edges: tuple[Edge, ...]
    parents: dict[State, tuple[State, str] | None]


@functools.cache
def graph() -> Graph:
    """The reachable states and the edges ``(state, action name, state)`` between them."""
    parents: dict[State, tuple[State, str] | None] = {INITIAL: None}
    queue = deque([INITIAL])
    edges: list[Edge] = []
    while queue:
        state = queue.popleft()
        for name, action in ACTIONS.items():
            for after in action(state):
                edges.append((state, name, after))
                if after not in parents:
                    parents[after] = (state, name)
                    queue.append(after)
    return Graph(tuple(parents), tuple(edges), parents)


def path_to(state: State) -> tuple[str, ...]:
    """The shortest run of action names from ``INITIAL`` to ``state``."""
    names: list[str] = []
    parents = graph().parents
    step = parents[state]
    while step is not None:
        state, name = step
        names.append(name)
        step = parents[state]
    return tuple(reversed(names))


@functools.cache
def landings_of(edge: Edge) -> tuple[Landing, ...]:
    """The landing attempts that take ``edge``; none for a fork or an abandon."""
    before, name, after = edge
    return tuple(
        result
        for state, result in outcomes(STEPS[name], before)
        if result is not None and canonical(state) == after
    )


@dataclass(frozen=True)
class Rule:
    """A promise about histories, held by every reachable state or by every landing attempt."""

    about: Literal["state", "step"]
    state_check: Callable[[State], bool] | None = None
    step_check: Callable[[Landing], bool] | None = None

    @property
    def doc(self) -> str:
        """The promise in the reader's words."""
        check = self.state_check or self.step_check
        return (check.__doc__ or "").strip() if check else ""

    def __call__(self, subject: State | Edge) -> bool:
        """Whether the state, or every landing attempt the edge makes, keeps the promise."""
        if isinstance(subject, State):
            assert self.state_check is not None
            return self.state_check(subject)
        assert self.step_check is not None
        return all(self.step_check(result) for result in landings_of(subject))


def counterexample(name: str) -> tuple[str, ...] | None:
    """The shortest run of actions that breaks the rule ``name``; ``None`` when it holds."""
    rule = RULES[name]
    found = graph()
    if rule.about == "state":
        return next((path_to(state) for state in found.states if not rule(state)), None)
    broken = (edge for edge in found.edges if not rule(edge))
    first = min(broken, key=lambda edge: len(path_to(edge[0])), default=None)
    return None if first is None else (*path_to(first[0]), first[1])


def arrival_is_free(result: Landing) -> bool:
    """The change that first brings a kind is free, however many instances it brings."""
    table = result.tip.table
    if result.merged is None or result.tip.found(table) or not result.merged.found(table):
        return True
    return result.landed or result.tip_held


def return_is_not_arrival(result: Landing) -> bool:
    """A kind that went back to none and returns while its row is owed is red."""
    tip, merged = result.tip, result.merged
    if merged is None or not result.tip_held or tip.found(tip.table):
        return True
    owes = tip.row is Row.LATER and merged.row is not Row.BOUND
    return not (owes and merged.found(tip.table) and result.landed)


def owed_takes_no_new_instance(result: Landing) -> bool:
    """While a kind's safeguard is owed, a change that adds another instance is red."""
    table, merged = result.tip.table, result.merged
    if merged is None or not owes_kind(result.tip) or merged.row is Row.BOUND:
        return True
    return not (result.landed and merged.found(table) - result.tip.found(table))


def owed_count_does_not_rise(result: Landing) -> bool:
    """While a kind's safeguard is owed, a change that raises its count is red."""
    merged = result.merged
    if merged is None or not owes_kind(result.tip) or merged.row is Row.BOUND:
        return True
    return not (result.landed and result.count_rose)


def bound_takes_instances(result: Landing) -> bool:
    """Once the row is bound and no review is owed, a change that only adds instances is green."""
    tip, merged = result.tip, result.merged
    if tip.row is not Row.BOUND or merged is None or merged.binding != tip.binding:
        return True
    if review_owed(tip, result.tip_start) and (tip.instances or result.tip_held):
        return True
    return result.landed or not merged.instances > tip.instances


def only_binding_leaves_owed(result: Landing) -> bool:
    """Binding the row is the only green way out of owed for a kind that still has instances."""
    merged = result.merged
    if not result.landed or merged is None or not owes_kind(result.tip):
        return True
    return owes_kind(merged) or not merged.instances or merged.row is Row.BOUND


def only_binding_leaves_owed_guarded(result: Landing) -> bool:
    """No change that lands without the owner takes a kind with instances out of owed."""
    return result.touches_binding or only_binding_leaves_owed(result)


def event_is_never_red(result: Landing) -> bool:
    """A declared event is never red: only the rise of a kind whose row or review is owed is."""
    if result.merged is None or result.landed:
        return True
    owes = result.tip.row is Row.LATER or review_owed(result.tip, result.tip_start)
    return owes and result.count_rose


def review_owed_takes_no_new_instance(result: Landing) -> bool:
    """While the architecture review is owed, a change that raises the kind's count is red."""
    tip, merged = result.tip, result.merged
    if not result.landed or merged is None or not review_owed(tip, result.tip_start):
        return True
    arriving = not tip.found(tip.table) and not result.tip_held
    return arriving or not result.count_rose


def reviewed_kind_is_judged_by_its_row(result: Landing) -> bool:
    """Once the review is held, or the kind was there when it was first asked, rows alone judge."""
    tip = result.tip
    if result.merged is None or tip.review is not Row.BOUND:
        return True
    if not tip.reviewed and result.tip_start is not Start.HELD:
        return True
    rows = judge(tip._replace(review=Row.ABSENT), result.merged, result.tip_held)
    return result.landed is not rows.red


def a_review_is_owed_only_after_its_arrival(state: State) -> bool:
    """No kind owes a review before the binding asks for it, nor one held when it first asked."""
    owing = reviews_owed(state) is not None
    return not owing or (state.main.review is Row.BOUND and state.start is Start.FRESH)


def owed_never_grows_unbound(state: State) -> bool:
    """No run of green changes grows an owed kind past what its arrival brought, unbound."""
    main = state.main
    return (
        main.row is Row.BOUND or state.allowance is None or len(main.instances) <= state.allowance
    )


def owed_never_grows_unbound_guarded(state: State) -> bool:
    """No run of changes landed without the owner grows an owed kind past its arrival, unbound."""
    main = state.main
    seen = main.row is Row.LATER and main.table is Table.FINDS
    return not seen or owed_never_grows_unbound(state)


def late_landing_adds_nothing(result: Landing) -> bool:
    """A branch green when it forked cannot add to a kind that became owed after the fork."""
    merged = result.merged
    if not result.branch_green or result.fork == result.tip or merged is None:
        return True
    if not owes_kind(result.tip) or merged.row is Row.BOUND:
        return True
    return not (result.landed and result.count_rose)


def nothing_blinds_the_gate(result: Landing) -> bool:
    """No green change makes the gate stop seeing a kind or puts its bound row back to later."""
    tip, merged = result.tip, result.merged
    if not result.landed or merged is None:
        return True
    blinded = tip.table is Table.FINDS and merged.table is Table.BLIND
    dropped = tip.row is not Row.ABSENT and merged.row is Row.ABSENT
    unbound = tip.row is Row.BOUND and merged.row is Row.LATER
    return not (blinded or dropped or unbound)


def nothing_blinds_the_gate_guarded(result: Landing) -> bool:
    """No change that lands without the owner makes the gate stop seeing a kind."""
    return result.touches_binding or nothing_blinds_the_gate(result)


RULES: dict[str, Rule] = {
    "arrival is free": Rule("step", step_check=arrival_is_free),
    "return is not arrival": Rule("step", step_check=return_is_not_arrival),
    "owed takes no new instance": Rule("step", step_check=owed_takes_no_new_instance),
    "owed count does not rise": Rule("step", step_check=owed_count_does_not_rise),
    "bound takes instances": Rule("step", step_check=bound_takes_instances),
    "only binding leaves owed": Rule("step", step_check=only_binding_leaves_owed),
    "only binding leaves owed, with the guard": Rule(
        "step", step_check=only_binding_leaves_owed_guarded
    ),
    "event is never red": Rule("step", step_check=event_is_never_red),
    "review owed takes no new instance": Rule("step", step_check=review_owed_takes_no_new_instance),
    "reviewed kind is judged by its row": Rule(
        "step", step_check=reviewed_kind_is_judged_by_its_row
    ),
    "a review is owed only after its arrival": Rule(
        "state", state_check=a_review_is_owed_only_after_its_arrival
    ),
    "owed never grows unbound": Rule("state", state_check=owed_never_grows_unbound),
    "owed never grows unbound, with the guard": Rule(
        "state", state_check=owed_never_grows_unbound_guarded
    ),
    "late landing adds nothing": Rule("step", step_check=late_landing_adds_nothing),
    "nothing blinds the gate": Rule("step", step_check=nothing_blinds_the_gate),
    "nothing blinds the gate, with the guard": Rule(
        "step", step_check=nothing_blinds_the_gate_guarded
    ),
}


def exported_rule(rule: Rule) -> model_tla.Rule:
    """A rule as the module checks it: an invariant on a state, or a rule on every step."""
    if rule.state_check is not None:
        return model_tla.Invariant(rule.doc, rule.state_check)

    def keeps(before: State, name: str, after: State) -> bool:
        return rule((before, name, after))

    return model_tla.Step(rule.doc, keeps)


class Kind(NamedTuple):
    """What the default branch holds of the kind and the event, and the two histories."""

    instances: int
    row: str
    table: str
    event: str
    event_row: str
    held: bool
    allowance: int
    """As ``State.allowance``, with -1 for none."""


def kind(state: State) -> Kind:
    """What the charts read of a state."""
    main = state.main
    allowance = -1 if state.allowance is None else state.allowance
    return Kind(
        len(main.instances), main.row, main.table, main.event, main.event_row, state.held, allowance
    )


def export() -> model_tla.Export:
    """The reachable graph as a TLA+ module, with the thirteen rules.

    Eleven rules are about a landing, so each is a ``model_tla.Step``, and the module holds a
    state once for each set of them the step into it broke.
    """
    return model_tla.export(
        "Safeguard",
        graph(),
        {model_tla.identifier(name): exported_rule(rule) for name, rule in RULES.items()},
        fields=(),
        derived={
            **{
                name: (lambda state, name=name: getattr(kind(state), name)) for name in Kind._fields
            },
            "branch": lambda state: "none" if state.branch is None else state.branch.change,
        },
        action=model_tla.identifier,
    )


def summary() -> model_tla.Export:
    """The graph seen through ``kind``, small enough for the statechart tool to draw whole."""
    seen = model_tla.summary(graph(), kind, model_tla.identifier)
    return model_tla.export("SafeguardKind", seen, {}, fields=Kind._fields)
