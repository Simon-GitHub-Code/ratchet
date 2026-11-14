"""The safeguard a project owes as it grows: the model's promises, and the real gate against it."""

import shutil
import tempfile
from pathlib import Path

from hypothesis import settings
from hypothesis import strategies as st
from hypothesis.stateful import RuleBasedStateMachine, rule

from tests.safeguard_model import (
    ACTIONS,
    INITIAL,
    RULES,
    STEPS,
    Debt,
    Landing,
    Row,
    State,
    allowance_after,
    canonical,
    counterexample,
    graph,
    judge,
    outcomes,
    owed,
)
from tests.safeguard_world import Attempt, Run, World

# The owner's seven rules, as the model finds them. A path is the shortest run that breaks the
# rule; ``None`` says it holds on every reachable state and landing.
FOUND: dict[str, tuple[str, ...] | None] = {
    # 1. The change that brings a kind is free, however many instances it brings.
    "arrival is free": None,
    # 2. Read as "a new instance", a move breaks it; read as "the count rises", it holds.
    "owed takes no new instance": (
        "another branch lands: add one instance",
        "another branch lands: move an instance",
    ),
    "owed count does not rise": None,
    # 3. Bound takes instances; leaving owed by dropping the row is green, and the guard holds it.
    "bound takes instances": None,
    "only binding leaves owed": (
        "another branch lands: add one instance",
        "another branch lands: drop the row",
    ),
    "only binding leaves owed, with the guard": None,
    # 4. A declared event is never red.
    "event is never red": None,
    # 5. Removing every instance makes the next arrival free again, with or without the guard.
    "owed never grows unbound": (
        "another branch lands: add one instance",
        "another branch lands: remove every instance",
        "another branch lands: add several instances",
    ),
    "owed never grows unbound, with the guard": (
        "another branch lands: add one instance",
        "another branch lands: remove every instance",
        "another branch lands: add several instances",
    ),
    # 6. The gate on the merge result catches a branch that lands late.
    "late landing adds nothing": None,
    # 7. The gate alone lets the binding be loosened; the guard sends that change to the owner.
    "nothing blinds the gate": ("another branch lands: drop the row",),
    "nothing blinds the gate, with the guard": None,
}


def test_each_rule_holds_or_breaks_by_its_shortest_run() -> None:
    assert {name: counterexample(name) for name in RULES} == FOUND


def test_each_rule_says_in_one_sentence_what_it_promises() -> None:
    for name, promise in RULES.items():
        assert promise.doc.endswith("."), name
        assert promise.doc.count(". ") == 0, name


def test_every_action_is_taken_somewhere_and_every_state_is_reached_from_the_start() -> None:
    found = graph()
    assert {name for _, name, _ in found.edges} == set(ACTIONS)
    assert found.states[0] == INITIAL
    assert all(canonical(state) == state for state in found.states)


def walk(*names: str) -> tuple[State, list[Landing]]:
    """The state the actions lead to, taking the first outcome of each, and what each landed."""
    state, seen = INITIAL, []
    for name in names:
        state, result = next(outcomes(STEPS[name], state))
        if result is not None:
            seen.append(result)
    return state, seen


def test_a_kind_arrives_free_with_several_instances_and_then_owes_its_row() -> None:
    state, [arrival] = walk("another branch lands: add several instances")
    assert arrival.landed
    assert judge(arrival.tip, arrival.head).arrived
    assert owed(state.main) == {Debt.KIND: 2}
    assert state.allowance == 2


def test_another_instance_of_an_owed_kind_is_red_and_lands_once_the_row_is_bound() -> None:
    _, [_, second] = walk(
        "another branch lands: add one instance", "another branch lands: add one instance"
    )
    assert not second.landed
    state, [*_, last] = walk(
        "another branch lands: add one instance",
        "another branch lands: bind the row",
        "another branch lands: add one instance",
    )
    assert last.landed
    assert state.main.row is Row.BOUND
    assert len(state.main.instances) == 2


def test_a_branch_forked_before_the_kind_arrived_is_red_when_it_lands_late() -> None:
    state, [_, late] = walk(
        "fork: add one instance", "another branch lands: add one instance", "land the branch"
    )
    assert late.branch_green
    assert not late.landed
    assert state.branch is not None


def test_a_declared_event_owes_its_row_and_a_red_landing_is_the_kind_s_alone() -> None:
    state, landings = walk(
        "another branch lands: declare the event",
        "another branch lands: add one instance",
        "another branch lands: add one instance",
    )
    declared, arrival, second = landings
    assert declared.landed
    assert arrival.landed
    assert judge(arrival.tip, arrival.merged or arrival.tip).event_owes
    assert not second.landed
    assert owed(state.main) == {Debt.KIND: 1, Debt.EVENT: 0}


class SafeguardMachine(RuleBasedStateMachine):
    """Random actions taken on the model and on a real repository at once."""

    def __init__(self) -> None:
        super().__init__()
        self.directory = Path(tempfile.mkdtemp(prefix="ratchet-safeguard-"))
        self.world = World.create(self.directory / "repo")
        self.state = INITIAL
        self.history = INITIAL
        self.path: list[str] = []
        self.holding = [name for name, path in FOUND.items() if path is None]

    def teardown(self) -> None:
        shutil.rmtree(self.directory, ignore_errors=True)

    def explain(self, *runs: Run | None) -> str:
        printed = "\n".join(run.out for run in runs if run is not None)
        steps = "\n".join(f"  {name}" for name in self.path)
        return f"actions:\n{steps}\nmodel: {canonical(self.state)}\ngate printed:\n{printed}"

    @rule(data=st.data())
    def act(self, data: st.DataObject) -> None:
        enabled = [name for name in STEPS if next(outcomes(STEPS[name], self.state), None)]
        verb = data.draw(st.sampled_from(list(dict.fromkeys(STEPS[name].verb for name in enabled))))
        name = data.draw(st.sampled_from([n for n in enabled if STEPS[n].verb is verb]))
        distinct = {
            canonical(after): (after, result) for after, result in outcomes(STEPS[name], self.state)
        }
        after, result = data.draw(st.sampled_from(list(distinct.values())))
        self.path.append(name)
        seen = self.world.take(STEPS[name], self.state, after=after, result=result)
        if seen is not None:
            assert result is not None
            self.compare(seen, result)
        self.state = after

    def compare(self, seen: Attempt, expected: Landing) -> None:
        said = self.explain(seen.branch_run, seen.merge_run)
        assert seen.landing == expected, f"seen:  {seen.landing}\nmodel: {expected}\n{said}"
        on_branch = judge(expected.fork, expected.head)
        assert (seen.branch_run.arrived, seen.branch_run.event_owes) == (
            on_branch.arrived,
            on_branch.event_owes,
        ), said
        if expected.merged is not None:
            assert seen.merge_run is not None, said
            on_merge = judge(expected.tip, expected.merged)
            assert (seen.merge_run.arrived, seen.merge_run.event_owes) == (
                on_merge.arrived,
                on_merge.event_owes,
            ), said
        for name in self.holding:
            check = RULES[name].step_check
            assert check is None or check(seen.landing), f"{name}\n{said}"
        if not expected.landed or expected.merged is None:
            return
        main = self.world.tree()
        assert self.world.owed() == owed(expected.merged), said
        self.history = State(main, allowance_after(self.history.allowance, main), None)
        for name in self.holding:
            check = RULES[name].state_check
            assert check is None or check(self.history), f"{name}\n{said}"


SafeguardMachine.TestCase.settings = settings(
    max_examples=4, stateful_step_count=15, derandomize=True, deadline=None
)
TestTheRealGateAgainstTheModel = SafeguardMachine.TestCase
