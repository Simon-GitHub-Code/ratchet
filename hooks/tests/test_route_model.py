"""The route against its reference model: the next command is what the model says it should be.

``route_model`` is the machine, pure. ``advice`` is what the method's pages say the route names,
and these tests hold it to every rule. ``real_advice`` is that with the named divergences applied:
it may break a rule only where a divergence says, and the real ``route.decide`` must give it in
every state. A sweep takes every step of the model on a real repository; a stateful test takes
long runs of actions there; each divergence keeps a strict expected failure that passes the day
the route is fixed. The exporter is held to a stable output, and the charts to the export.
"""

import itertools
import os
import shutil
import subprocess
import sys
import tempfile
from collections import defaultdict
from collections.abc import Callable
from functools import cache
from pathlib import Path
from typing import NamedTuple

import pytest
from hypothesis import Phase, settings
from hypothesis import strategies as st
from hypothesis.stateful import (
    RuleBasedStateMachine,
    initialize,
    invariant,
    precondition,
    rule,
    run_state_machine_as_test,
)

import hooks_installed
import route
from tests import model_tla
from tests import route_model as model
from tests import route_world as real
from tests.repos import git
from tests.route_model import ActionName, Advice, Setup, Ticket, World
from tests.support import PLUGIN_ROOT

HOOKS_DIRECTORY = Path(__file__).resolve().parents[1]
CHARTS = PLUGIN_ROOT / "docs/model/route.md"
HOOKS_EVERY = 7  # the steps between the times a run takes the commit hooks away
SWEEP_CHUNKS = 16

# Why no shrinking: every step runs real git, and the message names the whole path to the first
# disagreement, so a red run ends in seconds, not in the minutes Hypothesis spends shrinking.
STEPS = settings(
    max_examples=4,
    stateful_step_count=28,
    derandomize=True,
    deadline=None,
    phases=(Phase.explicit, Phase.generate),
)


def test_every_advice_is_followed_by_exactly_one_action() -> None:
    assert set(model.ADVISED) == set(Advice)
    followed = [advice for act in model.ACTIONS.values() for advice in act.follows]
    assert sorted(followed) == sorted(Advice)
    assert set(model.ACTIONS) == set(ActionName)


def test_every_advice_the_route_can_give_is_met_in_some_state() -> None:
    assert {model.advice(state) for state in model.graph().states} == set(Advice)


def test_every_action_the_model_has_happens_in_some_state() -> None:
    assert {edge.action for edge in model.graph().edges} == set(ActionName)


Label = Callable[[World], Advice]


def cannot_be_followed(label: Label) -> set[World]:
    """The states where the action ``label`` names has no step to take."""
    taken = defaultdict(set)
    for edge in model.graph().edges:
        taken[edge.source].add(edge.action)
    return {s for s in model.graph().states if model.ADVISED[label(s)] not in taken[s]}


def never_rests(_label: Label) -> set[World]:
    """The states from which following the specification's advice never reaches rest."""
    return {s for s, rank in model.ranks(model.graph()).items() if rank == model.LOOPS}


def rank_kept(label: Label) -> set[World]:
    """The states with a step that follows ``label`` away from rest and does not lower the rank."""
    ranks = model.ranks(model.graph())
    return {
        source
        for source, name, target in model.graph().edges
        if name is model.ADVISED[label(source)] and 0 < ranks[source] <= ranks[target]
    }


CHECKS: dict[str, tuple[Callable[[Label], set[World]], Label]] = {
    "AdviceCanBeFollowed": (cannot_be_followed, model.advice),
    "AdviceReachesRest": (never_rests, model.advice),
    "FollowingAdviceLowersRank": (rank_kept, model.advice),
    "RealAdviceCanBeFollowed": (cannot_be_followed, model.real_advice),
    "FollowingRealAdviceLowersRank": (rank_kept, model.real_advice),
}


def test_every_rule_the_export_names_is_checked_here() -> None:
    assert set(CHECKS) == set(model.RULES)


@pytest.mark.parametrize("name", list(CHECKS))
def test_a_rule_breaks_only_where_a_divergence_says_it_does(name: str) -> None:
    """On ``advice`` every rule holds; on ``real_advice`` it fails exactly where an entry says."""
    check, label = CHECKS[name]
    explained = {
        state
        for state in model.graph().states
        if (entry := model.divergence(state)) and name in entry.breaks
    }
    assert check(label) == explained
    assert label is model.real_advice or not explained


def advice_loop(graph: model.Graph) -> list[World]:
    """A run of advised steps that returns to a world it passed, resting aside; empty when none."""
    follows = {
        state: () if model.rest(state) else targets
        for state, targets in graph.followed(model.advice).items()
    }
    done: set[World] = set()

    def visit(state: World, path: list[World]) -> list[World]:
        if state in path:
            return [*path[path.index(state) :], state]
        if state in done:
            return []
        for target in follows[state]:
            if loop := visit(target, [*path, state]):
                return loop
        done.add(state)
        return []

    return next((loop for state in graph.states if (loop := visit(state, []))), [])


def test_following_advice_never_loops_except_through_rest() -> None:
    loop = advice_loop(model.graph())
    assert not loop, " -> ".join(model.advice(state) for state in loop)


def test_every_state_reaches_rest_by_some_actions() -> None:
    graph = model.graph()
    before = defaultdict(set)
    for edge in graph.edges:
        before[edge.target].add(edge.source)
    reaching = {state for state in graph.states if model.rest(state)}
    todo = list(reaching)
    while todo:
        for source in before[todo.pop()] - reaching:
            reaching.add(source)
            todo.append(source)
    assert set(graph.states) - reaching == set()


RELEASED_BY = {
    Ticket.HELD: ActionName.OWNER_ANSWERS_BLOCKED_RUN,
    Ticket.ASKED: ActionName.OWNER_ANSWERS_ON_THE_TICKET,
    Ticket.GUARDED: ActionName.OWNER_LANDS_GUARD_EDITS,
    Ticket.OWED: ActionName.AGENT_BINDS_SAFEGUARD,
    Ticket.PARKED: ActionName.OWNER_CHANGES_TICKET,
}


@pytest.mark.parametrize("hold", sorted(model.HOLDS))
def test_a_hold_is_released_by_its_own_step_and_by_no_other(hold: Ticket) -> None:
    """Each kind of hold has the one step the route names; any other change leaves it held.

    So a ticket parked for guard edits stays parked when another ticket is agreed or the binding
    changes, both commits on the default branch, and a note older than the kinds of hold is
    released only by a change to the ticket.
    """
    kept: set[ActionName] = set()
    released: set[ActionName] = set()
    for source, name, target in model.graph().edges:
        for slot, ticket in enumerate(source.tickets):
            if ticket is hold:
                (kept if target.tickets[slot] is hold else released).add(name)
    assert released == {RELEASED_BY[hold]}
    assert kept


def test_the_graph_is_the_same_every_time_it_is_walked() -> None:
    walked = model.graph.__wrapped__()
    assert walked == model.graph()
    assert walked.states[0] == World()


@pytest.mark.parametrize("entry", model.DIVERGENCES, ids=lambda entry: entry.name)
def test_a_divergence_names_a_sentence_of_the_pages_and_a_state_where_it_holds(
    entry: model.Divergence,
) -> None:
    page = (PLUGIN_ROOT / entry.source).read_text(encoding="utf-8")
    assert entry.contradicts in " ".join(page.split())
    assert entry.example in model.graph().states
    assert model.divergence(entry.example) is entry
    assert entry.breaks <= set(model.RULES)


def test_the_exporter_names_every_action_rule_and_advice() -> None:
    exported = model.export()
    for name in ActionName:
        assert f"\n{name} ==\n" in exported.module
    (following,) = (line for line in exported.module.splitlines() if line.startswith("Next == "))
    assert set(following.removeprefix("Next == ").split(" \\/ ")) == set(ActionName)
    for label in Advice:
        assert f'"{label}"' in exported.module
    for name, checked in model.RULES.items():
        kind = "INVARIANT" if isinstance(checked, model_tla.Invariant) else "PROPERTY"
        assert f"\n{kind} {name}\n" in exported.config
        assert f"\\* {checked.says}\n{name} ==\n" in exported.module
    assert exported.module.startswith("---- MODULE Route ----\n")
    assert exported.module.endswith("\n====\n")


def test_the_exporter_writes_the_same_bytes_whatever_the_hash_seed(tmp_path: Path) -> None:
    for seed in ("1", "2"):
        subprocess.run(  # noqa: S603 # -- a fixed argv, no shell
            [sys.executable, "-m", "tests.model_tla", "tests.route_model", str(tmp_path / seed)],
            cwd=HOOKS_DIRECTORY,
            env={**os.environ, "PYTHONHASHSEED": seed},
            check=True,
            capture_output=True,
        )
    for name in ("Route.tla", "Route.cfg"):
        assert (tmp_path / "1" / name).read_bytes() == (tmp_path / "2" / name).read_bytes()
    assert (tmp_path / "1" / "Route.tla").read_text(encoding="utf-8") == model.export().module


def test_the_charts_were_drawn_from_the_current_export() -> None:
    assert not (why := model_tla.stale(CHARTS, model.export())), why


def follow(repo: real.Repo, world: World, outcome: int) -> tuple[World, list[Advice]]:
    """Take the advised action, its ``outcome``-th outcome, until rest; the advice at each stop.

    The route is asked at every stop, rest included, and must name what the model says it names.
    """
    labels: list[Advice] = []
    while not labels or not model.rest(world):
        expected = model.real_advice(world)
        step = real.read(repo)
        assert real.classify(step) is expected, (labels, expected, step)
        labels.append(model.advice(world))
        name = model.ADVISED[model.advice(world)]
        after = list(model.ACTIONS[name].successors(world))[outcome]
        real.apply(repo, name, real.Change(world, after))
        world = after
    assert real.classify(real.read(repo)) is model.real_advice(world)
    return world, labels


def test_following_the_advice_from_an_empty_repository_reaches_rest(tmp_path: Path) -> None:
    world, labels = follow(real.Repo.start(tmp_path), World(), 0)
    assert model.rest(world)
    assert labels == [
        Advice.INIT,
        Advice.TRACKER,
        Advice.ADOPT,
        Advice.HOOKS,
        Advice.GRILL,
        Advice.COMMIT,
        Advice.MERGE,
    ]


def test_following_the_advice_after_an_edit_at_rest_commits_it_then_merges_it(
    tmp_path: Path,
) -> None:
    repo = real.Repo.start(tmp_path)
    world, _ = follow(repo, World(), 0)
    edited = next(model.ACTIONS[ActionName.SOMEONE_EDITS_CODE].successors(world))
    real.apply(repo, ActionName.SOMEONE_EDITS_CODE, real.Change(world, edited))
    world, labels = follow(repo, edited, 0)
    assert model.rest(world)
    assert labels == [Advice.COMMIT, Advice.MERGE]


@pytest.mark.parametrize("outcome", [0, -1], ids=["first outcomes", "last outcomes"])
def test_following_the_advice_through_a_whole_feature_returns_to_rest(
    tmp_path: Path, outcome: int
) -> None:
    repo = real.Repo.start(tmp_path)
    world, _ = follow(repo, World(), outcome)
    world, labels = follow(repo, world, outcome)
    assert model.rest(world)
    assert labels[0] is Advice.SPECIFY
    assert Advice.RESOLVE in labels


class Spanning(NamedTuple):
    """The graph's shortest-path tree, its states in the order a depth-first walk visits them."""

    order: tuple[World, ...]
    place: dict[World, int]
    parent: dict[World, model.Edge]
    children: dict[World, list[World]]
    size: dict[World, int]

    def trail(self, world: World) -> list[str]:
        """The actions on the tree's path from the start to ``world``."""
        names: list[str] = []
        while world in self.parent:
            names.insert(0, str(self.parent[world].action))
            world = self.parent[world].source
        return names


@cache
def spanning() -> Spanning:
    """The tree, each state with its edge from its parent and the size of the tree below it."""
    graph = model.graph()
    parent: dict[World, model.Edge] = {}
    children: dict[World, list[World]] = defaultdict(list)
    for edge in graph.edges:
        if edge.target not in parent and edge.target != graph.states[0]:
            parent[edge.target] = edge
            children[edge.source].append(edge.target)
    order: list[World] = []
    size: dict[World, int] = {}

    def visit(state: World) -> int:
        order.append(state)
        size[state] = 1 + sum(visit(child) for child in children[state])
        return size[state]

    visit(graph.states[0])
    place = {state: index for index, state in enumerate(order)}
    return Spanning(tuple(order), place, parent, children, size)


@cache
def leaving() -> dict[World, list[model.Edge]]:
    """Each state's steps, in the order the graph lists them."""
    out: dict[World, list[model.Edge]] = defaultdict(list)
    for edge in model.graph().edges:
        out[edge.source].append(edge)
    return out


def ask(repo: real.Repo, world: World, path: list[str]) -> None:
    """The route on ``repo`` names what the model says it names in ``world``."""
    step = real.read(repo)
    assert real.classify(step) is model.real_advice(world), disagreement(world, step, path)


def sweep(repo: real.Repo, world: World, span: range, *, scratch: Path) -> int:
    """Take every step out of each state below ``world`` whose place in the walk is in ``span``.

    Each state is built once, from its parent in the walk. A step to a state the walk goes on
    into is taken on the repository that state keeps; any other is taken on a copy, which is
    asked and thrown away. The route is asked after every step. Returns the steps taken.
    """
    tree = spanning()
    path = tree.trail(world)
    here = tree.place[world] in span
    below = [
        child
        for child in tree.children[world]
        if tree.place[child] < span.stop and tree.place[child] + tree.size[child] > span.start
    ]
    taken = 0
    for index, edge in enumerate(leaving()[world] if here else []):
        if edge.target in below and tree.parent[edge.target] is edge:
            continue
        copy = repo.copy(scratch / f"{tree.place[world]}-{index}")
        real.apply(copy, edge.action, real.Change(world, edge.target))
        ask(copy, edge.target, [*path, edge.action])
        shutil.rmtree(copy.root)
        taken += 1
    for index, child in enumerate(below):
        edge = tree.parent[child]
        last = index == len(below) - 1
        down = repo if last else repo.copy(scratch / str(tree.place[child]))
        real.apply(down, edge.action, real.Change(world, child))
        if here:
            ask(down, child, [*path, edge.action])
            taken += 1
        taken += sweep(down, child, span, scratch=scratch)
    return taken


@cache
def spans() -> list[range]:
    """The walk's places cut into chunks that each take about as many steps."""
    tree = spanning()
    steps = [len(leaving()[state]) for state in tree.order]
    total, bounds, done = sum(steps), [0], 0
    for place, count in enumerate(steps):
        done += count
        if done * SWEEP_CHUNKS >= total * len(bounds) and len(bounds) < SWEEP_CHUNKS:
            bounds.append(place + 1)
    bounds.append(len(tree.order))
    return [range(start, stop) for start, stop in itertools.pairwise(bounds)]


def test_the_chunks_cover_every_state_once() -> None:
    assert [place for span in spans() for place in span] == list(range(len(spanning().order)))
    assert len(spans()) == SWEEP_CHUNKS


@pytest.fixture
def hooks_checked_once(monkeypatch: pytest.MonkeyPatch) -> None:
    """``hooks_installed.check`` run once for each set of hook files, its green verdict kept.

    Why: it runs both hooks, a third of what a route call costs, and once they are installed every
    repository the sweep builds holds the same hook files under the same git config. A red verdict
    is never kept, so a repository without its hooks is checked again each time.
    """
    check = hooks_installed.check
    green: set[tuple[bytes | None, ...]] = set()

    def checked(root: Path) -> hooks_installed.Problem | None:
        files = [root / ".git/config", *(root / ".git/hooks" / n for n in hooks_installed.HOOKS)]
        key = tuple(path.read_bytes() if path.is_file() else None for path in files)
        if key in green:
            return None
        problem = check(root)
        if problem is None:
            green.add(key)
        return problem

    monkeypatch.setattr(hooks_installed, "check", checked)


@pytest.mark.usefixtures("hooks_checked_once")
@pytest.mark.parametrize("chunk", range(SWEEP_CHUNKS))
def test_the_route_names_what_the_model_says_after_every_step(tmp_path: Path, chunk: int) -> None:
    span = spans()[chunk]
    repo = real.Repo.start(tmp_path)
    if 0 in span:
        ask(repo, World(), [])
    taken = sweep(repo, World(), span, scratch=tmp_path / "copies")
    tree = spanning()
    assert taken == sum(len(leaving()[tree.order[place]]) for place in span)


def disagreement(world: World, step: route.Step, path: list[str]) -> str:
    """The path taken, the world, what the model says the route names and what it said."""
    entry = model.divergence(world)
    return "\n".join(
        [
            "the route and the model disagree after:",
            *(f"  {index}. {line}" for index, line in enumerate(path, 1)),
            f"world:        {world}",
            f"model:        {model.real_advice(world)}"
            + (f" (divergence {entry.name}; the pages say {model.advice(world)})" if entry else ""),
            f"classified:   {real.classify(step)}",
            f"route stage:  {step.stage}",
            f"route next:   {step.command}",
            f"route why:    {step.why}",
            f"route then:   {step.then}",
        ]
    )


def described(before: World, after: World) -> str:
    """What an action changed, as the fields that differ."""
    fields = (f for f in World.__dataclass_fields__ if getattr(before, f) != getattr(after, f))
    return ", ".join(f"{f}={getattr(after, f)!s}" for f in fields)


def toward(world: World, aim: Callable[[World], bool]) -> list[model.Edge]:
    """The first steps of the shortest runs from ``world`` to a world ``aim`` picks."""
    out: dict[World, list[model.Edge]] = defaultdict(list)
    for edge in model.graph().edges:
        out[edge.source].append(edge)
    frontier: dict[World, dict[model.Edge, None]] = {world: {}}
    seen = {world}
    while frontier:
        found = {
            first: None for state, firsts in frontier.items() if aim(state) for first in firsts
        }
        if found:
            return list(found)
        step: dict[World, dict[model.Edge, None]] = {}
        for state, firsts in frontier.items():
            for edge in out[state]:
                if edge.target not in seen:
                    step.setdefault(edge.target, {}).update(firsts or {edge: None})
        seen |= set(step)
        frontier = step
    return []


def path_to(target: World) -> list[model.Edge]:
    """The edges of a shortest run from the start to ``target``."""
    path: list[model.Edge] = []
    world = World()
    while world != target:
        path.append(toward(world, lambda state: state == target)[0])
        world = path[-1].target
    return path


@pytest.mark.parametrize(
    "entry",
    [
        pytest.param(
            entry,
            marks=pytest.mark.xfail(
                strict=True,
                reason=f"divergence {entry.name}: delete the entry once the route is fixed",
            ),
            id=entry.name,
        )
        for entry in model.DIVERGENCES
    ],
)
def test_the_route_names_what_the_pages_say_where_it_diverges(
    tmp_path: Path, entry: model.Divergence
) -> None:
    repo = real.Repo.start(tmp_path)
    for source, name, target in path_to(entry.example):
        real.apply(repo, name, real.Change(source, target))
    assert real.classify(real.read(repo)) is model.advice(entry.example)


def test_a_commit_the_hold_did_not_name_leaves_a_guard_hold_and_its_edits_release_it(
    tmp_path: Path,
) -> None:
    """An agreed ticket commits on main under a guard hold; landing the edits then builds.

    Why it stays beside the sweep, which takes both steps too: it also checks that each step moves
    the default branch.
    """
    parked = World(
        setup=Setup.BOUND, hooks=True, foundation=True, tickets=(Ticket.GUARDED, Ticket.TRIAGE)
    )
    repo = real.Repo.start(tmp_path)
    for source, name, target in path_to(parked):
        real.apply(repo, name, real.Change(source, target))
    worlds, tips = [parked], [git(repo.root, "rev-parse", "main")]
    said = [real.classify(real.read(repo))]
    for name in (ActionName.OWNER_AGREES_TICKET, ActionName.OWNER_LANDS_GUARD_EDITS):
        (after,) = model.ACTIONS[name].successors(worlds[-1])
        real.apply(repo, name, real.Change(worlds[-1], after))
        worlds.append(after)
        tips.append(git(repo.root, "rev-parse", "main"))
        said.append(real.classify(real.read(repo)))
    assert len(set(tips)) == len(tips), "each step commits on the default branch"
    expected = [Advice.LAND_GUARD, Advice.LAND_GUARD, Advice.BUILD]
    assert said == [model.real_advice(world) for world in worlds] == expected


def first_outcomes_meet() -> set[Advice]:
    """The advice met by following it, first outcome each time, to rest and through a feature."""
    world, met = World(), set()
    for _ in range(2):
        while True:
            label = model.advice(world)
            met.add(label)
            world = next(model.ACTIONS[model.ADVISED[label]].successors(world))
            if model.rest(world):
                break
    return met


# The advice that walk never meets: each needs a step the advice does not name, or an outcome
# other than the first. The stateful test goes to these.
DETOURS = [
    Advice.FINISH_RECORDS,
    Advice.MERGE_RECORDS,
    Advice.FILL,
    Advice.REVIEW,
    Advice.SAFEGUARD,
    Advice.BLOCKED,
    Advice.BLOCKED_RUN,
    Advice.ANSWER,
    Advice.LAND_GUARD,
    Advice.CHANGE_TICKET,
    Advice.DRAIN,
    Advice.CONJECTURE,
]


def test_the_detours_are_the_advice_the_first_outcomes_never_reach() -> None:
    assert set(DETOURS) == set(Advice) - first_outcomes_meet()


class RouteMachine(RuleBasedStateMachine):
    """The model and a real repository take the same step; the route must name what the model says.

    The setup commands are done at the start: the walk from an empty repository, which names each
    of them, is its own test, and so is the walk through a whole feature. This one goes where those
    do not. Each run is given an order for the detours, and a step heads for the first it has not
    met, so a short run goes deep and runs differ; once it has met every detour it follows the
    advice or takes any action.
    """

    def __init__(self) -> None:
        super().__init__()
        self.scratch = Path(tempfile.mkdtemp(prefix="route-model-"))
        self.repo = real.Repo.start(self.scratch)
        self.world = World()
        self.path: list[str] = []
        self.aims: list[Advice] = []
        self.met: set[Advice] = set()

    def teardown(self) -> None:
        shutil.rmtree(self.scratch, ignore_errors=True)

    def take(self, name: ActionName, after: World) -> None:
        """Do ``name`` on the repository and in the model."""
        real.apply(self.repo, name, real.Change(self.world, after))
        self.path.append(f"{name} ({described(self.world, after)})")
        self.world = after
        self.met.add(model.advice(after))

    @initialize(aims=st.permutations(DETOURS))
    def the_setup_is_done(self, aims: list[Advice]) -> None:
        self.aims = aims
        while not model.adopted(self.world):
            name = model.ADVISED[model.advice(self.world)]
            (after,) = model.ACTIONS[name].successors(self.world)
            self.take(name, after)

    @rule(data=st.data())
    def an_action_happens(self, data: st.DataObject) -> None:
        advised = model.ADVISED[model.advice(self.world)]
        every = [
            (name, after)
            for name, act in model.ACTIONS.items()
            for after in act.successors(self.world)
        ]
        aim = next((advice for advice in self.aims if advice not in self.met), None)
        heading = (
            [
                (edge.action, edge.target)
                for edge in toward(self.world, lambda s: model.advice(s) is aim)
            ]
            if aim
            else []
        )
        followed = [option for option in every if option[0] is advised]
        pool = heading or (followed if data.draw(st.booleans()) else every)
        self.take(*data.draw(st.sampled_from(pool)))

    @invariant()
    def the_route_names_what_the_model_says(self) -> None:
        step = real.read(self.repo)
        assert real.classify(step) is model.real_advice(self.world), disagreement(
            self.world, step, self.path
        )

    @precondition(lambda self: model.adopted(self.world) and len(self.path) % HOOKS_EVERY == 0)
    @invariant()
    def without_its_commit_hooks_the_repository_is_asked_for_them_first(self) -> None:
        """Whatever else is true, a repository that lost its hooks is told to install them."""
        real.remove_hooks(self.repo)
        step = real.read(self.repo)
        real.reinstall_hooks(self.repo)
        assert (step.stage, step.then) == ("hooks", route.NEXT), disagreement(
            self.world, step, self.path
        )


def test_the_route_names_what_the_model_says_on_long_runs_of_a_real_repository() -> None:
    run_state_machine_as_test(RouteMachine, settings=STEPS)
