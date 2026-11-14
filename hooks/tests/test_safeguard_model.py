"""The safeguard a project owes as it grows: the model's promises, and the real gate against it."""

import shutil
import tempfile
from pathlib import Path

import pytest
from hypothesis import settings
from hypothesis import strategies as st
from hypothesis.stateful import RuleBasedStateMachine, rule

from tests import model_tla, safeguard_model
from tests.safeguard_model import (
    ACTIONS,
    INITIAL,
    RULES,
    STEPS,
    Debt,
    Landing,
    Row,
    Start,
    State,
    allowance_after,
    canonical,
    counterexample,
    export,
    graph,
    judge,
    outcomes,
    owed,
    reviews_owed,
    summary,
)
from tests.safeguard_world import Attempt, Run, World
from tests.support import PLUGIN_ROOT

PAGE = PLUGIN_ROOT / "docs/model/safeguard.md"

# The owner's eight rules, as the model finds them. A path is the shortest run that breaks the
# rule; ``None`` says it holds on every reachable state and landing.
FOUND: dict[str, tuple[str, ...] | None] = {
    # 1. The change that first brings a kind is free, however many instances it brings; a kind
    # that went back to none and returns is not arriving (ADR 0048).
    "arrival is free": None,
    "return is not arrival": None,
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
    # 8. An arrival that reopens the architecture owes a review once the binding asks for one
    # (ADR 0052): another instance is red until its record lands, a record beside the instance
    # counts not, a record that landed before the kind arrived counts not, and a kind the binding
    # found when it first asked owes none. The route owes it exactly when the gate refuses.
    "review owed takes no new instance": None,
    "reviewed kind is judged by its row": None,
    "the route owes what the gate refuses": None,
    "an early record settles nothing": None,
    # 5. Removing every instance used to make the next arrival free again; ADR 0048 closed it.
    # The gate alone still lets a kind grow once its Triggers row points elsewhere.
    "owed never grows unbound": (
        "another branch lands: add one instance",
        "another branch lands: point the Triggers row elsewhere",
        "another branch lands: add one instance",
    ),
    "owed never grows unbound, with the guard": None,
    # 6. The gate on the merge result catches a branch that lands late.
    "late landing adds nothing": None,
    # 7. The gate alone lets the binding be loosened; the guard sends that change to the owner.
    "nothing blinds the gate": ("another branch lands: drop the row",),
    "nothing blinds the gate, with the guard": None,
}


def test_each_rule_holds_or_breaks_by_its_shortest_run() -> None:
    assert {name: counterexample(name) for name in RULES} == FOUND


def test_the_page_was_checked_and_drawn_from_the_current_export() -> None:
    assert not (why := model_tla.stale(PAGE, export(), summary())), why


def route_owes(state: State, *, counts_the_record: bool, counts_early: bool = False) -> int | None:
    """``reviews_owed`` with a part of it left out, as a mutation of the model."""
    main = state.main
    found = main.found(main.table)
    settled = (counts_the_record and main.reviewed) or (counts_early and main.early)
    asked = main.review is Row.BOUND and not settled
    return len(found) if asked and found and state.start is not Start.HELD else None


def test_the_rule_the_route_and_the_gate_share_catches_a_route_that_forgets_the_record(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Why: this mutation left the rule it replaced green. That rule asked only whether a review
    # was owed after the binding asked and the kind came after, which a route that never reads
    # the record still answers. Here the route owes a review the gate has stopped asking for.
    def forgets_the_record(state: State) -> int | None:
        return route_owes(state, counts_the_record=False)

    def old_rule(state: State) -> bool:
        owing = forgets_the_record(state) is not None
        return not owing or (state.main.review is Row.BOUND and state.start is Start.FRESH)

    monkeypatch.setattr(safeguard_model, "reviews_owed", forgets_the_record)
    assert all(old_rule(state) for state in graph().states)
    assert counterexample("the route owes what the gate refuses") is not None


def test_the_early_record_rule_catches_a_route_that_counts_a_record_older_than_the_arrival(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def counts_early(state: State) -> int | None:
        return route_owes(state, counts_the_record=True, counts_early=True)

    monkeypatch.setattr(safeguard_model, "reviews_owed", counts_early)
    # The record that came in the change that first brings the kind is older than the arrival too.
    assert counterexample("an early record settles nothing") == (
        "another branch lands: ask for the architecture review",
        "another branch lands: add one instance with its review",
    )


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
    assert judge(arrival.tip, arrival.head, arrival.tip_held).arrived
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
    assert judge(arrival.tip, arrival.merged or arrival.tip, arrival.tip_held).event_owes
    assert not second.landed
    assert owed(state.main) == {Debt.KIND: 1, Debt.EVENT: 0}


def test_a_kind_that_arrives_once_the_review_is_asked_owes_it_beside_a_bound_row() -> None:
    state, landings = walk(
        "another branch lands: ask for the architecture review",
        "another branch lands: bind the row",
        "another branch lands: add one instance",
        "another branch lands: add one instance",
        "another branch lands: add one instance with its review",
        "another branch lands: land the architecture review",
        "another branch lands: add one instance",
    )
    _, _, arrival, second, beside, review, after = landings
    assert judge(arrival.tip, arrival.head, arrival.tip_held, start=arrival.tip_start).reopens
    assert arrival.landed
    assert not second.landed
    assert not beside.landed
    assert review.landed
    assert after.landed
    assert state.start is Start.FRESH
    assert reviews_owed(state) is None


def test_a_kind_held_when_the_review_is_first_asked_owes_none() -> None:
    state, landings = walk(
        "another branch lands: add one instance",
        "another branch lands: bind the row",
        "another branch lands: ask for the architecture review",
        "another branch lands: add one instance",
    )
    assert all(landing.landed for landing in landings)
    assert state.start is Start.HELD
    assert reviews_owed(state) is None


def replay(directory: Path, *names: str) -> tuple[World, State, list[Attempt]]:
    """The actions taken on a real repository, each landing checked against the model's."""
    world = World.create(directory / "repo")
    state, seen = INITIAL, []
    for name in names:
        after, result = next(outcomes(STEPS[name], state))
        attempt = world.take(STEPS[name], state, after=after, result=result)
        assert attempt is not None, name
        printed = attempt.merge_run.out if attempt.merge_run else attempt.branch_run.out
        assert attempt.landing == result, f"{name}\n{printed}"
        seen.append(attempt)
        state = after
    return world, state, seen


@pytest.mark.parametrize("name", [name for name, path in FOUND.items() if path is not None])
def test_each_run_that_breaks_a_rule_lands_the_same_way_on_a_real_repository(
    name: str, tmp_path: Path
) -> None:
    # Why: the random run below may never take a pinned run, and a break the model alone shows
    # could be the model's mistake. Here the real gate lands every step of it as the model says.
    world, state, seen = replay(tmp_path, *(FOUND[name] or ()))
    assert all(attempt.landing.landed for attempt in seen)
    assert world.owed() == owed(state.main)


def test_a_kind_that_went_back_to_none_and_returns_is_red_until_its_row_is_bound(
    tmp_path: Path,
) -> None:
    # Why: the run that broke rule 5 before ADR 0048, and the way out the gate names.
    world, state, seen = replay(
        tmp_path,
        "another branch lands: add one instance",
        "another branch lands: remove every instance",
        "another branch lands: add several instances",
        "another branch lands: bind the row",
        "another branch lands: add several instances",
    )
    _, _, back, bound, again = seen
    assert not back.landing.landed
    assert back.merge_run is not None
    assert "returning, not arriving" in back.merge_run.out
    assert bound.landing.landed
    assert again.landing.landed
    assert len(state.main.instances) == 2
    assert world.owed() == {}


def test_the_real_gate_holds_another_instance_until_the_review_lands(tmp_path: Path) -> None:
    # Why: the review's rules hold on the model; here the real gate lands each step as it says,
    # the red one naming the review and the free one saying the kind reopens the architecture.
    world, state, seen = replay(
        tmp_path,
        "another branch lands: ask for the architecture review",
        "another branch lands: bind the row",
        "another branch lands: add one instance",
        "another branch lands: add one instance",
        "another branch lands: add one instance with its review",
        "another branch lands: land the architecture review",
        "another branch lands: add one instance",
    )
    _, _, arrival, second, beside, _, after = seen
    assert arrival.branch_run.reopens
    assert world.reviews() is None
    for red in (second, beside):
        assert red.merge_run is not None
        assert "architecture review owed" in red.merge_run.out
    assert after.landing.landed
    assert len(state.main.instances) == 2


def test_the_real_gate_owes_no_review_for_a_kind_held_when_it_was_first_asked(
    tmp_path: Path,
) -> None:
    world, _, seen = replay(
        tmp_path,
        "another branch lands: add one instance",
        "another branch lands: bind the row",
        "another branch lands: ask for the architecture review",
        "another branch lands: add one instance",
    )
    assert all(attempt.landing.landed for attempt in seen)
    assert world.reviews() is None


def test_a_record_that_landed_before_the_kind_arrived_settles_nothing(tmp_path: Path) -> None:
    # Why: a review settles only the arrivals it could have seen (ADR 0052). The record here
    # lands once the review is asked and before any instance exists, so it saw no kind; the
    # arrival is still noted as reopening the architecture, another instance is red, and only a
    # record landed after the arrival ends it.
    world, state, seen = replay(
        tmp_path,
        "another branch lands: ask for the architecture review",
        "another branch lands: bind the row",
        "another branch lands: land the architecture review",
        "another branch lands: add one instance",
        "another branch lands: add one instance",
        "another branch lands: land the architecture review",
        "another branch lands: add one instance",
    )
    _, _, early, arrival, second, late, after = seen
    assert early.landing.landed
    assert arrival.branch_run.reopens
    assert world.reviews() is None, "settled by the record that landed after the arrival"
    assert not second.landing.landed
    assert second.merge_run is not None
    assert "architecture review owed" in second.merge_run.out
    assert late.landing.landed
    assert after.landing.landed
    assert len(state.main.instances) == 2


def test_a_record_in_the_change_that_first_brings_the_kind_settles_nothing(tmp_path: Path) -> None:
    _, state, seen = replay(
        tmp_path,
        "another branch lands: ask for the architecture review",
        "another branch lands: add one instance with its review",
        "another branch lands: add one instance",
    )
    _, arrival, second = seen
    assert arrival.branch_run.reopens
    assert not second.landing.landed
    assert reviews_owed(state) == 1


def test_the_route_owes_a_review_a_record_before_the_arrival_did_not_settle(
    tmp_path: Path,
) -> None:
    world, state, _ = replay(
        tmp_path,
        "another branch lands: ask for the architecture review",
        "another branch lands: land the architecture review",
        "another branch lands: add one instance",
    )
    assert state.main.early
    assert not state.main.reviewed
    assert reviews_owed(state) == 1
    assert world.reviews() == 1


def test_a_row_dropped_and_put_back_does_not_make_the_return_an_arrival(tmp_path: Path) -> None:
    _, _, seen = replay(
        tmp_path,
        "another branch lands: add one instance",
        "another branch lands: remove every instance",
        "another branch lands: drop the row",
        "another branch lands: put the dropped row back as later",
        "another branch lands: add one instance",
    )
    assert [attempt.landing.landed for attempt in seen] == [True, True, True, True, False]


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
        on_branch = judge(
            expected.fork, expected.head, expected.fork_held, start=expected.fork_start
        )
        assert notes(seen.branch_run) == (
            on_branch.arrived,
            on_branch.event_owes,
            on_branch.reopens,
        ), said
        if expected.merged is not None:
            assert seen.merge_run is not None, said
            on_merge = judge(
                expected.tip, expected.merged, expected.tip_held, start=expected.tip_start
            )
            assert notes(seen.merge_run) == (
                on_merge.arrived,
                on_merge.event_owes,
                on_merge.reopens,
            ), said
        for name in self.holding:
            check = RULES[name].step_check
            assert check is None or check(seen.landing), f"{name}\n{said}"
        if not expected.landed or expected.merged is None:
            return
        main = self.world.tree()
        assert self.world.owed() == owed(expected.merged), said
        held = self.history.held or bool(main.instances)
        start = self.history.start
        if start is Start.NONE and main.review is Row.BOUND:
            start = Start.HELD if held else Start.FRESH
        allowance = allowance_after(self.history.allowance, main)
        self.history = State(main, allowance, None, held, start)
        assert self.world.reviews() == reviews_owed(self.history), said
        for name in self.holding:
            check = RULES[name].state_check
            assert check is None or check(self.history), f"{name}\n{said}"


def notes(run: Run) -> tuple[bool, bool, bool]:
    """The three notes a run of the gate printed, as a ``Verdict`` holds them."""
    return (run.arrived, run.event_owes, run.reopens)


SafeguardMachine.TestCase.settings = settings(
    max_examples=4, stateful_step_count=15, derandomize=True, deadline=None
)
TestTheRealGateAgainstTheModel = SafeguardMachine.TestCase
