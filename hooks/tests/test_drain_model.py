"""The queue drain's rules: on every state of its model, then on real repositories."""

from __future__ import annotations

import functools
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
from tests import drain_model as model
from tests import model_tla
from tests.drain_model import Branch, Kind, Merge, Name, Step
from tests.drain_world import BRANCH, DRAFT, LANDING_WAIT, REF, LeaseWorld, World, digest
from tests.repos import git, snapshot, write
from tests.support import PLUGIN_ROOT
from tests.test_drain import (
    FILES,
    HALVES,
    WHOLE,
    plan_file,
    scratch,  # noqa: F401 # -- an autouse fixture of the drain's tests
)

if TYPE_CHECKING:
    from collections.abc import Iterator

CAP, KEPT, JUDGED, NOT_STALE, ONCE, REPORT_WRITES, HELD, HELD_EDITED, REFUSED, RECOVERS, NO_TREE = (
    model.RULES
)

OPEN_A = ["drain.py open a", "the agent commits a"]
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
}


PARK_C = "drain.py close --outcome parked c"
# Why: for each kind of kill, on a landed ticket and on one not landed, the shortest path to one
# that leaves what neither the whole command nor none of it leaves, as the search finds them.
KILL_CUTS = [
    ["drain.py open a", LAND_CUT],
    ["drain.py open a", EDIT_A, "a kill mid-rebase stops drain.py update a"],
    ["drain.py open a", EDIT_A, KILL_AFTER_REBASE],
    [PARK_A, "drain.py open c", f"a kill before the discard stops {PARK_C}"],
    LAND_KILLED,
    [*JUDGED_A, LAND_CUT],
    [*JUDGED_A, "a kill before the fast-forward stops merge.py in drain.py land a"],
    [*JUDGED_A, CUT_OFF],
    [
        PARK_A,
        "drain.py open c",
        "the agent leaves work uncommitted in c",
        f"a kill after the stash stops {PARK_C}",
    ],
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
def search() -> Search:
    """What the graph's breadth-first steps show."""
    _, edges = model.graph()
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
    assert note_of(play)["digest"] == digest(FILES[REF[Name.A]]), "the hold is on the owner's text"
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
    LANDING_SPARED: None,
    # Why: no step meets it. No command but the landing records its merge while it is alive, so
    # its close never meets one recorded; the rule stays so that a change letting one in shows.
    RECORD_KEPT: None,
    LANDING_UNTOUCHED: None,
    # Why: no step writes a hold, so this holds as 9h does: `close` refuses a ticket whose landing
    # is alive, and the graph has no close outside one.
    HOLD_KEPT: None,
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


@pytest.mark.parametrize("name", list(model.LEASE_RULES))
def test_each_lease_rule_holds_on_every_step_or_breaks_on_its_recorded_path(name: str) -> None:
    found = lease_search()[0].get(name)
    assert found == LEASE_COUNTEREXAMPLES[name], f"{name}: shortest break is\n" + "\n".join(
        found or []
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

    def __init__(self, directory: Path) -> None:
        self.world = LeaseWorld(directory)
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


def refused_in_flight(play: LeasePlay, said: str) -> None:
    """Take ``said``, a command on the ticket of a live landing: exit 3, and nothing changed."""
    before = read_back(play)
    printed = play.named(said)
    command = "update" if said.endswith(Step.UPDATE.value) else "close"
    # Why: the feature skill reads exit 3 as a live landing, and 1 and 2 are `update`'s own.
    assert (play.world.world.code, printed) == (3, landing_runs(command)), said
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
    write(world.root, REF[Name.A], WHOLE[REF[Name.A]])
    snapshot(world.root, "docs(tracker): the ticket's promises")
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
    """What a command on the ticket meets in a live landing, and whether a session's end ends it before its merge or after; ``None`` for any other step."""  # noqa: E501
    if step in TICKET_STEPS and before.landing is not None:
        # Why: each is refused, before the merge lands and after. Not whose agent typed it, which
        # none of these commands reads, nor the tree, which none of them touches.
        return (step.value, before.merge is Merge.NONE)
    if step is Step.DIE:
        ends = before.landing is drain_ or (bool(before.shared) and before.landing is not None)
        return (step.value, ends, ends and before.merge is Merge.NONE)
    return None


def decision(before: model.Lease, said: str) -> tuple[object, ...]:
    """The branch of ``drain.lease`` or ``drain.other_drain`` a step takes, whether a ``clean --all`` takes another drain's tree, whether a merge no note says is recorded or left alone, and what a step meets in a live landing; any other step is its own."""  # noqa: E501
    drain_, step = model.LEASE_STEPS.get(said, (None, None))
    if (found := landing_decision(before, drain_, step)) is not None:
        return found
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
    landing = before.landing is not None
    if step in model.CLEANS:
        return "clean --all", branch, before.tree not in {None, drain_}, landing
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
