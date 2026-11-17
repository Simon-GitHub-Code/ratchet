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
IDS = 3
WIDEST = 2


class Row(enum.StrEnum):
    LATER = "later"
    BOUND = "bound"
    ABSENT = "absent"


class Table(enum.StrEnum):
    FINDS = "finds"
    BLIND = "blind"


class Event(enum.StrEnum):
    UNDECLARED = "undeclared"
    DECLARED = "declared"


class Start(enum.StrEnum):
    NONE = "not asked"
    FRESH = "asked before the kind"
    HELD = "asked with the kind held"


class Debt(enum.StrEnum):
    KIND = "kind"
    EVENT = "event"


class Tree(NamedTuple):
    instances: frozenset[int] = frozenset()
    row: Row = Row.LATER
    table: Table = Table.FINDS
    event: Event = Event.UNDECLARED
    event_row: Row = Row.LATER
    review: Row = Row.ABSENT
    reviewed: bool = False
    early: bool = False

    def found(self, table: Table) -> frozenset[int]:
        return self.instances if table is Table.FINDS else frozenset()

    @property
    def binding(self) -> tuple[Row, Table, Event, Row, Row]:
        return (self.row, self.table, self.event, self.event_row, self.review)


class Change(enum.StrEnum):
    ADD_ONE = "add one instance"
    ADD_SEVERAL = "add several instances"
    MOVE = "move an instance"
    REMOVE_SOME = "remove some instances"
    REMOVE_ALL = "remove every instance"
    BIND = "bind the row"
    BIND_AND_ADD = "bind the row and add one instance"
    UNBIND = "put the bound row back to later"
    DROP = "drop the row"
    RESTORE = "put the dropped row back as later"
    RETARGET = "point the Triggers row elsewhere"
    DECLARE = "declare the event"
    BIND_EVENT = "bind the event's row"
    ASK_REVIEW = "ask for the architecture review"
    REVIEW = "land the architecture review"
    ADD_REVIEWED = "add one instance with its review"


EVENT_CHANGES = frozenset({Change.DECLARE, Change.BIND_EVENT})
BINDING_CHANGES = frozenset({Change.UNBIND, Change.DROP, Change.RESTORE, Change.RETARGET})
REVIEW_CHANGES = frozenset({Change.ASK_REVIEW, Change.REVIEW, Change.ADD_REVIEWED})
LANDED_AT_ONCE = EVENT_CHANGES | BINDING_CHANGES | REVIEW_CHANGES
NO_BRANCH_OPEN = EVENT_CHANGES | REVIEW_CHANGES


class Branch(NamedTuple):
    fork: Tree
    change: Change
    head: Tree
    held: bool
    start: Start = Start.NONE


class State(NamedTuple):
    main: Tree
    allowance: int | None
    branch: Branch | None
    held: bool = False
    start: Start = Start.NONE


INITIAL = State(Tree(), None, None)


@dataclass(frozen=True)
class Verdict:
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
    debts: dict[Debt, int] = {}
    if tree.row is Row.LATER and tree.found(tree.table):
        debts[Debt.KIND] = len(tree.instances)
    if tree.event is Event.DECLARED and tree.event_row is Row.LATER:
        debts[Debt.EVENT] = 0
    return debts


def owes_kind(tree: Tree) -> bool:
    return Debt.KIND in owed(tree)


def reviews_owed(state: State) -> int | None:
    main = state.main
    found = main.found(main.table)
    asked = main.review is Row.BOUND and not main.reviewed
    return len(found) if asked and found and state.start is not Start.HELD else None


def review_owed(tree: Tree, start: Start) -> bool:
    return tree.review is Row.BOUND and not tree.reviewed and start is not Start.HELD


def three_way(fork: Field, ours: Field, theirs: Field) -> tuple[Field] | None:
    if theirs in (fork, ours):
        return (ours,)
    if ours == fork:
        return (theirs,)
    return None


@functools.cache
def merge(fork: Tree, tip: Tree, head: Tree) -> Tree | None:
    removed, added = fork.instances - head.instances, head.instances - fork.instances
    fields = [
        three_way(*sides) for sides in zip(fork.binding, tip.binding, head.binding, strict=True)
    ]
    reviewed = three_way(fork.reviewed, tip.reviewed, head.reviewed)
    early = three_way(fork.early, tip.early, head.early)
    if any(field is None for field in fields) or reviewed is None or early is None:
        return None
    row, table, event, event_row, review = (field[0] for field in fields if field is not None)
    instances = (tip.instances - removed) | added
    return Tree(instances, row, table, event, event_row, review, reviewed[0], early[0])


@dataclass(frozen=True)
class Landing:
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
        return self.fork.binding != self.head.binding

    @property
    def count_rose(self) -> bool:
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
    merged = merge(fork, tip, head)
    green = merged is not None and not judge(tip, merged, held[0], start=start[0]).red
    on_branch = not judge(fork, head, held[1], start=start[1]).red
    return Landing(tip, fork, head, merged, green, on_branch, *held, *start)


def allowance_after(allowance: int | None, tree: Tree) -> int | None:
    if tree.row is not Row.LATER:
        return None
    if allowance is None and tree.instances:
        return len(tree.instances)
    return allowance


def trees(state: State) -> list[Tree]:
    branch = state.branch
    return [state.main] + ([branch.fork, branch.head] if branch else [])


def places(state: State) -> dict[int, int]:
    bits: dict[int, int] = {}
    for n, tree in enumerate(trees(state)):
        for instance in tree.instances:
            bits[instance] = bits.get(instance, 0) | 1 << n
    return bits


def settled(state: State) -> bool:
    return state.start is Start.HELD or (state.start is Start.FRESH and state.main.reviewed)


def canonical(state: State) -> State:
    bits = places(state)
    order = sorted(bits, key=lambda n: (bits[n], n))
    names = {old: new for new, old in enumerate(order)}
    calm = settled(state)
    if order == list(range(len(order))) and not calm:
        return state

    def renamed(tree: Tree) -> Tree:
        instances = frozenset(names[n] for n in tree.instances)
        return tree._replace(
            instances=instances,
            reviewed=tree.reviewed and not calm,
            early=tree.early and not calm,
        )

    branch = state.branch
    if branch is not None:
        branch = branch._replace(fork=renamed(branch.fork), head=renamed(branch.head))
        branch = branch._replace(start=Start.HELD) if calm else branch
    start = Start.HELD if calm else state.start
    return state._replace(main=renamed(state.main), branch=branch, start=start)


def fresh(state: State, count: int) -> frozenset[int] | None:
    used = places(state)
    if len(used) + count > IDS:
        return None
    start = max(used, default=-1) + 1
    return frozenset(range(start, start + count))


def choices(state: State) -> list[int]:
    bits = places(state)
    picked: dict[int, int] = {}
    for instance in sorted(state.main.instances):
        picked.setdefault(bits[instance], instance)
    return list(picked.values())


def heads(state: State, change: Change) -> Iterator[Tree]:
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
    if change is Change.REVIEW:
        return record(state, frozenset())
    if change is Change.ADD_REVIEWED:
        return record(state, new)
    if change is Change.BIND_AND_ADD:
        bound = binding_edit(state.main, Change.BIND)
        if bound is None or new is None or len(bound.instances | new) > WIDEST:
            return None
        return bound._replace(instances=bound.instances | new)
    return binding_edit(state.main, change)


def record(state: State, new: frozenset[int] | None) -> Tree | None:
    tip = state.main
    if state.start is not Start.FRESH or new is None or len(tip.instances | new) > WIDEST:
        return None
    if state.held:
        return None if tip.reviewed else tip._replace(instances=tip.instances | new, reviewed=True)
    return None if tip.early else tip._replace(instances=tip.instances | new, early=True)


def binding_edit(tip: Tree, change: Change) -> Tree | None:
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
    FORK = "fork"
    LAND = "land"
    ABANDON = "abandon"
    LAND_AT_ONCE = "land at once"


class Step(NamedTuple):
    verb: Verb
    change: Change | None = None


def forks(state: State) -> bool:
    tip = state.main
    return tip.table is Table.FINDS and (tip.row is not Row.ABSENT or review_owed(tip, state.start))


def landed(state: State, result: Landing) -> State:
    if not result.landed or result.merged is None:
        return state
    held = state.held or bool(result.merged.instances)
    allowance = allowance_after(state.allowance, result.merged)
    start = state.start
    if start is Start.NONE and result.merged.review is Row.BOUND:
        start = Start.HELD if held else Start.FRESH
    return State(result.merged, allowance, state.branch, held, start)


def outcomes(step: Step, state: State) -> Iterator[tuple[State, Landing | None]]:
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
    elif step.verb is Verb.FORK and branch is None and forks(state):
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
    yield from dict.fromkeys(canonical(after) for after, _ in outcomes(step, state))


ACTIONS: dict[str, Callable[[State], Iterator[State]]] = {
    name: functools.partial(successors, step) for name, step in STEPS.items()
}

Edge = tuple[State, str, State]


class Graph(NamedTuple):
    states: tuple[State, ...]
    edges: tuple[Edge, ...]
    parents: dict[State, tuple[State, str] | None]


@functools.cache
def graph() -> Graph:
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
    before, name, after = edge
    return tuple(
        result
        for state, result in outcomes(STEPS[name], before)
        if result is not None and canonical(state) == after
    )


@dataclass(frozen=True)
class Rule:
    about: Literal["state", "step"]
    state_check: Callable[[State], bool] | None = None
    step_check: Callable[[Landing], bool] | None = None

    @property
    def doc(self) -> str:
        check = self.state_check or self.step_check
        return (check.__doc__ or "").strip() if check else ""

    def __call__(self, subject: State | Edge) -> bool:
        if isinstance(subject, State):
            assert self.state_check is not None
            return self.state_check(subject)
        assert self.step_check is not None
        return all(self.step_check(result) for result in landings_of(subject))


def counterexample(name: str) -> tuple[str, ...] | None:
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


def binding_with_an_instance_waits_only_on_the_review(result: Landing) -> bool:
    """A change that binds the row and adds an instance is green exactly when no review is owed."""
    tip, merged = result.tip, result.merged
    binds = result.fork.row is Row.LATER and result.head.row is Row.BOUND
    if merged is None or not binds or not result.count_rose:
        return True
    owing = review_owed(tip, result.tip_start) and (bool(tip.instances) or result.tip_held)
    return result.landed is not owing


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


def route_owes_what_the_gate_refuses(result: Landing) -> bool:
    """The route owes a kind's review exactly when the gate refuses one more of its instances."""
    merged = result.merged
    if not result.landed or merged is None or not merged.found(merged.table):
        return True
    after = landed(State(result.tip, None, None, result.tip_held, result.tip_start), result)
    probe = merged._replace(instances=merged.instances | {-1}, row=Row.BOUND)
    refused = judge(merged, probe, after.held, start=after.start).red
    return (reviews_owed(after) is not None) == refused


def an_early_record_settles_nothing(state: State) -> bool:
    """A decision record that landed before the kind first arrived leaves its review owed."""
    main = state.main
    stray = main.early and not main.reviewed and main.review is Row.BOUND
    arrived = state.start is Start.FRESH and bool(main.found(main.table))
    return not (stray and arrived) or reviews_owed(state) is not None


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
    "binding with an instance waits only on the review": Rule(
        "step", step_check=binding_with_an_instance_waits_only_on_the_review
    ),
    "only binding leaves owed": Rule("step", step_check=only_binding_leaves_owed),
    "only binding leaves owed, with the guard": Rule(
        "step", step_check=only_binding_leaves_owed_guarded
    ),
    "event is never red": Rule("step", step_check=event_is_never_red),
    "review owed takes no new instance": Rule("step", step_check=review_owed_takes_no_new_instance),
    "reviewed kind is judged by its row": Rule(
        "step", step_check=reviewed_kind_is_judged_by_its_row
    ),
    "the route owes what the gate refuses": Rule(
        "step", step_check=route_owes_what_the_gate_refuses
    ),
    "an early record settles nothing": Rule("state", state_check=an_early_record_settles_nothing),
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
    if rule.state_check is not None:
        return model_tla.Invariant(rule.doc, rule.state_check)

    def keeps(before: State, name: str, after: State) -> bool:
        return rule((before, name, after))

    return model_tla.Step(rule.doc, keeps)


class Kind(NamedTuple):
    instances: int
    row: str
    table: str
    event: str
    event_row: str
    review: str
    reviewed: bool
    held: bool
    start: str
    allowance: int
    """As ``State.allowance``, with -1 for none."""


def kind(state: State) -> Kind:
    main = state.main
    allowance = -1 if state.allowance is None else state.allowance
    return Kind(
        len(main.instances),
        main.row,
        main.table,
        main.event,
        main.event_row,
        main.review,
        main.reviewed,
        state.held,
        state.start,
        allowance,
    )


def export() -> model_tla.Export:
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
    seen = model_tla.summary(graph(), kind, model_tla.identifier)
    return model_tla.export("SafeguardKind", seen, {}, fields=Kind._fields)
