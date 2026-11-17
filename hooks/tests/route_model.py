from __future__ import annotations

import functools
from collections import deque
from collections.abc import Callable, Iterable, Iterator
from dataclasses import dataclass, replace
from enum import StrEnum
from typing import Final, NamedTuple

from tests import model_tla


class Setup(StrEnum):
    BARE = "bare"
    STARTED = "started"
    NAMED = "named"
    TRACKED = "tracked"
    ADOPTING = "adopting"
    PINNED = "pinned"
    BOUND = "bound"


class Head(StrEnum):
    MAIN = "main"
    WORK = "work"
    MERGED = "merged"
    RECORDS = "records"


class Tree(StrEnum):
    CLEAN = "clean"
    RECORDS = "records"
    CODE = "code"


class Spec(StrEnum):
    NONE = "none"
    BARE = "bare"
    STATED = "stated"


class Ticket(StrEnum):
    NONE = "none"
    TRIAGE = "triage"
    READY = "ready"
    STATED = "stated"
    STALE = "stale"
    HELD = "held"
    ASKED = "asked"
    GUARDED = "guarded"
    OWED = "owed"
    PARKED = "parked"
    OWES_ITS_ROW = "owes-its-row"
    BUILT = "built"
    MERGED = "merged"
    RESOLVED = "resolved"


class Architecture(StrEnum):
    UNASKED = "unasked"
    OWED = "owed"
    RECORDED = "recorded"


class Flag(StrEnum):
    WAITS = "waits"
    MISSING = "missing"
    ASKED = "asked"


class Upkeep(StrEnum):
    CLEAR = "clear"
    BEHIND = "behind"
    WAITING = "waiting"
    WAITING_BEHIND = "waiting-behind"
    TICKET = "ticket"
    UNSTATED = "unstated"
    FIXED = "fixed"


class Defect(StrEnum):
    NONE = "none"
    OPEN = "open"
    KEPT = "kept"


class Answers(StrEnum):
    NONE = "none"
    ON_THE_UNIT = "on-the-unit"
    CARRIED = "carried"
    DROPPED = "dropped"


class Advice(StrEnum):
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
    FORMAL_TICKET = "formal-ticket"
    FORMAL_ROUND = "formal-round"
    FORMAL_UPKEEP = "formal-upkeep"
    EXPIRE = "expire"
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
    OWNER_EDITS_CRITERIA = "OwnerEditsCriteria"
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
    UPGRADE_ASKS_THE_FLAG_LINE = "UpgradeAsksTheFlagLine"
    UPGRADE_LEAVES_ROWS_TO_FILL = "UpgradeLeavesRowsToFill"
    AGENT_FILLS_ROWS = "AgentFillsRows"
    KIND_OF_CODE_ARRIVES = "KindOfCodeArrives"
    KIND_THAT_REOPENS_THE_ARCHITECTURE_ARRIVES = "KindThatReopensTheArchitectureArrives"
    AGENT_LANDS_THE_REVIEW = "AgentLandsTheReview"
    AGENT_BINDS_SAFEGUARD = "AgentBindsSafeguard"
    OWNER_RECORDS_ARCHITECTURE = "OwnerRecordsArchitecture"
    UPGRADE_FINDS_AN_EXPIRED_UNIT = "UpgradeFindsAnExpiredUnit"
    AGENT_DELETES_THE_EXPIRED_UNIT = "AgentDeletesTheExpiredUnit"
    SOMEONE_EDITS_CODE = "SomeoneEditsCode"
    AGENT_COMMITS_ON_A_BRANCH = "AgentCommitsOnABranch"
    BRANCH_MERGES = "BranchMerges"
    MERGE_LEAVES_THE_BRANCH_CHECKED_OUT = "MergeLeavesTheBranchCheckedOut"
    SOMEONE_CHECKS_OUT_A_MERGED_BRANCH = "SomeoneChecksOutAMergedBranch"
    AGENT_SWITCHES_TO_THE_DEFAULT_BRANCH = "AgentSwitchesToTheDefaultBranch"
    AGENT_LANDS_RECORDS = "AgentLandsRecords"
    RECORDS_RUN_STOPS = "RecordsRunStops"
    OWNER_MERGES_RECORDS = "OwnerMergesRecords"
    MIRRORED_CODE_LANDS = "MirroredCodeLands"
    AGENT_RUNS_A_ROUND = "AgentRunsARound"
    OWNER_MERGES_THE_ROUND = "OwnerMergesTheRound"
    AGENT_FIXES_THE_ROUND_TICKET = "AgentFixesTheRoundTicket"


Slots = tuple[Ticket, Ticket]
BLOCKED_BY: Final = {1: 0}
HOLDS: Final = frozenset(
    {Ticket.HELD, Ticket.ASKED, Ticket.GUARDED, Ticket.OWED, Ticket.PARKED, Ticket.OWES_ITS_ROW}
)
STATED: Final = frozenset({Ticket.STATED})
LABELLED: Final = (
    HOLDS | STATED | frozenset({Ticket.READY, Ticket.STALE, Ticket.BUILT, Ticket.MERGED})
)
OPEN: Final = LABELLED | {Ticket.TRIAGE}
CLOSED: Final = frozenset({Ticket.NONE, Ticket.RESOLVED})


@dataclass(frozen=True)
class World:
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
    flag: Flag = Flag.WAITS
    answers: Answers = Answers.NONE
    left: bool = False
    upkeep: Upkeep = Upkeep.CLEAR
    defect: Defect = Defect.NONE


def adopted(world: World) -> bool:
    return world.setup in {Setup.PINNED, Setup.BOUND} and world.hooks


def settled(world: World) -> bool:
    return adopted(world) and (world.head, world.tree, world.parked) == (
        Head.MAIN,
        Tree.CLEAN,
        False,
    )


def calm(world: World) -> bool:
    return settled(world) and world.foundation


def rest(world: World) -> bool:
    return (
        calm(world)
        and world.setup is Setup.BOUND
        and world.spec is Spec.NONE
        and not world.owes
        and not world.reviews
        and not world.fills
        and world.architecture is not Architecture.OWED
        and all(ticket in CLOSED for ticket in world.tickets)
        and not world.left
        and world.upkeep is Upkeep.CLEAR
        and world.defect is Defect.NONE
    )


def live(world: World) -> bool:
    return world.spec is not Spec.NONE or any(ticket in OPEN for ticket in world.tickets)


def expires(before: World, after: World) -> bool:
    return live(before) and not live(after) and Ticket.RESOLVED in after.tickets


def with_ticket(world: World, slot: int, ticket: Ticket) -> World:
    first, second = world.tickets
    return replace(world, tickets=(ticket, second) if slot == 0 else (first, ticket))


def labelled(world: World) -> list[int]:
    return [slot for slot, ticket in enumerate(world.tickets) if ticket in LABELLED]


def unblocked(world: World, slot: int) -> bool:
    blocker = BLOCKED_BY.get(slot)
    return blocker is None or world.tickets[blocker] not in OPEN


def triage_waits(world: World) -> bool:
    return any(
        ticket is Ticket.TRIAGE and unblocked(world, slot)
        for slot, ticket in enumerate(world.tickets)
    )


def free(world: World) -> list[int]:
    return [slot for slot in labelled(world) if unblocked(world, slot)]


def buildable(world: World) -> list[int]:
    return [slot for slot in free(world) if world.tickets[slot] not in HOLDS]


def states(world: World, slot: int) -> bool:
    return world.tickets[slot] in STATED and world.flag is not Flag.MISSING


def stated(world: World) -> list[int]:
    return [slot for slot in labelled(world) if states(world, slot)]


def queue_builds(world: World) -> bool:
    return any(states(world, slot) for slot in buildable(world))


def lacks_the_line(world: World) -> bool:
    return (
        world.spec is Spec.STATED
        or any(ticket in STATED | HOLDS | {Ticket.STALE} for ticket in world.tickets)
        or (world.inherits and Ticket.TRIAGE in world.tickets)
    )


def held_for(world: World, ticket: Ticket) -> bool:
    slots = free(world)
    return bool(slots) and not buildable(world) and world.tickets[slots[0]] is ticket


@dataclass(frozen=True)
class Rule:
    advice: Advice
    holds: Callable[[World], bool]


def _no_ticket(world: World) -> bool:
    return not labelled(world)


def _spec_unstated(world: World) -> bool:
    return world.spec is Spec.BARE or (world.spec is Spec.STATED and world.flag is Flag.MISSING)


def defect_open(world: World) -> bool:
    return world.defect is Defect.OPEN


BEHIND: Final = frozenset({Upkeep.BEHIND, Upkeep.FIXED})
WAITING: Final = frozenset({Upkeep.WAITING, Upkeep.WAITING_BEHIND})
SAFEGUARD_OWED: Final = Rule(Advice.SAFEGUARD, lambda w: w.owes and not defect_open(w))
ROUND_TICKETS: Final = {Upkeep.TICKET: Advice.BUILD, Upkeep.UNSTATED: Advice.CONJECTURE}
ROUND_TICKET: Final = Rule(Advice.FORMAL_TICKET, lambda w: w.upkeep is Upkeep.TICKET)
ROUND_WAITING: Final = Rule(Advice.FORMAL_ROUND, lambda w: w.upkeep in WAITING)
MODEL_BEHIND: Final = Rule(Advice.FORMAL_UPKEEP, lambda w: w.upkeep in BEHIND)
MEET: Final = Rule(Advice.CHANGE_TICKET, lambda w: held_for(w, Ticket.OWES_ITS_ROW))


CHAIN: Final = (
    Rule(Advice.INIT, lambda w: w.setup in {Setup.BARE, Setup.STARTED}),
    Rule(Advice.TRACKER, lambda w: w.setup is Setup.NAMED),
    Rule(Advice.ADOPT, lambda w: w.setup in {Setup.TRACKED, Setup.ADOPTING, Setup.PINNED}),
    Rule(Advice.HOOKS, lambda w: not w.hooks),
    Rule(Advice.FINISH_RECORDS, lambda w: w.head is Head.RECORDS),
    Rule(Advice.LAND_RECORDS, lambda w: (w.head, w.tree) == (Head.MAIN, Tree.RECORDS)),
    Rule(Advice.COMMIT, lambda w: w.tree is not Tree.CLEAN),
    Rule(Advice.SWITCH, lambda w: w.head is Head.MERGED),
    Rule(Advice.MERGE, lambda w: w.head is Head.WORK),
    Rule(Advice.MERGE_RECORDS, lambda w: w.parked),
    Rule(Advice.FILL, lambda w: w.fills),
    Rule(Advice.REVIEW, lambda w: w.reviews),
    SAFEGUARD_OWED,
    Rule(Advice.ARCHITECTURE, lambda w: w.architecture is Architecture.OWED),
    ROUND_TICKET,
    ROUND_WAITING,
    MODEL_BEHIND,
    Rule(Advice.EXPIRE, lambda w: w.left),
    Rule(Advice.GRILL, lambda w: not w.foundation),
    Rule(Advice.CONJECTURE, lambda w: w.upkeep is Upkeep.UNSTATED),
    Rule(Advice.TRIAGE, lambda w: _no_ticket(w) and triage_waits(w)),
    Rule(Advice.CONJECTURE_SPEC, lambda w: _no_ticket(w) and _spec_unstated(w)),
    Rule(Advice.SPLIT, lambda w: _no_ticket(w) and w.spec is Spec.STATED),
    Rule(Advice.SPECIFY, _no_ticket),
    Rule(Advice.RESOLVE, lambda w: Ticket.MERGED in w.tickets),
    Rule(Advice.BLOCKED, lambda w: not free(w)),
    Rule(Advice.BLOCKED_RUN, lambda w: held_for(w, Ticket.HELD)),
    Rule(Advice.ANSWER, lambda w: held_for(w, Ticket.ASKED)),
    Rule(Advice.LAND_GUARD, lambda w: held_for(w, Ticket.GUARDED)),
    Rule(Advice.SAFEGUARD, lambda w: held_for(w, Ticket.OWED)),
    MEET,
    Rule(Advice.CHANGE_TICKET, lambda w: held_for(w, Ticket.PARKED)),
    Rule(Advice.DRAIN, lambda w: queue_builds(w) and len(stated(w)) > 1),
    Rule(Advice.BUILD, queue_builds),
    Rule(Advice.CONJECTURE, lambda _w: True),
)


Chain = tuple[Rule, ...]


def advice(world: World, chain: Chain = CHAIN) -> Advice:
    return next(rule.advice for rule in chain if rule.holds(world))


def swapped(rule: Rule, *by: Rule) -> Chain:
    index = CHAIN.index(rule)
    return (*CHAIN[:index], *by, *CHAIN[index + 1 :])


def beneath(world: World, chain: Chain = CHAIN) -> str:
    if Ticket.BUILT in world.tickets:
        return "building"
    if world.upkeep in ROUND_TICKETS:
        return ROUND_TICKETS[world.upkeep]
    clean = replace(world, head=Head.MAIN, tree=Tree.CLEAN, parked=False)
    recorded = replace(clean, architecture=Architecture.RECORDED)
    owed = replace(recorded, owes=False, reviews=False, fills=False)
    return advice(replace(owed, upkeep=Upkeep.CLEAR), chain)


@dataclass(frozen=True)
class Divergence:
    name: str
    holds: Callable[[World], bool]
    reads: Callable[[World], World]
    contradicts: str
    source: str
    breaks: frozenset[str]
    example: World


def as_proved_by_its_pin(world: World) -> World:
    return replace(world, setup=Setup.BOUND)


def as_its_criteria_unread(world: World) -> World:
    first, second = (Ticket.STATED if t is Ticket.STALE else t for t in world.tickets)
    return replace(world, tickets=(first, second))


DIVERGENCES: Final[tuple[Divergence, ...]] = (
    Divergence(
        name="adoption-proved-by-its-pin",
        holds=lambda world: world.setup is Setup.PINNED,
        reads=as_proved_by_its_pin,
        contradicts=(
            "Done when `check` is green in a fresh worktree of the commit, `conformance` and"
            " `check-deep` exit 0, no rehearsal worktree is left, and the rehearsal passed, or its"
            " second round ended with the edits it still needs named."
        ),
        source="skills/adopt/SKILL.md",
        breaks=frozenset({"FollowingRealAdviceLowersRank", "RealUnprovedAdoptionNeverBuilds"}),
        example=World(
            setup=Setup.PINNED,
            hooks=True,
            foundation=True,
            tickets=(Ticket.STATED, Ticket.TRIAGE),
            inherits=True,
            architecture=Architecture.RECORDED,
            answers=Answers.ON_THE_UNIT,
        ),
    ),
    Divergence(
        name="stale-conjecture-read-as-stated",
        holds=lambda world: Ticket.STALE in world.tickets,
        reads=as_its_criteria_unread,
        contradicts=(
            "When the criteria change after it, `/ratchet:conjecture <ticket>` finds another"
            " digest, drafts the three sections again, keeps every line under `## Answers`, asks"
            " only the forks the change opened, and prints what changed in the conjecture."
        ),
        source="skills/method/records.md",
        breaks=frozenset({"RealAdviceCanBeFollowed", "RealStaleConjectureBeforeTheBuild"}),
        example=World(
            setup=Setup.BOUND,
            hooks=True,
            foundation=True,
            tickets=(Ticket.STALE, Ticket.TRIAGE),
            architecture=Architecture.RECORDED,
            answers=Answers.ON_THE_UNIT,
        ),
    ),
)


def as_the_route_reads(world: World) -> World:
    for entry in DIVERGENCES:
        if entry.holds(world):
            world = entry.reads(world)
    return world


def divergences(world: World) -> tuple[Divergence, ...]:
    if advice(as_the_route_reads(world)) is advice(world):
        return ()
    return tuple(entry for entry in DIVERGENCES if entry.holds(world))


def real_advice(world: World, chain: Chain = CHAIN) -> Advice:
    return advice(as_the_route_reads(world), chain)


Successors = Callable[[World], Iterator[World]]
Deleter = Callable[[World, World], World]


def deletes(before: World, after: World) -> World:
    if not expires(before, after):
        return after
    carried = after.answers is Answers.ON_THE_UNIT and after.tree is Tree.RECORDS
    return replace(after, answers=Answers.CARRIED if carried else Answers.NONE)


def leaves(before: World, after: World) -> World:
    return replace(after, left=True) if expires(before, after) else after


def drops_the_answers(before: World, after: World) -> World:
    if expires(before, after) and after.answers is Answers.ON_THE_UNIT:
        return replace(after, answers=Answers.DROPPED)
    return after


@dataclass(frozen=True)
class Variant:
    deleter: Deleter = deletes
    chain: Chain = CHAIN
    refused_behind: frozenset[ActionName] = frozenset()

    def advice(self, world: World) -> Advice:
        return advice(world, self.chain)


PAGES: Final = Variant()


@dataclass(frozen=True)
class Action:
    name: ActionName
    follows: tuple[Advice, ...]
    outcomes: Successors

    def successors(self, world: World, variant: Variant = PAGES) -> Iterator[World]:
        allowed = allowed_steps(world, variant.chain)
        if allowed is not None and self.name not in allowed:
            return iter(())
        if self.name in variant.refused_behind and world.upkeep in BEHIND:
            return iter(())
        return (
            lined(kept(variant.deleter(world, answered(self.name, world, after))))
            for after in self.outcomes(world)
        )


ACTIONS: Final[dict[ActionName, Action]] = {}
ADVISED: Final[dict[Advice, ActionName]] = {}


ANSWERING: Final = frozenset(
    {ActionName.OWNER_ANSWERS_ON_THE_TICKET, ActionName.OWNER_ANSWERS_BLOCKED_RUN}
)


def answered(name: ActionName, world: World, after: World) -> World:
    if not defect_open(world) or after.answers is not Answers.NONE:
        return after
    flagged = name is ActionName.OWNER_STATES_CONJECTURE and after.flag is Flag.ASKED
    if name in ANSWERING or flagged:
        return replace(after, answers=Answers.ON_THE_UNIT)
    return after


def kept(world: World) -> World:
    if world.defect is Defect.OPEN and world.tickets[0] in CLOSED:
        return replace(world, defect=Defect.KEPT)
    return world


def lined(world: World) -> World:
    if world.flag is Flag.MISSING and not lacks_the_line(world):
        return replace(world, flag=Flag.ASKED)
    return world


def following_today(world: World, chain: Chain = CHAIN) -> frozenset[ActionName]:
    return frozenset({ActionName.OWNER_ADOPTS, ADVISED[real_advice(world, chain)]})


OFFERED: Final = frozenset({Advice.FORMAL_ROUND, Advice.FORMAL_UPKEEP})
LABELS: Final = frozenset(Advice)


def beside_the_upkeep(world: World, chain: Chain) -> frozenset[ActionName]:
    named = advice(world, chain)
    under = beneath(world, chain)
    offered = {ADVISED[Advice(under)]} if named in OFFERED and under in LABELS else set()
    return frozenset({ADVISED[named], ActionName.MIRRORED_CODE_LANDS, *offered})


def allowed_steps(world: World, chain: Chain) -> frozenset[ActionName] | None:
    if world.setup is Setup.PINNED:
        return following_today(world, chain)
    if world.defect is Defect.KEPT:
        return frozenset({ADVISED[advice(world, chain)]})
    if world.upkeep is not Upkeep.CLEAR:
        return beside_the_upkeep(world, chain)
    return None


def action(name: ActionName, *follows: Advice) -> Callable[[Successors], Successors]:
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


def on_the_adoption_branch(world: World) -> bool:
    return world.head is Head.WORK and not world.foundation


@action(ActionName.OWNER_ADOPTS, Advice.ADOPT)
def _owner_adopts(world: World) -> Iterator[World]:
    if world.setup is Setup.PINNED and not on_the_adoption_branch(world):
        yield replace(world, setup=Setup.BOUND)
        return
    if world.setup in {Setup.TRACKED, Setup.ADOPTING, Setup.PINNED}:
        yield replace(
            world,
            setup=Setup.BOUND,
            head=Head.MAIN,
            tree=Tree.CLEAN,
            architecture=Architecture.RECORDED,
        )


@action(ActionName.OLDER_PLUGIN_ADOPTS)
def _older_plugin_adopts(world: World) -> Iterator[World]:
    if world.setup is Setup.TRACKED:
        yield replace(world, setup=Setup.BOUND)


@action(ActionName.ADOPTION_STOPS)
def _adoption_stops(world: World) -> Iterator[World]:
    if world.setup is Setup.TRACKED:
        yield replace(world, setup=Setup.ADOPTING, head=Head.WORK)
    if world.setup in {Setup.TRACKED, Setup.ADOPTING}:
        yield replace(
            world,
            setup=Setup.PINNED,
            head=Head.WORK,
            tree=Tree.CODE,
            architecture=Architecture.RECORDED,
        )


@action(ActionName.AGENT_INSTALLS_HOOKS, Advice.HOOKS)
def _agent_installs_hooks(world: World) -> Iterator[World]:
    if world.setup in {Setup.PINNED, Setup.BOUND} and not world.hooks:
        yield replace(world, hooks=True)


@action(ActionName.OWNER_GRILLS, Advice.GRILL)
def _owner_grills(world: World) -> Iterator[World]:
    if settled(world) and not world.foundation:
        yield replace(world, foundation=True, tree=Tree.CODE)


@action(ActionName.OWNER_WRITES_SPEC, Advice.SPECIFY)
def _owner_writes_spec(world: World) -> Iterator[World]:
    if (
        calm(world)
        and not world.left
        and world.spec is Spec.NONE
        and all(t in CLOSED for t in world.tickets)
    ):
        yield replace(
            world,
            spec=Spec.BARE,
            tickets=(Ticket.NONE, Ticket.NONE),
            inherits=False,
            tree=Tree.RECORDS,
            answers=Answers.ON_THE_UNIT,
        )


@action(ActionName.OWNER_STATES_SPEC_CONJECTURE, Advice.CONJECTURE_SPEC)
def _owner_states_spec_conjecture(world: World) -> Iterator[World]:
    if calm(world) and _spec_unstated(world):
        flag = Flag.ASKED if world.flag is Flag.MISSING else world.flag
        yield replace(world, spec=Spec.STATED, tree=Tree.RECORDS, flag=flag)


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
    before: frozenset[Ticket]
    after: Ticket
    needs_free: bool = False
    tree: Tree | None = None
    head: Head | None = None


def runnable(world: World, slot: int) -> bool:
    return unblocked(world, slot) and not world.fills and world.flag is not Flag.MISSING


def runnable_slots(world: World) -> list[int]:
    return [slot for slot in range(len(world.tickets)) if runnable(world, slot)]


def moves(world: World, move: Move) -> Iterator[World]:
    if not calm(world):
        return
    for slot, ticket in enumerate(world.tickets):
        if ticket in move.before and (not move.needs_free or runnable(world, slot)):
            moved = with_ticket(world, slot, move.after)
            yield replace(moved, tree=move.tree or moved.tree, head=move.head or moved.head)


def per_ticket(name: ActionName, follows: tuple[Advice, ...], move: Move) -> None:
    action(name, *follows)(lambda world: moves(world, move))


def one(ticket: Ticket) -> frozenset[Ticket]:
    return frozenset({ticket})


def inherited(world: World) -> Ticket:
    return Ticket.STALE if Ticket.STALE in world.tickets else Ticket.STATED


@action(ActionName.OWNER_AGREES_TICKET, Advice.TRIAGE)
def _owner_agrees_ticket(world: World) -> Iterator[World]:
    after = inherited(world) if world.inherits else Ticket.READY
    yield from moves(world, Move(one(Ticket.TRIAGE), after))


per_ticket(
    ActionName.OWNER_WORKS_TICKET_THEMSELF,
    (Advice.BLOCKED,),
    Move(one(Ticket.TRIAGE), Ticket.RESOLVED),
)


@action(ActionName.OWNER_STATES_CONJECTURE, Advice.CONJECTURE)
def _owner_states_conjecture(world: World) -> Iterator[World]:
    flag = Flag.ASKED if world.flag is Flag.MISSING else world.flag
    for moved in moves(world, Move(one(Ticket.READY), Ticket.STATED)):
        yield replace(moved, flag=flag)
    slots = range(len(world.tickets))
    for redraft in [slots] if world.inherits else [[slot] for slot in slots]:
        yield from (replace(w, flag=flag) for w in redrafted(world, redraft))
    if calm(world) and world.flag is Flag.MISSING and STATED & set(world.tickets):
        yield replace(world, flag=flag)
    if calm(world) and world.upkeep is Upkeep.UNSTATED:
        yield replace(world, upkeep=Upkeep.TICKET)


def redrafted(world: World, slots: Iterable[int]) -> Iterator[World]:
    stale = [slot for slot in slots if world.tickets[slot] is Ticket.STALE]
    if calm(world) and stale:
        first, second = (
            Ticket.STATED if slot in stale else ticket for slot, ticket in enumerate(world.tickets)
        )
        yield replace(world, tickets=(first, second))


@action(ActionName.OWNER_EDITS_CRITERIA)
def _owner_edits_criteria(world: World) -> Iterator[World]:
    if not calm(world):
        return
    if world.inherits:
        if Ticket.STATED in world.tickets and not HOLDS & set(world.tickets):
            first, second = (Ticket.STALE if t is Ticket.STATED else t for t in world.tickets)
            yield replace(world, tickets=(first, second))
        return
    for slot, ticket in enumerate(world.tickets):
        if ticket is Ticket.STATED:
            yield with_ticket(world, slot, Ticket.STALE)


per_ticket(
    ActionName.AGENT_BUILDS_TICKET,
    (Advice.BUILD,),
    Move(STATED, Ticket.BUILT, needs_free=True, head=Head.WORK),
)


@action(ActionName.BUILD_STOPS)
def _build_stops(world: World) -> Iterator[World]:
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
    if defect_open(world):
        for parked in moves(world, Move(one(Ticket.STATED), Ticket.OWES_ITS_ROW, needs_free=True)):
            if parked.tickets[0] is Ticket.OWES_ITS_ROW:
                yield parked


@action(ActionName.DRAIN_PARKS_FOR_A_SAFEGUARD)
def _drain_parks_for_a_safeguard(world: World) -> Iterator[World]:
    if (
        calm(world)
        and not (world.owes or world.fills)
        and world.flag is not Flag.MISSING
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
    Move(frozenset({Ticket.PARKED, Ticket.OWES_ITS_ROW}), Ticket.STATED),
)
per_ticket(
    ActionName.OWNER_RESOLVES_TICKET,
    (Advice.RESOLVE,),
    Move(one(Ticket.MERGED), Ticket.RESOLVED, tree=Tree.RECORDS),
)


@action(ActionName.UPGRADE_LEAVES_ROWS_TO_FILL)
def _upgrade_leaves_rows_to_fill(world: World) -> Iterator[World]:
    if rest(world) and world.tickets == (Ticket.RESOLVED, Ticket.RESOLVED):
        owed = world.architecture is Architecture.UNASKED
        yield replace(
            world, fills=True, architecture=Architecture.OWED if owed else world.architecture
        )


@action(ActionName.UPGRADE_ASKS_THE_FLAG_LINE)
def _upgrade_asks_the_flag_line(world: World) -> Iterator[World]:
    building = on_a_clean_branch(world, Head.WORK) and Ticket.BUILT in world.tickets
    if (
        (calm(world) or (building and world.foundation and not world.parked))
        and world.flag is Flag.WAITS
        and world.architecture is Architecture.RECORDED
        and world.defect is Defect.NONE
    ):
        yield replace(world, flag=Flag.MISSING if lacks_the_line(world) else Flag.ASKED)


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
    if rest(world) and Ticket.RESOLVED in world.tickets:
        yield replace(world, reviews=True)


@action(ActionName.AGENT_LANDS_THE_REVIEW, Advice.REVIEW)
def _agent_lands_the_review(world: World) -> Iterator[World]:
    if calm(world) and world.reviews:
        yield replace(world, reviews=False)


@action(ActionName.AGENT_BINDS_SAFEGUARD, Advice.SAFEGUARD)
def _agent_binds_safeguard(world: World) -> Iterator[World]:
    if calm(world) and world.owes and not defect_open(world):
        first, second = (Ticket.STATED if t is Ticket.OWED else t for t in world.tickets)
        yield replace(world, owes=False, defect=Defect.NONE, tickets=(first, second))
        if (
            world.defect is Defect.NONE
            and world.architecture is Architecture.RECORDED
            and rest(replace(world, owes=False))
        ):
            yield replace(
                world,
                defect=Defect.OPEN,
                tickets=(Ticket.READY, Ticket.RESOLVED),
                inherits=False,
            )


@action(ActionName.OWNER_RECORDS_ARCHITECTURE, Advice.ARCHITECTURE)
def _owner_records_architecture(world: World) -> Iterator[World]:
    if calm(world) and world.architecture is Architecture.OWED:
        yield replace(world, architecture=Architecture.RECORDED, head=Head.WORK)


@action(ActionName.UPGRADE_FINDS_AN_EXPIRED_UNIT)
def _upgrade_finds_an_expired_unit(world: World) -> Iterator[World]:
    if rest(world) and Ticket.RESOLVED in world.tickets:
        yield replace(world, left=True)


@action(ActionName.AGENT_DELETES_THE_EXPIRED_UNIT, Advice.EXPIRE)
def _agent_deletes_the_expired_unit(world: World) -> Iterator[World]:
    if calm(world) and world.left:
        carried = world.answers is Answers.ON_THE_UNIT
        yield replace(
            world,
            left=False,
            answers=Answers.CARRIED if carried else Answers.NONE,
            tree=Tree.RECORDS,
        )


def on_a_clean_branch(world: World, *heads: Head) -> bool:
    return adopted(world) and world.head in heads and world.tree is Tree.CLEAN


@action(ActionName.SOMEONE_EDITS_CODE)
def _someone_edits_code(world: World) -> Iterator[World]:
    at_rest = on_a_clean_branch(world, Head.MERGED) and rest(replace(world, head=Head.MAIN))
    if rest(world) or on_a_clean_branch(world, Head.WORK) or at_rest:
        yield replace(world, tree=Tree.CODE)


@action(ActionName.AGENT_COMMITS_ON_A_BRANCH, Advice.COMMIT)
def _agent_commits_on_a_branch(world: World) -> Iterator[World]:
    if adopted(world) and world.tree is Tree.CODE:
        yield replace(world, tree=Tree.CLEAN, head=Head.WORK)


def merged(world: World, head: Head) -> World:
    first, second = (Ticket.MERGED if t is Ticket.BUILT else t for t in world.tickets)
    return replace(world, head=head, tickets=(first, second))


@action(ActionName.BRANCH_MERGES, Advice.MERGE)
def _branch_merges(world: World) -> Iterator[World]:
    if on_a_clean_branch(world, Head.WORK):
        yield merged(world, Head.MAIN)
    if on_a_clean_branch(world, Head.MERGED):
        yield world


@action(ActionName.MERGE_LEAVES_THE_BRANCH_CHECKED_OUT)
def _merge_leaves_the_branch_checked_out(world: World) -> Iterator[World]:
    if on_a_clean_branch(world, Head.WORK):
        yield merged(world, Head.MERGED)


@action(ActionName.SOMEONE_CHECKS_OUT_A_MERGED_BRANCH)
def _someone_checks_out_a_merged_branch(world: World) -> Iterator[World]:
    if rest(world):
        yield replace(world, head=Head.MERGED)


@action(ActionName.AGENT_SWITCHES_TO_THE_DEFAULT_BRANCH, Advice.SWITCH)
def _agent_switches_to_the_default_branch(world: World) -> Iterator[World]:
    if on_a_clean_branch(world, Head.MERGED):
        yield replace(world, head=Head.MAIN)


def landed(world: World) -> World:
    return replace(world, answers=Answers.NONE) if world.answers is Answers.CARRIED else world


@action(ActionName.AGENT_LANDS_RECORDS, Advice.LAND_RECORDS, Advice.FINISH_RECORDS)
def _agent_lands_records(world: World) -> Iterator[World]:
    if adopted(world) and (
        (world.head, world.tree) == (Head.MAIN, Tree.RECORDS) or world.head is Head.RECORDS
    ):
        yield landed(replace(world, head=Head.MAIN, tree=Tree.CLEAN))
        yield replace(world, head=Head.MAIN, tree=Tree.CLEAN, parked=True)


@action(ActionName.RECORDS_RUN_STOPS)
def _records_run_stops(world: World) -> Iterator[World]:
    if adopted(world) and (world.head, world.tree) == (Head.MAIN, Tree.RECORDS):
        yield replace(world, head=Head.RECORDS)


@action(ActionName.OWNER_MERGES_RECORDS, Advice.MERGE_RECORDS)
def _owner_merges_records(world: World) -> Iterator[World]:
    if adopted(world) and world.parked and (world.head, world.tree) == (Head.MAIN, Tree.CLEAN):
        yield landed(replace(world, parked=False))


def at_rest_but_the_upkeep(world: World) -> bool:
    return rest(replace(world, upkeep=Upkeep.CLEAR)) and Ticket.RESOLVED in world.tickets


@action(ActionName.MIRRORED_CODE_LANDS)
def _mirrored_code_lands(world: World) -> Iterator[World]:
    landing = {Upkeep.CLEAR: Upkeep.BEHIND, Upkeep.WAITING: Upkeep.WAITING_BEHIND}
    if (
        world.upkeep in landing
        and world.architecture is Architecture.RECORDED
        and at_rest_but_the_upkeep(world)
    ):
        yield replace(world, upkeep=landing[world.upkeep])


@action(ActionName.AGENT_RUNS_A_ROUND, Advice.FORMAL_UPKEEP)
def _agent_runs_a_round(world: World) -> Iterator[World]:
    if calm(world) and world.upkeep in BEHIND:
        yield replace(world, upkeep=Upkeep.WAITING)
        if world.upkeep is Upkeep.BEHIND and at_rest_but_the_upkeep(world):
            yield replace(world, upkeep=Upkeep.TICKET)
            yield replace(world, upkeep=Upkeep.UNSTATED)


@action(ActionName.OWNER_MERGES_THE_ROUND, Advice.FORMAL_ROUND)
def _owner_merges_the_round(world: World) -> Iterator[World]:
    merged = {Upkeep.WAITING: Upkeep.CLEAR, Upkeep.WAITING_BEHIND: Upkeep.BEHIND}
    if calm(world) and world.upkeep in merged:
        yield replace(world, upkeep=merged[world.upkeep])


@action(ActionName.AGENT_FIXES_THE_ROUND_TICKET, Advice.FORMAL_TICKET)
def _agent_fixes_the_round_ticket(world: World) -> Iterator[World]:
    if calm(world) and world.upkeep is Upkeep.TICKET:
        yield replace(world, upkeep=Upkeep.FIXED)


class Edge(NamedTuple):
    source: World
    action: ActionName
    target: World


@dataclass(frozen=True)
class Graph:
    states: tuple[World, ...]
    edges: tuple[Edge, ...]
    variant: Variant = PAGES

    def followed(self, label: Callable[[World], Advice]) -> dict[World, tuple[World, ...]]:
        out: dict[World, list[World]] = {state: [] for state in self.states}
        for source, name, target in self.edges:
            if name is ADVISED[label(source)]:
                out[source].append(target)
        return {state: tuple(targets) for state, targets in out.items()}


@functools.cache
def graph(start: World = World(), variant: Variant = PAGES) -> Graph:  # noqa: B008 # -- World is frozen
    seen = {start: None}
    edges: list[Edge] = []
    todo = deque([start])
    while todo:
        source = todo.popleft()
        for name, act in ACTIONS.items():
            for target in act.successors(source, variant):
                edges.append(Edge(source, name, target))
                if target not in seen:
                    seen[target] = None
                    todo.append(target)
    return Graph(tuple(seen), tuple(edges), variant)


LOOPS: Final = -1


@functools.cache
def ranks(of: Graph) -> dict[World, int]:
    advised = of.followed(of.variant.advice)
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
    return "\n".join(f'     \\/ ({label} = "{a}" /\\ {act})' for a, act in ADVISED.items())


def can_be_followed(label: str) -> str:
    return "\n".join(f'  /\\ ({label} = "{a}" => ENABLED {act})' for a, act in ADVISED.items())


def lowers_rank(label: str) -> str:
    return f"[][(rank > 0 /\\\n{following(label)}) => rank' < rank]_vars"


def changes_the_state(label: Callable[[World], Advice]) -> Callable[[World], bool]:
    def check(world: World) -> bool:
        return world not in ACTIONS[ADVISED[label(world)]].successors(world)

    return check


def never_merges_a_merged_branch(label: str) -> str:
    return f'head = "{Head.MERGED}" => {label} # "{Advice.MERGE}"'


def above_architecture_open(world: World) -> bool:
    return not (
        adopted(world)
        and (world.head, world.tree, world.parked) == (Head.MAIN, Tree.CLEAN, False)
        and not (world.fills or world.reviews or (world.owes and not defect_open(world)))
    )


ABOVE_ARCHITECTURE_CLEAR: Final = (
    'setup = "bound" /\\ hooks /\\ head = "main" /\\ tree = "clean" /\\ ~parked'
    f' /\\ ~fills /\\ ~reviews /\\ (~owes \\/ defect = "{Defect.OPEN}")'
)


def beside(*named: Advice) -> str:
    names = ", ".join(f'"{label}"' for label in named)
    return "\n".join(
        f'  /\\ (advice \\in {{{names}}} /\\ beneath = "{a}" => ENABLED {act})'
        for a, act in ADVISED.items()
    )


def defect_before_the_safeguard(label: str) -> str:
    return f'defect = "{Defect.OPEN}" => {label} # "{Advice.SAFEGUARD}"'


AHEAD_OF_BEHIND: Final = frozenset({Upkeep.TICKET, Upkeep.UNSTATED, Upkeep.WAITING_BEHIND})


def round_before_behind(label: str) -> str:
    ahead = ", ".join(f'"{upkeep}"' for upkeep in sorted(AHEAD_OF_BEHIND))
    return f'upkeep \\in {{{ahead}}} => {label} # "{Advice.FORMAL_UPKEEP}"'


def only(action_name: ActionName, before: str, after: str) -> str:
    return (
        f'[][(architecture {before} "{Architecture.OWED}" /\\'
        f' architecture\' {after} "{Architecture.OWED}") => {action_name}]_vars'
    )


RUNS: Final = (
    ActionName.AGENT_BUILDS_TICKET,
    ActionName.BUILD_STOPS,
    ActionName.FEATURE_RUN_BLOCKS,
    ActionName.DRAIN_MERGES_TICKET,
    ActionName.DRAIN_PARKS_TICKET,
    ActionName.DRAIN_PARKS_FOR_A_SAFEGUARD,
)
BUILDS: Final = frozenset({Advice.BUILD, Advice.DRAIN})


def line_before_the_build(label: Callable[[World], Advice]) -> Callable[[World], bool]:
    def check(world: World) -> bool:
        return world.flag is not Flag.MISSING or label(world) not in BUILDS

    return check


def proved_before_the_build(label: Callable[[World], Advice]) -> Callable[[World], bool]:
    def check(world: World) -> bool:
        return world.setup is not Setup.PINNED or label(world) not in BUILDS

    return check


def stale_before_the_build(label: Callable[[World], Advice]) -> Callable[[World], bool]:
    def check(world: World) -> bool:
        if Ticket.STALE not in world.tickets:
            return True
        named = label(world)
        return named is not Advice.DRAIN and (named is not Advice.BUILD or queue_builds(world))

    return check


EACH_TICKET = "\\A slot \\in DOMAIN tickets"


def no_run_on_a_stale_conjecture() -> str:
    runs = " \\/ ".join(RUNS)
    return (
        f'[][{EACH_TICKET} : (tickets[slot] = "{Ticket.STALE}" /\\ ({runs}))'
        f' => tickets\'[slot] = "{Ticket.STALE}"]_vars'
    )


def only_the_redraft_clears_it() -> str:
    return (
        f'[][{EACH_TICKET} : (tickets[slot] = "{Ticket.STALE}"'
        f' /\\ tickets\'[slot] # "{Ticket.STALE}") => ({ActionName.OWNER_STATES_CONJECTURE}'
        f' /\\ tickets\'[slot] = "{Ticket.STATED}")]_vars'
    )


def the_edit_moves_nothing_else() -> str:
    others = ", ".join(name for name in World.__dataclass_fields__ if name != "tickets")
    return (
        f"[][{ActionName.OWNER_EDITS_CRITERIA} => (UNCHANGED <<{others}>> /\\\n"
        f"  {EACH_TICKET} : tickets'[slot] = tickets[slot]"
        f' \\/ (tickets[slot] = "{Ticket.STATED}" /\\ tickets\'[slot] = "{Ticket.STALE}"))]_vars'
    )


NEW_WORK: Final = frozenset(
    {
        Advice.SPECIFY,
        Advice.CONJECTURE_SPEC,
        Advice.SPLIT,
        Advice.TRIAGE,
        Advice.CONJECTURE,
        Advice.BUILD,
        Advice.DRAIN,
    }
)


def expired_before_the_work(label: Callable[[World], Advice]) -> Callable[[World], bool]:
    def check(world: World) -> bool:
        return not world.left or label(world) not in NEW_WORK

    return check


def only_the_upgrade_finds_a_unit_left() -> str:
    return f"[][(~left /\\ left') => {ActionName.UPGRADE_FINDS_AN_EXPIRED_UNIT}]_vars"


def no_run_without_the_line() -> str:
    runs = " \\/ ".join(RUNS)
    return f'[][flag = "{Flag.MISSING}" => ~({runs})]_vars'


def upgrade_moves_no_ticket() -> str:
    return f"[][{ActionName.UPGRADE_ASKS_THE_FLAG_LINE} => UNCHANGED <<tickets, inherits>>]_vars"


def only_the_upgrade_leaves_the_line_missing() -> str:
    return (
        f'[][(flag # "{Flag.MISSING}" /\\ flag\' = "{Flag.MISSING}")'
        f" => {ActionName.UPGRADE_ASKS_THE_FLAG_LINE}]_vars"
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
        beside(Advice.ARCHITECTURE),
    ),
    "OnlyAnUpgradeOwesTheArchitecture": model_tla.Property(
        "Only an upgrade leaves the architecture owed; an adoption never does.",
        only(ActionName.UPGRADE_LEAVES_ROWS_TO_FILL, "#", "="),
    ),
    "OnlyRecordingEndsTheOwedArchitecture": model_tla.Property(
        "Recording the architecture is the only step that ends it being owed.",
        only(ActionName.OWNER_RECORDS_ARCHITECTURE, "=", "#"),
    ),
    "FlagLineBeforeTheBuild": model_tla.Invariant(
        "Where a labelled ticket has no `Flag:` line, on it or on its spec, the route names no"
        " build and no drain.",
        line_before_the_build(advice),
    ),
    "RealFlagLineBeforeTheBuild": model_tla.Invariant(
        "Where a labelled ticket has no `Flag:` line, on it or on its spec, the real route names"
        " no build and no drain.",
        line_before_the_build(real_advice),
    ),
    "NoRunStartsWithoutTheFlagLine": model_tla.Property(
        "No feature run and no drain starts while a ticket's `Flag:` line is missing.",
        no_run_without_the_line(),
    ),
    "OnlyTheUpgradeLeavesTheFlagLineMissing": model_tla.Property(
        "Only the upgrade that asks the `Flag:` line before deployment leaves it missing.",
        only_the_upgrade_leaves_the_line_missing(),
    ),
    "TheUpgradeMovesNoTicket": model_tla.Property(
        "The upgrade that asks the `Flag:` line moves no ticket and no inheritance: a built ticket"
        " stays built.",
        upgrade_moves_no_ticket(),
    ),
    "UnprovedAdoptionNeverBuilds": model_tla.Invariant(
        "A repository whose adoption stopped before its proof is never told to build.",
        proved_before_the_build(advice),
    ),
    "RealUnprovedAdoptionNeverBuilds": model_tla.Invariant(
        "The real route never tells a repository whose adoption stopped before its proof to build.",
        proved_before_the_build(real_advice),
    ),
    "StaleConjectureBeforeTheBuild": model_tla.Invariant(
        "Where a ticket's conjecture is stale, the route names no drain, and no build but of a"
        " ticket whose conjecture is current.",
        stale_before_the_build(advice),
    ),
    "RealStaleConjectureBeforeTheBuild": model_tla.Invariant(
        "Where a ticket's conjecture is stale, the real route names no drain, and no build but of"
        " a ticket whose conjecture is current.",
        stale_before_the_build(real_advice),
    ),
    "NoRunStartsOnAStaleConjecture": model_tla.Property(
        "No feature run and no drain starts on a ticket whose conjecture is stale.",
        no_run_on_a_stale_conjecture(),
    ),
    "OnlyTheRedraftClearsAStaleConjecture": model_tla.Property(
        "Only `/ratchet:conjecture` ends a stale conjecture, and it leaves the ticket stated.",
        only_the_redraft_clears_it(),
    ),
    "TheCriteriaEditMovesNothingElse": model_tla.Property(
        "An edit of the criteria makes a stated conjecture stale and changes nothing else.",
        the_edit_moves_nothing_else(),
    ),
    "NoStepLeavesTheUnitItExpires": model_tla.Property(
        "A step that makes the unit expired deletes it: only the upgrade that ships ADR 0064 finds"
        " one left in the tree.",
        only_the_upgrade_finds_a_unit_left(),
    ),
    "ExpiredUnitBeforeTheWork": model_tla.Invariant(
        "Where an expired unit is left in the tree, the route names no spec, ticket, build or"
        " drain.",
        expired_before_the_work(advice),
    ),
    "RealExpiredUnitBeforeTheWork": model_tla.Invariant(
        "Where an expired unit is left in the tree, the real route names no spec, ticket, build or"
        " drain.",
        expired_before_the_work(real_advice),
    ),
    "TheOwnersAnswersOutliveTheUnit": model_tla.Invariant(
        "No unit is deleted with the owner's `## Answers` lines left out of the brief.",
        f'answers # "{Answers.DROPPED}"',
    ),
    "DefectTicketBeforeTheSafeguard": model_tla.Invariant(
        "Where a defect ticket names the owed row, the route never names `/ratchet:safeguard`:"
        " the ticket is the work.",
        defect_before_the_safeguard("advice"),
    ),
    "RealDefectTicketBeforeTheSafeguard": model_tla.Invariant(
        "Where a defect ticket names the owed row, the real route never names"
        " `/ratchet:safeguard`: the ticket is the work.",
        defect_before_the_safeguard("real_advice"),
    ),
    "RoundBeforeBehind": model_tla.Invariant(
        "Where a round's ticket is open or its change waits for the owner's merge, the route"
        " names that, not another round, though mirrored code changed since.",
        round_before_behind("advice"),
    ),
    "RealRoundBeforeBehind": model_tla.Invariant(
        "Where a round's ticket is open or its change waits for the owner's merge, the real route"
        " names that, not another round, though mirrored code changed since.",
        round_before_behind("real_advice"),
    ),
    "UpkeepNeverHidesTheWork": model_tla.Invariant(
        "Where the route names a round waiting for the owner or a model behind, the step it would"
        " otherwise name can be taken: neither is red.",
        beside(Advice.FORMAL_ROUND, Advice.FORMAL_UPKEEP),
    ),
}


class Shown(NamedTuple):
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
    missing: bool
    upkeep: str
    defect: bool


RELEASED: Final = frozenset(
    {Advice.ANSWER, Advice.LAND_GUARD, Advice.CHANGE_TICKET, Advice.SAFEGUARD}
)
READ_AS: Final = {Upkeep.FIXED: Upkeep.BEHIND, Upkeep.WAITING_BEHIND: Upkeep.WAITING}


def charted(label: str) -> str:
    if label in RELEASED:
        return "release"
    return Advice.BUILD if label in BUILDS else label


def shown(world: World) -> Shown:
    return Shown(
        advice(world),
        real_advice(world),
        charted(beneath(world)),
        world.head,
        world.tree,
        world.parked,
        world.fills,
        world.reviews,
        world.owes,
        world.architecture,
        world.flag is Flag.MISSING,
        READ_AS.get(world.upkeep, world.upkeep),
        defect_open(world),
    )


def export() -> model_tla.Export:
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
    return model_tla.export(
        "RouteShown", model_tla.summary(graph(), shown), {}, fields=Shown._fields
    )
