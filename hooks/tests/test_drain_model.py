"""The queue drain's rules: on every state of its model, then on real repositories."""

from __future__ import annotations

import functools
import shutil
import tempfile
from pathlib import Path

import pytest
from hypothesis import HealthCheck, Phase, settings
from hypothesis import strategies as st
from hypothesis.stateful import RuleBasedStateMachine, rule, run_state_machine_as_test

import drain
import merge_ready
from tests import drain_model as model
from tests.drain_model import Branch, Kind, Name
from tests.drain_world import BRANCH, DRAFT, REF, World
from tests.repos import git
from tests.test_drain import scratch  # noqa: F401 # -- an autouse fixture of the drain's tests

CAP, KEPT, JUDGED, NOT_STALE, ONCE, HELD, REFUSED, RECOVERS, NO_TREE = model.RULES

OPEN_A = ["drain.py open a", "the agent commits a"]
JUDGED_A = [*OPEN_A, "the agent commits the verdict record of a"]
PARK_A = "drain.py close --outcome parked a"
EDIT_A = "the owner edits ticket a"
# Why: a rule the code does not keep is pinned here by the shortest path from the initial state
# to a step that breaks it, so the break is a recorded fact and not a red test; each pinned path
# is replayed below on a real repository.
COUNTEREXAMPLES: dict[str, list[str] | None] = {
    CAP: [*OPEN_A, PARK_A, "drain.py open c", EDIT_A, "drain.py update a"],
    KEPT: None,
    JUDGED: None,
    NOT_STALE: None,
    ONCE: [*JUDGED_A, "drain.py land a", PARK_A],
    HELD: [*JUDGED_A, PARK_A, "drain.py land a"],
    REFUSED: None,
    RECOVERS: [*JUDGED_A, EDIT_A, "a kill after the rebase stops drain.py update a"],
    NO_TREE: [
        PARK_A,
        "drain.py open c",
        "the agent leaves work uncommitted in c",
        "git leaves a stale index lock in the tree of c",
        "drain.py clean --all",
    ],
}


@functools.cache
def search() -> tuple[dict[str, list[str]], frozenset[str]]:
    """Each rule's shortest break, read off the graph's breadth-first steps; every action taken."""
    _, edges = model.graph()
    came: dict[model.State, tuple[model.State, str] | None] = {model.INITIAL: None}
    breaks: dict[str, list[str]] = {}
    for before, said, after in edges:
        for name, rule_holds in model.RULES.items():
            if name not in breaks and not rule_holds(before, said, after):
                path, at = [said], came[before]
                while at is not None:
                    path.append(at[1])
                    at = came[at[0]]
                breaks[name] = path[::-1]
        came.setdefault(after, (before, said))
    return breaks, frozenset(said for _, said, _ in edges)


@pytest.mark.parametrize("name", list(model.RULES))
def test_each_rule_holds_on_every_step_of_the_model_or_breaks_on_its_recorded_path(
    name: str,
) -> None:
    found = search()[0].get(name)
    assert found == COUNTEREXAMPLES[name], f"{name}: shortest break is\n" + "\n".join(found or [])


def test_every_rule_reads_as_one_sentence() -> None:
    for name, rule_holds in model.RULES.items():
        sentence = (rule_holds.__doc__ or "").strip()
        assert sentence.endswith("."), name
        assert ". " not in sentence, name


def test_every_kind_of_action_is_reachable() -> None:
    _, taken = search()
    for kind in Kind:
        assert any(name.startswith(kind.value) for name in taken), kind


class Play:
    """The model and a real repository taking the same actions, compared after each one."""

    def __init__(self, directory: Path) -> None:
        self.world = World(directory)
        self.state = model.INITIAL
        self.path: list[str] = []

    def says(self, what: str) -> str:
        lines = [what, "path:", *(f"  {name}" for name in self.path)]
        return "\n".join([*lines, f"the last command printed:\n{self.world.printed}"])

    def named(self, name: str, *, compare: bool = True) -> str:
        """Take the action the model calls ``name``; what the command printed."""
        move = next((move for move in model.moves(self.state) if move[0].name == name), None)
        assert move is not None, self.says(f"no action is called {name!r} here")
        self.take(*move, compare=compare)
        return self.world.printed

    def take(self, action: model.Action, after: model.State, *, compare: bool = True) -> None:
        self.state = after
        self.path.append(action.name)
        self.world.printed = ""
        self.world.perform(action)
        if compare:
            self.agree()

    def agree(self) -> None:
        """Assert the repository is what the model says it is now."""
        seen = self.world.seen(self.state)
        statuses = model.classify(self.state)
        assert seen.statuses == statuses, self.says(f"the plan says {seen.statuses}")
        assert seen.start == model.start(self.state), self.says(f"the plan starts {seen.start}")
        for name, real, kept in zip(Name, seen.tickets, self.state.tickets, strict=True):
            assert real == kept, self.says(f"ticket {name.value} is {real}\nnot {kept}")


# Why: the families an action is drawn from, a family as often as it is listed. A landing runs
# merge.py in a process of its own, several times the cost of any other step. The order is the
# first-choice example's: it reaches first what the replays below do not take.
WEIGHTED = [
    Kind.OPEN,
    Kind.OPEN,
    Kind.COMMIT,
    Kind.COMMIT,
    Kind.RECORD,
    Kind.RECORD,
    Kind.EDIT,
    Kind.KILL_MID_REBASE,
    Kind.KILL_AFTER_REBASE,
    Kind.UPDATE,
    Kind.CLOSE,
    Kind.LAND,
    Kind.WORK,
    Kind.KILL_AFTER_STASH,
    Kind.KILL_BEFORE_NOTE,
    Kind.CLEAN,
    Kind.CLEAN_ALL,
    Kind.REPORT,
    Kind.LOCK,
    Kind.UNLOCK,
]


class DrainOnARepository(RuleBasedStateMachine):
    """Random actions on the model and on a real repository together."""

    def __init__(self) -> None:
        super().__init__()
        self.scratch = Path(tempfile.mkdtemp(prefix="drain-model-"))
        self.play = Play(self.scratch)

    @rule(data=st.data())
    def act(self, data: st.DataObject) -> None:
        # Why: Hypothesis tries the first choice of every draw first. Listing the actions that
        # change the state ahead of those that do not makes that example walk the lifecycle,
        # not repeat one refused command.
        state = self.play.state
        moves = sorted(model.moves(state), key=lambda move: move[1] == state)
        changes = [
            kind for kind in WEIGHTED if any(a.kind is kind and b != state for a, b in moves)
        ]
        kinds = changes + [kind for kind in WEIGHTED if kind not in changes]
        open_to = {action.kind for action, _ in moves}
        chosen = data.draw(st.sampled_from([kind for kind in kinds if kind in open_to]))
        action, after = data.draw(
            st.sampled_from([move for move in moves if move[0].kind is chosen]), label="action"
        )
        self.play.take(action, after)

    def teardown(self) -> None:
        shutil.rmtree(self.scratch, ignore_errors=True)


def test_the_real_drain_agrees_with_the_model_on_every_step() -> None:
    # Why: every shrink replays git and merge.py; a red run reports its path unshrunk, and the
    # exhaustive tests above give each rule's shortest break.
    run_state_machine_as_test(
        DrainOnARepository,
        settings=settings(
            max_examples=2,
            stateful_step_count=8,
            derandomize=True,
            deadline=None,
            phases=[Phase.explicit, Phase.reuse, Phase.generate],
            suppress_health_check=[HealthCheck.too_slow],
        ),
    )


def plan_of(play: Play) -> dict[str, drain.Entry]:
    drafted = drain.plan(drain.Drain(play.world.root, None, model.CAP))
    return {entry.branch: entry for entry in drafted.tickets}


# Why: each history below replays a pinned path, and the steps of the past defects on the way,
# comparing with the model at its end; the stateful run compares every step.


def test_a_tree_made_again_by_update_takes_the_cap_past_what_open_allows(tmp_path: Path) -> None:
    play = Play(tmp_path)
    for name in OPEN_A:
        play.named(name, compare=False)
    refused = play.named("drain.py open c", compare=False)
    assert refused.endswith("1 tickets are building: the cap\n"), "a built tree holds its place"
    play.named(PARK_A, compare=False)
    entries = plan_of(play)
    assert entries[BRANCH[Name.A]].state == "held", "a ticket parked after it has commits"
    assert entries[BRANCH[Name.B]].state == "refused", "its dependent is refused, not waiting"
    for name in (COUNTEREXAMPLES[CAP] or [])[3:]:
        printed = play.named(name, compare=False)
    play.agree()
    assert play.path[:2] + play.path[3:] == COUNTEREXAMPLES[CAP], "the pinned path, one refusal in"
    assert printed == '{"moved": true, "conflicts": []}\n'
    trees = drain.worktrees(play.world.root)
    assert trees == {play.world.tree(Name.A), play.world.tree(Name.C)}
    assert len(trees) > model.CAP


def test_a_branch_judged_on_an_older_tip_does_not_land_and_is_retried(tmp_path: Path) -> None:
    play = Play(tmp_path)
    for name in [*JUDGED_A, "the owner edits ticket c"]:
        play.named(name, compare=False)
    stale = play.named("drain.py land a", compare=False)
    assert "merge: owner\nbehind: the branch does not hold the tip of `main`" in stale
    play.agree()
    assert play.state.of(Name.A).note == model.Note(
        model.Outcome.CLOSED, hold=False, text=0, attempts=1
    ), "behind alone is a reason the drain resolves itself"


def test_land_merges_a_held_ticket_and_a_close_after_it_unsays_the_merge(tmp_path: Path) -> None:
    # Why: rule 6's pinned path, then rule 5's last step; rule 5's own pinned path closes the
    # merged ticket straight after the landing, which reads the same way.
    play = Play(tmp_path)
    for name in [*JUDGED_A, PARK_A]:
        play.named(name, compare=False)
    assert plan_of(play)[BRANCH[Name.A]].state == "held", "a ticket parked after it has commits"
    landed = play.named("drain.py land a", compare=False)
    assert play.path == COUNTEREXAMPLES[HELD]
    assert "merge: agent\nmerged: `feature/1-alpha` into `main`" in landed
    play.named(PARK_A)
    note = drain.queue_notes.read_note(play.world.root, BRANCH[Name.A])
    assert (note["outcome"], note["reported"]) == ("parked", False)
    merged, parked = play.named("drain.py report").split("## Parked")
    assert "Alpha" not in merged.split("## Merged")[1]
    assert "Alpha" in parked.split("## Not built")[0]


def test_clean_all_leaves_a_tree_git_cannot_stash_and_a_killed_close_keeps_its_work(
    tmp_path: Path,
) -> None:
    play = Play(tmp_path)
    for name in COUNTEREXAMPLES[NO_TREE] or []:
        printed = play.named(name, compare=False)
    play.agree()
    tree = play.world.tree(Name.C)
    assert printed.startswith(f"left: {tree}: the drain that made it was interrupted, and git")
    assert (tree / DRAFT).read_text(encoding="utf-8") == "half done\n"
    play.named("the owner removes the stale index lock of c", compare=False)
    play.named("a kill after the stash stops drain.py close --outcome parked c")
    play.named("a kill before the note stops drain.py close --outcome parked c")
    assert not tree.exists()
    assert play.named("drain.py close --outcome parked c") == f"closed: {REF[Name.C]}: parked\n"
    assert git(play.world.root, "show", f"stash@{{0}}^3:{DRAFT}") == "half done"


def test_clean_all_after_an_update_killed_past_its_rebase_keeps_a_record_no_review_wrote(
    tmp_path: Path,
) -> None:
    play = Play(tmp_path)
    for name in COUNTEREXAMPLES[RECOVERS] or []:
        play.named(name, compare=False)
    play.agree()
    assert play.state.of(Name.A).branch is Branch.STALE
    play.named("drain.py clean --all")
    assert play.named("drain.py update a") == '{"moved": false, "conflicts": []}\n'
    tip = git(play.world.root, "rev-parse", BRANCH[Name.A])
    asked = merge_ready.Asked(current=True)
    reasons = merge_ready.judge(play.world.root, "refs/heads/main", tip, asked=asked)
    assert [reason.code for reason in reasons] == ["record"]
    assert "a commit came after the review" in reasons[0].text
