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
KILLED_PAST_REBASE = [*JUDGED_A, EDIT_A, "a kill after the rebase stops drain.py update a"]
LAND_KILLED = [*JUDGED_A, "a kill before the note stops drain.py land a"]
MARK_LEFT = ["drain.py open a", EDIT_A, "a kill mid-rebase stops drain.py update a"]
# Why: a rule the code does not keep is pinned here by the shortest path from the initial state
# to a step that breaks it, so the break is a recorded fact and not a red test; each pinned path
# is replayed below on a real repository.
COUNTEREXAMPLES: dict[str, list[str] | None] = {
    CAP: None,
    KEPT: None,
    JUDGED: None,
    NOT_STALE: None,
    ONCE: [*LAND_KILLED, PARK_A],
    HELD: None,
    REFUSED: None,
    # Why: the shortest break of 8. ``LAND_KILLED`` breaks it as well, replayed below.
    RECOVERS: MARK_LEFT,
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
    Kind.KILL_LAND_BEFORE_NOTE,
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


def broken_by(path: list[str]) -> set[str]:
    """The rules the last step of ``path`` breaks in the model."""
    state = before = model.INITIAL
    for name in path:
        before = state
        state = next(after for action, after in model.moves(state) if action.name == name)
    return {rule for rule, holds in model.RULES.items() if not holds(before, path[-1], state)}


def replay(tmp_path: Path, path: list[str]) -> Play:
    """``path`` played on the model and a repository, compared after every step."""
    play = Play(tmp_path)
    for name in path:
        play.named(name)
    return play


def test_update_makes_no_tree_past_the_cap(tmp_path: Path) -> None:
    path = [*OPEN_A, PARK_A, "drain.py open c", EDIT_A, "drain.py update a"]
    assert broken_by(path) == set()
    play = replay(tmp_path, path)
    refused = f"update: {REF[Name.A]} is not rebased: 1 tickets are building: the cap\n"
    assert (play.world.code, play.world.printed) == (1, refused)
    assert drain.worktrees(play.world.root) == {play.world.tree(Name.C)}


def test_a_branch_judged_on_an_older_tip_does_not_land_and_is_retried(tmp_path: Path) -> None:
    play = replay(tmp_path, [*JUDGED_A, "the owner edits ticket c", "drain.py land a"])
    assert "merge: owner\nbehind: the branch does not hold the tip of `main`" in play.world.printed
    assert play.state.of(Name.A).note == model.Note(
        model.Outcome.CLOSED, hold=False, text=0, fresh=True, attempts=1
    ), "behind alone is a reason the drain resolves itself"


def test_land_refuses_a_held_ticket(tmp_path: Path) -> None:
    path = [*JUDGED_A, PARK_A, "drain.py land a"]
    assert broken_by(path) == set()
    play = replay(tmp_path, path)
    assert play.world.code == 2
    assert play.world.printed.startswith(f"land: {REF[Name.A]} is held and is not landed: ")
    assert plan_of(play)[BRANCH[Name.A]].state == "held"


def test_a_close_after_the_landing_is_refused_and_the_merge_reported_once(tmp_path: Path) -> None:
    path = [*JUDGED_A, "drain.py land a", PARK_A]
    assert broken_by(path) == set()
    play = replay(tmp_path, path)
    refused = f"drain: {REF[Name.A]} merged, and a merge is not unsaid by closing it as parked\n"
    assert (play.world.code, play.world.printed) == (2, refused)
    merged, parked = play.named("drain.py report").split("## Parked")
    assert "Alpha" in merged.split("## Merged")[1]
    assert "Alpha" not in parked.split("## Not built")[0]
    assert "Alpha" not in play.named("drain.py report"), "a second report does not print it"


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


def test_an_update_killed_past_its_rebase_is_put_right_by_update_but_the_plan_lands_it(
    tmp_path: Path,
) -> None:
    assert broken_by(KILLED_PAST_REBASE) == {RECOVERS}
    swept = [*KILLED_PAST_REBASE, "drain.py clean --all"]
    play = replay(tmp_path, swept)
    assert play.state.of(Name.A).branch is Branch.STALE
    assert not play.world.tree(Name.A).exists()
    entry = plan_of(play)[BRANCH[Name.A]]
    assert (entry.state, entry.why) == ("built", ()), "not behind: the skill lands it next"
    again = replay(tmp_path / "again", swept)
    assert again.named("drain.py update a") == '{"moved": true, "conflicts": []}\n'
    assert again.state.of(Name.A).branch is Branch.DRAFT, "update drops the record no review wrote"
    assert "merge: owner\nrecord: " in play.named("drain.py land a")
    assert plan_of(play)[BRANCH[Name.A]].state == "held", "and the landing holds it"


def test_open_keeps_a_close_the_report_has_not_printed(tmp_path: Path) -> None:
    play = replay(tmp_path, [PARK_A, EDIT_A, "drain.py open a"])
    note = drain.queue_notes.read_note(play.world.root, BRANCH[Name.A])
    assert (note["outcome"], note["reported"], note.get("hold")) == ("parked", False, None)
    parked = play.named("drain.py report").split("## Parked")[1]
    assert "Alpha" in parked.split("## Not built")[0]


def test_clean_all_keeps_the_mark_of_an_update_killed_on_a_branch_with_no_commit(
    tmp_path: Path,
) -> None:
    play = replay(tmp_path, [*MARK_LEFT, "drain.py clean --all"])
    assert drain.queue_notes.read_note(play.world.root, BRANCH[Name.A])["rebasing"]
    refused = f"update: {REF[Name.A]} is not rebased: `{BRANCH[Name.A]}` has no queue work tree\n"
    assert play.named("drain.py update a") == refused


def test_a_landing_killed_before_its_note_is_never_recorded_by_the_next_drain(
    tmp_path: Path,
) -> None:
    assert broken_by(LAND_KILLED) == {RECOVERS}
    play = replay(tmp_path, LAND_KILLED)
    assert "merged: `feature/1-alpha` into `main`" in play.world.printed
    assert not play.world.tree(Name.A).exists()
    assert play.named("drain.py clean --all") == f"drain: {play.world.token}\n", "nothing recorded"
    assert "outcome" not in drain.queue_notes.read_note(play.world.root, BRANCH[Name.A])
    assert BRANCH[Name.A] not in plan_of(play), "the next drain's plan does not list it"
    merged = play.named("drain.py report").split("## Waits for you")[0]
    assert merged.endswith("## Merged\n\n- none\n\n"), "and its report does not say it merged"
    assert play.named("drain.py land a") == f"closed: {REF[Name.A]}: merged\n", "land again does"


def test_a_merge_no_note_says_is_closed_as_parked_and_reported_so(tmp_path: Path) -> None:
    play = replay(tmp_path, COUNTEREXAMPLES[ONCE] or [])
    assert play.world.printed == f"closed: {REF[Name.A]}: parked\n"
    first = play.named("drain.py report").split("## Parked")
    assert "Alpha" not in first[0].split("## Merged")[1]
    assert "Alpha" in first[1].split("## Not built")[0], "a merged ticket reported as parked"
    assert play.named("drain.py land a") == f"closed: {REF[Name.A]}: merged\n"
    second = play.named("drain.py report").split("## Waits for you")[0]
    assert "Alpha" in second, "and then reported again, as merged"
