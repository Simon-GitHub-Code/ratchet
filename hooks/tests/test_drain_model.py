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
from tests.drain_model import Branch, Deed, Keep, Kind, Merge, Name, Stage, Step, Tree
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
from tests.test_queue_gate import COMMIT, judged

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

GATE = "queue-final-check"
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
    PARTS_FIRST: None,
    PARTS_AWAITED: None,
    RECOVERS: None,
    NO_TREE: [
        PARK_A,
        "drain.py open c",
        "the agent leaves work uncommitted in c",
        "git leaves a stale index lock in the tree of c",
        "drain.py clean --all",
    ],
    UNLANDED: None,
    UNTOUCHED: [
        "drain.py open a",
        "a role of the pipeline run commits in a",
        "the agent commits the verdict record of a",
    ],
}


PARK_C = "drain.py close --outcome parked c"
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
    taken: frozenset[str]
    cuts: dict[tuple[str, bool], list[str]]


@functools.cache
def main_graph(*, gate: bool) -> tuple[frozenset[model.State], list[model.Edge]]:
    return model.graph(model.guarded) if gate else model.graph()


RULE_PARTS = tuple(tuple(sorted(model.RULES))[part::3] for part in range(3))


@functools.cache
def search(*, gate: bool, rules: tuple[str, ...]) -> Search:
    _, edges = main_graph(gate=gate)
    checked = {name: model.RULES[name] for name in rules}
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
        for name, rule_holds in checked.items():
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


def landed_unchecked(*, gate: bool) -> list[str]:
    return [
        said
        for before, said, after in main_graph(gate=gate)[1]
        for name in Name
        if after.of(name).merged and model.unchecked(before.of(name))
    ]


def committed_while_marked(*, gate: bool) -> list[str]:
    commits = {kind.value for kind in model.COMMITS}
    return [
        said
        for before, said, _ in main_graph(gate=gate)[1]
        if said.rpartition(" ")[0] in commits
        and (name := model.ticket_of(said)) is not None
        and (note := before.of(name).note) is not None
        and note.rebasing
    ]


@pytest.mark.parametrize(
    "part", [pytest.param(n, marks=pytest.mark.shard_slot(4 * n)) for n in range(len(RULE_PARTS))]
)
def test_each_rule_holds_on_every_step_of_the_model_or_breaks_on_its_recorded_path(
    part: int,
) -> None:
    found = search(gate=False, rules=RULE_PARTS[part])
    for name in RULE_PARTS[part]:
        path = found.breaks.get(name)
        assert path == COUNTEREXAMPLES[name], f"{name}: shortest break is\n" + "\n".join(path or [])
    if part:
        return
    assert list(found.cuts.values()) == KILL_CUTS, "the kills' cuts are\n" + "\n".join(
        map(str, found.cuts.values())
    )
    for kind in Kind:
        assert any(name.startswith(kind.value) for name in found.taken), kind
    unchecked = landed_unchecked(gate=False)
    assert not unchecked, f"a branch lands on a start no check passed: {unchecked[0]}"
    assert committed_while_marked(gate=False), "without the gate, a commit follows a cut-off update"


@pytest.mark.parametrize(
    "part",
    [pytest.param(n, marks=pytest.mark.shard_slot(slot)) for n, slot in enumerate((2, 6, 8))],
)
def test_with_the_guards_gate_each_rule_but_10_holds_and_no_role_commits(part: int) -> None:
    found = search(gate=True, rules=RULE_PARTS[part])
    for name in RULE_PARTS[part]:
        path = found.breaks.get(name)
        pinned = COUNTEREXAMPLES[name] if name == NO_TREE else None
        assert path == pinned, f"{name}: shortest break is\n" + "\n".join(path or [])
    if part:
        return
    assert list(found.cuts.values()) == KILL_CUTS, "the kills' cuts are the same"
    for kind in Kind:
        taken = any(name.startswith(kind.value) for name in found.taken)
        assert taken is (kind is not Kind.PUBLISH), kind
    unchecked = landed_unchecked(gate=True)
    assert not unchecked, f"a branch lands on a start no check passed: {unchecked[0]}"
    marked = committed_while_marked(gate=True)
    assert not marked, f"a commit goes through on a branch update moved: {marked[0]}"
    states, edges = main_graph(gate=True)
    moved = {
        (ticket.branch, ticket.note is not None and ticket.note.rebasing)
        for state in states
        for ticket in state.tickets
        if ticket.since
    }
    assert moved == {(Branch.BARE, True)}, "only a killed update moves a branch from its start"
    told = {
        after == before
        for before, said, after in edges
        if said.startswith(Kind.CHECK.value)
        and (name := model.ticket_of(said)) is not None
        and before.of(name).since
    }
    assert told == {True}, "a check of a branch a killed update moved changes nothing"


def test_every_rule_reads_as_one_sentence() -> None:
    for name, rule_holds in model.RULES.items():
        sentence = (rule_holds.__doc__ or "").strip()
        assert sentence.endswith("."), name
        assert ". " not in sentence, name


class Play:
    def __init__(self, directory: Path) -> None:
        self.world = World(directory)
        self.state = model.INITIAL
        self.path: list[str] = []
        self.answers: set[bool] = set()

    def says(self, what: str) -> str:
        lines = [what, "path:", *(f"  {name}" for name in self.path)]
        return "\n".join([*lines, f"the last command printed:\n{self.world.printed}"])

    def named(self, name: str, *, compare: bool = True) -> str:
        move = next((move for move in model.moves(self.state) if move[0].name == name), None)
        assert move is not None, self.says(f"no action is called {name!r} here")
        self.take(*move, compare=compare)
        return self.world.printed

    def cut(self, kind: Kind, name: Name) -> str:
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
        seen = self.world.seen(self.state)
        statuses = model.classify(self.state)
        assert seen.statuses == statuses, self.says(f"the plan says {seen.statuses}")
        assert seen.start == model.start(self.state), self.says(f"the plan starts {seen.start}")
        merging = (seen.scratch, seen.half)
        assert merging == (self.state.scratch, self.state.half), self.says(f"merge.py: {merging}")
        for name, real, kept in zip(Name, seen.tickets, self.state.tickets, strict=True):
            assert real == kept, self.says(f"ticket {name.value} is {real}\nnot {kept}")
            cut = drain.cut_off(self.world.root, BRANCH[name])
            assert cut is model.cut_off(kept), self.says(f"drain.cut_off of {name.value}: {cut}")
        for name, kept in self.state.each():
            if kept.tree is Tree.NONE:
                continue
            gated = self.guard_refuses(name)
            said = f"the guard {'refuses' if gated else 'lets through'} a commit in {name.value}"
            refused = model.commit_refused(kept)
            assert gated == refused, self.says(said)

    def guard_refuses(self, name: Name) -> bool:
        return GATE in {v.rule for v in judged(COMMIT, self.world.tree(name))}

    def guard_allows(self, action: model.Action) -> bool:
        name = action.ticket
        if action.kind not in model.COMMITS or name is None:
            return True
        refused = self.guard_refuses(name)
        said = f"the guard {'refuses' if refused else 'lets through'} {action.name}"
        assert refused is not model.guarded(self.state, action), self.says(said)
        self.answers.add(not refused)
        return not refused


WEIGHTED = [
    Kind.OPEN,
    Kind.OPEN,
    Kind.COMMIT,
    Kind.COMMIT,
    Kind.RECORD,
    Kind.RECORD,
    Kind.CHECK_NOT_FINAL,
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
    Kind.CHECK_HALTED_NOT_FINAL,
]
WALKED: list[Play] = []


class DrainOnARepository(RuleBasedStateMachine):
    def __init__(self) -> None:
        super().__init__()
        self.scratch = Path(tempfile.mkdtemp(prefix="drain-model-"))
        self.play = Play(self.scratch)

    @rule(data=st.data())
    def act(self, data: st.DataObject) -> None:
        state = self.play.state
        allowed = [move for move in model.moves(state) if self.play.guard_allows(move[0])]
        moves = sorted(allowed, key=lambda move: move[1] == state)
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
        WALKED.append(self.play)
        shutil.rmtree(self.scratch, ignore_errors=True)


def test_the_real_drain_agrees_with_the_model_on_every_step() -> None:
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
    answers = {answer for play in WALKED for answer in play.answers}
    assert answers == {True, False}, (
        "the guard refuses a commit the walk is offered, and allows one"
    )


def test_the_real_drain_agrees_with_the_model_after_each_kind_of_kill(tmp_path: Path) -> None:
    assert {Kind(path[-1].rpartition(" ")[0]) for path in KILL_CUTS} == set(model.KILLS)
    elsewhere = 0
    for at, path in enumerate(KILL_CUTS):
        kill, _, name = path[-1].rpartition(" ")
        then = [*path, "drain.py report", f"{model.KILLS[Kind(kill)].value} {name}"]
        if any(other[: len(then)] == then for other in REPLAYED_AFTER_A_KILL):
            elsewhere += 1
            continue
        replay(tmp_path / str(at), then)
    assert elsewhere == len(REPLAYED_AFTER_A_KILL), "each replay listed takes a kill's steps"


def plan_of(play: Play) -> dict[str, drain.Entry]:
    drafted = drain.plan(drain.Drain(play.world.root, None, model.CAP))
    return {entry.branch: entry for entry in drafted.tickets}


def broken_by(path: list[str]) -> set[str]:
    state = before = model.INITIAL
    for name in path:
        before = state
        state = next(after for action, after in model.moves(state) if action.name == name)
    return {rule for rule, holds in model.RULES.items() if not holds(before, path[-1], state)}


COMPARED: set[tuple[str, ...]] = set()


def replay(tmp_path: Path, path: list[str]) -> Play:
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


def test_the_agent_that_commits_before_the_check_commits_on_a_branch_a_role_committed_on(
    tmp_path: Path,
) -> None:
    path = COUNTEREXAMPLES[UNTOUCHED] or []
    assert broken_by(path) == {UNTOUCHED}
    play = replay(tmp_path, path[:-2])
    for name in path[-2:]:
        step = next(action for action, _ in model.moves(play.state) if action.name == name)
        assert not play.guard_allows(step), f"the guard refuses {name}"
        play.named(name)


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


def test_check_records_the_start_and_marks_it_passed_in_the_note(tmp_path: Path) -> None:
    play = replay(tmp_path, ["drain.py open a"])
    opened = json.loads(play.world.printed)
    note = note_of(play)
    assert note["start"] == opened["start"], "open records the start and prints it"
    assert "passed" not in note
    assert play.named(CHECK_A).startswith(publish_check.GREEN)
    assert play.world.code == 0
    assert note_of(play)["passed"] is True


def test_a_check_after_an_update_killed_past_the_rebase_of_a_bare_branch_says_to_run_update_again(
    tmp_path: Path,
) -> None:
    path = [*MARK_LEFT[:-1], KILL_AFTER_REBASE, CHECK_A, UPDATE_A, CHECK_A]
    assert {rule for at in range(1, len(path) + 1) for rule in broken_by(path[:at])} == set()
    states = walked(path)
    assert model.cut_off(states[2].of(Name.A)), "the mark stands and the branch moved"
    assert states[3] == states[2], "the check changes nothing"
    play = replay(tmp_path, path[:3])
    kept = note_of(play)
    told = f"check: {REF[Name.A]} {drain.CUT_OFF}\n"
    assert (play.named(CHECK_A), play.world.code) == (told, drain.UNREAD)
    assert note_of(play) == kept, "the check parks nothing and writes no note"
    assert play.named(UPDATE_A) == '{"moved": true, "conflicts": []}\n'
    assert (play.named(CHECK_A), play.world.code) == (f"{publish_check.GREEN}\n", 0)
    assert note_of(play)["passed"] is True


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


NOT_FINAL_A = f"{Kind.CHECK_NOT_FINAL.value} a"
HALTED_NOT_FINAL_A = f"{Kind.CHECK_HALTED_NOT_FINAL.value} a"
RECORD_A = "the agent commits the verdict record of a"
LAND_A = "drain.py land a"
RESUMED = ["drain.py open a", NOT_FINAL_A, ROLE_A, RECORD_A, LAND_A]
NOT_PASSED = (
    f"land: {REF[Name.A]} is not landed: no publish check has passed its start; run"
    f" `drain.py check {REF[Name.A]} --final` first\n"
)


def test_land_refuses_a_branch_a_role_committed_on_in_the_resume_after_a_check_without_final(
    tmp_path: Path,
) -> None:
    broken = [broken_by(RESUMED[:at]) for at in range(1, len(RESUMED) + 1)]
    assert broken == [set(), set(), set(), {UNTOUCHED}, set()], broken
    states = walked(RESUMED)
    assert states[1] == states[0], "a check without --final marks nothing"
    for before, name in [(states[1], ROLE_A), (states[2], RECORD_A)]:
        step = next(action for action, _ in model.moves(before) if action.name == name)
        assert not model.guarded(before, step), f"the guard refuses {name}"
    assert states[-1] == states[-2], "land refuses it"
    play = replay(tmp_path, RESUMED[:2])
    assert (play.world.code, play.world.printed) == (0, f"{publish_check.GREEN}\n")
    assert "passed" not in note_of(play)
    for name in RESUMED[2:]:
        play.named(name)
    assert (play.world.code, play.world.printed) == (3, NOT_PASSED)
    subjects = git(play.world.root, "log", "--format=%s", "main").splitlines()
    assert PUBLISHED not in subjects, "the role's commit is not on the default branch"


def test_a_check_without_final_parks_a_branch_that_moved_as_the_last_check_does(
    tmp_path: Path,
) -> None:
    path = ["drain.py open a", ROLE_A, NOT_FINAL_A]
    assert walked(path)[-1] == walked([*path[:-1], CHECK_A])[-1]
    play = replay(tmp_path, path)
    assert play.world.code == 1
    assert note_of(play)["reasons"][0].startswith("a role published: `feature/1-alpha` moved")


def test_a_check_without_final_that_finds_only_an_unfinished_rebase_leaves_the_start_unpassed(
    tmp_path: Path,
) -> None:
    path = [*REJUDGED, HALTED_NOT_FINAL_A, LAND_A, CHECK_A]
    assert {rule for at in range(1, len(path) + 1) for rule in broken_by(path[:at])} == set()
    play = replay(tmp_path, path[:-2])
    assert play.world.code == 3
    assert play.state.of(Name.A).note == model.Note(
        model.Outcome.CLOSED, text=0, fresh=True, attempts=1
    ), "waits to be retried, its start unpassed"
    assert note_of(play)["reasons"][0] == HALTED_WHY
    assert (play.named(LAND_A), play.world.code) == (NOT_PASSED, 3)
    assert play.named(CHECK_A).startswith(publish_check.GREEN), "the next check makes the tree"
    assert note_of(play)["passed"] is True


def test_a_check_that_cannot_read_marks_nothing_with_the_flag_or_without_it(
    tmp_path: Path,
) -> None:
    play = replay(tmp_path, ["drain.py open a", ROLE_A])
    play.state = walked([*play.path, UNREAD_A])[-1]
    play.path.append(f"{UNREAD_A}, run without --final")
    with play.world.unreadable():
        play.world.command("check", REF[Name.A])
    assert play.world.code == 3
    play.agree()


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


def test_the_upgrade_is_a_commit_on_the_default_branch_so_an_open_branch_is_then_behind(
    tmp_path: Path,
) -> None:
    path = ["drain.py open a", UPGRADE, UPDATE_A]
    assert {rule for at in range(1, len(path) + 1) for rule in broken_by(path[:at])} == set()
    assert not walked(path[:2])[-1].of(Name.A).current
    play = replay(tmp_path, path)
    assert play.world.printed == '{"moved": true, "conflicts": []}\n'


def built_and_landed(name: Name) -> list[str]:
    return [
        f"drain.py open {name.value}",
        f"drain.py check {name.value}",
        f"the agent commits {name.value}",
        f"the agent commits the verdict record of {name.value}",
        f"drain.py land {name.value}",
    ]


def test_a_ticket_waiting_on_a_split_ticket_the_owner_closed_waits_for_its_parts(
    tmp_path: Path,
) -> None:
    path = ["drain.py open a", "drain.py split a", "the owner closes ticket a", "drain.py open b"]
    assert broken_by(path) == set()
    play = replay(tmp_path, path)
    drafted = plan_of(play)
    parts = tuple(drain.key(REF[part]) for part in model.PARTS[Name.A])
    beta = drafted[BRANCH[Name.B]]
    assert (beta.state, beta.waits_on) == ("waiting", parts), "a closed split blocker is its parts"
    assert play.world.code == 1
    assert not play.world.tree(Name.B).is_dir()


def test_a_ticket_waiting_on_a_split_ticket_waits_for_its_parts_then_starts(
    tmp_path: Path,
) -> None:
    path = ["drain.py open a", "drain.py split a"]
    landed = [*path, PARK_C, *built_and_landed(Name.D), *built_and_landed(Name.E)]
    assert broken_by(landed) == set()
    play = replay(tmp_path, path)
    root = play.world.root
    assert git(root, "status", "--porcelain") == "", "the split commits its parts"
    on_main = git(
        root, "ls-tree", "--name-only", "main", "--", *(REF[p] for p in model.PARTS[Name.A])
    )
    assert on_main.splitlines() == [REF[part] for part in model.PARTS[Name.A]], "on the default tip"
    parts = tuple(drain.key(REF[part]) for part in model.PARTS[Name.A])
    beta = plan_of(play)[BRANCH[Name.B]]
    assert (beta.state, beta.waits_on) == ("waiting", parts), "b waits on the parts, not on a"
    play = replay(tmp_path / "landed", landed)
    assert all(play.state.of(part).merged for part in model.PARTS[Name.A]), "both parts landed"
    assert plan_of(play)[BRANCH[Name.B]].state == "buildable"


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


def test_a_commit_goes_through_after_the_final_check_and_not_in_a_rebase_a_kill_left(
    tmp_path: Path,
) -> None:
    stopped = replay(tmp_path / "stopped", ["drain.py open a", CHECK_A]).state.of(Name.A)
    assert stopped.tree is Tree.CLEAN
    assert not model.commit_refused(stopped)
    halted = replay(tmp_path / "halted", [*OPEN_A, EDIT_A, MARK_LEFT[-1]]).state.of(Name.A)
    assert (halted.tree, model.unchecked(halted)) == (Tree.REBASING, False), "on a passed start"
    assert model.commit_refused(halted), "the mark refuses it"


RECORDED = (
    f"recorded: {BRANCH[Name.A]}: merged, and the run that landed it stopped before it said so\n"
)
UNSAID = f"drain: {REF[Name.A]} merged, and a merge is not unsaid by closing it as parked\n"


def listed_under(report: str, heading: str) -> str:
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
    killed = walked(KILLED_TWICE)[-1].of(Name.A)
    assert not model.unchecked(killed), "the start a check passed is kept"
    assert model.commit_refused(killed), "the mark refuses a commit"
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
    assert model.commit_refused(play.state.of(Name.A)), "the new start awaits its check"
    assert play.named(CHECK_A).startswith(publish_check.GREEN)
    assert not model.commit_refused(play.state.of(Name.A)), "the final check lets a commit through"


@pytest.mark.parametrize("kill", [MARK_LEFT[-1], KILL_AFTER_REBASE])
def test_an_update_cut_off_after_the_agent_committed_on_a_passed_start_parks_nothing(
    tmp_path: Path, kill: str
) -> None:
    path = ["drain.py open a", CHECK_A, "the agent commits a", EDIT_C, kill, UPDATE_A]
    assert {rule for at in range(1, len(path) + 1) for rule in broken_by(path[:at])} == set()
    states = walked(path)
    assert model.commit_refused(states[-2].of(Name.A)), "the mark refuses a commit"
    assert not any(model.held_by_note(state.of(Name.A)) for state in states), "nothing parks"
    play = replay(tmp_path, path)
    assert play.world.code == 0
    assert "a role published" not in play.world.printed
    assert note_of(play).get("outcome") != drain.PARKED


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
    LIVE_SPARED: TAKEN_UNDER_ONE,
    TOLD_APART: [*TAKEN_OVER, "drain 2: drain.py clean --all"],
    STOPPED_FREE: STOPPED,
    GONE_FREE: None,
    OWN_LEASE: [UNDER_ONE, D1_STARTS[0], "drain 2: drain.py report"],
    PRINTED_ONCE: None,
    LANDING_SPARED: None,
    RECORD_KEPT: None,
    LANDING_UNTOUCHED: None,
    HOLD_KEPT: None,
    SEEN_ALIVE: None,
    UNSEEN_ENDED: None,
    CLOSED_BY_OWNER: None,
    ONE_MERGE: None,
}


@functools.cache
def lease_search() -> tuple[dict[str, list[str]], frozenset[str]]:
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
    lease = before = model.LEASE_INITIAL
    for said in path:
        before = lease
        lease = dict(model.lease_moves(lease))[said]
    return {name for name, holds in model.LEASE_RULES.items() if not holds(before, path[-1], lease)}


class LeasePlay:
    def __init__(self, directory: Path, check: str = "true", before_merge: str = "") -> None:
        self.world = LeaseWorld(directory, check, before_merge)
        self.lease = model.LEASE_INITIAL
        self.path: list[str] = []

    def named(self, said: str) -> str:
        self.lease = dict(model.lease_moves(self.lease))[said]
        self.path.append(said)
        self.world.world.printed = ""
        self.world.perform(said)
        seen = self.world.seen(self.lease)
        lines = ["path:", *(f"  {step}" for step in self.path), self.world.world.printed]
        assert seen == self.lease, "\n".join([f"seen {seen}", f"not {self.lease}", *lines])
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
    root = play.world.world.root
    note = drain.queue_notes.read_note(root, BRANCH[Name.A])
    tips = git(root, "rev-parse", "main", BRANCH[Name.A])
    return play.world.seen(play.lease), note, tips, drain.worktrees(root)


def lease_note(play: LeasePlay) -> dict[str, object]:
    return drain.queue_notes.read_note(play.world.world.root, BRANCH[Name.A])


def landing_runs(command: str) -> str:
    return (
        f"{command}: a landing of `{BRANCH[Name.A]}` is running in another command; nothing was"
        " done; wait for it to end, then run plan: the landing records the ticket's outcome"
        " itself\n"
    )


def merge_runs(command: str) -> str:
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
    before = read_back(play)
    by_merge = model.seen_by(play.lease) == "merge"
    printed = play.named(said)
    command = "update" if said.endswith(Step.UPDATE.value) else "close"
    said_by = merge_runs if by_merge else landing_runs
    assert (play.world.world.code, printed) == (3, said_by(command)), said
    assert read_back(play) == before, f"{said}: nothing changed"


def plan_of_a(play: LeasePlay) -> list[tuple[str, tuple[str, ...]]]:
    drafted = drain.plan(drain.Drain(play.world.world.root, None, model.CAP))
    return [(entry.state, entry.why) for entry in drafted.tickets if entry.branch == BRANCH[Name.A]]


def test_a_second_land_while_one_is_in_flight_is_refused_and_the_merge_printed_once(
    two_drains: LeasePlay,
) -> None:
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
    world.command("check", REF[Name.A], "--final")
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
    seen = model.seen_by(before)
    if step in TICKET_STEPS and seen is not None:
        return (step.value, seen, before.merge is Merge.NONE)
    if step in {Step.UPDATE, Step.PARK} and before.doomed:
        return (step.value, "unseen", before.merge)
    if step is Step.LAND_REST:
        return (step.value, before.merge is Merge.UNSAID)
    if step is Step.DIE:
        ends = before.landing is drain_ or (bool(before.shared) and before.landing is not None)
        return (step.value, ends, ends and before.merge is Merge.NONE)
    return None


def decision(before: model.Lease, said: str) -> tuple[object, ...]:
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


def merge_runs_seen(play: LeasePlay) -> int:
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


(LAST_DELETES, NEVER_REFUSED, CARRIED, RESOLVED, SPEC_FOUND, LAND_RECOVERS) = model.EXPIRY_RULES
BUILT_A = ["the agent commits a", "documents.py expire runs on a"]
BUILT_C = ["the agent commits c", "documents.py expire runs on c"]
VERDICT_A = "the agent commits the verdict record of a"
VERDICT_C = "the agent commits the verdict record of c"
REJUDGE_A = "a judge-again run commits the verdict record of a"
REJUDGE_C = "a judge-again run commits the verdict record of c"
ANSWERED = "the owner lands an Answers line on a ticket of the unit"
LANDED_A = [*BUILT_A, VERDICT_A, "drain.py land a"]
EDITED_ACROSS = [*LANDED_A, *BUILT_C, ANSWERED, "drain.py update c"]
OUT_OF_ORDER = [
    "the agent commits a",
    "the agent commits c",
    "documents.py expire runs on a",
    "documents.py expire runs on c",
    VERDICT_A,
    "drain.py land a",
    "drain.py update c",
    REJUDGE_C,
    "drain.py land c",
]
SQUASHED = [
    *BUILT_A,
    "the owner squashes by hand the branch of a",
    ANSWERED,
    "drain.py update a",
    REJUDGE_A,
]
UNKEPT: dict[frozenset[Keep], dict[str, list[str]]] = {
    frozenset(): {SPEC_FOUND: SQUASHED},
    frozenset({Keep.UPDATE_EXPIRE}): {SPEC_FOUND: SQUASHED, NEVER_REFUSED: OUT_OF_ORDER},
    frozenset({Keep.CARRY}): {
        SPEC_FOUND: SQUASHED,
        CARRIED: EDITED_ACROSS,
        NEVER_REFUSED: [*EDITED_ACROSS, REJUDGE_C, "drain.py land c"],
    },
    frozenset({Keep.ONCE}): {SPEC_FOUND: SQUASHED, CARRIED: EDITED_ACROSS},
    frozenset({Keep.RESOLVE}): {SPEC_FOUND: SQUASHED, RESOLVED: EDITED_ACROSS},
    frozenset({Keep.JUDGE}): {SPEC_FOUND: SQUASHED},
    frozenset({Keep.HISTORY}): {SPEC_FOUND: [*BUILT_A, ANSWERED, "drain.py update a", REJUDGE_A]},
    frozenset({Keep.NOTE}): {
        SPEC_FOUND: SQUASHED,
        LAND_RECOVERS: [
            *LANDED_A,
            *BUILT_C,
            VERDICT_C,
            "a kill before the note stops drain.py land c",
            "drain.py land c",
        ],
    },
    frozenset({Keep.UPDATE_EXPIRE, Keep.JUDGE}): {SPEC_FOUND: SQUASHED, LAST_DELETES: OUT_OF_ORDER},
}


def expiry_breaks(keeps: frozenset[Keep]) -> dict[str, list[str]]:
    _, edges = model.expiry_graph(keeps)
    came: dict[model.Expiry, tuple[model.Expiry, str] | None] = {model.EXPIRY_INITIAL: None}
    breaks: dict[str, list[str]] = {}
    for before, said, after in edges:
        for name, rule_holds in model.EXPIRY_RULES.items():
            if name not in breaks and not rule_holds(before, said, after):
                path, at = [said], came[before]
                while at is not None:
                    path.append(at[1])
                    at = came[at[0]]
                breaks[name] = path[::-1]
        came.setdefault(after, (before, said))
    return breaks


def expiry_walk(path: list[str], keeps: frozenset[Keep] = model.KEEPS) -> model.Expiry:
    state = model.EXPIRY_INITIAL
    for said in path:
        state = dict(model.expiry_moves(state, keeps))[said]
    return state


def test_each_expiry_rule_holds_on_every_step_but_12d_which_a_hand_squash_breaks() -> None:
    states, edges = model.expiry_graph()
    assert (len(states), len(edges)) == (1237, 2752)
    assert expiry_breaks(model.KEEPS) == UNKEPT[frozenset()]
    taken = {said.removesuffix(" a").removesuffix(" c") for _, said, _ in edges}
    assert taken == {deed.value for deed in Deed}


@pytest.mark.parametrize(
    "dropped", list(UNKEPT)[1:], ids=lambda keeps: "-".join(sorted(k.name for k in keeps))
)
def test_each_keep_the_adr_asks_for_is_what_holds_its_rule(dropped: frozenset[Keep]) -> None:
    assert expiry_breaks(model.KEEPS - dropped) == UNKEPT[dropped]


def test_expire_converges_on_every_branch_the_drain_leaves() -> None:
    def unconverged(keeps: frozenset[Keep]) -> list[model.Change]:
        states, _ = model.expiry_graph(keeps)
        return [
            change
            for state in states
            for name, change in zip(model.SIBLINGS, state.changes, strict=True)
            if change is not None
            and not change.merged
            and change.stage not in {Stage.BUILT, Stage.STOPPED}
            and model.expire(change, name, keeps)._replace(stage=change.stage) != change
        ]

    assert not unconverged(model.KEEPS), "a second run of expire changes nothing"
    assert unconverged(model.KEEPS - {Keep.UPDATE_EXPIRE}), "an update that skips it leaves work"


@pytest.mark.parametrize("first", [Name.A, Name.C], ids=lambda name: f"{name.value}-lands-first")
def test_whichever_ticket_lands_last_deletes_the_unit_and_carries_its_line(first: Name) -> None:
    last = Name.C if first is Name.A else Name.A
    path = [
        *BUILT_A,
        VERDICT_A,
        *BUILT_C,
        VERDICT_C,
        f"drain.py land {first.value}",
        f"drain.py update {last.value}",
        f"a judge-again run commits the verdict record of {last.value}",
        f"drain.py land {last.value}",
    ]
    end = expiry_walk(path)
    deleter, keeper = end.change(last), end.change(first)
    assert (end.tip.unit, end.tip.brief) == (False, (0,))
    assert deleter is not None
    assert keeper is not None
    assert (deleter.dropped, deleter.recorded) == (True, True)
    assert (keeper.merged, keeper.dropped) == (True, False)


def test_a_records_edit_between_expire_and_update_reaches_the_brief_once_and_lands() -> None:
    end = expiry_walk([*EDITED_ACROSS, REJUDGE_C, "drain.py land c"])
    assert (end.tip.unit, end.tip.brief) == (False, (0, model.OWNERS_LINE))


def test_a_judge_again_run_after_two_updates_reads_the_deleted_spec() -> None:
    path = [
        *BUILT_A,
        *BUILT_C,
        VERDICT_C,
        "drain.py land c",
        "drain.py update a",
        ANSWERED,
        "drain.py update a",
        REJUDGE_A,
        "drain.py land a",
    ]
    end = expiry_walk(path)
    change = end.change(Name.A)
    assert change is not None
    assert (change.history, change.blind) == (True, False)
    assert (end.tip.unit, end.tip.brief) == (False, (0, model.OWNERS_LINE))


def test_the_page_was_checked_and_drawn_from_the_current_exports() -> None:
    page = PLUGIN_ROOT / "docs/model/drain.md"
    assert not (why := model_tla.stale(page, *model.exports())), why


def test_each_module_drawn_whole_holds_no_more_states_than_the_statechart_tool_draws() -> None:
    drawn_whole = {
        "DrainLanding",
        "DrainWork",
        "DrainTicketLife",
        "DrainTicketLifeGated",
        "DrainLife",
        "DrainLeaseShown",
        "DrainExpiryShown",
    }
    tables = {
        export.name: export.module.partition("_ ==\n  <<\n")[2].partition("\n  >>")[0]
        for export in model.exports()
    }
    states = {name: rows.count("\n") + 1 for name, rows in tables.items()}
    assert drawn_whole <= states.keys()
    assert {name: states[name] for name in drawn_whole if states[name] > 500} == {}
