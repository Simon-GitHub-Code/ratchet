"""The route's reference model: what the owner is told next, as a small state machine.

``scripts/route.py`` decides the owner's next command from the checkout, so the repository is the
state and a priority chain is the transition function. This module writes that machine down once,
pure: a frozen ``World`` abstracts the repository, ``advice`` is what the method's pages say the
route names, in the order ``route.decide`` asks, and ``ACTIONS`` is every way the world changes,
each registered with the advice it follows. ``advice`` is the specification. Where the real route
names something else, ``DIVERGENCES`` says so by name, and ``real_advice`` is the specification
with those entries applied: the tests hold the real route to ``real_advice``, so a fix to the route
is red until its entry is deleted. ``docs/model/route.md`` is the page for the owner.

Scope: no forge (no ``origin``), local tickets, offline. Two tickets, the second blocked by the
first. One thing happens at a time: a spec or ticket action needs a clean default branch with
nothing parked. One checkout: no other work tree holds the default branch.
"""

from __future__ import annotations

import functools
from collections import deque
from collections.abc import Callable, Iterator
from dataclasses import dataclass, replace
from enum import StrEnum
from typing import Final, NamedTuple

from tests import model_tla


class Setup(StrEnum):
    """How far the one-time setup has got.

    Nothing; an init that stopped with the purpose placeholder in ``AGENTS.md``; init done; the
    tracker; an adoption that stopped before it wrote the plugin pin; the binding, pinned.
    """

    BARE = "bare"
    STARTED = "started"
    NAMED = "named"
    TRACKED = "tracked"
    ADOPTING = "adopting"
    BOUND = "bound"


class Head(StrEnum):
    """Where HEAD is: the default branch, a working branch, or a ``records/<time>`` branch.

    ``MERGED`` is a working branch the default branch already holds: a merge that landed and left
    the checkout on it. Its commits are merged, so a new commit makes it a working branch again.
    """

    MAIN = "main"
    WORK = "work"
    MERGED = "merged"
    RECORDS = "records"


class Tree(StrEnum):
    """What the work tree holds: nothing, records alone, or more than records (code, the brief)."""

    CLEAN = "clean"
    RECORDS = "records"
    CODE = "code"


class Spec(StrEnum):
    """A spec not yet split into tickets: none, written, or stating its conjecture."""

    NONE = "none"
    BARE = "bare"
    STATED = "stated"


class Ticket(StrEnum):
    """A ticket's life.

    None yet, open for triage, agent-ready with or without a conjecture; held by a blocked
    feature run's stash; held by the drain's note for open questions, for guard edits, for an owed
    safeguard, or by a note written before the drain recorded kinds or the base commit that
    landing guard edits is read from; built on a branch, merged but
    still open, or resolved. A ticket parked for guard edits the owner has since landed is not a
    state of its own: landing them releases the hold, so the route reads it as agent-ready again.
    A guard hold whose note keeps no base commit cannot be released that way: the route reads it
    as ``PARKED``, which only a change to the ticket releases.
    """

    NONE = "none"
    TRIAGE = "triage"
    READY = "ready"
    STATED = "stated"
    HELD = "held"
    ASKED = "asked"
    GUARDED = "guarded"
    OWED = "owed"
    PARKED = "parked"
    BUILT = "built"
    MERGED = "merged"
    RESOLVED = "resolved"


class Architecture(StrEnum):
    """The architecture's record (ADR 0049).

    Not asked: no decision record and no declared parts, before adoption or after an adoption by
    a plugin from before the architecture was asked; owed: an upgrade wrote the ``architecture``
    check's entries in ``quality/owed.toml``; recorded: the record and the binding's parts exist.
    """

    UNASKED = "unasked"
    OWED = "owed"
    RECORDED = "recorded"


class Advice(StrEnum):
    """What the route tells the owner: one label per distinct next step."""

    INIT = "init"
    TRACKER = "tracker"
    ADOPT = "adopt"
    HOOKS = "hooks"
    FINISH_RECORDS = "finish-records"
    LAND_RECORDS = "land-records"
    COMMIT = "commit"
    SWITCH = "switch"
    MERGE = "merge"
    MERGE_RECORDS = "merge-records"
    FILL = "fill"
    REVIEW = "review"
    SAFEGUARD = "safeguard"
    ARCHITECTURE = "architecture"
    GRILL = "grill"
    TRIAGE = "triage"
    CONJECTURE_SPEC = "conjecture-spec"
    SPLIT = "split"
    SPECIFY = "specify"
    RESOLVE = "resolve"
    BLOCKED = "blocked"
    BLOCKED_RUN = "blocked-run"
    ANSWER = "answer"
    LAND_GUARD = "land-guard"
    CHANGE_TICKET = "change-ticket"
    DRAIN = "drain"
    BUILD = "build"
    CONJECTURE = "conjecture"


class ActionName(StrEnum):
    """Every way the world changes; the value is the name a TLA+ module gives the action."""

    OWNER_INITS = "OwnerInits"
    INIT_STOPS = "InitStops"
    OWNER_SETS_UP_TRACKER = "OwnerSetsUpTracker"
    OWNER_ADOPTS = "OwnerAdopts"
    OLDER_PLUGIN_ADOPTS = "OlderPluginAdopts"
    ADOPTION_STOPS = "AdoptionStops"
    AGENT_INSTALLS_HOOKS = "AgentInstallsHooks"
    OWNER_GRILLS = "OwnerGrills"
    OWNER_WRITES_SPEC = "OwnerWritesSpec"
    OWNER_STATES_SPEC_CONJECTURE = "OwnerStatesSpecConjecture"
    OWNER_SPLITS_SPEC = "OwnerSplitsSpec"
    OWNER_AGREES_TICKET = "OwnerAgreesTicket"
    OWNER_WORKS_TICKET_THEMSELF = "OwnerWorksTicketThemself"
    OWNER_STATES_CONJECTURE = "OwnerStatesConjecture"
    AGENT_BUILDS_TICKET = "AgentBuildsTicket"
    BUILD_STOPS = "BuildStops"
    FEATURE_RUN_BLOCKS = "FeatureRunBlocks"
    OWNER_ANSWERS_BLOCKED_RUN = "OwnerAnswersBlockedRun"
    DRAIN_MERGES_TICKET = "DrainMergesTicket"
    DRAIN_PARKS_TICKET = "DrainParksTicket"
    DRAIN_PARKS_FOR_A_SAFEGUARD = "DrainParksForASafeguard"
    OWNER_ANSWERS_ON_THE_TICKET = "OwnerAnswersOnTheTicket"
    OWNER_LANDS_GUARD_EDITS = "OwnerLandsGuardEdits"
    OWNER_CHANGES_TICKET = "OwnerChangesTicket"
    OWNER_RESOLVES_TICKET = "OwnerResolvesTicket"
    UPGRADE_LEAVES_ROWS_TO_FILL = "UpgradeLeavesRowsToFill"
    AGENT_FILLS_ROWS = "AgentFillsRows"
    KIND_OF_CODE_ARRIVES = "KindOfCodeArrives"
    KIND_THAT_REOPENS_THE_ARCHITECTURE_ARRIVES = "KindThatReopensTheArchitectureArrives"
    AGENT_LANDS_THE_REVIEW = "AgentLandsTheReview"
    AGENT_BINDS_SAFEGUARD = "AgentBindsSafeguard"
    OWNER_RECORDS_ARCHITECTURE = "OwnerRecordsArchitecture"
    SOMEONE_EDITS_CODE = "SomeoneEditsCode"
    AGENT_COMMITS_ON_A_BRANCH = "AgentCommitsOnABranch"
    BRANCH_MERGES = "BranchMerges"
    MERGE_LEAVES_THE_BRANCH_CHECKED_OUT = "MergeLeavesTheBranchCheckedOut"
    SOMEONE_CHECKS_OUT_A_MERGED_BRANCH = "SomeoneChecksOutAMergedBranch"
    AGENT_SWITCHES_TO_THE_DEFAULT_BRANCH = "AgentSwitchesToTheDefaultBranch"
    AGENT_LANDS_RECORDS = "AgentLandsRecords"
    RECORDS_RUN_STOPS = "RecordsRunStops"
    OWNER_MERGES_RECORDS = "OwnerMergesRecords"


Slots = tuple[Ticket, Ticket]
BLOCKED_BY: Final = {1: 0}
HOLDS: Final = frozenset({Ticket.HELD, Ticket.ASKED, Ticket.GUARDED, Ticket.OWED, Ticket.PARKED})
STATED: Final = frozenset({Ticket.STATED})
LABELLED: Final = HOLDS | STATED | frozenset({Ticket.READY, Ticket.BUILT, Ticket.MERGED})
OPEN: Final = LABELLED | {Ticket.TRIAGE}
CLOSED: Final = frozenset({Ticket.NONE, Ticket.RESOLVED})


@dataclass(frozen=True)
class World:
    """The repository as far as the route can tell.

    ``parked``: a ``records/<time>`` branch waits unmerged. ``foundation``: the glossary and the
    design brief are written. ``inherits``: the tickets were split from a spec that states its
    conjecture, so none needs one of its own. ``owes``: a safeguard is owed. ``reviews``: an
    architecture review is owed (ADR 0052). ``fills``: an upgrade left rows an agent writes from
    the repository alone (ADR 0050). ``architecture``: whether the architecture is recorded, owed
    since an upgrade, or not yet asked (ADR 0049).
    """

    setup: Setup = Setup.BARE
    hooks: bool = False
    head: Head = Head.MAIN
    tree: Tree = Tree.CLEAN
    parked: bool = False
    foundation: bool = False
    spec: Spec = Spec.NONE
    tickets: Slots = (Ticket.NONE, Ticket.NONE)
    inherits: bool = False
    owes: bool = False
    reviews: bool = False
    fills: bool = False
    architecture: Architecture = Architecture.UNASKED


def adopted(world: World) -> bool:
    """Whether the setup is done and the commit hooks are in place."""
    return world.setup is Setup.BOUND and world.hooks


def settled(world: World) -> bool:
    """Whether the default branch is checked out clean with nothing parked, after setup."""
    return adopted(world) and (world.head, world.tree, world.parked) == (
        Head.MAIN,
        Tree.CLEAN,
        False,
    )


def calm(world: World) -> bool:
    """Whether the owner can start the next piece of work: settled, with the records written."""
    return settled(world) and world.foundation


def rest(world: World) -> bool:
    """Whether there is nothing left to finish: the route's answer is to write the next spec."""
    return (
        calm(world)
        and world.spec is Spec.NONE
        and not world.owes
        and not world.reviews
        and not world.fills
        and world.architecture is not Architecture.OWED
        and all(ticket in CLOSED for ticket in world.tickets)
    )


def with_ticket(world: World, slot: int, ticket: Ticket) -> World:
    """``world`` with the ticket in ``slot`` set to ``ticket``."""
    first, second = world.tickets
    return replace(world, tickets=(ticket, second) if slot == 0 else (first, ticket))


def labelled(world: World) -> list[int]:
    """The slots of the open agent-ready tickets."""
    return [slot for slot, ticket in enumerate(world.tickets) if ticket in LABELLED]


def unblocked(world: World, slot: int) -> bool:
    """Whether the ticket in ``slot`` has no open blocker."""
    blocker = BLOCKED_BY.get(slot)
    return blocker is None or world.tickets[blocker] not in OPEN


def triage_waits(world: World) -> bool:
    """Whether an open ticket with no agent-ready label has no open blocker."""
    return any(
        ticket is Ticket.TRIAGE and unblocked(world, slot)
        for slot, ticket in enumerate(world.tickets)
    )


def free(world: World) -> list[int]:
    """The agent-ready tickets with no open blocker."""
    return [slot for slot in labelled(world) if unblocked(world, slot)]


def buildable(world: World) -> list[int]:
    """The free tickets nothing holds."""
    return [slot for slot in free(world) if world.tickets[slot] not in HOLDS]


def stated(world: World) -> list[int]:
    """The agent-ready tickets that state a conjecture and that nothing holds."""
    return [slot for slot in labelled(world) if world.tickets[slot] in STATED]


def queue_builds(world: World) -> bool:
    """Whether a buildable ticket states its conjecture, so the route names a build."""
    return any(world.tickets[slot] in STATED for slot in buildable(world))


def held_for(world: World, ticket: Ticket) -> bool:
    """Whether no free ticket can be built and the first free one is held as ``ticket``."""
    slots = free(world)
    return bool(slots) and not buildable(world) and world.tickets[slots[0]] is ticket


@dataclass(frozen=True)
class Rule:
    """One link of the advice chain: ``advice`` is the answer when nothing before it held."""

    advice: Advice
    holds: Callable[[World], bool]


def _no_ticket(world: World) -> bool:
    return not labelled(world)


# The order is the order ``route.decide`` asks: setup, an unfinished change, the records, the rows
# an upgrade left for an agent to fill, the architecture review, the safeguard, the architecture an
# upgrade left owed, then the glossary and the tickets. The last rule holds for any world, so the
# chain is total.
CHAIN: Final = (
    Rule(Advice.INIT, lambda w: w.setup in {Setup.BARE, Setup.STARTED}),
    Rule(Advice.TRACKER, lambda w: w.setup is Setup.NAMED),
    Rule(Advice.ADOPT, lambda w: w.setup in {Setup.TRACKED, Setup.ADOPTING}),
    Rule(Advice.HOOKS, lambda w: not w.hooks),
    Rule(Advice.FINISH_RECORDS, lambda w: w.head is Head.RECORDS),
    Rule(Advice.LAND_RECORDS, lambda w: (w.head, w.tree) == (Head.MAIN, Tree.RECORDS)),
    Rule(Advice.COMMIT, lambda w: w.tree is not Tree.CLEAN),
    Rule(Advice.SWITCH, lambda w: w.head is Head.MERGED),
    Rule(Advice.MERGE, lambda w: w.head is Head.WORK),
    Rule(Advice.MERGE_RECORDS, lambda w: w.parked),
    Rule(Advice.FILL, lambda w: w.fills),
    Rule(Advice.REVIEW, lambda w: w.reviews),
    Rule(Advice.SAFEGUARD, lambda w: w.owes),
    Rule(Advice.ARCHITECTURE, lambda w: w.architecture is Architecture.OWED),
    Rule(Advice.GRILL, lambda w: not w.foundation),
    Rule(Advice.TRIAGE, lambda w: _no_ticket(w) and triage_waits(w)),
    Rule(Advice.CONJECTURE_SPEC, lambda w: _no_ticket(w) and w.spec is Spec.BARE),
    Rule(Advice.SPLIT, lambda w: _no_ticket(w) and w.spec is Spec.STATED),
    Rule(Advice.SPECIFY, _no_ticket),
    Rule(Advice.RESOLVE, lambda w: Ticket.MERGED in w.tickets),
    Rule(Advice.BLOCKED, lambda w: not free(w)),
    Rule(Advice.BLOCKED_RUN, lambda w: held_for(w, Ticket.HELD)),
    Rule(Advice.ANSWER, lambda w: held_for(w, Ticket.ASKED)),
    Rule(Advice.LAND_GUARD, lambda w: held_for(w, Ticket.GUARDED)),
    Rule(Advice.SAFEGUARD, lambda w: held_for(w, Ticket.OWED)),
    Rule(Advice.CHANGE_TICKET, lambda w: held_for(w, Ticket.PARKED)),
    Rule(Advice.DRAIN, lambda w: queue_builds(w) and len(stated(w)) > 1),
    Rule(Advice.BUILD, queue_builds),
    Rule(Advice.CONJECTURE, lambda _w: True),
)


def advice(world: World) -> Advice:
    """What the method's pages say the route names in ``world``: the first rule that holds."""
    return next(rule.advice for rule in CHAIN if rule.holds(world))


def beneath(world: World) -> str:
    """The advice once nothing is unfinished in the checkout and nothing is owed.

    A ticket built on its branch is ``building``: its merge is what the checkout shows.
    """
    if Ticket.BUILT in world.tickets:
        return "building"
    clean = replace(world, head=Head.MAIN, tree=Tree.CLEAN, parked=False)
    recorded = replace(clean, architecture=Architecture.RECORDED)
    return advice(replace(recorded, owes=False, reviews=False, fills=False))


@dataclass(frozen=True)
class Divergence:
    """A state where the real route names something other than the pages say, as a fact.

    ``holds`` picks the worlds; ``reads`` is the world as the real route reads it there, so the
    label it gives is ``advice(reads(world))``. ``contradicts`` is the sentence of ``source`` it
    goes against, ``breaks`` the rules that fail there on ``real_advice``, and ``example`` the
    world the replay reaches: the shortest path to it from an empty repository.
    """

    name: str
    holds: Callable[[World], bool]
    reads: Callable[[World], World]
    contradicts: str
    source: str
    breaks: frozenset[str]
    example: World


DIVERGENCES: Final[tuple[Divergence, ...]] = ()


def divergence(world: World) -> Divergence | None:
    """The entry whose reading changes what the route names in ``world``, if one does."""
    return next(
        (
            entry
            for entry in DIVERGENCES
            if entry.holds(world) and advice(entry.reads(world)) is not advice(world)
        ),
        None,
    )


def real_advice(world: World) -> Advice:
    """What the real route names in ``world``: the specification, with the divergences applied."""
    entry = divergence(world)
    return advice(world) if entry is None else advice(entry.reads(world))


Successors = Callable[[World], Iterator[World]]


@dataclass(frozen=True)
class Action:
    """A way the world changes: its name, the advice it follows if any, and its outcomes."""

    name: ActionName
    follows: tuple[Advice, ...]
    successors: Successors


ACTIONS: Final[dict[ActionName, Action]] = {}
ADVISED: Final[dict[Advice, ActionName]] = {}


def action(name: ActionName, *follows: Advice) -> Callable[[Successors], Successors]:
    """Register ``successors`` as action ``name``; each advice in ``follows`` leads to it."""

    def register(successors: Successors) -> Successors:
        ACTIONS[name] = Action(name, follows, successors)
        for advice_label in follows:
            if advice_label in ADVISED:
                message = f"{advice_label} is followed by {ADVISED[advice_label]} and {name}"
                raise ValueError(message)
            ADVISED[advice_label] = name
        return successors

    return register


@action(ActionName.OWNER_INITS, Advice.INIT)
def _owner_inits(world: World) -> Iterator[World]:
    if world.setup in {Setup.BARE, Setup.STARTED}:
        yield replace(world, setup=Setup.NAMED)


@action(ActionName.INIT_STOPS)
def _init_stops(world: World) -> Iterator[World]:
    if world.setup is Setup.BARE:
        yield replace(world, setup=Setup.STARTED)


@action(ActionName.OWNER_SETS_UP_TRACKER, Advice.TRACKER)
def _owner_sets_up_tracker(world: World) -> Iterator[World]:
    if world.setup is Setup.NAMED:
        yield replace(world, setup=Setup.TRACKED)


@action(ActionName.OWNER_ADOPTS, Advice.ADOPT)
def _owner_adopts(world: World) -> Iterator[World]:
    if world.setup in {Setup.TRACKED, Setup.ADOPTING}:
        yield replace(
            world,
            setup=Setup.BOUND,
            head=Head.MAIN,
            tree=Tree.CLEAN,
            architecture=Architecture.RECORDED,
        )


@action(ActionName.OLDER_PLUGIN_ADOPTS)
def _older_plugin_adopts(world: World) -> Iterator[World]:
    """An adoption by a plugin from before ADR 0049, which asked no architecture."""
    if world.setup is Setup.TRACKED:
        yield replace(world, setup=Setup.BOUND)


@action(ActionName.ADOPTION_STOPS)
def _adoption_stops(world: World) -> Iterator[World]:
    """An adoption stops on its branch: before it writes the pin, committed; or just after."""
    if world.setup is Setup.TRACKED:
        yield replace(world, setup=Setup.ADOPTING, head=Head.WORK)
    if world.setup in {Setup.TRACKED, Setup.ADOPTING}:
        yield replace(
            world,
            setup=Setup.BOUND,
            head=Head.WORK,
            tree=Tree.CODE,
            architecture=Architecture.RECORDED,
        )


@action(ActionName.AGENT_INSTALLS_HOOKS, Advice.HOOKS)
def _agent_installs_hooks(world: World) -> Iterator[World]:
    if world.setup is Setup.BOUND and not world.hooks:
        yield replace(world, hooks=True)


@action(ActionName.OWNER_GRILLS, Advice.GRILL)
def _owner_grills(world: World) -> Iterator[World]:
    if settled(world) and not world.foundation:
        # Why: the design brief is no kind of record to the merge judge, so the grill's output goes
        # on a branch like code; only the glossary alone would land as records.
        yield replace(world, foundation=True, tree=Tree.CODE)


@action(ActionName.OWNER_WRITES_SPEC, Advice.SPECIFY)
def _owner_writes_spec(world: World) -> Iterator[World]:
    if calm(world) and world.spec is Spec.NONE and all(t in CLOSED for t in world.tickets):
        yield replace(
            world,
            spec=Spec.BARE,
            tickets=(Ticket.NONE, Ticket.NONE),
            inherits=False,
            tree=Tree.RECORDS,
        )


@action(ActionName.OWNER_STATES_SPEC_CONJECTURE, Advice.CONJECTURE_SPEC)
def _owner_states_spec_conjecture(world: World) -> Iterator[World]:
    if calm(world) and world.spec is Spec.BARE:
        yield replace(world, spec=Spec.STATED, tree=Tree.RECORDS)


@action(ActionName.OWNER_SPLITS_SPEC, Advice.SPLIT)
def _owner_splits_spec(world: World) -> Iterator[World]:
    if calm(world) and world.spec is not Spec.NONE:
        yield replace(
            world,
            spec=Spec.NONE,
            tickets=(Ticket.TRIAGE, Ticket.TRIAGE),
            inherits=world.spec is Spec.STATED,
            tree=Tree.RECORDS,
        )


@dataclass(frozen=True)
class Move:
    """One ticket's step from one of ``before`` to ``after``, and what it leaves behind.

    ``tree`` or ``head`` is what the checkout holds afterwards.
    """

    before: frozenset[Ticket]
    after: Ticket
    needs_free: bool = False
    tree: Tree | None = None
    head: Head | None = None


def runnable(world: World, slot: int) -> bool:
    """Whether a feature run or the drain may start on the ticket in ``slot``.

    Why: ADR 0050. The feature preflight and the drain refuse while rows an agent fills are owed.
    """
    return unblocked(world, slot) and not world.fills


def runnable_slots(world: World) -> list[int]:
    """The slots of the tickets a feature run or the drain may start on."""
    return [slot for slot in range(len(world.tickets)) if runnable(world, slot)]


def moves(world: World, move: Move) -> Iterator[World]:
    """Each way a calm ``world`` takes one ticket through ``move``."""
    if not calm(world):
        return
    for slot, ticket in enumerate(world.tickets):
        if ticket in move.before and (not move.needs_free or runnable(world, slot)):
            moved = with_ticket(world, slot, move.after)
            yield replace(moved, tree=move.tree or moved.tree, head=move.head or moved.head)


def per_ticket(name: ActionName, follows: tuple[Advice, ...], move: Move) -> None:
    """Register the action that takes one ticket through ``move``."""
    action(name, *follows)(lambda world: moves(world, move))


def one(ticket: Ticket) -> frozenset[Ticket]:
    return frozenset({ticket})


@action(ActionName.OWNER_AGREES_TICKET, Advice.TRIAGE)
def _owner_agrees_ticket(world: World) -> Iterator[World]:
    after = Ticket.STATED if world.inherits else Ticket.READY
    yield from moves(world, Move(one(Ticket.TRIAGE), after))


per_ticket(
    ActionName.OWNER_WORKS_TICKET_THEMSELF,
    (Advice.BLOCKED,),
    Move(one(Ticket.TRIAGE), Ticket.RESOLVED),
)
per_ticket(
    ActionName.OWNER_STATES_CONJECTURE,
    (Advice.CONJECTURE,),
    Move(one(Ticket.READY), Ticket.STATED),
)
per_ticket(
    ActionName.AGENT_BUILDS_TICKET,
    (Advice.BUILD,),
    Move(STATED, Ticket.BUILT, needs_free=True, head=Head.WORK),
)


@action(ActionName.BUILD_STOPS)
def _build_stops(world: World) -> Iterator[World]:
    """The build stops with its code uncommitted, or on the branch it cut before it wrote any.

    Why the second is a merged branch: a branch cut from the default branch's tip and holding no
    commit of its own is in the default branch, and the route reads it so.
    """
    yield from moves(
        world, Move(STATED, Ticket.BUILT, needs_free=True, head=Head.WORK, tree=Tree.CODE)
    )
    if calm(world) and any(world.tickets[slot] in STATED for slot in runnable_slots(world)):
        yield replace(world, head=Head.MERGED)


per_ticket(
    ActionName.FEATURE_RUN_BLOCKS,
    (),
    Move(one(Ticket.STATED), Ticket.HELD, needs_free=True),
)
per_ticket(
    ActionName.OWNER_ANSWERS_BLOCKED_RUN,
    (Advice.BLOCKED_RUN,),
    Move(one(Ticket.HELD), Ticket.STATED),
)
per_ticket(
    ActionName.DRAIN_MERGES_TICKET,
    (Advice.DRAIN,),
    Move(STATED, Ticket.MERGED, needs_free=True),
)


@action(ActionName.DRAIN_PARKS_TICKET)
def _drain_parks_ticket(world: World) -> Iterator[World]:
    for kind in (Ticket.ASKED, Ticket.GUARDED, Ticket.PARKED):
        yield from moves(world, Move(one(Ticket.STATED), kind, needs_free=True))


@action(ActionName.DRAIN_PARKS_FOR_A_SAFEGUARD)
def _drain_parks_for_a_safeguard(world: World) -> Iterator[World]:
    """The drain merges the first ticket, which brings a new kind, and parks the second on it."""
    if (
        calm(world)
        and not (world.owes or world.fills)
        and world.tickets == (Ticket.STATED, Ticket.STATED)
    ):
        yield replace(world, tickets=(Ticket.MERGED, Ticket.OWED), owes=True)


per_ticket(
    ActionName.OWNER_ANSWERS_ON_THE_TICKET,
    (Advice.ANSWER,),
    Move(one(Ticket.ASKED), Ticket.STATED),
)
per_ticket(
    ActionName.OWNER_LANDS_GUARD_EDITS,
    (Advice.LAND_GUARD,),
    Move(one(Ticket.GUARDED), Ticket.STATED),
)
per_ticket(
    ActionName.OWNER_CHANGES_TICKET,
    (Advice.CHANGE_TICKET,),
    Move(one(Ticket.PARKED), Ticket.STATED),
)
per_ticket(
    ActionName.OWNER_RESOLVES_TICKET,
    (Advice.RESOLVE,),
    Move(one(Ticket.MERGED), Ticket.RESOLVED, tree=Tree.RECORDS),
)


@action(ActionName.UPGRADE_LEAVES_ROWS_TO_FILL)
def _upgrade_leaves_rows_to_fill(world: World) -> Iterator[World]:
    """An upgrade at rest: rows to fill, and the architecture where none was asked (ADR 0049)."""
    if rest(world) and world.tickets == (Ticket.RESOLVED, Ticket.RESOLVED):
        owed = world.architecture is Architecture.UNASKED
        yield replace(
            world, fills=True, architecture=Architecture.OWED if owed else world.architecture
        )


@action(ActionName.AGENT_FILLS_ROWS, Advice.FILL)
def _agent_fills_rows(world: World) -> Iterator[World]:
    if calm(world) and world.fills:
        yield replace(world, fills=False)


@action(ActionName.KIND_OF_CODE_ARRIVES)
def _kind_of_code_arrives(world: World) -> Iterator[World]:
    if rest(world) and Ticket.RESOLVED in world.tickets:
        yield replace(world, owes=True)


@action(ActionName.KIND_THAT_REOPENS_THE_ARCHITECTURE_ARRIVES)
def _kind_that_reopens_the_architecture_arrives(world: World) -> Iterator[World]:
    """The first instance of a kind that reopens the architecture and owes no row of its own.

    Why no row: a kind that owes both is a state with both owed, which takes the graph past what
    a chart can draw; ``test_architecture_review`` holds the order there, the review and then the
    safeguard.
    """
    if rest(world) and Ticket.RESOLVED in world.tickets:
        yield replace(world, reviews=True)


@action(ActionName.AGENT_LANDS_THE_REVIEW, Advice.REVIEW)
def _agent_lands_the_review(world: World) -> Iterator[World]:
    if calm(world) and world.reviews:
        yield replace(world, reviews=False)


@action(ActionName.AGENT_BINDS_SAFEGUARD, Advice.SAFEGUARD)
def _agent_binds_safeguard(world: World) -> Iterator[World]:
    if calm(world) and world.owes:
        first, second = (Ticket.STATED if t is Ticket.OWED else t for t in world.tickets)
        yield replace(world, owes=False, tickets=(first, second))


@action(ActionName.OWNER_RECORDS_ARCHITECTURE, Advice.ARCHITECTURE)
def _owner_records_architecture(world: World) -> Iterator[World]:
    """``/ratchet:architecture``: the record, the parts and the settled ledger, on a branch.

    Why the branch is left to merge: the record and the binding are guard paths, so the merge
    judge leaves the merge to the owner.
    """
    if calm(world) and world.architecture is Architecture.OWED:
        yield replace(world, architecture=Architecture.RECORDED, head=Head.WORK)


def on_a_clean_branch(world: World, *heads: Head) -> bool:
    """Whether the setup is done and a clean branch of ``heads`` is checked out."""
    return adopted(world) and world.head in heads and world.tree is Tree.CLEAN


@action(ActionName.SOMEONE_EDITS_CODE)
def _someone_edits_code(world: World) -> Iterator[World]:
    """An edit at rest, on a clean working branch, or on a merged branch checked out at rest.

    Why a merged branch only at rest: what the route makes of its dirty tree does not depend on
    the tickets or on what is owed, and each other state it is edited in adds a working branch.
    """
    at_rest = on_a_clean_branch(world, Head.MERGED) and rest(replace(world, head=Head.MAIN))
    if rest(world) or on_a_clean_branch(world, Head.WORK) or at_rest:
        yield replace(world, tree=Tree.CODE)


@action(ActionName.AGENT_COMMITS_ON_A_BRANCH, Advice.COMMIT)
def _agent_commits_on_a_branch(world: World) -> Iterator[World]:
    if adopted(world) and world.tree is Tree.CODE:
        yield replace(world, tree=Tree.CLEAN, head=Head.WORK)


def merged(world: World, head: Head) -> World:
    """``world`` once its working branch is merged, the checkout left on ``head``."""
    first, second = (Ticket.MERGED if t is Ticket.BUILT else t for t in world.tickets)
    return replace(world, head=head, tickets=(first, second))


@action(ActionName.BRANCH_MERGES, Advice.MERGE)
def _branch_merges(world: World) -> Iterator[World]:
    """The merge entry point merges the branch and switches the checkout to the default branch.

    Why it can be run on a branch already merged, and changes nothing there: the merge judge
    refuses it, behind a merge commit that holds it, or with nothing of its own to judge.
    """
    if on_a_clean_branch(world, Head.WORK):
        yield merged(world, Head.MAIN)
    if on_a_clean_branch(world, Head.MERGED):
        yield world


@action(ActionName.MERGE_LEAVES_THE_BRANCH_CHECKED_OUT)
def _merge_leaves_the_branch_checked_out(world: World) -> Iterator[World]:
    """The default branch holds the branch, and the checkout is still on it.

    It stands for a merge cut off after it moved the default branch and before it switched, a
    merge that could not switch, and the owner's merge of the branch from another checkout.
    """
    if on_a_clean_branch(world, Head.WORK):
        yield merged(world, Head.MERGED)


@action(ActionName.SOMEONE_CHECKS_OUT_A_MERGED_BRANCH)
def _someone_checks_out_a_merged_branch(world: World) -> Iterator[World]:
    """A merge keeps the branch it merged, so it can be checked out again at rest."""
    if rest(world):
        yield replace(world, head=Head.MERGED)


@action(ActionName.AGENT_SWITCHES_TO_THE_DEFAULT_BRANCH, Advice.SWITCH)
def _agent_switches_to_the_default_branch(world: World) -> Iterator[World]:
    if on_a_clean_branch(world, Head.MERGED):
        yield replace(world, head=Head.MAIN)


@action(ActionName.AGENT_LANDS_RECORDS, Advice.LAND_RECORDS, Advice.FINISH_RECORDS)
def _agent_lands_records(world: World) -> Iterator[World]:
    if adopted(world) and (
        (world.head, world.tree) == (Head.MAIN, Tree.RECORDS) or world.head is Head.RECORDS
    ):
        yield replace(world, head=Head.MAIN, tree=Tree.CLEAN)
        yield replace(world, head=Head.MAIN, tree=Tree.CLEAN, parked=True)


@action(ActionName.RECORDS_RUN_STOPS)
def _records_run_stops(world: World) -> Iterator[World]:
    if adopted(world) and (world.head, world.tree) == (Head.MAIN, Tree.RECORDS):
        yield replace(world, head=Head.RECORDS)


@action(ActionName.OWNER_MERGES_RECORDS, Advice.MERGE_RECORDS)
def _owner_merges_records(world: World) -> Iterator[World]:
    if adopted(world) and world.parked and (world.head, world.tree) == (Head.MAIN, Tree.CLEAN):
        yield replace(world, parked=False)


class Edge(NamedTuple):
    """One way an action takes ``source`` to ``target``."""

    source: World
    action: ActionName
    target: World


@dataclass(frozen=True)
class Graph:
    """Every world reachable from the start, in the order a breadth-first walk finds them."""

    states: tuple[World, ...]
    edges: tuple[Edge, ...]

    def followed(self, label: Callable[[World], Advice]) -> dict[World, tuple[World, ...]]:
        """For each world, the worlds the action that ``label`` names there can lead to."""
        out: dict[World, list[World]] = {state: [] for state in self.states}
        for source, name, target in self.edges:
            if name is ADVISED[label(source)]:
                out[source].append(target)
        return {state: tuple(targets) for state, targets in out.items()}


@functools.cache
def graph(start: World = World()) -> Graph:  # noqa: B008 # -- World is frozen
    """The reachable worlds and the edges between them, from ``start``."""
    seen = {start: None}
    edges: list[Edge] = []
    todo = deque([start])
    while todo:
        source = todo.popleft()
        for name, act in ACTIONS.items():
            for target in act.successors(source):
                edges.append(Edge(source, name, target))
                if target not in seen:
                    seen[target] = None
                    todo.append(target)
    return Graph(tuple(seen), tuple(edges))


LOOPS: Final = -1


@functools.cache
def ranks(of: Graph) -> dict[World, int]:
    """Each world's longest run of advised steps to rest, or ``LOOPS`` when advice never gets there.

    A world is ``LOOPS`` when following advice can return to a world already passed (resting is
    the one place a run may start again) or has no advised step to take.
    """
    advised = of.followed(advice)
    found: dict[World, int] = {}

    def rank(world: World, path: frozenset[World]) -> int:
        if world in found:
            return found[world]
        if rest(world):
            found[world] = 0
        elif world in path:
            return LOOPS
        else:
            below = [rank(target, path | {world}) for target in advised[world]]
            found[world] = LOOPS if not below or LOOPS in below else 1 + max(below)
        return found[world]

    return {state: rank(state, frozenset()) for state in of.states}


def following(label: str) -> str:
    """TLA+ text: the step taken is the action that ``label`` names in the state it leaves."""
    return "\n".join(f'     \\/ ({label} = "{a}" /\\ {act})' for a, act in ADVISED.items())


def can_be_followed(label: str) -> str:
    """TLA+ text: the action that ``label`` names can be taken."""
    return "\n".join(f'  /\\ ({label} = "{a}" => ENABLED {act})' for a, act in ADVISED.items())


def lowers_rank(label: str) -> str:
    """TLA+ text: every step that follows ``label`` away from rest lowers the rank."""
    return f"[][(rank > 0 /\\\n{following(label)}) => rank' < rank]_vars"


def changes_the_state(label: Callable[[World], Advice]) -> Callable[[World], bool]:
    """Whether every way of taking the step ``label`` names in a world leads out of it.

    Why a fact of each state: TLA+ cannot see a step that leaves every variable as it was.
    """

    def check(world: World) -> bool:
        return world not in ACTIONS[ADVISED[label(world)]].successors(world)

    return check


def never_merges_a_merged_branch(label: str) -> str:
    """TLA+ text: on a branch the default branch already holds, ``label`` is not its merge."""
    return f'head = "{Head.MERGED}" => {label} # "{Advice.MERGE}"'


def above_architecture_open(world: World) -> bool:
    """Whether anything the route names before an owed architecture is open in ``world``.

    Said apart from ``CHAIN``, field by field, so a change of the chain's order is a change here.
    """
    return not (
        adopted(world)
        and (world.head, world.tree, world.parked) == (Head.MAIN, Tree.CLEAN, False)
        and not (world.fills or world.reviews or world.owes)
    )


ABOVE_ARCHITECTURE_CLEAR: Final = (
    'setup = "bound" /\\ hooks /\\ head = "main" /\\ tree = "clean" /\\ ~parked'
    " /\\ ~fills /\\ ~reviews /\\ ~owes"
)


def beside_architecture() -> str:
    """TLA+ text: where the owed architecture is named, the step beneath it can be taken."""
    return "\n".join(
        f'  /\\ (advice = "{Advice.ARCHITECTURE}" /\\ beneath = "{a}" => ENABLED {act})'
        for a, act in ADVISED.items()
    )


def only(action_name: ActionName, before: str, after: str) -> str:
    """TLA+ text: a step from ``before`` to ``after`` on the architecture is ``action_name``."""
    return (
        f'[][(architecture {before} "{Architecture.OWED}" /\\'
        f' architecture\' {after} "{Architecture.OWED}") => {action_name}]_vars'
    )


RULES: Final[dict[str, model_tla.Rule]] = {
    "AdviceCanBeFollowed": model_tla.Invariant(
        "The step the route names can be taken.", can_be_followed("advice")
    ),
    "AdviceReachesRest": model_tla.Invariant(
        "Following the route's advice reaches a state with nothing left to finish.", "rank >= 0"
    ),
    "FollowingAdviceLowersRank": model_tla.Property(
        "Each step the route names gets nearer to having nothing left to finish.",
        lowers_rank("advice"),
    ),
    "RealAdviceCanBeFollowed": model_tla.Invariant(
        "The step the real route names can be taken.", can_be_followed("real_advice")
    ),
    "FollowingRealAdviceLowersRank": model_tla.Property(
        "Each step the real route names gets nearer to having nothing left to finish.",
        lowers_rank("real_advice"),
    ),
    "AdviceChangesTheState": model_tla.Invariant(
        "Taking the step the route names changes the state: its effect has not already happened.",
        changes_the_state(advice),
    ),
    "RealAdviceChangesTheState": model_tla.Invariant(
        "Taking the step the real route names changes the state: its effect has not already"
        " happened.",
        changes_the_state(real_advice),
    ),
    "AdviceNeverMergesAMergedBranch": model_tla.Invariant(
        "The route never names the merge of a branch the default branch already holds.",
        never_merges_a_merged_branch("advice"),
    ),
    "RealAdviceNeverMergesAMergedBranch": model_tla.Invariant(
        "The real route never names the merge of a branch the default branch already holds.",
        never_merges_a_merged_branch("real_advice"),
    ),
    "ArchitectureNamedExactlyWhenOwed": model_tla.Invariant(
        "The owed architecture is named exactly when the ledger owes it and nothing named before"
        " it is open.",
        f'(advice = "{Advice.ARCHITECTURE}") <=>\n'
        f'  (architecture = "{Architecture.OWED}" /\\ {ABOVE_ARCHITECTURE_CLEAR})',
    ),
    "OwedArchitectureNeverHidesTheWork": model_tla.Invariant(
        "Where the owed architecture is named, the step the route would otherwise name can be"
        " taken.",
        beside_architecture(),
    ),
    "OnlyAnUpgradeOwesTheArchitecture": model_tla.Property(
        "Only an upgrade leaves the architecture owed; an adoption never does.",
        only(ActionName.UPGRADE_LEAVES_ROWS_TO_FILL, "#", "="),
    ),
    "OnlyRecordingEndsTheOwedArchitecture": model_tla.Property(
        "Recording the architecture is the only step that ends it being owed.",
        only(ActionName.OWNER_RECORDS_ARCHITECTURE, "=", "#"),
    ),
}


class Shown(NamedTuple):
    """What the charts read of a world: what the route names, the checkout, and what is owed."""

    advice: str
    real_advice: str
    beneath: str
    head: str
    tree: str
    parked: bool
    fills: bool
    reviews: bool
    owes: bool
    architecture: str


def shown(world: World) -> Shown:
    """What the charts read of ``world``."""
    return Shown(
        advice(world),
        real_advice(world),
        beneath(world),
        world.head,
        world.tree,
        world.parked,
        world.fills,
        world.reviews,
        world.owes,
        world.architecture,
    )


def export() -> model_tla.Export:
    """The reachable graph as a TLA+ module, with what the route names and how far rest is."""
    rank = ranks(graph())
    return model_tla.export(
        "Route",
        graph(),
        RULES,
        derived={
            "advice": advice,
            "real_advice": real_advice,
            "rank": rank.__getitem__,
            "beneath": beneath,
        },
    )


def summary() -> model_tla.Export:
    """The graph seen through what the charts show, small enough for the statechart tool."""
    return model_tla.export(
        "RouteShown", model_tla.summary(graph(), shown), {}, fields=Shown._fields
    )
