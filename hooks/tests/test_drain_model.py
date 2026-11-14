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
KILL_AFTER_REBASE = "a kill after the rebase stops drain.py update a"
KILLED_PAST_REBASE = [*JUDGED_A, EDIT_A, KILL_AFTER_REBASE]
KILLED_TWICE = [*KILLED_PAST_REBASE, KILL_AFTER_REBASE]
LAND_KILLED = [*JUDGED_A, "a kill before the note stops drain.py land a"]
LAND_CUT = "a kill before the discard stops drain.py land a"
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
    # Why: the shortest break of 8. ``KILLED_TWICE`` breaks it as well, replayed below.
    RECOVERS: ["drain.py open a", LAND_CUT],
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
    Kind.KILL_BEFORE_DISCARD,
    Kind.KILL_AFTER_STASH,
    Kind.KILL_LAND_BEFORE_NOTE,
    Kind.KILL_LAND_BEFORE_DISCARD,
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
    play.named("a kill before the discard stops drain.py close --outcome parked c")
    assert (tree / DRAFT).read_text(encoding="utf-8") == "half done\n", "the note came first"
    play.named("a kill after the stash stops drain.py close --outcome parked c")
    assert plan_of(play)[BRANCH[Name.C]].state == "held", "the note says parked"
    assert git(play.world.root, "show", f"stash@{{0}}^3:{DRAFT}") == "half done"
    assert play.named("drain.py close --outcome parked c") == f"closed: {REF[Name.C]}: parked\n"
    assert not tree.exists()


def test_an_update_killed_past_its_rebase_is_called_behind_and_put_right_by_update(
    tmp_path: Path,
) -> None:
    assert broken_by(KILLED_PAST_REBASE) == set()
    play = replay(tmp_path, [*KILLED_PAST_REBASE, "drain.py clean --all"])
    assert play.state.of(Name.A).branch is Branch.STALE
    assert not play.world.tree(Name.A).exists()
    assert drain.queue_notes.read_note(play.world.root, BRANCH[Name.A])["rebasing"]
    entry = plan_of(play)[BRANCH[Name.A]]
    assert (entry.state, entry.why) == ("built", (drain.BEHIND,)), "the plan sends it to update"
    assert play.named("drain.py update a") == '{"moved": true, "conflicts": []}\n'
    assert play.state.of(Name.A).branch is Branch.DRAFT, "update drops the record no review wrote"
    assert plan_of(play)[BRANCH[Name.A]].why == ()


def test_open_keeps_a_close_the_report_has_not_printed(tmp_path: Path) -> None:
    play = replay(tmp_path, [PARK_A, EDIT_A, "drain.py open a"])
    note = drain.queue_notes.read_note(play.world.root, BRANCH[Name.A])
    assert (note["outcome"], note["reported"], note.get("hold")) == ("parked", False, None)
    parked = play.named("drain.py report").split("## Parked")[1]
    assert "Alpha" in parked.split("## Not built")[0]


def test_clean_all_clears_the_mark_of_an_update_killed_on_a_branch_with_no_commit(
    tmp_path: Path,
) -> None:
    assert broken_by(MARK_LEFT) == set()
    play = replay(tmp_path, [*MARK_LEFT, "drain.py clean --all"])
    tree = play.world.tree(Name.A)
    assert play.world.printed.startswith(f"removed: {tree}: the drain that made it was interrupted")
    assert "rebasing" not in drain.queue_notes.read_note(play.world.root, BRANCH[Name.A])
    assert plan_of(play)[BRANCH[Name.A]].state == "buildable"


def test_a_landing_killed_before_its_note_is_recorded_by_the_next_drains_clean_all(
    tmp_path: Path,
) -> None:
    assert broken_by(LAND_KILLED) == set()
    play = replay(tmp_path, LAND_KILLED)
    assert "merged: `feature/1-alpha` into `main`" in play.world.printed
    tree = play.world.tree(Name.A)
    assert tree.exists(), "the note comes before the discard, so the tree is still there"
    swept = f"removed: {tree}: its branch is merged\ndrain: "
    assert play.named("drain.py clean --all").startswith(swept)
    note = drain.queue_notes.read_note(play.world.root, BRANCH[Name.A])
    assert (note["outcome"], note["reported"]) == ("merged", False)
    assert BRANCH[Name.A] not in plan_of(play)
    merged = play.named("drain.py report").split("## Waits for you")[0]
    assert "Alpha" in merged.split("## Merged")[1], "the next report says it merged"


def test_clean_records_a_merge_whose_tree_is_gone_and_a_close_is_then_refused(
    tmp_path: Path,
) -> None:
    play = replay(tmp_path, [*(COUNTEREXAMPLES[ONCE] or []), "drain.py clean"])
    assert not play.world.tree(Name.A).exists()
    note = drain.queue_notes.read_note(play.world.root, BRANCH[Name.A])
    assert (note["outcome"], note["reported"]) == ("merged", False)
    refused = f"drain: {REF[Name.A]} merged, and a merge is not unsaid by closing it as parked\n"
    assert play.named(PARK_A) == refused
    assert play.world.code == 2


def test_a_merge_no_note_says_is_closed_as_parked_and_reported_so(tmp_path: Path) -> None:
    assert broken_by(COUNTEREXAMPLES[ONCE] or []) == {ONCE}
    play = replay(tmp_path, COUNTEREXAMPLES[ONCE] or [])
    assert play.world.printed == f"closed: {REF[Name.A]}: parked\n"
    first = play.named("drain.py report").split("## Parked")
    assert "Alpha" not in first[0].split("## Merged")[1]
    assert "Alpha" in first[1].split("## Not built")[0], "a merged ticket reported as parked"
    assert play.named("drain.py land a") == f"closed: {REF[Name.A]}: merged\n"
    second = play.named("drain.py report").split("## Waits for you")[0]
    assert "Alpha" in second, "and then reported again, as merged"


def test_a_landing_killed_between_its_note_and_its_discard_is_not_finished_by_land_again(
    tmp_path: Path,
) -> None:
    path = COUNTEREXAMPLES[RECOVERS] or []
    assert broken_by(path) == {RECOVERS}
    play = replay(tmp_path, path)
    tree = play.world.tree(Name.A)
    held = f"land: {REF[Name.A]} is held and is not landed: record: "
    assert play.named("drain.py land a").startswith(held)
    assert (play.world.code, tree.exists()) == (2, True), "refused, and the tree stays"
    merged = replay(tmp_path / "merged", [*JUDGED_A, LAND_CUT])
    unsaid = f"drain: {REF[Name.A]} merged, and a merge is not unsaid by closing it as waits\n"
    assert merged.named("drain.py land a").endswith(unsaid), "merge.py runs again and is refused"
    assert (merged.world.code, merged.world.tree(Name.A).exists()) == (2, True)
    swept = f"removed: {tree}: the drain that made it was interrupted\ndrain: "
    assert play.named("drain.py clean --all").startswith(swept), "the next drain sweeps it"


def test_a_refused_landing_killed_before_its_discard_counts_its_retry_twice(
    tmp_path: Path,
) -> None:
    path = [*JUDGED_A, EDIT_A, LAND_CUT]
    assert broken_by(path) == {RECOVERS}
    play = replay(tmp_path, [*path, "drain.py land a"])
    assert play.world.printed.endswith(f"closed: {REF[Name.A]}: waits\n")
    note = drain.queue_notes.read_note(play.world.root, BRANCH[Name.A])
    assert note["attempts"] == 2, "merge.py refused it once a run, and the note says twice"


def test_an_update_killed_twice_before_it_drops_the_record_loses_its_mark(
    tmp_path: Path,
) -> None:
    assert broken_by(KILLED_TWICE) == {RECOVERS}
    play = replay(tmp_path, [*KILLED_TWICE, "drain.py clean --all"])
    assert play.state.of(Name.A).branch is Branch.STALE
    assert "rebasing" not in drain.queue_notes.read_note(play.world.root, BRANCH[Name.A])
    entry = plan_of(play)[BRANCH[Name.A]]
    assert (entry.state, entry.why) == ("built", ()), "not behind: the skill lands it next"
    assert play.named("drain.py update a") == '{"moved": false, "conflicts": []}\n'
    assert "merge: owner\nrecord: " in play.named("drain.py land a")
    assert plan_of(play)[BRANCH[Name.A]].state == "held", "and the landing holds it"
