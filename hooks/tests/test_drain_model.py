"""The queue drain's rules: on every state of its model, then on real repositories."""

from __future__ import annotations

import contextlib
import functools
import json
import os
import shutil
import signal
import tempfile
import time
from collections import deque
from pathlib import Path
from typing import TYPE_CHECKING, NamedTuple

import pytest
from hypothesis import HealthCheck, Phase, settings
from hypothesis import strategies as st
from hypothesis.stateful import RuleBasedStateMachine, rule, run_state_machine_as_test

import drain
import gatelib
import publish_check
from tests import drain_model as model
from tests import model_tla
from tests.drain_model import Branch, Kind, Merge, Name, Step
from tests.drain_world import (
    BRANCH,
    DRAFT,
    HALTED_WHY,
    LANDING_WAIT,
    PUBLISHED,
    REF,
    TEXTS,
    LeaseWorld,
    World,
    digest,
    lock_held,
    running,
)
from tests.repos import git
from tests.support import PLUGIN_ROOT
from tests.test_drain import (
    HALVES,
    plan_file,
    scratch,  # noqa: F401 # -- an autouse fixture of the drain's tests
)

if TYPE_CHECKING:
    from collections.abc import Iterator

(
    CAP,
    KEPT,
    JUDGED,
    NOT_STALE,
    ONCE,
    REPORT_WRITES,
    HELD,
    HELD_EDITED,
    REFUSED,
    REFUSED_UNOPENED,
    RELEASED_BY_THE_LINE,
    PARTS_FIRST,
    PARTS_AWAITED,
    RECOVERS,
    NO_TREE,
    UNLANDED,
    UNTOUCHED,
) = model.RULES

CHECK_A = "drain.py check a"
OPEN_A = ["drain.py open a", CHECK_A, "the agent commits a"]
JUDGED_A = [*OPEN_A, "the agent commits the verdict record of a"]
PARK_A = "drain.py close --outcome parked a"
EDIT_A = "the owner edits ticket a"
KILL_AFTER_REBASE = "a kill after the rebase stops drain.py update a"
KILLED_TWICE = [*JUDGED_A, EDIT_A, KILL_AFTER_REBASE, KILL_AFTER_REBASE]
LAND_KILLED = [*JUDGED_A, "a kill before the note stops drain.py land a"]
LAND_CUT = "a kill before the discard stops drain.py land a"
LAND_LEFT = "a kill after the discard stops drain.py land a"
RETRIED = [*JUDGED_A, EDIT_A, LAND_LEFT, "drain.py report", "drain.py land a"]
REPLAYED_AFTER_A_KILL = [RETRIED]
PARKED_THEN_LANDED = [PARK_A, EDIT_A, *JUDGED_A, LAND_KILLED[-1]]
MARK_LEFT = ["drain.py open a", EDIT_A, "a kill mid-rebase stops drain.py update a"]
CUT_OFF = "a kill mid fast-forward stops merge.py in drain.py land a"
# Why: a rule the code does not keep is pinned here by the shortest path from the initial state
# to a step that breaks it, so the break is a recorded fact and not a red test; each pinned path
# is replayed below on a real repository.
COUNTEREXAMPLES: dict[str, list[str] | None] = {
    CAP: None,
    KEPT: None,
    JUDGED: None,
    NOT_STALE: None,
    ONCE: None,
    REPORT_WRITES: None,
    HELD: None,
    HELD_EDITED: None,
    REFUSED: None,
    REFUSED_UNOPENED: None,
    RELEASED_BY_THE_LINE: None,
    # Why: `drain.pending` reads a blocker's open or closed state alone, never the note that says
    # it was split into parts, so a split blocker the owner closes no longer holds its dependent
    # back while the parts are unbuilt.
    PARTS_FIRST: [
        "drain.py open a",
        "drain.py split a",
        "the owner closes ticket a",
        "drain.py open b",
    ],
    # Why: the same. A split blocker stays open and held, so `drain.settled` refuses its
    # dependent as waiting on a ticket the drain cannot merge, while the parts are built and after.
    PARTS_AWAITED: ["drain.py open a", "drain.py split a"],
    RECOVERS: None,
    # Why: the docstring accepts it: "A work tree whose uncommitted work git cannot stash (a stale
    # lock, a merge left unfinished, a damaged index) is not removed".
    NO_TREE: [
        PARK_A,
        "drain.py open c",
        "the agent leaves work uncommitted in c",
        "git leaves a stale index lock in the tree of c",
        "drain.py clean --all",
    ],
    UNLANDED: None,
    # Why: the agent's commits are free here, and nothing in the drain stops one before the check.
    # The feature skill's order does: the rule holds on every step that keeps it.
    UNTOUCHED: [
        "drain.py open a",
        "a role of the pipeline run commits in a",
        "the agent commits the verdict record of a",
    ],
}


PARK_C = "drain.py close --outcome parked c"
# Why: for each kind of kill, on a landed ticket and on one not landed, the shortest path to one
# that leaves what neither the whole command nor none of it leaves, as the search finds them.
KILL_CUTS = [
    ["drain.py open a", CHECK_A, LAND_CUT],
    ["drain.py open a", EDIT_A, "a kill mid-rebase stops drain.py update a"],
    ["drain.py open a", EDIT_A, KILL_AFTER_REBASE],
    [PARK_A, "drain.py open c", f"a kill before the discard stops {PARK_C}"],
    [
        PARK_A,
        "drain.py open c",
        "the agent leaves work uncommitted in c",
        f"a kill after the stash stops {PARK_C}",
    ],
    LAND_KILLED,
    [*JUDGED_A, LAND_CUT],
    [*JUDGED_A, "a kill before the fast-forward stops merge.py in drain.py land a"],
    [*JUDGED_A, CUT_OFF],
    [*JUDGED_A, EDIT_A, LAND_LEFT],
]


class Search(NamedTuple):
    breaks: dict[str, list[str]]
    """Each rule's shortest break."""
    taken: frozenset[str]
    """Every action taken."""
    cuts: dict[tuple[str, bool], list[str]]
    """For each kind of kill, on a landed ticket or not, the shortest path to one that leaves
    what neither the whole command nor none of it leaves."""


@functools.cache
def main_graph() -> tuple[frozenset[model.State], list[model.Edge]]:
    return model.graph()


@functools.cache
def search() -> Search:
    """What the graph's breadth-first steps show."""
    _, edges = main_graph()
    came: dict[model.State, tuple[model.State, str] | None] = {model.INITIAL: None}
    breaks: dict[str, list[str]] = {}
    cuts: dict[tuple[str, bool], list[str]] = {}

    def path_to(said: str, at: tuple[model.State, str] | None) -> list[str]:
        path = [said]
        while at is not None:
            path.append(at[1])
            at = came[at[0]]
        return path[::-1]

    for before, said, after in edges:
        for name, rule_holds in model.RULES.items():
            if name not in breaks and not rule_holds(before, said, after):
                breaks[name] = path_to(said, came[before])
        kill, _, last = said.rpartition(" ")
        command, ticket = model.KILLED.get(kill), model.BY_VALUE.get(last)
        if command is not None and ticket is not None:
            cut = (kill, after.of(ticket).merged)
            if cut not in cuts and after not in {before, command(before, ticket)}:
                cuts[cut] = path_to(said, came[before])
        came.setdefault(after, (before, said))
    return Search(breaks, frozenset(said for _, said, _ in edges), cuts)


def test_each_rule_holds_on_every_step_of_the_model_or_breaks_on_its_recorded_path() -> None:
    # Why: one test reads the graph, so a run that spreads the tests over processes builds it once.
    found = search()
    for name in model.RULES:
        path = found.breaks.get(name)
        assert path == COUNTEREXAMPLES[name], f"{name}: shortest break is\n" + "\n".join(path or [])
    assert list(found.cuts.values()) == KILL_CUTS, "the kills' cuts are\n" + "\n".join(
        map(str, found.cuts.values())
    )
    for kind in Kind:
        assert any(name.startswith(kind.value) for name in found.taken), kind
    unchecked = [
        said
        for before, said, after in main_graph()[1]
        for name in Name
        if after.of(name).merged and model.unchecked(before.of(name))
    ]
    assert not unchecked, f"a branch lands on a start no check passed: {unchecked[0]}"


def test_a_branch_a_role_committed_on_is_never_committed_on_where_the_skill_keeps_its_order() -> (
    None
):
    # Why: rule 11b holds only where the agent commits after the check, as the feature skill says.
    _, edges = model.graph(model.in_order)
    holds = model.RULES[UNTOUCHED]
    broken = next((said for before, said, after in edges if not holds(before, said, after)), None)
    assert broken is None, broken
    assert all(model.RULES[UNLANDED](before, said, after) for before, said, after in edges)


def test_every_rule_reads_as_one_sentence() -> None:
    for name, rule_holds in model.RULES.items():
        sentence = (rule_holds.__doc__ or "").strip()
        assert sentence.endswith("."), name
        assert ". " not in sentence, name


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

    def cut(self, kind: Kind, name: Name) -> str:
        """A kill the model takes as its whole command, which leaves what the whole one leaves."""
        whole = model.ON_TICKET[model.KILLS[kind]](self.state, name)
        assert whole is not None
        self.take(model.Action(kind, name), whole)
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
        merging = (seen.scratch, seen.half)
        assert merging == (self.state.scratch, self.state.half), self.says(f"merge.py: {merging}")
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
    Kind.CHECK,
    Kind.CHECK,
    Kind.PUBLISH,
    Kind.UPGRADE,
    Kind.LINE,
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
    Kind.KILL_LAND_AFTER_DISCARD,
    Kind.CLEAN,
    Kind.CLEAN_ALL,
    Kind.REPORT,
    Kind.LOCK,
    Kind.UNLOCK,
    Kind.SPLIT,
    Kind.RESOLVE,
    Kind.CHECK_HALTED,
]
LANDS_A = frozenset(
    {Kind.LAND, *(kill for kill, command in model.KILLS.items() if command is Kind.LAND)}
)
UPDATES_A = frozenset({Kind.UPDATE, Kind.KILL_MID_REBASE, Kind.KILL_AFTER_REBASE})


def taken_today(world: World, before: model.State, action: model.Action) -> bool:
    """Whether today's drain.py takes ``action`` as the model does.

    The steps the script change of ADR 0060 adds are left to the replays that wait for it: a
    check that cannot read, a check that makes the tree, ``land`` refused on a start no check
    passed, and ``update`` parking a ticket its own check finds published.
    """
    name = action.ticket
    if name is None or world.scripted(name):
        return True
    if action.kind in {Kind.CHECK_UNREADABLE, Kind.UPDATE_UNREADABLE}:
        return False
    state = model.recovered(before)
    ticket = state.of(name)
    if action.kind in {Kind.CHECK, Kind.CHECK_HALTED}:
        return ticket.tree is not model.Tree.NONE
    if action.kind in LANDS_A:
        done = model.held_by_note(ticket) or model.says_merged(ticket) or model.unrecorded(ticket)
        return done or not model.unchecked(ticket)
    if action.kind in UPDATES_A:
        ready = model.unmarked(state, name)
        return ready is None or model.checked_first(ready, name) is not None
    return True


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
        today = [
            move for move in model.moves(state) if taken_today(self.play.world, state, move[0])
        ]
        moves = sorted(today, key=lambda move: move[1] == state)
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


def test_the_real_drain_agrees_with_the_model_after_each_kind_of_kill(tmp_path: Path) -> None:
    # Why: the stateful run above takes few steps and reaches few kills. Each kill is taken here
    # where it first leaves a state of its own, on a landed ticket and on one not landed, then a
    # report and the killed command run again: what rules 5 and 8 read after a kill.
    assert {Kind(path[-1].rpartition(" ")[0]) for path in KILL_CUTS} == set(model.KILLS)
    elsewhere = 0
    for at, path in enumerate(KILL_CUTS):
        kill, _, name = path[-1].rpartition(" ")
        then = [*path, "drain.py report", f"{model.KILLS[Kind(kill)].value} {name}"]
        # Why: a replay below that takes the same steps checks them; this one would repeat it.
        if any(other[: len(then)] == then for other in REPLAYED_AFTER_A_KILL):
            elsewhere += 1
            continue
        replay(tmp_path / str(at), then)
    assert elsewhere == len(REPLAYED_AFTER_A_KILL), "each replay listed takes a kill's steps"


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


# Why: a path played on a new repository reads back the same at each step, so a step another
# replay of this run compared after the same steps is not compared again; most replays begin
# with the same few steps, and a comparison costs as much as the drain command it follows.
COMPARED: set[tuple[str, ...]] = set()


def replay(tmp_path: Path, path: list[str]) -> Play:
    """``path`` played on the model and a repository, compared at its end and after each step new to this run."""  # noqa: E501
    play = Play(tmp_path)
    for at, name in enumerate(path, 1):
        steps = (*play.path, name)
        play.named(name, compare=at == len(path) or steps not in COMPARED)
        COMPARED.add(steps)
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
        model.Outcome.CLOSED, hold=False, text=0, fresh=True, attempts=1, passed=True
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
    play.named("a kill before the discard stops drain.py close --outcome parked c")
    finished = play.named("drain.py clean --all")
    assert f"kept: the work tree stays at {tree}: git could not stash" in finished
    assert "discarding" not in drain.queue_notes.read_note(play.world.root, BRANCH[Name.C])
    play.named("the owner removes the stale index lock of c", compare=False)
    play.named("a kill before the discard stops drain.py close --outcome parked c")
    assert (tree / DRAFT).read_text(encoding="utf-8") == "half done\n", "the note came first"
    play.named("a kill after the stash stops drain.py close --outcome parked c")
    assert plan_of(play)[BRANCH[Name.C]].state == "held", "the note says parked"
    assert git(play.world.root, "show", f"stash@{{0}}^3:{DRAFT}") == "half done"
    play.cut(Kind.KILL_LAND_AFTER_DISCARD, Name.C)
    assert not tree.exists(), "land finishes the close's discard before a second kill"
    held = f"land: {REF[Name.C]} is held and is not landed: "
    assert play.named("drain.py land c").startswith(held)
    assert plan_of(play)[BRANCH[Name.C]].state == "held"


ROLE_A = "a role of the pipeline run commits in a"
NOT_YET = (
    "drain.py does not run the publish check yet: the script change that docs/model/drain.md"
    " names under 'What the next script change must do' turns this on"
)
waits_for_the_script = pytest.mark.xfail(reason=NOT_YET, strict=True)


def test_the_agent_that_commits_before_the_check_commits_on_a_branch_a_role_committed_on(
    tmp_path: Path,
) -> None:
    path = COUNTEREXAMPLES[UNTOUCHED] or []
    assert broken_by(path) == {UNTOUCHED}
    before = model.INITIAL
    for name in path[:-1]:
        before = next(after for action, after in model.moves(before) if action.name == name)
    last = next(action for action, _ in model.moves(before) if action.name == path[-1])
    assert not model.in_order(before, last), "the skill checks before the agent's first commit"
    replay(tmp_path, path)


@waits_for_the_script
def test_land_refuses_a_branch_a_role_committed_on_whose_start_no_check_passed(
    tmp_path: Path,
) -> None:
    landed = [*(COUNTEREXAMPLES[UNTOUCHED] or []), "drain.py land a"]
    assert broken_by(landed) == set()
    play = replay(tmp_path, landed)
    assert play.world.code == 3
    assert "check" in play.world.printed, "land says to run the check first"
    subjects = git(play.world.root, "log", "--format=%s", "main").splitlines()
    assert PUBLISHED not in subjects, "the role's commit is not on the default branch"


def test_the_check_parks_a_branch_a_role_committed_on_and_holds_it_until_its_text_changes(
    tmp_path: Path,
) -> None:
    path = ["drain.py open a", ROLE_A, CHECK_A]
    assert broken_by(path) == set()
    play = replay(tmp_path, path)
    note = model.Note(model.Outcome.CLOSED, hold=True, text=0, fresh=True)
    assert play.state.of(Name.A).note == note, "parked, held, never retried"
    reasons = note_of(play)["reasons"]
    assert reasons[0].startswith("a role published: `feature/1-alpha` moved"), reasons
    assert play.named("drain.py land a").startswith(f"land: {REF[Name.A]} is held")


@waits_for_the_script
def test_check_records_the_start_and_marks_it_passed_in_the_note(tmp_path: Path) -> None:
    play = replay(tmp_path, ["drain.py open a"])
    opened = json.loads(play.world.printed)
    note = note_of(play)
    assert note["start"] == opened["start"], "open records the start and prints it"
    assert "passed" not in note
    assert play.named(CHECK_A).startswith(publish_check.GREEN)
    assert play.world.code == 0
    assert note_of(play)["passed"] is True


@waits_for_the_script
def test_a_check_after_a_run_that_died_makes_the_tree_and_parks_a_branch_a_role_committed_on(
    tmp_path: Path,
) -> None:
    path = ["drain.py open a", ROLE_A, "drain.py clean --all", CHECK_A]
    assert broken_by(path) == set()
    play = replay(tmp_path, path)
    assert play.world.code == 1
    assert play.state.of(Name.A).note == model.Note(
        model.Outcome.CLOSED, hold=True, text=0, fresh=True
    )


@waits_for_the_script
def test_update_checks_a_start_no_check_passed_and_parks_a_branch_a_role_committed_on(
    tmp_path: Path,
) -> None:
    path = ["drain.py open a", ROLE_A, "the owner edits ticket c", "drain.py update a"]
    assert broken_by(path) == set()
    play = replay(tmp_path, path)
    assert model.held_by_note(play.state.of(Name.A)), "held until its text changes"
    assert play.state.of(Name.A).branch is Branch.DRAFT, "update did not rebase it"


EDIT_C = "the owner edits ticket c"
UPDATE_A = "drain.py update a"
REJUDGED = [*JUDGED_A, EDIT_C, UPDATE_A]
HALTED_A = "drain.py check finds only a rebase left unfinished in a"
UNREAD_A = "drain.py check cannot read the present state of a"
UPDATE_UNREAD_A = "the check of drain.py update cannot read the present state of a"


def walked(path: list[str]) -> list[model.State]:
    """The model's state after each step of ``path``."""
    states = [model.INITIAL]
    for name in path:
        states.append(
            next(after for action, after in model.moves(states[-1]) if action.name == name)
        )
    return states[1:]


def test_a_moved_update_records_a_new_start_and_its_rejudge_lands_once_checked_again(
    tmp_path: Path,
) -> None:
    path = [*REJUDGED, CHECK_A, "the agent commits the verdict record of a", "drain.py land a"]
    assert {rule for at in range(1, len(path) + 1) for rule in broken_by(path[:at])} == set()
    updated = walked(path)[len(REJUDGED) - 1]
    assert model.unchecked(updated.of(Name.A)), "the start is the rebased head, unpassed"
    assert model.landing(updated, Name.A) is None, "land refuses it until the check"
    play = replay(tmp_path, path)
    assert play.world.printed.endswith(f"closed: {REF[Name.A]}: merged\n")


def test_a_check_that_finds_only_an_unfinished_rebase_passes_the_start_and_the_ticket_waits(
    tmp_path: Path,
) -> None:
    path = [*REJUDGED, HALTED_A, UPDATE_A, "drain.py land a"]
    assert {rule for at in range(1, len(path) + 1) for rule in broken_by(path[:at])} == set()
    play = replay(tmp_path, path[:-2])
    assert play.state.of(Name.A).note == model.Note(
        model.Outcome.CLOSED, text=0, fresh=True, attempts=1, passed=True
    ), "waits to be retried, its start passed"
    assert note_of(play)["reasons"][0] == HALTED_WHY
    assert play.named(UPDATE_A) == '{"moved": false, "conflicts": []}\n', "no check runs again"
    play.named(path[-1])


@waits_for_the_script
def test_a_check_that_cannot_read_leaves_the_start_unpassed_and_the_retrys_update_checks_it(
    tmp_path: Path,
) -> None:
    path = ["drain.py open a", ROLE_A, UNREAD_A, "the owner edits ticket a", UPDATE_A]
    assert {rule for at in range(1, len(path) + 1) for rule in broken_by(path[:at])} == set()
    states = walked(path)
    assert states[2].of(Name.A).note == model.Note(
        model.Outcome.CLOSED, text=0, fresh=True, attempts=1
    ), "the ticket waits to be retried, its start unpassed"
    assert model.held_by_note(states[-1].of(Name.A)), "the retry's update parks it"
    play = replay(tmp_path, path)
    assert play.state.of(Name.A).branch is Branch.DRAFT


@waits_for_the_script
def test_update_is_refused_when_its_check_cannot_read_the_present_state(tmp_path: Path) -> None:
    path = ["drain.py open a", ROLE_A, EDIT_C, UPDATE_UNREAD_A]
    assert broken_by(path) == set()
    play = replay(tmp_path, path)
    assert play.world.code == 3
    assert play.state.of(Name.A).branch is Branch.DRAFT


UPGRADE = "an upgrade makes the preflight ask the Flag: line before deployment"
LINE_A = "the owner lands the Flag: line of a"


def test_the_upgrade_refuses_a_ticket_with_no_flag_line_and_its_line_releases_the_cascade(
    tmp_path: Path,
) -> None:
    path = [UPGRADE, "drain.py open a", LINE_A, "drain.py open a"]
    assert {rule for at in range(1, len(path) + 1) for rule in broken_by(path[:at])} == set()
    play = replay(tmp_path, path[:2])
    assert play.world.code == 1
    assert play.world.printed.startswith(f"open: {REF[Name.A]} is not started: it is refused; ")
    assert "lacks `Flag:`" in play.world.printed, "the preflight names the line"
    assert model.classify(play.state)[Name.B] is model.Status.REFUSED, "b waits on a refused a"
    play.named(path[2])
    statuses = model.classify(play.state)
    assert (statuses[Name.A], statuses[Name.B]) == (model.Status.BUILDABLE, model.Status.WAITING)
    assert play.named(path[3]).startswith("{"), "the plan starts a once its line has landed"
    assert play.world.code == 0


def built_and_landed(name: Name) -> list[str]:
    return [
        f"drain.py open {name.value}",
        f"drain.py check {name.value}",
        f"the agent commits {name.value}",
        f"the agent commits the verdict record of {name.value}",
        f"drain.py land {name.value}",
    ]


def test_a_ticket_waiting_on_a_split_ticket_the_owner_closed_starts_before_its_parts(
    tmp_path: Path,
) -> None:
    path = COUNTEREXAMPLES[PARTS_FIRST] or []
    assert broken_by(path) == {PARTS_FIRST}
    play = replay(tmp_path, path[:-1])
    drafted = plan_of(play)
    beta = drafted[BRANCH[Name.B]]
    assert (beta.state, beta.waits_on) == ("buildable", ()), "a closed blocker is met"
    parts = [drafted[BRANCH[part]].state for part in model.PARTS[Name.A]]
    assert parts == ["buildable", "waiting"], "no part of it is built"
    assert play.named(path[-1]).startswith("{"), "the drain opens b"
    assert play.world.code == 0
    assert play.world.tree(Name.B).is_dir()


def test_a_ticket_waiting_on_a_split_ticket_is_refused_and_stays_refused_once_its_parts_land(
    tmp_path: Path,
) -> None:
    path = COUNTEREXAMPLES[PARTS_AWAITED] or []
    assert broken_by(path) == {PARTS_AWAITED}
    landed = [*path, PARK_C, *built_and_landed(Name.D), *built_and_landed(Name.E)]
    assert broken_by(landed) == {PARTS_AWAITED}
    play = replay(tmp_path, landed)
    assert all(play.state.of(part).merged for part in model.PARTS[Name.A]), "both parts landed"
    parts = ", ".join(REF[part] for part in model.PARTS[Name.A])
    why = (
        f"blocked: waits on {REF[Name.A]}, which this drain cannot merge (held):"
        f" too large; split into {parts}"
    )
    beta = plan_of(play)[BRANCH[Name.B]]
    assert (beta.state, beta.why) == ("refused", (why,)), "b waits on the split a, still held"


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


RECORDED = (
    f"recorded: {BRANCH[Name.A]}: merged, and the run that landed it stopped before it said so\n"
)
UNSAID = f"drain: {REF[Name.A]} merged, and a merge is not unsaid by closing it as parked\n"


def listed_under(report: str, heading: str) -> str:
    """The rows of the report's section ``heading``."""
    return report.split(f"## {heading}\n")[1].split("\n## ", maxsplit=1)[0]


def note_of(play: Play) -> dict[str, object]:
    return drain.queue_notes.read_note(play.world.root, BRANCH[Name.A])


def test_a_landing_killed_before_its_note_is_recorded_by_the_next_drains_clean_all(
    tmp_path: Path,
) -> None:
    assert broken_by(LAND_KILLED) == set()
    play = replay(tmp_path, LAND_KILLED)
    assert "merged: `feature/1-alpha` into `main`" in play.world.printed
    tree = play.world.tree(Name.A)
    assert tree.exists(), "the note comes before the discard, so the tree is still there"
    swept = f"{RECORDED}removed: {tree}: its branch is merged\ndrain: "
    assert play.named("drain.py clean --all").startswith(swept)
    note = note_of(play)
    assert (note["outcome"], note["reported"]) == ("merged", False)
    assert BRANCH[Name.A] not in plan_of(play)
    report = play.named("drain.py report")
    assert "Alpha" in listed_under(report, "Merged"), "the next report says it merged"


def test_a_merge_no_note_says_is_recorded_by_a_close_as_parked_and_the_close_refused(
    tmp_path: Path,
) -> None:
    path = [*LAND_KILLED, PARK_A]
    assert broken_by(path) == set()
    play = replay(tmp_path, path)
    assert (play.world.code, play.world.printed) == (2, RECORDED + UNSAID)
    note = note_of(play)
    assert (note["outcome"], note["reported"]) == ("merged", False)
    assert not play.world.tree(Name.A).exists(), "the record finishes the landing's discard"
    report = play.named("drain.py report")
    assert "Alpha" in listed_under(report, "Merged")
    assert "Alpha" not in listed_under(report, "Parked")
    assert "Alpha" not in play.named("drain.py report"), "a second report does not print it"


def test_an_update_killed_twice_before_it_drops_the_record_keeps_its_mark(
    tmp_path: Path,
) -> None:
    assert broken_by(KILLED_TWICE) == set()
    play = replay(tmp_path, [*KILLED_TWICE, "drain.py clean --all"])
    assert play.state.of(Name.A).branch is Branch.STALE
    assert note_of(play)["rebasing"], "the mark stays until the record is dropped"
    entry = plan_of(play)[BRANCH[Name.A]]
    assert (entry.state, entry.why) == ("built", (drain.BEHIND,)), "the plan sends it to update"
    assert play.named("drain.py update a") == '{"moved": true, "conflicts": []}\n'
    assert play.world.code == 0
    assert play.state.of(Name.A).branch is Branch.DRAFT, "update drops the record no review wrote"
    assert "rebasing" not in note_of(play)
    assert plan_of(play)[BRANCH[Name.A]].why == ()


def test_update_records_a_landed_branchs_merge_and_does_not_rebase_it(tmp_path: Path) -> None:
    path = [*LAND_KILLED, "drain.py update a"]
    assert broken_by(path) == set()
    play = replay(tmp_path, path)
    refused = f"{RECORDED}update: {REF[Name.A]} is merged and is not rebased\n"
    assert (play.world.printed, play.world.code) == (refused, 1)
    assert BRANCH[Name.A] in drain.route.merged_features(play.world.root, "main")
    assert play.named("drain.py land a") == f"closed: {REF[Name.A]}: merged\n"
    assert "Alpha" in listed_under(play.named("drain.py report"), "Merged")


def test_a_report_records_a_merge_no_note_says_and_lists_it_as_merged_not_as_its_kept_close(
    tmp_path: Path,
) -> None:
    path = [*PARKED_THEN_LANDED, "drain.py report"]
    assert broken_by(path) == set()
    play = replay(tmp_path, path)
    assert play.world.printed.startswith(RECORDED)
    assert "Alpha" in listed_under(play.world.printed, "Merged")
    assert "Alpha" not in listed_under(play.world.printed, "Parked")
    assert not play.world.tree(Name.A).exists(), "the record discards the tree, as a landing does"
    assert "Alpha" not in play.named("drain.py report"), "a second report does not print it"


def test_a_refused_landing_killed_after_its_discard_and_run_again_counts_its_retry_once(
    tmp_path: Path,
) -> None:
    assert {rule for at in range(5, len(RETRIED) + 1) for rule in broken_by(RETRIED[:at])} == set()
    play = replay(tmp_path, RETRIED[:-2])
    note = note_of(play)
    assert (note["attempts"], note["discarding"], play.world.tree(Name.A).exists()) == (
        1,
        True,
        False,
    )
    assert "Alpha" in listed_under(play.named(RETRIED[-2]), "Waits for you")
    assert play.named(RETRIED[-1]).endswith(f"closed: {REF[Name.A]}: waits\n")
    note = note_of(play)
    assert (note["attempts"], note["hold"], "discarding" in note) == (1, False, False), (
        "merge.py refused it once a run, and the note says once"
    )


def test_a_close_after_a_cut_off_fast_forward_puts_it_back_first_and_holds_the_ticket_on_its_text(
    tmp_path: Path,
) -> None:
    # Why: the path that broke 6a and 8 before every command recovered first; they hold on it now.
    path = [*JUDGED_A, CUT_OFF, PARK_A, "drain.py land a", "drain.py land a"]
    assert {rule for at in range(1, len(path) + 1) for rule in broken_by(path[:at])} == set()
    play = replay(tmp_path, path[:-2])
    assert play.world.printed.endswith(
        "merge: a run that stopped was moving `main`; its work tree is put back\n"
        f"closed: {REF[Name.A]}: parked\n"
    ), "the work tree is put back before the close reads the ticket"
    assert note_of(play)["digest"] == digest(TEXTS[REF[Name.A]]), "the hold is on the owner's text"
    held = f"land: {REF[Name.A]} is held and is not landed: "
    for name in path[-2:]:
        assert held in play.named(name), name
        assert play.world.code == 2
    assert plan_of(play)[BRANCH[Name.A]].state == "held", "the owner edited nothing"
    assert BRANCH[Name.A] not in drain.route.merged_features(play.world.root, "main")


# --- the lease ---------------------------------------------------------------------------------

(
    LIVE_SPARED,
    TOLD_APART,
    STOPPED_FREE,
    GONE_FREE,
    OWN_LEASE,
    PRINTED_ONCE,
    LANDING_SPARED,
    RECORD_KEPT,
    LANDING_UNTOUCHED,
    HOLD_KEPT,
    SEEN_ALIVE,
    UNSEEN_ENDED,
    CLOSED_BY_OWNER,
    ONE_MERGE,
) = model.LEASE_RULES
APART = "each drain runs in a session of its own"
UNDER_ONE = "one long-lived process runs both drains"
D1_STARTS = ["drain 1: drain.py clean --all", "drain 1: drain.py open a"]
TAKEN_UNDER_ONE = [UNDER_ONE, *D1_STARTS, "drain 2: drain.py clean --all"]
REPORTED_AWAY = [APART, *D1_STARTS, "drain 2: drain.py report", "drain 2: drain.py clean --all"]
PROGRAM = "drain 1: its agent starts a program built in its tree"
LANDS = "drain 1: drain.py land a, up to the merge.py it runs"
MERGES = "drain 1: its merge.py lands the merge"
CLOSES = "drain 1: drain.py land a, the close after the merge"
PARKS = "drain.py close --outcome parked a"
REPORTED_UNDER_ONE = [UNDER_ONE, *D1_STARTS, "drain 2: drain.py report"]
TAKEN_OVER = [APART, *D1_STARTS, LANDS, "drain 2: drain.py clean --all --take-over"]
TAKEN_OVER += ["drain 1: a kill stops its land and its merge.py before the merge"]
ORPHANED = f"drain 1: {Step.ORPHANED.value}"
ORPHAN_MERGES = f"drain 1: {Step.ORPHAN_MERGES.value}"
ORPHAN_ENDS = f"drain 1: {Step.ORPHAN_ENDS.value}"
KILLED_AT_START = f"drain 1: {Step.KILLED_AT_START.value}"
DOOMED_STOPS = Step.DOOMED_STOPS.value
LOCKED_OUT = f"drain 1: {Step.LOCKED_OUT.value}"
LEFT_RUNNING = [APART, *D1_STARTS, LANDS, ORPHANED]
AT_START = [APART, *D1_STARTS, LANDS, KILLED_AT_START]
STOPPED = [
    APART,
    "drain 1: drain.py clean --all",
    "drain 1: stops, its session still open",
    "drain 2: drain.py clean --all",
]
LEASE_COUNTEREXAMPLES: dict[str, list[str] | None] = {
    # Why: 9 breaks where the drain's docstring accepts it: "two drains run by one process on one
    # repository, with drain 1 between commands and nothing running from its tree, cannot be told
    # apart". 9a, which allows only that, holds there.
    LIVE_SPARED: TAKEN_UNDER_ONE,
    # Why: 9a breaks only after the owner's `--take-over` while the first drain is alive, which
    # the docstring accepts: "a landing that drain runs keeps its tree while it runs, and the next
    # `clean --all` takes the tree once that landing has ended, its work all committed". The
    # take-over leaves the tree of the first drain's live landing; once that landing is killed,
    # here before its merge, the drain that took over takes the tree on its next `clean --all`.
    TOLD_APART: [*TAKEN_OVER, "drain 2: drain.py clean --all"],
    # Why: 9b breaks where the docstring accepts it: "A drain is alive while a process of its own
    # is", so one that stopped with its session open blocks the next until the owner says
    # `--take-over`. 9c, which asks only that a drain whose processes are gone block nothing,
    # holds.
    STOPPED_FREE: STOPPED,
    GONE_FREE: None,
    # Why: 9d breaks only where the docstring accepts it: under one process a report cannot tell
    # whose drain it ends. In sessions of their own it ends only its own drain's lease.
    OWN_LEASE: [UNDER_ONE, D1_STARTS[0], "drain 2: drain.py report"],
    PRINTED_ONCE: None,
    # Why: 9f, 9h, 9i, 9j, 9l and 9m broke where `land` was killed alone and its merge.py ran on
    # with no lock any command read. merge.py now holds its own lock on the branch from before its
    # probe to its end, and every command reads it, so the merge past its probe is seen; one
    # before its probe finds its land's private lock free and stops, merging nothing.
    LANDING_SPARED: None,
    # Why: no step meets it. No command but the landing records its merge while it is alive, so
    # its close never meets one recorded; the rule stays so that a change letting one in shows.
    RECORD_KEPT: None,
    LANDING_UNTOUCHED: None,
    HOLD_KEPT: None,
    SEEN_ALIVE: None,
    # Why: what `drain.landing_holder` reads are the locks of a `land` and of a merge.py alive,
    # which a process either left behind does not hold; the replay of a check, and of a shell,
    # that leaves one shows it on real drains.
    UNSEEN_ENDED: None,
    CLOSED_BY_OWNER: None,
    ONE_MERGE: None,
}


@functools.cache
def lease_search() -> tuple[dict[str, list[str]], frozenset[str]]:
    """Each lease rule's shortest break, read off the lease graph; every step taken."""
    _, edges = model.lease_graph()
    came: dict[model.Lease, tuple[model.Lease, str] | None] = {model.LEASE_INITIAL: None}
    breaks: dict[str, list[str]] = {}
    for before, said, after in edges:
        for name, rule_holds in model.LEASE_RULES.items():
            if name not in breaks and not rule_holds(before, said, after):
                path, at = [said], came[before]
                while at is not None:
                    path.append(at[1])
                    at = came[at[0]]
                breaks[name] = path[::-1]
        came.setdefault(after, (before, said))
    return breaks, frozenset(said for _, said, _ in edges)


def test_each_lease_rule_holds_on_every_step_or_breaks_on_its_recorded_path() -> None:
    # Why: one test and not one a rule, so a run that spreads the tests over processes builds the
    # lease graph in fewer of them; a red run names every rule whose break moved.
    breaks = lease_search()[0]
    moved = {
        name: breaks.get(name)
        for name in model.LEASE_RULES
        if breaks.get(name) != LEASE_COUNTEREXAMPLES[name]
    }
    assert not moved, "\n".join(
        f"{name}: shortest break is\n" + "\n".join(path or []) for name, path in moved.items()
    )


def test_every_lease_step_is_taken_and_every_lease_rule_reads_as_one_sentence() -> None:
    taken = lease_search()[1]
    assert {said.rpartition(": ")[2] for said in taken} == {step.value for step in model.Step}
    for name, rule_holds in model.LEASE_RULES.items():
        sentence = (rule_holds.__doc__ or "").strip()
        assert sentence.endswith("."), name
        assert ". " not in sentence, name


def lease_broken_by(path: list[str]) -> set[str]:
    """The lease rules the last step of ``path`` breaks in the model."""
    lease = before = model.LEASE_INITIAL
    for said in path:
        before = lease
        lease = dict(model.lease_moves(lease))[said]
    return {name for name, holds in model.LEASE_RULES.items() if not holds(before, path[-1], lease)}


class LeasePlay:
    """The lease model and two real drains taking the same steps, compared after each one."""

    def __init__(self, directory: Path, check: str = "true", before_merge: str = "") -> None:
        self.world = LeaseWorld(directory, check, before_merge)
        self.lease = model.LEASE_INITIAL
        self.path: list[str] = []

    def named(self, said: str) -> str:
        """Take the step the model calls ``said``, compare, and return what the command printed."""
        self.lease = dict(model.lease_moves(self.lease))[said]
        self.path.append(said)
        self.world.world.printed = ""
        self.world.perform(said)
        seen = self.world.seen(self.lease)
        lines = ["path:", *(f"  {step}" for step in self.path), self.world.world.printed]
        assert seen == self.lease, "\n".join([f"seen {seen}", f"not {self.lease}", *lines])
        # Why: the model's merge lock is held only by a merge past its probe whose land is gone;
        # a land's own merge here waits before the lock, or has ended.
        merging = lock_held(self.world.merge_lock)
        assert merging == (self.lease.orphan is not None), "\n".join(["merge lock", *lines])
        return self.world.world.printed


@pytest.fixture
def two_drains(tmp_path: Path) -> Iterator[LeasePlay]:
    play = LeasePlay(tmp_path)
    try:
        yield play
    finally:
        play.world.close()


def test_one_process_running_both_drains_lets_the_second_take_the_first_tree(
    two_drains: LeasePlay,
) -> None:
    path = [*TAKEN_UNDER_ONE[:3], "drain 1: its agent leaves a draft in its tree"]
    path.append(TAKEN_UNDER_ONE[3])
    assert lease_broken_by(path) == {LIVE_SPARED}
    for said in path:
        printed = two_drains.named(said)
    tree = two_drains.world.world.tree(Name.A)
    assert f"removed: {tree}: the drain that made it was interrupted" in printed
    stash = git(two_drains.world.world.root, "stash", "list", "--format=%gs")
    assert stash == f"On {BRANCH[Name.A]}: {BRANCH[Name.A]}: interrupted run", "the draft is kept"


def test_one_process_running_both_drains_spares_a_tree_a_program_runs_from(
    two_drains: LeasePlay,
) -> None:
    path = [*TAKEN_UNDER_ONE[:3], PROGRAM, TAKEN_UNDER_ONE[3]]
    assert lease_broken_by(path) == set()
    for said in path:
        printed = two_drains.named(said)
    assert printed.startswith("drain: another drain is running on this repository (process ")


def test_a_second_drains_report_leaves_the_first_drains_lease_and_its_clean_all_is_refused(
    two_drains: LeasePlay,
) -> None:
    path = [*REPORTED_AWAY[:3], PROGRAM, *REPORTED_AWAY[3:]]
    assert lease_broken_by(path) == set()
    for said in path[:-2]:
        two_drains.named(said)
    session = two_drains.world.sessions[model.Drain.ONE].pid
    reported = two_drains.named(path[-2])
    assert reported.endswith(
        f"report: the drain lease stays: another drain is running (process {session});"
        f" {drain.LANDING_MAY_RUN}, so a merge no note says is left alone\n"
    )
    refused = two_drains.named(path[-1])
    assert refused.startswith(
        f"drain: another drain is running on this repository (process {session})"
    )
    assert two_drains.world.world.code == 2, "the skill says: stop and report it"
    program = two_drains.world.program
    assert program is not None
    assert program.poll() is None, "the first drain's program runs on"
    own = two_drains.named("drain 1: drain.py report --drain with its token")
    assert "lease stays" not in own, "the first drain's own report ends its lease"


def test_under_one_process_a_program_refuses_clean_all_once_a_report_ended_the_lease(
    two_drains: LeasePlay,
) -> None:
    path = [*REPORTED_UNDER_ONE, PROGRAM, TAKEN_UNDER_ONE[-1]]
    assert lease_broken_by(path[:4]) == {OWN_LEASE}
    assert lease_broken_by(path) == set()
    for said in path[:4]:
        reported = two_drains.named(said)
    assert "lease stays" not in reported, "under one process the report ends drain 1's lease"
    two_drains.named(PROGRAM)
    program = two_drains.world.program
    assert program is not None
    refused = two_drains.named(path[-1])
    assert refused.startswith(
        "drain: another drain is running on this repository"
        f" (process {program.pid} runs in its work trees)"
    )
    assert two_drains.world.world.code == 2
    assert program.poll() is None, "the program runs on"
    assert two_drains.world.world.tree(Name.A).exists()


def test_another_drains_report_and_clean_leave_a_live_landing_alone_and_it_is_printed_once(
    two_drains: LeasePlay,
) -> None:
    path = [APART, *D1_STARTS, LANDS, MERGES, "drain 2: drain.py report", "drain 2: drain.py clean"]
    path += [CLOSES, "drain 1: drain.py report --drain with its token"]
    assert {name for at in range(1, len(path) + 1) for name in lease_broken_by(path[:at])} == set()
    for said in path[:5]:
        two_drains.named(said)
    session = two_drains.world.sessions[model.Drain.ONE].pid
    tree = two_drains.world.world.tree(Name.A)
    reported = two_drains.named(path[5])
    assert reported.endswith(
        f"another drain is running (process {session}); {drain.LANDING_MAY_RUN},"
        " so a merge no note says is left alone\n"
    )
    assert "Alpha" not in listed_under(reported, "Merged")
    cleaned = two_drains.named(path[6])
    assert f"clean: {drain.LANDING_MAY_RUN} (process {session})" in cleaned
    assert f"kept: {tree}: `{BRANCH[Name.A]}` has a landing running" in cleaned
    assert two_drains.named(path[7]).endswith(f"closed: {REF[Name.A]}: merged\n")
    own = two_drains.named(path[8])
    assert "Alpha" in listed_under(own, "Merged")
    assert "lease stays" not in own


def test_one_process_running_both_drains_leaves_a_live_landing_alone_and_it_is_printed_once(
    two_drains: LeasePlay,
) -> None:
    path = [*TAKEN_UNDER_ONE[:3], LANDS, MERGES, TAKEN_UNDER_ONE[3], "drain 2: drain.py clean"]
    path += ["drain 2: drain.py report", CLOSES, "drain 1: drain.py report --drain with its token"]
    path.append("drain 2: drain.py report --drain with its token")
    assert {name for at in range(1, len(path) + 1) for name in lease_broken_by(path[:at])} == set()
    for said in path[:5]:
        two_drains.named(said)
    tree = two_drains.world.world.tree(Name.A)
    kept = f"kept: {tree}: `{BRANCH[Name.A]}` has a landing running\n"
    assert kept in two_drains.named(path[5]), "the second drain's clean --all keeps the tree"
    assert kept in two_drains.named(path[6])
    reported = two_drains.named(path[7])
    assert reported.startswith(
        f"report: a landing of `{BRANCH[Name.A]}` is running in another command, so it is left"
        " alone\n"
    )
    assert "Alpha" not in listed_under(reported, "Merged")
    closed = two_drains.named(path[8])
    assert f"merged: `{BRANCH[Name.A]}` into `main`" in closed
    assert closed.endswith(f"closed: {REF[Name.A]}: merged\n"), "the landing records its merge"
    assert "Alpha" in listed_under(two_drains.named(path[9]), "Merged")
    assert "Alpha" not in two_drains.named(path[10]), "the merge is printed once"


def test_a_take_over_leaves_a_live_landing_and_takes_its_tree_once_the_landing_is_killed(
    two_drains: LeasePlay,
) -> None:
    path = LEASE_COUNTEREXAMPLES[TOLD_APART] or []
    assert lease_broken_by(path) == {LIVE_SPARED, TOLD_APART}
    for said in path[:4]:
        two_drains.named(said)
    tree = two_drains.world.world.tree(Name.A)
    taken_over = two_drains.named(path[4])
    assert f"kept: {tree}: `{BRANCH[Name.A]}` has a landing running\n" in taken_over
    two_drains.named(path[5])
    assert tree.exists(), "the killed landing leaves its tree"
    taken = two_drains.named(path[6])
    assert f"removed: {tree}: the drain that made it was interrupted\n" in taken
    assert "recorded:" not in taken, "the kill came before the merge, so none is recorded"
    opened = two_drains.named("drain 2: drain.py open a")
    assert opened.startswith(f"open: {REF[Name.A]} is not started: it is built"), opened
    assert "Alpha" not in listed_under(two_drains.named("drain 2: drain.py report"), "Merged")


def read_back(play: LeasePlay) -> tuple[object, ...]:
    """The lease, the note, the default and branch tips and the work trees, as the world reads them now."""  # noqa: E501
    root = play.world.world.root
    note = drain.queue_notes.read_note(root, BRANCH[Name.A])
    tips = git(root, "rev-parse", "main", BRANCH[Name.A])
    return play.world.seen(play.lease), note, tips, drain.worktrees(root)


def lease_note(play: LeasePlay) -> dict[str, object]:
    return drain.queue_notes.read_note(play.world.world.root, BRANCH[Name.A])


def landing_runs(command: str) -> str:
    """What ``update``, ``close`` or ``split`` prints when a landing of the ticket is alive."""
    return (
        f"{command}: a landing of `{BRANCH[Name.A]}` is running in another command; nothing was"
        " done; wait for it to end, then run plan: the landing records the ticket's outcome"
        " itself\n"
    )


def merge_runs(command: str) -> str:
    """What ``update``, ``close`` or ``split`` prints when only a merge of the ticket is seen."""
    return (
        f"{command}: a merge of `{BRANCH[Name.A]}` is running in another command; nothing was"
        " done; wait for it to end, then run plan\n"
    )


LAND_MEETS_A_MERGE = (
    f"land: a merge of `{BRANCH[Name.A]}` is running in another command; run land again once it"
    " ends\n"
)
LAND_GONE = (
    "merge: the land that started this merge has stopped; nothing was merged; run land again\n"
)
LOCK_HELD = f"merge: a merge of `{BRANCH[Name.A]}` is already running; nothing was merged\n"


def refused_in_flight(play: LeasePlay, said: str) -> None:
    """Take ``said``, a command on the ticket of a live landing: exit 3, and nothing changed."""
    before = read_back(play)
    by_merge = model.seen_by(play.lease) == "merge"
    printed = play.named(said)
    command = "update" if said.endswith(Step.UPDATE.value) else "close"
    said_by = merge_runs if by_merge else landing_runs
    # Why: the feature skill reads exit 3 as a live landing, and 1 and 2 are `update`'s own.
    assert (play.world.world.code, printed) == (3, said_by(command)), said
    assert read_back(play) == before, f"{said}: nothing changed"


def plan_of_a(play: LeasePlay) -> list[tuple[str, tuple[str, ...]]]:
    """What the real ``plan`` says of the ticket: its state and why; nothing once it is resolved."""
    drafted = drain.plan(drain.Drain(play.world.world.root, None, model.CAP))
    return [(entry.state, entry.why) for entry in drafted.tickets if entry.branch == BRANCH[Name.A]]


def test_a_second_land_while_one_is_in_flight_is_refused_and_the_merge_printed_once(
    two_drains: LeasePlay,
) -> None:
    # Why: drain 1 again is its agent whose shell timed out on the first `land`.
    again = [f"{which.value}: {Step.LAND.value}" for which in model.Drain]
    path = [APART, *D1_STARTS, LANDS, *again, MERGES, *again, CLOSES]
    path.append("drain 1: drain.py report --drain with its token")
    assert {name for at in range(1, len(path) + 1) for name in lease_broken_by(path[:at])} == set()
    refused = (
        f"land: a landing of `{BRANCH[Name.A]}` is already running in another command; run land"
        " again once it ends\n"
    )
    for said in path[:-2]:
        alive = two_drains.lease.landing is not None
        before = read_back(two_drains) if alive else None
        start = time.monotonic()
        printed = two_drains.named(said)
        if alive and said in again:
            took = time.monotonic() - start
            assert read_back(two_drains) == before, f"{said}: the second land changed nothing"
            assert (two_drains.world.world.code, printed) == (2, refused), said
            assert took < LANDING_WAIT / 2, f"{said}: it does not wait for the landing to end"
    closed = two_drains.named(path[-2])
    assert closed.count(f"merged: `{BRANCH[Name.A]}` into `main`") == 1
    assert closed.endswith(f"closed: {REF[Name.A]}: merged\n"), "the first landing records it"
    merged = listed_under(two_drains.named(path[-1]), "Merged")
    assert merged.count("Alpha") == 1, "the next report prints the merge once"


def test_a_close_as_parked_while_the_merge_runs_is_refused_and_the_landing_merges_it(
    two_drains: LeasePlay,
) -> None:
    path = [APART, *D1_STARTS, LANDS, f"drain 1: {PARKS}", MERGES, CLOSES]
    path.append("drain 2: drain.py report")
    assert {name for at in range(1, len(path) + 1) for name in lease_broken_by(path[:at])} == set()
    for said in path[:4]:
        two_drains.named(said)
    refused_in_flight(two_drains, path[4])
    assert two_drains.named(path[5]) == "", "merge.py lands the merge"
    closed = two_drains.named(path[6])
    assert closed.count(f"merged: `{BRANCH[Name.A]}` into `main`") == 1
    assert closed.endswith(f"closed: {REF[Name.A]}: merged\n"), "the landing records its merge"
    note = lease_note(two_drains)
    assert (note["outcome"], note["hold"], note["reasons"]) == ("merged", False, [])
    reported = two_drains.named(path[7])
    assert listed_under(reported, "Merged").count("Alpha") == 1
    assert "Alpha" not in listed_under(reported, "Parked")
    assert plan_of_a(two_drains) == [], "the plan has the merged ticket no more"


def test_another_drains_close_as_parked_while_the_merge_runs_is_refused_and_reported_once(
    two_drains: LeasePlay,
) -> None:
    path = [APART, *D1_STARTS, LANDS, f"drain 2: {PARKS}", "drain 2: drain.py report", MERGES]
    path += [CLOSES, "drain 1: drain.py report --drain with its token"]
    assert {name for at in range(1, len(path) + 1) for name in lease_broken_by(path[:at])} == set()
    for said in path[:4]:
        two_drains.named(said)
    refused_in_flight(two_drains, path[4])
    reported = two_drains.named(path[5])
    assert "Alpha" not in listed_under(reported, "Parked"), "no close was written to report"
    assert "Alpha" not in listed_under(reported, "Merged")
    two_drains.named(path[6])
    assert two_drains.named(path[7]).endswith(f"closed: {REF[Name.A]}: merged\n")
    assert lease_note(two_drains)["reasons"] == [], "the landing recorded its merge itself"
    assert listed_under(two_drains.named(path[8]), "Merged").count("Alpha") == 1
    assert plan_of_a(two_drains) == []


def test_an_update_and_a_close_after_the_merge_are_refused_and_the_landing_records_it(
    two_drains: LeasePlay,
) -> None:
    path = [*TAKEN_UNDER_ONE[:3], LANDS, MERGES, "drain 2: drain.py update a", f"drain 1: {PARKS}"]
    path += [CLOSES, "drain 1: drain.py report --drain with its token"]
    assert {name for at in range(1, len(path) + 1) for name in lease_broken_by(path[:at])} == set()
    for said in path[:5]:
        two_drains.named(said)
    refused_in_flight(two_drains, path[5])
    refused_in_flight(two_drains, path[6])
    closed = two_drains.named(path[7])
    assert closed.count(f"merged: `{BRANCH[Name.A]}` into `main`") == 1
    assert closed.endswith(f"closed: {REF[Name.A]}: merged\n"), "the landing records its merge"
    assert lease_note(two_drains)["reasons"] == [], "no command said the landing stopped"
    assert listed_under(two_drains.named(path[8]), "Merged").count("Alpha") == 1
    assert plan_of_a(two_drains) == []


# Why: the lease graph has no mark of a close cut off before its discard, no `split`, and its
# default branch moves only by the merge. These replays take its steps up to a real landing in
# flight, then do what the model cannot.


def test_an_update_while_the_merge_runs_is_refused_and_the_landing_waits_as_behind(
    two_drains: LeasePlay,
) -> None:
    for said in [APART, *D1_STARTS]:
        two_drains.named(said)
    lease_world, world = two_drains.world, two_drains.world.world
    world.by_hand(Kind.EDIT, Name.C)
    lease_world.land(model.Drain.ONE)
    before = read_back(two_drains)
    updated = lease_world.command(model.Drain.ONE, "update", REF[Name.A])
    assert (world.code, updated) == (3, landing_runs("update"))
    assert read_back(two_drains) == before, "the branch is not rebased under the live landing"
    lease_world.merge_the_landing()
    lease_world.close_the_landing()
    assert world.code == 1
    assert "merge: owner\nbehind: the branch does not hold the tip of `main`" in world.printed
    assert world.printed.endswith(f"closed: {REF[Name.A]}: waits\n")
    note = lease_note(two_drains)
    assert (note["outcome"], note["hold"], note["attempts"]) == ("waits", False, 1), (
        "behind is a reason the drain resolves itself: retried, not held"
    )
    behind = (
        "behind the default branch: `update` it and judge it again before it lands, since its"
        " last review was of a tree the default branch has moved past"
    )
    assert plan_of_a(two_drains) == [("built", (behind,))], "the plan sends it back to update"


def test_a_split_while_the_merge_runs_is_refused_and_files_no_part(
    two_drains: LeasePlay, tmp_path: Path
) -> None:
    world = two_drains.world.world
    plan = plan_file(tmp_path, HALVES)
    issues = (world.root / REF[Name.A]).parent
    filed = sorted(path.name for path in issues.iterdir())
    for said in [APART, *D1_STARTS, LANDS]:
        two_drains.named(said)
    before = read_back(two_drains)
    split = two_drains.world.command(model.Drain.TWO, "split", REF[Name.A], "--plan", str(plan))
    assert (world.code, split) == (3, landing_runs("split"))
    assert read_back(two_drains) == before, "nothing changed"
    assert sorted(path.name for path in issues.iterdir()) == filed, "no part was filed"
    two_drains.named(MERGES)
    assert two_drains.named(CLOSES).endswith(f"closed: {REF[Name.A]}: merged\n")
    assert sorted(path.name for path in issues.iterdir()) == filed
    assert plan_of_a(two_drains) == []


def test_clean_all_under_a_live_landing_keeps_the_tree_and_mark_a_killed_close_left(
    two_drains: LeasePlay,
) -> None:
    for said in TAKEN_UNDER_ONE[:3]:
        two_drains.named(said)
    lease_world, world = two_drains.world, two_drains.world.world
    note_of_a = functools.partial(drain.queue_notes.read_note, world.root, BRANCH[Name.A])
    world.by_hand(Kind.COMMIT, Name.A)
    world.by_hand(Kind.RECORD, Name.A)
    world.by_hand(Kind.EDIT, Name.C)
    assert lease_world.land_killed_at_discard() == -signal.SIGKILL
    tree, marked = world.tree(Name.A), note_of_a()
    assert (marked["outcome"], marked["attempts"], marked["discarding"]) == ("waits", 1, True), (
        "merge.py refused it as behind, and the kill came between the note and the discard"
    )
    assert tree in drain.worktrees(world.root)
    lease_world.land_held(model.Drain.ONE)
    cleaned = lease_world.command(model.Drain.TWO, "clean", "--all")
    assert tree in drain.worktrees(world.root), "the live landing keeps its tree"
    assert note_of_a() == marked, "and its note keeps the mark"
    assert world.code == 0
    assert f"kept: {tree}: `{BRANCH[Name.A]}` has a landing running\n" in cleaned
    lease_world.close_the_landing()
    assert world.code == 1
    assert "merge: owner\nbehind: the branch does not hold the tip of `main`" in world.printed
    assert world.printed.endswith(f"closed: {REF[Name.A]}: waits\n")
    assert tree not in drain.worktrees(world.root), "the landing's own close removes the tree"
    note = note_of_a()
    assert (note["outcome"], note["hold"], "discarding" in note) == ("waits", False, False)
    assert note["attempts"] == 1, "the run the kill cut off is the same refusal, counted once"


TICKET_STEPS = frozenset({Step.OPEN, Step.UPDATE, Step.PARK, Step.LAND})


def landing_decision(
    before: model.Lease, drain_: model.Drain | None, step: Step | None
) -> tuple[object, ...] | None:
    """What a command on the ticket meets in a live landing, by which lock, or while a doomed merge starts; whether the landing's close meets a merge another command recorded, and whether a session's end ends it before its merge or after; ``None`` for any other step."""  # noqa: E501
    seen = model.seen_by(before)
    if step in TICKET_STEPS and seen is not None:
        # Why: each is refused, before the merge lands and after, with a line for each lock. Not
        # whose agent typed it, which none of these commands reads, nor the tree, which none of
        # them touches.
        return (step.value, seen, before.merge is Merge.NONE)
    if step in {Step.UPDATE, Step.PARK} and before.doomed:
        # Why: no lock is held, so each runs as with no landing, on a merge that has landed or not.
        return (step.value, "unseen", before.merge)
    if step is Step.LAND_REST:
        return (step.value, before.merge is Merge.UNSAID)
    if step is Step.DIE:
        ends = before.landing is drain_ or (bool(before.shared) and before.landing is not None)
        return (step.value, ends, ends and before.merge is Merge.NONE)
    return None


def decision(before: model.Lease, said: str) -> tuple[object, ...]:
    """The branch of ``drain.lease`` or ``drain.other_drain`` a step takes, whether a ``clean --all`` takes another drain's tree, whether a merge no note says is recorded or left alone, what a step meets in a live landing, and whether `open` meets a branch with commits; any other step is its own."""  # noqa: E501
    drain_, step = model.LEASE_STEPS.get(said, (None, None))
    if (found := landing_decision(before, drain_, step)) is not None:
        return found
    if step is Step.OPEN:
        return (step.value, before.built)
    if step not in model.CLEANS | model.REPORTS | {Step.CLEAN}:
        return (said.rpartition(": ")[2],)
    holder = before.holder
    branch = (
        "its own token"
        if step in model.OWN and holder is drain_
        else "taken over"
        if step is Step.TAKE_OVER
        else "a process of its chain"
        if holder not in {None, drain_} and model.of(before.alive, holder) and not before.shared
        else "a program in its tree"
        if before.running
        else "no lease"
        if holder is None
        else "gone"
    )
    landing = model.seen_by(before)
    if step in model.CLEANS:
        # Why: the merge lock is read where the landing lock is, after the lease is taken, so
        # each branch of the lease is taken under a live landing already.
        lease = landing if landing == "merge" else branch
        return "clean --all", lease, before.tree not in {None, drain_}, landing is not None
    said_by = "report" if step in model.REPORTS else "clean"
    if before.merge is Merge.UNSAID:
        return (
            said_by,
            "a merge no note says",
            branch in {"a process of its chain", "a program in its tree"},
            landing,
        )
    return said_by, branch


Steps = dict[model.Lease, list[tuple[str, model.Lease]]]


def nearest(out: Steps, start: model.Lease, untaken: set[tuple[object, ...]]) -> list[str]:
    """The fewest steps from ``start`` to a decision in ``untaken``; none when none is reached."""
    came: dict[model.Lease, tuple[model.Lease, str] | None] = {start: None}
    frontier = deque([start])
    while frontier:
        lease = frontier.popleft()
        for said, after in out.get(lease, []):
            if decision(lease, said) in untaken:
                leg = [said]
                while (back := came[lease]) is not None:
                    lease, step = back
                    leg.append(step)
                return leg[::-1]
            if after not in came:
                came[after] = (lease, said)
                frontier.append(after)
    return []


@functools.cache
def lease_tour() -> list[list[str]]:
    """Walks from the initial lease that take every decision: each goes on to the nearest one not yet taken."""  # noqa: E501
    _, edges = model.lease_graph()
    out: Steps = {}
    for before, said, after in edges:
        out.setdefault(before, []).append((said, after))
    untaken = {decision(before, said) for before, said, _ in edges}
    walks: list[list[str]] = []
    while untaken:
        at, walk = model.LEASE_INITIAL, []
        while leg := nearest(out, at, untaken):
            for said in leg:
                untaken.discard(decision(at, said))
                at = dict(out[at])[said]
            walk += leg
        walks.append(walk)
    return walks


def test_two_real_drains_agree_with_the_lease_model_on_every_decision(tmp_path: Path) -> None:
    # Why: every step of the lease graph is some 16,000 commands on real repositories, and each
    # landing a process. Each decision the code makes, for each kind of step, is under a hundred,
    # chained into a few walks; a walk lands one merge at most, so a landing ends most walks.
    for at, walk in enumerate(lease_tour()):
        play = LeasePlay(tmp_path / str(at))
        try:
            for said in walk:
                play.named(said)
        finally:
            play.world.close()


def test_the_runs_that_broke_on_a_land_killed_alone_now_meet_a_merge_that_is_seen() -> None:
    lease = model.LEASE_INITIAL
    for said in LEFT_RUNNING:
        lease = dict(model.lease_moves(lease))[said]
    assert model.seen_by(lease) == "merge"
    moves = dict(model.lease_moves(lease))
    for said in [
        f"drain 1: {PARKS}",
        f"drain 2: {PARKS}",
        "drain 2: drain.py update a",
        "drain 1: drain.py clean --all",
        "drain 2: drain.py clean --all --take-over",
        "drain 1: drain.py clean",
        "drain 2: drain.py report",
        LANDS,
        f"drain 2: {Step.LAND.value}",
    ]:
        after = moves[said]
        assert (after.tree, after.held, after.merge, after.orphan) == (
            lease.tree,
            lease.held,
            lease.merge,
            lease.orphan,
        ), said
        assert lease_broken_by([*LEFT_RUNNING, said]) == set(), said


# Why: the replays below kill a `land` by its pid alone: past its merge's probe, once its
# merge.py has judged and merged the branch in its scratch tree and is about to run the check,
# or before the probe, while its merge.py waits to start.


def merge_runs_seen(play: LeasePlay) -> int:
    """Assert the killed land's merge.py runs and the drain sees a merge running; its pid."""
    orphan = play.world.orphan
    assert orphan is not None, "a land was killed alone"
    assert running(orphan[1]), "its merge.py runs on"
    holder = drain.landing_holder(play.world.world.root, BRANCH[Name.A])
    assert holder == "merge", "only the merge lock is held"
    return orphan[1]


def merges_on_main(play: LeasePlay) -> int:
    subjects = git(play.world.world.root, "log", "--format=%s", "main").splitlines()
    return subjects.count(f"Merge branch '{BRANCH[Name.A]}'")


def recorded_once(play: LeasePlay, holder: str) -> None:
    """The merge no note says: the holder's report records and prints it, and no report again."""
    assert merges_on_main(play) == 1
    assert "outcome" not in lease_note(play), "no note says it merged"
    assert plan_of_a(play) == [], "the merge resolved the ticket on the default branch"
    reported = play.named(f"{holder}: drain.py report --drain with its token")
    assert reported.startswith(RECORDED), "the report records the merge"
    assert listed_under(reported, "Merged").count("Alpha") == 1
    assert "Alpha" not in listed_under(reported, "Parked")
    note = lease_note(play)
    assert (note["outcome"], note["hold"]) == ("merged", False)
    assert "Alpha" not in play.named(f"{holder}: drain.py report"), "printed once"


def test_a_close_update_or_split_while_a_killed_lands_merge_runs_is_refused_and_it_lands_once(
    two_drains: LeasePlay, tmp_path: Path
) -> None:
    world = two_drains.world.world
    plan = plan_file(tmp_path, HALVES)
    issues = (world.root / REF[Name.A]).parent
    filed = sorted(path.name for path in issues.iterdir())
    path = [*LEFT_RUNNING, f"drain 1: {PARKS}", f"drain 2: {PARKS}", "drain 2: drain.py update a"]
    path += [ORPHAN_MERGES]
    assert {name for at in range(1, len(path) + 1) for name in lease_broken_by(path[:at])} == set()
    for said in LEFT_RUNNING:
        two_drains.named(said)
    merge_runs_seen(two_drains)
    for said in path[len(LEFT_RUNNING) : -1]:
        refused_in_flight(two_drains, said)
    before = read_back(two_drains)
    split = two_drains.world.command(model.Drain.TWO, "split", REF[Name.A], "--plan", str(plan))
    assert (world.code, split) == (3, merge_runs("split"))
    assert read_back(two_drains) == before, "nothing changed"
    assert sorted(path.name for path in issues.iterdir()) == filed, "no part was filed"
    assert lease_note(two_drains).get("hold") is not True, "no close held the ticket"
    two_drains.named(path[-1])
    recorded_once(two_drains, "drain 1")


@pytest.mark.parametrize(
    "cleans",
    ["drain 1: drain.py clean --all", "drain 2: drain.py clean --all --take-over"],
    ids=["same-drain", "take-over"],
)
def test_clean_all_while_a_killed_lands_merge_runs_keeps_its_tree_and_it_lands_once(
    two_drains: LeasePlay, cleans: str
) -> None:
    path = [*LEFT_RUNNING, cleans, ORPHAN_MERGES]
    assert {name for at in range(1, len(path) + 1) for name in lease_broken_by(path[:at])} == set()
    for said in LEFT_RUNNING:
        two_drains.named(said)
    tree = two_drains.world.world.tree(Name.A)
    merge_runs_seen(two_drains)
    cleaned = two_drains.named(cleans)
    assert two_drains.world.world.code == 0
    assert f"kept: {tree}: `{BRANCH[Name.A]}` has a merge running\n" in cleaned
    assert tree.exists(), "the tree under the merge in flight is kept"
    two_drains.named(ORPHAN_MERGES)
    recorded_once(two_drains, cleans.partition(":")[0])
    assert not tree.exists(), "the report that records the merge removes the tree"


@pytest.mark.parametrize("again", [LANDS, f"drain 2: {Step.LAND.value}"], ids=["same", "other"])
def test_a_second_land_while_a_killed_lands_merge_runs_is_refused_and_closes_nothing(
    two_drains: LeasePlay, again: str
) -> None:
    path = [*LEFT_RUNNING, again, ORPHAN_MERGES]
    assert {name for at in range(1, len(path) + 1) for name in lease_broken_by(path[:at])} == set()
    for said in LEFT_RUNNING:
        two_drains.named(said)
    merge_runs_seen(two_drains)
    before = read_back(two_drains)
    refused = two_drains.named(again)
    assert (two_drains.world.world.code, refused) == (2, LAND_MEETS_A_MERGE)
    assert read_back(two_drains) == before, "the second land changed nothing"
    two_drains.named(ORPHAN_MERGES)
    recorded_once(two_drains, "drain 1")


def test_a_land_killed_before_its_merge_probes_it_merges_nothing_and_a_close_then_holds_it(
    two_drains: LeasePlay,
) -> None:
    path = [*AT_START, f"drain 2: {PARKS}", "drain 1: drain.py clean", DOOMED_STOPS]
    path.append("drain 2: drain.py report")
    assert {name for at in range(1, len(path) + 1) for name in lease_broken_by(path[:at])} == set()
    for said in AT_START:
        two_drains.named(said)
    world = two_drains.world.world
    tree = world.tree(Name.A)
    assert two_drains.world.doomed is not None, "its merge.py waits to start"
    assert drain.landing_holder(world.root, BRANCH[Name.A]) is None, "no lock is held"
    parked = two_drains.named(path[len(AT_START)])
    assert (world.code, parked) == (0, f"closed: {REF[Name.A]}: parked\n"), "as with no landing"
    assert not tree.exists()
    two_drains.named(path[len(AT_START) + 1])
    assert world.code == 0
    two_drains.named(DOOMED_STOPS)
    assert two_drains.world.stopped == (2, LAND_GONE), "its probe finds its land gone"
    assert merges_on_main(two_drains) == 0, "nothing was merged"
    note = lease_note(two_drains)
    assert (note["outcome"], note["hold"]) == ("parked", True)
    landed = two_drains.world.command(model.Drain.ONE, "land", REF[Name.A])
    assert world.code == 2
    assert f"land: {REF[Name.A]} is held and is not landed: " in landed
    assert merges_on_main(two_drains) == 0
    assert [state for state, _ in plan_of_a(two_drains)] == ["held"]
    reported = two_drains.named(path[-1])
    assert "Alpha" in listed_under(reported, "Parked")
    assert "Alpha" not in listed_under(reported, "Merged")


@pytest.mark.parametrize(
    "order",
    [[DOOMED_STOPS, MERGES], [MERGES, DOOMED_STOPS], [LOCKED_OUT]],
    ids=["dead-lands-merge-first", "live-lands-merge-first", "locked-out"],
)
def test_two_lands_across_the_instant_land_once_under_the_live_land_or_close_nothing(
    two_drains: LeasePlay, order: list[str]
) -> None:
    path = [*AT_START, LANDS, *order, CLOSES] if order != [LOCKED_OUT] else [*AT_START, LANDS]
    if order == [LOCKED_OUT]:
        path += [LOCKED_OUT, LANDS, MERGES, CLOSES]
    path.append("drain 1: drain.py report --drain with its token")
    assert {name for at in range(1, len(path) + 1) for name in lease_broken_by(path[:at])} == set()
    for said in [*AT_START, LANDS]:
        two_drains.named(said)
    world = two_drains.world.world
    assert drain.landing_holder(world.root, BRANCH[Name.A]) == "landing", "the second land runs"
    for said in order:
        two_drains.named(said)
        if said == DOOMED_STOPS:
            assert two_drains.world.stopped == (2, LAND_GONE), "the dead land's merge stops"
    if order == [LOCKED_OUT]:
        command = gatelib.entry_point(world.root, "merge")
        assert (world.code, world.printed) == (
            2,
            f"{LOCK_HELD}land: `{command}` stopped (exit 2); run land again to finish it\n",
        ), "the live land's merge gave up on the lock, and its land closed nothing"
        assert two_drains.world.stopped == (2, LAND_GONE)
        assert merges_on_main(two_drains) == 0
        assert "outcome" not in lease_note(two_drains)
        assert [state for state, _ in plan_of_a(two_drains)] == ["built"]
        two_drains.named(LANDS)
        two_drains.named(MERGES)
    closed = two_drains.named(CLOSES)
    assert closed.count(f"merged: `{BRANCH[Name.A]}` into `main`") == 1
    assert closed.endswith(f"closed: {REF[Name.A]}: merged\n"), "the live land records its merge"
    assert merges_on_main(two_drains) == 1
    assert lease_note(two_drains)["outcome"] == "merged"
    reported = two_drains.named(path[-1])
    assert listed_under(reported, "Merged").count("Alpha") == 1


@pytest.mark.parametrize("first", ["hand", "land", "land-then-lock"])
def test_an_owners_hand_merge_is_seen_as_a_merge_running_and_one_merge_lands(
    two_drains: LeasePlay, first: str
) -> None:
    for said in [APART, *D1_STARTS]:
        two_drains.named(said)
    lease_world, world = two_drains.world, two_drains.world.world
    if first == "hand":
        world.by_hand(Kind.COMMIT, Name.A)
        world.by_hand(Kind.RECORD, Name.A)
        hand = lease_world.hand_merge(hold=True)
        before = read_back(two_drains)
        for argv, code, said in [
            (["update", REF[Name.A]], 3, merge_runs("update")),
            (world.argv(Kind.CLOSE, REF[Name.A]), 3, merge_runs("close")),
            (["land", REF[Name.A]], 2, LAND_MEETS_A_MERGE),
        ]:
            assert (lease_world.command(model.Drain.ONE, *argv), world.code) == (said, code)
        assert read_back(two_drains) == before, "nothing changed"
        lease_world.run_out(*hand)
        assert lease_world.stopped is not None
        assert lease_world.stopped[0] == 0, "the hand merge lands"
        landed = lease_world.command(model.Drain.ONE, "land", REF[Name.A])
        assert landed.startswith(RECORDED), "the next land records the owner's merge"
    else:
        lease_world.land(model.Drain.ONE)
        if first == "land":
            lease_world.past_the_probe()
            lease_world.run_out(*lease_world.hand_merge(hold=False))
            assert lease_world.stopped == (2, LOCK_HELD), "the hand merge merges nothing"
            assert merges_on_main(two_drains) == 0
            lease_world.close_the_landing()
            assert world.printed.endswith(f"closed: {REF[Name.A]}: merged\n")
        else:
            hand = lease_world.hand_merge(hold=True)
            note = lease_note(two_drains)
            lease_world.close_the_landing()
            command = gatelib.entry_point(world.root, "merge")
            assert (world.code, world.printed) == (
                2,
                f"{LOCK_HELD}land: `{command}` stopped (exit 2); run land again to finish it\n",
            ), "the land's merge meets the owner's and its land closes nothing"
            assert lease_note(two_drains) == note
            lease_world.run_out(*hand)
            assert lease_world.stopped is not None
            assert lease_world.stopped[0] == 0
            lease_world.command(model.Drain.ONE, "land", REF[Name.A])
            assert world.printed.startswith(RECORDED)
    assert merges_on_main(two_drains) == 1, "one merge landed"
    assert lease_note(two_drains)["outcome"] == "merged"
    reported = lease_world.command(model.Drain.ONE, "report")
    assert listed_under(reported, "Merged").count("Alpha") == 1


LEFT = "left"
"""The file a check or a shell that leaves a process behind writes that process's pid to."""


@pytest.mark.parametrize(
    ("ends", "by"),
    [
        ([MERGES, CLOSES], "check"),
        ([ORPHANED, ORPHAN_MERGES], "check"),
        ([MERGES, CLOSES], "shell"),
    ],
    ids=["land-ends", "land-killed-alone", "shell-before-the-merge"],
)
def test_a_process_the_merges_check_or_shell_leaves_running_does_not_keep_the_landing_seen(
    tmp_path: Path, ends: list[str], by: str
) -> None:
    left = tmp_path / LEFT
    leaves = f"sleep 600 >/dev/null 2>&1 & echo $! >> {left}"
    play = (
        LeasePlay(tmp_path, f"sh -c '{leaves}'")
        if by == "check"
        else LeasePlay(tmp_path, before_merge=leaves)
    )
    try:
        for said in [APART, *D1_STARTS, LANDS, *ends]:
            play.named(said)
        pids = [int(pid) for pid in left.read_text(encoding="utf-8").split()]
        assert [running(pid) for pid in pids] == [True], f"the {by} left a process running"
        root = play.world.world.root
        assert drain.landing_holder(root, BRANCH[Name.A]) is None, "no landing is seen"
        if ends[-1] == CLOSES:
            updated = play.world.command(model.Drain.ONE, "update", REF[Name.A])
            assert (play.world.world.code, updated) == (
                1,
                f"update: {REF[Name.A]} is merged and is not rebased\n",
            )
        else:
            parked = play.world.command(
                model.Drain.ONE, *play.world.world.argv(Kind.CLOSE, REF[Name.A])
            )
            assert (play.world.world.code, parked) == (2, RECORDED + UNSAID)
        reported = play.named("drain 1: drain.py report --drain with its token")
        assert listed_under(reported, "Merged").count("Alpha") == 1
    finally:
        play.world.close()
        for pid in left.read_text(encoding="utf-8").split() if left.exists() else []:
            with contextlib.suppress(ProcessLookupError):
                os.kill(int(pid), signal.SIGKILL)


def test_a_stopped_drain_blocks_the_next_until_its_session_ends(two_drains: LeasePlay) -> None:
    assert lease_broken_by(STOPPED) == {STOPPED_FREE}
    for said in STOPPED[:-1]:
        two_drains.named(said)
    session = two_drains.world.sessions[model.Drain.ONE].pid
    refused = two_drains.named(STOPPED[-1])
    assert refused.startswith(
        f"drain: another drain is running on this repository (process {session})"
    )
    two_drains.named("drain 1: its session ends")
    taken = two_drains.named("drain 2: drain.py clean --all")
    assert taken == f"drain: {two_drains.world.tokens[model.Drain.TWO]}\n", (
        "a gone drain blocks not"
    )


def test_the_page_was_checked_and_drawn_from_the_current_exports() -> None:
    page = PLUGIN_ROOT / "docs/model/drain.md"
    assert not (why := model_tla.stale(page, *model.exports())), why
