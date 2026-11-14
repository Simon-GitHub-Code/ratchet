"""The threshold ratchet's rules: on every state of its model, then on real git histories."""

from __future__ import annotations

import functools
import shutil
import tempfile
from collections import deque
from dataclasses import replace
from pathlib import Path

import pytest
from hypothesis import HealthCheck, Phase, settings
from hypothesis import strategies as st
from hypothesis.stateful import RuleBasedStateMachine, rule, run_state_machine_as_test

from tests import ratchet_model as model
from tests.ratchet_world import Gate, Landing, World

NAMED, BROUGHT, NOT_LATE, REMEMBERED = model.RULES

# Why: a rule the gate does not keep is pinned here by the shortest path from the initial state
# to a step that breaks it, so the break is a recorded fact and not a red test. Rules 2 and 4
# were pinned that way until ADR 0047; every rule holds now.
COUNTEREXAMPLES: dict[str, list[str] | None] = {
    NAMED: None,
    BROUGHT: None,
    NOT_LATE: None,
    REMEMBERED: None,
}
HOLDING = [name for name, path in COUNTEREXAMPLES.items() if path is None]
# Why: the two paths that broke rules 2 and 4 before ADR 0047, replayed below as red landings.
RECORD_LANDED_ALONE = [
    "branch a opens from the default tip",
    "branch b opens from the default tip",
    "branch a lowers gate.floor to 1",
    "branch b records a loosening it does not make: 'Relaxes: gate.floor from 2 to 1'",
    "branch b tries to land",
    "branch a tries to land",
]
REMOVED_THEN_BACK = [
    "branch a opens from the default tip",
    "branch a removes gate.floor",
    "branch a records its own loosening: 'Relaxes: gate.floor from 2 to removed'",
    "branch a tries to land",
    "branch a opens from the default tip",
    "branch a adds gate.floor to 1",
    "branch a tries to land",
]
OWN_RECORD = [
    "branch a records its own loosening: 'Relaxes: gate.floor from 2 to 1'",
    "branch a tries to land",
]
# Why: the stateful run checks real histories past the exhaustive graph's bound.
LONGER = 6


@functools.cache
def graph() -> model.Graph:
    return model.graph()


def shortest_break(name: str) -> list[str] | None:
    """The fewest actions from the initial state to a step that breaks the rule, or ``None``."""
    rule_holds = model.RULES[name]
    came: dict[model.State, tuple[model.State, str] | None] = {model.INITIAL: None}
    queue = deque([model.INITIAL])
    while queue:
        state = queue.popleft()
        for action, after in model.moves(state):
            if not rule_holds(state, action, after):
                path, at = [action.name], came[state]
                while at is not None:
                    path.append(at[1])
                    at = came[at[0]]
                return path[::-1]
            if after not in came:
                came[after] = (state, action.name)
                queue.append(after)
    return None


@pytest.mark.parametrize("name", list(model.RULES))
def test_each_rule_holds_on_every_step_of_the_model_or_breaks_on_its_recorded_path(
    name: str,
) -> None:
    found = shortest_break(name)
    assert found == COUNTEREXAMPLES[name], f"{name}: shortest break is\n" + "\n".join(found or [])


def test_every_rule_reads_as_one_sentence() -> None:
    for name, rule_holds in model.RULES.items():
        sentence = (rule_holds.__doc__ or "").strip()
        assert sentence.endswith("."), name
        assert ". " not in sentence, name


def test_every_kind_of_action_is_reachable() -> None:
    _, edges = graph()
    assert {action.kind for _, action, _ in edges} == set(model.Kind)


def landings() -> list[tuple[model.State, model.Branch, model.State]]:
    """Every landing attempt in the graph: the state, the branch, and the state after."""
    states, _ = graph()
    return [
        (state, branch, model.land(state, slot))
        for state in states
        if state.left
        for slot in model.Slot
        if (branch := state.branch(slot)) is not None
    ]


def test_a_branch_left_open_while_the_ceiling_fell_cannot_land_a_value_between() -> None:
    late = [
        (before, after)
        for before, branch, after in landings()
        if branch.fork.ceiling is model.Value.THREE
        and before.default.ceiling is model.Value.ONE
        and branch.head.ceiling is model.Value.TWO
    ]
    assert late, "the model never reaches the late landing the rule is about"
    assert all(before == after for before, after in late)


def test_a_branch_left_open_can_land_its_own_recorded_loosening_late() -> None:
    late = [
        branch
        for before, branch, after in landings()
        if after.left < before.left
        and branch.fork != before.default
        and model.loosenings(before.default, after.default, before.last)
    ]
    assert late, "rule 3 would hold for want of a late landing that loosens"


def test_a_number_is_left_behind_through_a_flag_by_two_changes_each_with_its_record() -> None:
    # Why: ADR 0047 allows this chain, so rule 4 is about a removed key and not about a retyped
    # one; this shows the chain is reachable, and rule 2 that each of its steps brought a record.
    _, edges = graph()
    flagged = [
        after
        for before, _, after in edges
        if before.default.floor is model.Value.TWO and after.default.floor is model.Value.ON
    ]
    numbered = [
        after
        for before, _, after in edges
        if before.default.floor is model.Value.ON and after.default.floor is model.Value.ONE
    ]
    assert flagged, "no landing turns the floor into a flag"
    assert numbered, "no landing turns the flag into a lower number"
    assert all(len(after.default.records) == 2 for after in numbered)


def real_steps(world: World) -> list[model.Edge]:
    """Each merge the default branch moved to, as a model step, so the rules read it."""
    steps: list[model.Edge] = []
    state = model.INITIAL
    held = [state.last]
    for merge in world.landings:
        branch = model.Branch(merge.fork, merge.head, held[merge.forked_after])
        before = model.State(merge.before, (branch, None), state.last, state.left)
        state = model.State(
            merge.after, (None, None), model.remember(state.last, merge.after), state.left - 1
        )
        held.append(state.last)
        steps.append((before, model.Action(model.Kind.LAND, model.Slot.A), state))
    return steps


class Play:
    """The model and a real repository taking the same actions, compared after each one."""

    def __init__(self, directory: Path, state: model.State) -> None:
        self.world = World(directory)
        self.state = state
        self.path: list[str] = []

    def says(self, what: str, *printed: str) -> str:
        lines = [what, "path:", *(f"  {name}" for name in self.path), f"model: {self.state}"]
        return "\n".join([*lines, *(f"the gate printed:\n{text}" for text in printed)])

    def named(self, name: str) -> Landing | None:
        """Take the action the model calls ``name``."""
        move = next((move for move in model.moves(self.state) if move[0].name == name), None)
        assert move is not None, self.says(f"no action is called {name!r} here")
        return self.take(*move)

    def take(self, action: model.Action, after: model.State) -> Landing | None:
        before, self.state = self.state, after
        self.path.append(action.name)
        seen = self.world.perform(action)
        if isinstance(seen, Landing):
            self.judge_landing(before, action.slot, seen)
            return seen
        branch = after.branch(action.slot)
        if branch is not None:
            real = self.world.branch_tree(action.slot)
            assert real == branch.head, self.says(f"the branch holds {real}")
        return None

    def agree(self, where: str, real: Gate, green: bool) -> None:  # noqa: FBT001
        said = f"the gate {where} is {'red' if real.green else 'green'} only in the model"
        assert real.green == green, self.says(said, real.output)

    def judge_landing(self, before: model.State, slot: model.Slot, seen: Landing) -> None:
        branch = before.branch(slot)
        assert branch is not None
        on_branch = model.green(branch.fork, branch.head, branch.held)
        self.agree("on the branch", seen.branch, on_branch)
        merged = model.merge(branch.fork, before.default, branch.head)
        clash = f"git conflicts: {seen.merge is None}, the model's merge: {merged is None}"
        assert (seen.merge is None) == (merged is None), self.says(clash)
        if seen.merge is not None and merged is not None:
            on_merge = model.green(before.default, merged, before.last)
            self.agree("on the merge", seen.merge, on_merge)
        assert seen.landed == (self.state.left < before.left), self.says(f"landed: {seen.landed}")
        if seen.landed:
            real = self.world.landings[-1].after
            assert real == self.state.default, self.says(f"the default branch holds {real}")
        for step in real_steps(self.world):
            for name in HOLDING:
                assert model.RULES[name](*step), self.says(f"the real history breaks {name}")


def family(action: model.Action) -> str:
    if action.kind in model.EDITS or action.kind is model.Kind.DELETE:
        return "change"
    if action.kind in model.RECORDS:
        return "record"
    return action.kind.name


# Why: two slots keep opening and abandoning possible at nearly every step; drawn as often as
# changes and landings, they spent most of a run's git calls on branches that did nothing.
WEIGHTED = ["change", "change", "record", "record", "LAND", "LAND", "OPEN", "ABANDON"]


def worth_taking(state: model.State, action: model.Action) -> bool:
    """Whether ``action`` is anything but landing a branch that changed nothing."""
    branch = state.branch(action.slot)
    idle = branch is not None and not branch.changed and not branch.own_records
    return action.kind is not model.Kind.LAND or not idle


class RatchetOnARepository(RuleBasedStateMachine):
    """Random actions on the model and on a real repository together."""

    def __init__(self) -> None:
        super().__init__()
        self.scratch = Path(tempfile.mkdtemp(prefix="ratchet-model-"))
        self.play = Play(self.scratch, replace(model.INITIAL, left=LONGER))

    @rule(data=st.data())
    def act(self, data: st.DataObject) -> None:
        state = self.play.state
        moves = [move for move in model.moves(state) if worth_taking(state, move[0])]
        if not moves:
            return
        open_to = {family(action) for action, _ in moves}
        chosen = data.draw(st.sampled_from([name for name in WEIGHTED if name in open_to]))
        action, after = data.draw(
            st.sampled_from([move for move in moves if family(move[0]) == chosen]),
            label="action",
        )
        self.play.take(action, after)

    def teardown(self) -> None:
        shutil.rmtree(self.scratch, ignore_errors=True)


def test_the_real_gate_agrees_with_the_model_and_real_histories_keep_its_rules() -> None:
    # Why: every shrink replays git; shrinking a red run took over five minutes, so a red run
    # reports its path unshrunk, and the exhaustive tests above give each rule's shortest one.
    run_state_machine_as_test(
        RatchetOnARepository,
        settings=settings(
            max_examples=5,
            stateful_step_count=20,
            derandomize=True,
            deadline=None,
            phases=[Phase.explicit, Phase.reuse, Phase.generate],
            suppress_health_check=[HealthCheck.too_slow],
        ),
    )


def replay(
    directory: Path, path: list[str], landings: int = model.LANDINGS
) -> list[Landing | None]:
    play = Play(directory, replace(model.INITIAL, left=landings))
    return [play.named(name) for name in path]


def test_a_record_landed_alone_excuses_no_later_branch(tmp_path: Path) -> None:
    *_, early, late = replay(tmp_path, RECORD_LANDED_ALONE)
    assert early is not None
    assert early.landed
    assert late is not None
    assert late.merge is not None
    assert not late.landed
    assert "gate.floor: lowered from 2.0 to 1.0\n" in late.branch.output
    assert "gate.floor: lowered from 2.0 to 1.0\n" in late.merge.output


def test_a_record_once_landed_does_not_let_the_same_loosening_land_again(tmp_path: Path) -> None:
    # Why: three landings, one past the exhaustive graph's bound. The floor was lowered with a
    # record and raised back; lowering it again needs a record of its own, in a file of its own.
    *_, again, _, own = replay(
        tmp_path,
        [
            "branch a opens from the default tip",
            "branch a lowers gate.floor to 1",
            *OWN_RECORD,
            "branch a opens from the default tip",
            "branch a raises gate.floor to 2",
            "branch a tries to land",
            "branch a opens from the default tip",
            "branch a lowers gate.floor to 1",
            "branch a tries to land",
            *OWN_RECORD,
        ],
        landings=3,
    )
    assert again is not None
    assert again.merge is not None
    assert not again.branch.green
    assert not again.landed
    assert "gate.floor: lowered from 2.0 to 1.0\n" in again.merge.output
    assert own is not None
    assert own.landed
    assert own.merge is not None
    assert "lowered from 2.0 to 1.0, relaxed by docs/adr/" in own.merge.output


def test_a_key_removed_by_a_record_comes_back_looser_only_with_a_record(tmp_path: Path) -> None:
    *_, back, _, own = replay(tmp_path, [*REMOVED_THEN_BACK, *OWN_RECORD], landings=3)
    assert back is not None
    assert back.merge is not None
    assert not back.branch.green
    assert not back.landed
    assert "gate.floor: lowered from 2.0 to 1.0\n" in back.merge.output
    assert own is not None
    assert own.landed


def test_a_branch_left_open_while_the_ceiling_fell_conflicts_on_a_real_repository(
    tmp_path: Path,
) -> None:
    *_, late = replay(
        tmp_path,
        [
            "branch a opens from the default tip",
            "branch b opens from the default tip",
            "branch b lowers ceilings.gate.seconds to 1",
            "branch b tries to land",
            "branch a lowers ceilings.gate.seconds to 2",
            "branch a tries to land",
        ],
    )
    assert late is not None
    assert late.branch.green
    assert late.merge is None, "the two changes to one line conflict"
