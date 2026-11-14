"""The queue drain's rules: on every state of its model, then on real repositories."""

from __future__ import annotations

import functools
import shutil
import tempfile
from collections import deque
from pathlib import Path
from typing import TYPE_CHECKING, NamedTuple

import pytest
from hypothesis import HealthCheck, Phase, settings
from hypothesis import strategies as st
from hypothesis.stateful import RuleBasedStateMachine, rule, run_state_machine_as_test

import drain
from tests import drain_model as model
from tests.drain_model import Branch, Kind, Name, Step
from tests.drain_world import BRANCH, DRAFT, REF, LeaseWorld, World
from tests.repos import git
from tests.test_drain import scratch  # noqa: F401 # -- an autouse fixture of the drain's tests

if TYPE_CHECKING:
    from collections.abc import Iterator

CAP, KEPT, JUDGED, NOT_STALE, ONCE, HELD, HELD_EDITED, REFUSED, RECOVERS, NO_TREE = model.RULES

OPEN_A = ["drain.py open a", "the agent commits a"]
JUDGED_A = [*OPEN_A, "the agent commits the verdict record of a"]
PARK_A = "drain.py close --outcome parked a"
EDIT_A = "the owner edits ticket a"
KILL_AFTER_REBASE = "a kill after the rebase stops drain.py update a"
KILLED_PAST_REBASE = [*JUDGED_A, EDIT_A, KILL_AFTER_REBASE]
KILLED_TWICE = [*KILLED_PAST_REBASE, KILL_AFTER_REBASE]
LAND_KILLED = [*JUDGED_A, "a kill before the note stops drain.py land a"]
LAND_CUT = "a kill before the discard stops drain.py land a"
LAND_LEFT = "a kill after the discard stops drain.py land a"
MERGE_CUT = [*JUDGED_A, LAND_CUT]
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
    # Why: the shortest break of 5. With ``update`` refusing a landed branch, it breaks next on
    # ``PARKED_THEN_LANDED`` and a report, replayed below.
    ONCE: [*LAND_KILLED, "drain.py update a"],
    HELD: None,
    # Why: a finding. `close` reads the ticket from the default branch's work tree, which a
    # fast-forward cut off half-way left holding the merge's text, and writes its hold on that
    # text; `land` reads it before it recovers. Once that text is put back the hold is released as
    # if the owner had edited the ticket, and the next `land` merges it.
    HELD_EDITED: [*JUDGED_A, CUT_OFF, PARK_A, "drain.py land a", "drain.py land a"],
    REFUSED: None,
    RECOVERS: [*JUDGED_A, EDIT_A, LAND_LEFT],
    NO_TREE: [
        PARK_A,
        "drain.py open c",
        "the agent leaves work uncommitted in c",
        "git leaves a stale index lock in the tree of c",
        "drain.py clean --all",
    ],
}


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


@pytest.mark.parametrize("name", list(model.RULES))
def test_each_rule_holds_on_every_step_of_the_model_or_breaks_on_its_recorded_path(
    name: str,
) -> None:
    found = search().breaks.get(name)
    assert found == COUNTEREXAMPLES[name], f"{name}: shortest break is\n" + "\n".join(found or [])


def test_every_rule_reads_as_one_sentence() -> None:
    for name, rule_holds in model.RULES.items():
        sentence = (rule_holds.__doc__ or "").strip()
        assert sentence.endswith("."), name
        assert ". " not in sentence, name


def test_every_kind_of_action_is_reachable() -> None:
    for kind in Kind:
        assert any(name.startswith(kind.value) for name in search().taken), kind


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
    cuts = search().cuts
    # Why: with its tree gone a mark is read by nothing, so in the model's terms this kill
    # leaves what the whole landing leaves; rule 8 still reads what land run again does.
    assert {Kind(kill) for kill, _ in cuts} == set(model.KILLS) - {Kind.KILL_LAND_AFTER_DISCARD}
    for at, path in enumerate(cuts.values()):
        play = replay(tmp_path / str(at), path)
        kill, _, name = path[-1].rpartition(" ")
        play.named("drain.py report")
        play.named(f"{model.KILLS[Kind(kill)].value} {name}")


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


def test_a_merge_whose_landing_was_cut_before_its_discard_is_finished_and_printed_once(
    tmp_path: Path,
) -> None:
    assert broken_by(MERGE_CUT) == set()
    play = replay(tmp_path, [*MERGE_CUT, "drain.py report"])
    assert "Alpha" in listed_under(play.world.printed, "Merged")
    assert play.world.tree(Name.A).exists(), "the landing was cut before its discard"
    finished = f"closed: {REF[Name.A]}: merged\n"
    assert play.named("drain.py land a") == finished, "merge.py does not run again"
    assert (play.world.code, play.world.tree(Name.A).exists()) == (0, False)
    note = note_of(play)
    assert (note["reported"], "discarding" in note) == (True, False), "the report stays said"
    assert (play.named(PARK_A), play.world.code) == (UNSAID, 2)
    assert "Alpha" not in play.named("drain.py report"), "a second report does not print it"


def test_a_held_landing_cut_before_its_discard_is_finished_by_land_again_and_stays_held(
    tmp_path: Path,
) -> None:
    path = ["drain.py open a", LAND_CUT, "drain.py land a"]
    assert broken_by(path[:2]) == broken_by(path) == set()
    play = replay(tmp_path, path)
    held = f"land: {REF[Name.A]} is held and is not landed: record: "
    assert play.world.printed.startswith(held)
    assert (play.world.code, play.world.tree(Name.A).exists()) == (2, False)
    note = note_of(play)
    assert (note["hold"], "discarding" in note) == (True, False)
    assert plan_of(play)[BRANCH[Name.A]].state == "held"


def test_a_retry_after_a_held_landing_killed_after_its_discard_is_counted_once(
    tmp_path: Path,
) -> None:
    path = ["drain.py open a", LAND_LEFT, "drain.py land a", "drain.py clean --all"]
    assert broken_by(path[:2]) == set()
    play = replay(tmp_path, path)
    assert note_of(play)["discarding"] is True, "with its tree gone, the mark is read by nothing"
    for name in [EDIT_A, "drain.py open a", *OPEN_A[1:], JUDGED_A[-1]]:
        play.named(name, compare=False)
    play.agree()
    assert "discarding" not in note_of(play), "open drops the mark of an earlier run"
    # Why: a second edit is past the model's bound, so the repository takes these steps alone.
    play.world.perform(model.Action(Kind.EDIT, Name.C))
    play.world.perform(model.Action(Kind.LAND, Name.A))
    assert play.world.code == 1
    assert "\nbehind: " in play.world.printed
    assert play.world.printed.endswith(f"closed: {REF[Name.A]}: waits\n")
    note = note_of(play)
    assert (note["attempts"], note["hold"]) == (1, False), "the run's refusal is counted once"


def test_a_refused_landing_killed_before_its_discard_counts_its_retry_once(
    tmp_path: Path,
) -> None:
    path = [*JUDGED_A, EDIT_A, LAND_CUT]
    assert broken_by(path) == set()
    play = replay(tmp_path, path)
    assert (note_of(play)["attempts"], note_of(play)["discarding"]) == (1, True)
    assert play.named("drain.py land a").endswith(f"closed: {REF[Name.A]}: waits\n")
    assert play.world.code == 1
    note = note_of(play)
    assert (note["attempts"], note["hold"], "discarding" in note) == (1, False, False), (
        "merge.py refused it once a run, and the note says once"
    )
    assert "Alpha" in listed_under(play.named("drain.py report"), "Waits for you")


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


def test_update_moves_a_landed_branch_off_its_merge_and_land_then_closes_it_as_waiting(
    tmp_path: Path,
) -> None:
    path = COUNTEREXAMPLES[ONCE] or []
    assert broken_by(path) == {ONCE}
    play = replay(tmp_path, path)
    assert (play.world.printed, play.world.code) == ('{"moved": true, "conflicts": []}\n', 0)
    assert BRANCH[Name.A] not in drain.route.merged_features(play.world.root, "main")
    assert play.named("drain.py land a").endswith(f"closed: {REF[Name.A]}: waits\n")
    report = play.named("drain.py report")
    assert "Alpha" in listed_under(report, "Waits for you"), "the landed ticket is said to wait"
    assert "Alpha" not in listed_under(report, "Merged"), "and its merge is never recorded"


def test_a_report_after_a_landing_killed_before_its_note_lists_its_kept_close_as_parked(
    tmp_path: Path,
) -> None:
    path = [*PARKED_THEN_LANDED, "drain.py report"]
    assert broken_by(path) == {ONCE}
    play = replay(tmp_path, path)
    assert "Alpha" in listed_under(play.world.printed, "Parked"), "a ticket that landed"
    assert play.named("drain.py clean --all").startswith(RECORDED)
    assert "Alpha" in listed_under(play.named("drain.py report"), "Merged"), "and then merged"


def test_a_refused_landing_killed_after_its_discard_and_run_again_counts_its_retry_twice(
    tmp_path: Path,
) -> None:
    path = COUNTEREXAMPLES[RECOVERS] or []
    assert broken_by(path) == {RECOVERS}
    play = replay(tmp_path, path)
    note = note_of(play)
    assert (note["attempts"], note["discarding"], play.world.tree(Name.A).exists()) == (
        1,
        True,
        False,
    )
    assert play.named("drain.py land a").endswith(f"closed: {REF[Name.A]}: waits\n")
    assert note_of(play)["attempts"] == 2, "merge.py refused it once a run, and the note says twice"


def test_merge_py_killed_before_and_mid_fast_forward_is_put_back_by_the_next_command(
    tmp_path: Path,
) -> None:
    before = "a kill before the fast-forward stops merge.py in drain.py land a"
    play = replay(tmp_path, [*JUDGED_A, before])
    assert play.world.printed.endswith("stopped (exit -9); run land again to finish it\n")
    play.named(CUT_OFF)
    root = play.world.root
    assert play.world.printed.startswith("merge: removed the work tree "), "the first kill's"
    assert git(root, "status", "--porcelain"), "files of the merge are in the default branch's tree"
    assert drain.plan(drain.Drain(root, None, model.CAP)).blocked, "the checkout is not fit"
    swept = play.named("drain.py clean --all")
    assert "merge: a run that stopped was moving `main`; its work tree is put back\n" in swept
    assert git(root, "status", "--porcelain") == ""
    landed = play.named("drain.py land a")
    assert landed.endswith(f"closed: {REF[Name.A]}: merged\n")
    assert "Alpha" in listed_under(play.named("drain.py report"), "Merged")


def test_a_ticket_parked_while_its_fast_forward_is_cut_off_is_landed_with_no_edit(
    tmp_path: Path,
) -> None:
    path = COUNTEREXAMPLES[HELD_EDITED] or []
    assert broken_by(path) == {HELD_EDITED}
    play = replay(tmp_path, path[:-1])
    assert "merge: a run that stopped was moving `main`; its work tree is put back\n" in (
        play.world.printed
    )
    assert f"land: {REF[Name.A]} is held and is not landed: " in play.world.printed
    assert plan_of(play)[BRANCH[Name.A]].state == "built", "the text put back releases the hold"
    assert git(play.world.root, "log", "-1", "--format=%s", "main", "--", REF[Name.A]) == (
        "feat(core): the clean state"
    ), "and the owner never changed the ticket"
    assert "merged: `feature/1-alpha` into `main`" in play.named(path[-1])
    assert note_of(play)["outcome"] == "merged", "a ticket the drain parked has landed"


# --- the lease ---------------------------------------------------------------------------------

LIVE_SPARED, TOLD_APART, STOPPED_FREE, GONE_FREE, OWN_LEASE = model.LEASE_RULES
APART = "each drain runs in a session of its own"
UNDER_ONE = "one long-lived process runs both drains"
D1_STARTS = ["drain 1: drain.py clean --all", "drain 1: drain.py open a"]
TAKEN_UNDER_ONE = [UNDER_ONE, *D1_STARTS, "drain 2: drain.py clean --all"]
REPORTED_AWAY = [APART, *D1_STARTS, "drain 2: drain.py report", "drain 2: drain.py clean --all"]
PROGRAM = "drain 1: its agent starts a program built in its tree"
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
    # Why: a finding. Under one process a second drain's report ends the first's lease, which the
    # docstring accepts; with no lease `rival` looks for no program, so once the first drain
    # starts one in its tree the second's `clean --all` ends it and takes the tree.
    TOLD_APART: [UNDER_ONE, *D1_STARTS, "drain 2: drain.py report", PROGRAM, TAKEN_UNDER_ONE[-1]],
    # Why: 9b breaks where the docstring accepts it: "A drain is alive while a process of its own
    # is", so one that stopped with its session open blocks the next until the owner says
    # `--take-over`. 9c, which asks only that a drain whose processes are gone block nothing,
    # holds.
    STOPPED_FREE: STOPPED,
    GONE_FREE: None,
    # Why: 9d breaks only where the docstring accepts it: under one process a report cannot tell
    # whose drain it ends. In sessions of their own it ends only its own drain's lease.
    OWN_LEASE: [UNDER_ONE, D1_STARTS[0], "drain 2: drain.py report"],
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
        f"report: the drain lease stays: another drain is running (process {session})\n"
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


def test_a_report_under_one_process_ends_the_first_drains_lease_and_clean_all_its_program(
    two_drains: LeasePlay,
) -> None:
    path = LEASE_COUNTEREXAMPLES[TOLD_APART] or []
    assert lease_broken_by(path[:4]) == {OWN_LEASE}
    assert lease_broken_by(path) == {LIVE_SPARED, TOLD_APART}
    for said in path[:4]:
        reported = two_drains.named(said)
    assert "lease stays" not in reported, "under one process the report ends drain 1's lease"
    two_drains.named(PROGRAM)
    program = two_drains.world.program
    assert program is not None
    taken = two_drains.named(path[-1])
    tree = two_drains.world.world.tree(Name.A)
    assert f"ended: process {program.pid}, which ran a program of {tree}" in taken
    assert f"removed: {tree}: the drain that made it was interrupted" in taken


def decision(before: model.Lease, said: str) -> tuple[object, ...]:
    """The branch of ``drain.lease`` or ``drain.release`` a step takes, and whether a ``clean --all`` takes another drain's tree; any other step is its own."""  # noqa: E501
    drain_, step = model.LEASE_STEPS.get(said, (None, None))
    if step not in model.CLEANS | model.REPORTS:
        return (said.rpartition(": ")[2],)
    holder = before.holder
    branch = (
        "no lease"
        if holder is None
        else "its own token"
        if step in model.OWN and holder is drain_
        else "taken over"
        if step is Step.TAKE_OVER
        else "a process of its chain"
        if model.of(before.alive, holder) and not before.shared and holder is not drain_
        else "a program in its tree"
        if before.running
        else "gone"
    )
    taken = step in model.CLEANS and before.tree not in {None, drain_}
    return step in model.CLEANS, branch, taken


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
    # Why: every step of the lease graph is some 3,900 commands on real repositories. Each decision
    # the code makes on a lease, for each kind of step, is a few dozen, chained into a few walks.
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
