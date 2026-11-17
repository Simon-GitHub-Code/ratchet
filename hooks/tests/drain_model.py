from __future__ import annotations

import dataclasses
import functools
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass
from enum import Enum
from typing import TYPE_CHECKING, Any, NamedTuple

from tests import model_tla

if TYPE_CHECKING:
    from collections.abc import Iterator, Mapping

CAP = 1
RETRIES = 3


class Fact(Enum):
    __hash__ = object.__hash__


class Name(Fact):
    A = "a"
    B = "b"
    C = "c"
    D = "d"
    E = "e"


NAMES = tuple(Name)
INDEX = {name: index for index, name in enumerate(NAMES)}
BY_VALUE = {name.value: name for name in NAMES}
BLOCKER: dict[Name, Name] = {Name.B: Name.A, Name.E: Name.D}
PARTS: dict[Name, tuple[Name, ...]] = {Name.A: (Name.D, Name.E)}
PARTED = frozenset(part for parts in PARTS.values() for part in parts)


class Tree(Fact):
    NONE = "no work tree"
    CLEAN = "a clean work tree"
    DIRTY = "uncommitted work in its tree"
    LOCKED = "uncommitted work behind a stale index lock"
    REBASING = "a rebase under way, the tree detached at the default tip"


HOLDS_WORK = frozenset({Tree.DIRTY, Tree.LOCKED})


class Flag(Fact):
    UNASKED = "the preflight asks no Flag: line before deployment"
    MISSING = "the preflight asks the Flag: line, and a has none"
    LANDED = "the owner has landed a's Flag: line"


class Branch(Fact):
    NONE = "no branch"
    BARE = "a branch with no commit of its own"
    DRAFT = "commits, the verdict record not last"
    JUDGED = "commits, the verdict record last"
    STALE = "commits, the verdict record last and certifying a tree the branch no longer has"


RECORDED = frozenset({Branch.JUDGED, Branch.STALE})
AHEAD = frozenset({Branch.DRAFT, *RECORDED})


class Outcome(Fact):
    OPENED = "opened"
    MERGED = "merged"
    CLOSED = "waits or parked"
    SPLIT = "split"


class Split(Fact):
    WHOLE = "a is not split, and d and e are not filed"
    SPLIT = "a is split into d and e, and stays open"
    CLOSED = "a is split, and the owner has closed it"


class Note(NamedTuple):
    outcome: Outcome
    hold: bool = False
    text: int | None = None
    fresh: bool = False
    attempts: int = 0
    rebasing: bool = False
    discarding: bool = False
    passed: bool = False


class Ticket(NamedTuple):
    tree: Tree = Tree.NONE
    branch: Branch = Branch.NONE
    current: bool = True
    reviewed: bool = False
    merged: bool = False
    note: Note | None = None
    text: int = 0
    stashes: int = 0
    published: bool = False
    since: bool = False

    @property
    def ahead(self) -> bool:
        return self.branch in AHEAD and not self.merged


def tip_matters(ticket: Ticket) -> bool:
    if ticket.branch is Branch.NONE or ticket.merged:
        return False
    return ticket.branch is not Branch.BARE or ticket.tree is not Tree.NONE


def normal(ticket: Ticket) -> Ticket:
    current = ticket.current or not tip_matters(ticket)
    reviewed = ticket.reviewed and ticket.branch is Branch.JUDGED and not ticket.merged
    published = ticket.published and not ticket.merged
    note = ticket.note
    retried = ticket.branch is Branch.JUDGED and not ticket.merged
    if note is not None and note.discarding and ticket.tree is Tree.NONE and not retried:
        note = note._replace(discarding=False)
    live = ticket.branch is not Branch.NONE and not ticket.merged
    read = ticket.ahead or ticket.tree is not Tree.NONE
    since = ticket.since and live and read and not (note is not None and note.passed)
    facts = (current, reviewed, published, since, note)
    if facts == (ticket.current, ticket.reviewed, ticket.published, ticket.since, ticket.note):
        return ticket
    return ticket._replace(
        current=current, reviewed=reviewed, published=published, since=since, note=note
    )


@dataclass(frozen=True)
class State:
    tickets: tuple[Ticket, ...]
    worked: bool = False
    scratch: bool = False
    half: Name | None = None
    flag: Flag = Flag.UNASKED
    split: Split = Split.WHOLE

    @property
    def edited(self) -> bool:
        return any(ticket.text for ticket in self.tickets)

    def of(self, name: Name) -> Ticket:
        return self.tickets[INDEX[name]]

    def with_ticket(self, name: Name, ticket: Ticket) -> State:
        tickets = list(self.tickets)
        tickets[INDEX[name]] = normal(ticket)
        first = tickets[INDEX[Name.A]]
        flag = Flag.UNASKED if first.ahead or first.merged else self.flag
        return State(tuple(tickets), self.worked, self.scratch, self.half, flag, self.split)

    def __hash__(self) -> int:
        cached = self.__dict__.get("hashed")
        if cached is None:
            facts = (self.tickets, self.worked, self.scratch, self.half, self.flag, self.split)
            cached = hash(facts)
            object.__setattr__(self, "hashed", cached)
        return cached

    def each(self) -> Iterator[tuple[Name, Ticket]]:
        return zip(NAMES, self.tickets, strict=True)

    def filed(self, name: Name) -> bool:
        return name not in PARTED or self.split is not Split.WHOLE


INITIAL = State(tickets=tuple(Ticket() for _ in NAMES))


class Status(Fact):
    BUILDABLE = "buildable"
    BUILDING = "building"
    BUILT = "built"
    WAITING = "waiting"
    HELD = "held"
    REFUSED = "refused"


LIVE = frozenset({Status.BUILDABLE, Status.BUILDING, Status.BUILT})


def held_by_note(ticket: Ticket) -> bool:
    note = ticket.note
    return note is not None and note.hold and note.text == ticket.text


def resolved_of(state: State) -> set[Name]:
    resolved = {name for name, ticket in state.each() if ticket.merged or state.half is name}
    return resolved | {Name.A} if state.split is Split.CLOSED else resolved


@functools.cache
def classify(state: State) -> dict[Name, Status]:
    found: dict[Name, Status] = {}
    resolved = resolved_of(state)
    for name, ticket in state.each():
        if name in resolved or not state.filed(name):
            continue
        if held_by_note(ticket):
            found[name] = Status.HELD
        elif ticket.ahead:
            found[name] = Status.BUILT
        elif ticket.tree is not Tree.NONE:
            found[name] = Status.BUILDING
        elif name is Name.A and state.flag is Flag.MISSING:
            found[name] = Status.REFUSED
        elif any(part not in resolved for part in awaited(state, name)):
            found[name] = Status.WAITING
        else:
            found[name] = Status.BUILDABLE
    return settled(state, found, resolved)


def settled(state: State, found: dict[Name, Status], resolved: set[Name]) -> dict[Name, Status]:
    live = {name for name, status in found.items() if status in LIVE}
    waiting = [name for name, status in found.items() if status is Status.WAITING]
    reached = True
    while reached:
        reached = False
        for name in waiting:
            owed = {part for part in awaited(state, name) if part not in resolved}
            if name not in live and live.issuperset(owed):
                live.add(name)
                reached = True
    return {
        name: Status.REFUSED if name in waiting and name not in live else status
        for name, status in found.items()
    }


def building(state: State) -> int:
    return sum(ticket.tree is not Tree.NONE for ticket in state.tickets if not ticket.merged)


def start(state: State) -> tuple[Name, ...]:
    if state.half is not None:
        return ()
    statuses = classify(state)
    buildable = tuple(name for name in NAMES if statuses.get(name) is Status.BUILDABLE)
    return buildable[: max(0, CAP - building(state))]


class Kind(Fact):
    OPEN = "drain.py open"
    WORK = "the agent leaves work uncommitted in"
    COMMIT = "the agent commits"
    RECORD = "the agent commits the verdict record of"
    PUBLISH = "a role of the pipeline run commits in"
    UPDATE = "drain.py update"
    LAND = "drain.py land"
    CLOSE = "drain.py close --outcome parked"
    CLEAN = "drain.py clean"
    CLEAN_ALL = "drain.py clean --all"
    REPORT = "drain.py report"
    EDIT = "the owner edits ticket"
    LINE = "the owner lands the Flag: line of"
    UPGRADE = "an upgrade makes the preflight ask the Flag: line before deployment"
    LOCK = "git leaves a stale index lock in the tree of"
    UNLOCK = "the owner removes the stale index lock of"
    KILL_MID_REBASE = "a kill mid-rebase stops drain.py update"
    KILL_AFTER_REBASE = "a kill after the rebase stops drain.py update"
    KILL_BEFORE_DISCARD = "a kill before the discard stops drain.py close --outcome parked"
    KILL_AFTER_STASH = "a kill after the stash stops drain.py close --outcome parked"
    KILL_LAND_BEFORE_NOTE = "a kill before the note stops drain.py land"
    KILL_LAND_BEFORE_DISCARD = "a kill before the discard stops drain.py land"
    KILL_LAND_AFTER_DISCARD = "a kill after the discard stops drain.py land"
    KILL_BEFORE_FAST_FORWARD = "a kill before the fast-forward stops merge.py in drain.py land"
    KILL_MID_FAST_FORWARD = "a kill mid fast-forward stops merge.py in drain.py land"
    SPLIT = "drain.py split"
    RESOLVE = "the owner closes ticket"
    CHECK = "drain.py check"
    CHECK_NOT_FINAL = "drain.py check without --final"
    CHECK_UNREADABLE = "drain.py check cannot read the present state of"
    CHECK_HALTED = "drain.py check finds only a rebase left unfinished in"
    CHECK_HALTED_NOT_FINAL = "drain.py check without --final finds only a rebase left unfinished in"
    UPDATE_UNREADABLE = "the check of drain.py update cannot read the present state of"


KILLS = {
    Kind.KILL_MID_REBASE: Kind.UPDATE,
    Kind.KILL_AFTER_REBASE: Kind.UPDATE,
    Kind.KILL_BEFORE_DISCARD: Kind.CLOSE,
    Kind.KILL_AFTER_STASH: Kind.CLOSE,
    Kind.KILL_LAND_BEFORE_NOTE: Kind.LAND,
    Kind.KILL_LAND_BEFORE_DISCARD: Kind.LAND,
    Kind.KILL_LAND_AFTER_DISCARD: Kind.LAND,
    Kind.KILL_BEFORE_FAST_FORWARD: Kind.LAND,
    Kind.KILL_MID_FAST_FORWARD: Kind.LAND,
}


@dataclass(frozen=True)
class Action:
    kind: Kind
    ticket: Name | None = None

    @functools.cached_property
    def name(self) -> str:
        return self.kind.value if self.ticket is None else f"{self.kind.value} {self.ticket.value}"


Move = tuple[Action, State]


def moved(state: State) -> State:
    for name, ticket in state.each():
        state = state.with_ticket(name, ticket._replace(current=False, reviewed=False))
    return state


def discard(ticket: Ticket) -> Ticket:
    if ticket.tree is Tree.LOCKED:
        return ticket
    stashes = ticket.stashes + (ticket.tree is Tree.DIRTY)
    return ticket._replace(tree=Tree.NONE, stashes=stashes)


def noted(ticket: Ticket, outcome: Outcome, *, retry: bool = False) -> Ticket:
    prior = ticket.note
    same = prior is not None and prior.text == ticket.text and prior.outcome is not Outcome.OPENED
    counted = 0 if prior is not None and prior.discarding else 1
    attempts = min(RETRIES, (prior.attempts if same and prior else 0) + counted) if retry else 0
    merged = outcome is Outcome.MERGED
    hold = not merged and (attempts >= RETRIES or not retry)
    passed = prior is not None and prior.passed
    note = Note(
        outcome, hold, ticket.text, fresh=True, attempts=attempts, discarding=True, passed=passed
    )
    return ticket._replace(note=note)


def closed(ticket: Ticket, outcome: Outcome, *, retry: bool = False) -> Ticket:
    settled = discard(noted(ticket, outcome, retry=retry))
    note = settled.note
    return settled._replace(note=note._replace(discarding=False)) if note else settled


def says_merged(ticket: Ticket) -> bool:
    return ticket.note is not None and ticket.note.outcome is Outcome.MERGED


def recorded(ticket: Ticket) -> Ticket:
    return closed(ticket, Outcome.MERGED)


def unrecorded(ticket: Ticket) -> bool:
    note = ticket.note
    return ticket.merged and note is not None and note.outcome is not Outcome.MERGED


def finished(ticket: Ticket) -> Ticket:
    note = ticket.note
    if note is None or not note.discarding:
        return ticket
    return discard(ticket)._replace(note=note._replace(discarding=False))


def _open(state: State, name: Name) -> State:
    if name not in start(state):
        return state
    ticket = state.of(name)
    prior = ticket.note
    kept = prior is not None and prior.fresh and prior.outcome is Outcome.CLOSED
    opened = Ticket(
        tree=Tree.CLEAN,
        branch=Branch.BARE,
        note=(
            prior._replace(hold=False, attempts=0, rebasing=False, discarding=False, passed=False)
            if prior and kept
            else Note(Outcome.OPENED)
        ),
        text=ticket.text,
        stashes=ticket.stashes,
    )
    return state.with_ticket(name, opened)


def _work(state: State, name: Name) -> State | None:
    ticket = state.of(name)
    if state.worked or ticket.merged or ticket.tree is not Tree.CLEAN:
        return None
    dirtied = state.with_ticket(name, ticket._replace(tree=Tree.DIRTY))
    return dataclasses.replace(dirtied, worked=True)


def _commit(state: State, name: Name) -> State | None:
    ticket = state.of(name)
    usable = ticket.tree in {Tree.CLEAN, Tree.DIRTY} and not ticket.merged
    if not usable or (ticket.branch is not Branch.BARE and ticket.tree is not Tree.DIRTY):
        return None
    committed = ticket._replace(tree=Tree.CLEAN, branch=Branch.DRAFT, reviewed=False, since=True)
    return state.with_ticket(name, committed)


def _record(state: State, name: Name) -> State | None:
    ticket = state.of(name)
    if ticket.merged or ticket.tree is not Tree.CLEAN or ticket.branch is not Branch.DRAFT:
        return None
    judged = ticket._replace(branch=Branch.JUDGED, reviewed=ticket.current, since=True)
    return state.with_ticket(name, judged)


def unchecked(ticket: Ticket) -> bool:
    note = ticket.note
    live = ticket.branch is not Branch.NONE and not ticket.merged
    return live and not (note is not None and note.passed)


def commit_refused(ticket: Ticket) -> bool:
    mark = ticket.note is not None and ticket.note.rebasing
    return ticket.tree is not Tree.NONE and (unchecked(ticket) or mark)


def checked(ticket: Ticket) -> Ticket:
    note = ticket.note or Note(Outcome.OPENED)
    return ticket._replace(note=note._replace(passed=True))


def restarted(ticket: Ticket) -> Ticket:
    note = ticket.note or Note(Outcome.OPENED)
    return ticket._replace(note=note._replace(passed=False), since=False)


def _publish(state: State, name: Name) -> State | None:
    ticket = state.of(name)
    runs = ticket.tree is Tree.CLEAN and ticket.branch in {Branch.BARE, Branch.DRAFT}
    if ticket.merged or ticket.published or not runs or not unchecked(ticket):
        return None
    moved_by_a_role = ticket._replace(
        branch=Branch.DRAFT, reviewed=False, published=True, since=True
    )
    return state.with_ticket(name, moved_by_a_role)


def asked(ticket: Ticket) -> bool:
    return unchecked(ticket) and not held_by_note(ticket)


def cut_off(ticket: Ticket) -> bool:
    note = ticket.note
    return note is not None and note.rebasing and ticket.tree is not Tree.REBASING


def in_its_tree(state: State, name: Name) -> Ticket | None:
    ticket = state.of(name)
    if not asked(ticket) or cut_off(ticket):
        return None
    if ticket.tree is Tree.NONE:
        if not ticket.ahead or trees(state) >= CAP or ticket.note is None:
            return None
        ticket = ticket._replace(tree=Tree.CLEAN, note=ticket.note._replace(discarding=False))
    return ticket if ticket.tree in {Tree.CLEAN, Tree.DIRTY} else None


def _check(state: State, name: Name, *, final: bool = True) -> State | None:
    if asked(state.of(name)) and cut_off(state.of(name)):
        return state
    ticket = in_its_tree(state, name)
    if ticket is None:
        return None
    if ticket.since:
        return state.with_ticket(name, closed(ticket, Outcome.CLOSED))
    return state.with_ticket(name, checked(ticket) if final else ticket)


def _check_unreadable(state: State, name: Name) -> State | None:
    ticket = in_its_tree(state, name)
    if ticket is None or not ticket.ahead:
        return None
    return state.with_ticket(name, closed(ticket, Outcome.CLOSED, retry=True))


def _check_halted(state: State, name: Name, *, final: bool = True) -> State | None:
    ticket = in_its_tree(state, name)
    rejudged = state.of(name).tree is not Tree.NONE and state.of(name).branch in AHEAD
    if ticket is None or ticket.since or not rejudged:
        return None
    read = checked(ticket) if final else ticket
    return state.with_ticket(name, closed(read, Outcome.CLOSED, retry=True))


def rebased(ticket: Ticket) -> Ticket:
    branch = Branch.DRAFT if ticket.branch in RECORDED else ticket.branch
    return ticket._replace(current=True, branch=branch, reviewed=False)


def marked(ticket: Ticket, *, rebasing: bool) -> Ticket:
    note = ticket.note or Note(Outcome.OPENED)
    return ticket._replace(note=note._replace(rebasing=rebasing, discarding=False))


def trees(state: State) -> int:
    return sum(ticket.tree is not Tree.NONE for ticket in state.tickets)


def revived(state: State, name: Name) -> Ticket | None:
    ticket = state.of(name)
    if ticket.tree is Tree.NONE:
        mark = ticket.note is not None and ticket.note.rebasing
        if not ticket.ahead or (ticket.current and not mark) or trees(state) >= CAP:
            return None
        ticket = ticket._replace(tree=Tree.CLEAN)
    elif ticket.tree is Tree.REBASING:
        ticket = ticket._replace(tree=Tree.CLEAN)
    return ticket if ticket.tree is Tree.CLEAN else None


def unmarked(state: State, name: Name) -> State | None:
    ticket = state.of(name)
    if unrecorded(ticket) or says_merged(ticket):
        return None
    note = ticket.note
    if note is None or not note.discarding:
        return state
    return state.with_ticket(name, ticket._replace(note=note._replace(discarding=False)))


def to_rebase(state: State, name: Name) -> Ticket | None:
    ticket = revived(state, name)
    if ticket is None or ticket.current or ticket.branch is Branch.NONE:
        return None
    return marked(ticket, rebasing=True)


def finishing(ticket: Ticket) -> bool:
    return ticket.current and ticket.note is not None and ticket.note.rebasing


def checked_first(state: State, name: Name) -> State | None:
    ticket = state.of(name)
    if not unchecked(ticket) or not ticket.ahead or revived(state, name) is None:
        return state
    return None if ticket.since else state.with_ticket(name, checked(ticket))


def _update(state: State, name: Name) -> State:
    ticket = state.of(name)
    ready = unmarked(state, name)
    if ready is None:
        return state.with_ticket(name, recorded(ticket) if unrecorded(ticket) else finished(ticket))
    working = revived(ready, name)
    if working is None:
        return ready
    passed = checked_first(ready, name)
    if passed is None:
        return ready.with_ticket(name, closed(working, Outcome.CLOSED))
    ready, working = passed, revived(passed, name) or working
    if finishing(working):
        return ready.with_ticket(name, restarted(rebased(marked(working, rebasing=False))))
    began = to_rebase(ready, name)
    if began is None:
        return ready
    return ready.with_ticket(name, restarted(marked(rebased(began), rebasing=False)))


def _update_unreadable(state: State, name: Name) -> State | None:
    ready = unmarked(state, name)
    if ready is None or (working := revived(ready, name)) is None:
        return None
    if not unchecked(working) or not working.ahead:
        return None
    return ready.with_ticket(name, working)


def _kill_mid_rebase(state: State, name: Name) -> State | None:
    ready = unmarked(state, name)
    if ready is not None:
        ready = checked_first(ready, name)
    if ready is None or (began := to_rebase(ready, name)) is None:
        return None
    return ready.with_ticket(name, began._replace(tree=Tree.REBASING))


def _kill_after_rebase(state: State, name: Name) -> State | None:
    ready = unmarked(state, name)
    if ready is not None:
        ready = checked_first(ready, name)
    if ready is None:
        return None
    began = to_rebase(ready, name)
    if began is not None:
        branch = Branch.STALE if began.branch in RECORDED else began.branch
        rebased_only = began._replace(current=True, branch=branch, reviewed=False, since=True)
        return ready.with_ticket(name, rebased_only)
    ticket = revived(ready, name)
    if ticket is None or not finishing(ticket) or ticket.branch not in RECORDED:
        return None
    return ready.with_ticket(name, ticket)


def parkable(ticket: Ticket) -> bool:
    return not says_merged(ticket) and not unrecorded(ticket)


def _kill_after_stash(state: State, name: Name) -> State | None:
    ticket = state.of(name)
    if ticket.tree is not Tree.DIRTY or not parkable(ticket):
        return None
    stashed = noted(ticket, Outcome.CLOSED)._replace(tree=Tree.CLEAN, stashes=ticket.stashes + 1)
    return state.with_ticket(name, stashed)


def _kill_before_discard(state: State, name: Name) -> State | None:
    ticket = state.of(name)
    if not parkable(ticket):
        return None
    killed = state.with_ticket(name, noted(ticket, Outcome.CLOSED))
    return None if killed == _close(state, name) else killed


def judge(ticket: Ticket) -> list[str]:
    if ticket.merged:
        return ["behind", "record"]
    reasons = [] if ticket.current else ["behind"]
    return reasons if ticket.branch is Branch.JUDGED else [*reasons, "record"]


class Landing(NamedTuple):
    judged: State
    outcome: Outcome
    retry: bool


def recovered(state: State) -> State:
    if not state.scratch and state.half is None:
        return state
    return dataclasses.replace(state, scratch=False, half=None)


@functools.cache
def landing(state: State, name: Name) -> Landing | None:
    state = recovered(state)
    ticket = state.of(name)
    if unrecorded(ticket):
        return Landing(state, Outcome.MERGED, retry=False)
    if ticket.branch is Branch.NONE or held_by_note(ticket) or says_merged(ticket):
        return None
    if unchecked(ticket):
        return None
    reasons = judge(ticket)
    if not reasons:
        merged = ticket._replace(merged=True, current=False, reviewed=False)
        return Landing(moved(state).with_ticket(name, merged), Outcome.MERGED, retry=False)
    return Landing(state, Outcome.CLOSED, retry=reasons == ["behind"])


def _land(state: State, name: Name) -> State:
    state = recovered(state)
    ticket = state.of(name)
    if not unrecorded(ticket) and (says_merged(ticket) or held_by_note(ticket)):
        return state.with_ticket(name, finished(ticket))
    run = landing(state, name)
    if run is None:
        return state
    ticket = closed(run.judged.of(name), run.outcome, retry=run.retry)
    return run.judged.with_ticket(name, ticket)


def _kill_land_before_note(state: State, name: Name) -> State | None:
    run = landing(state, name)
    if run is None or run.judged == recovered(state):
        return None
    return run.judged


def _kill_land_before_discard(state: State, name: Name) -> State | None:
    run = landing(state, name)
    if run is None:
        return None
    ticket = noted(run.judged.of(name), run.outcome, retry=run.retry)
    return run.judged.with_ticket(name, ticket)


def _kill_land_after_discard(state: State, name: Name) -> State | None:
    run = landing(state, name)
    if run is None or discard(run.judged.of(name)) == run.judged.of(name):
        return None
    ticket = discard(noted(run.judged.of(name), run.outcome, retry=run.retry))
    return run.judged.with_ticket(name, ticket)


def _kill_before_fast_forward(state: State, name: Name) -> State | None:
    run = landing(state, name)
    if run is None or run.judged == recovered(state):
        return None
    return dataclasses.replace(recovered(state), scratch=True)


def _kill_mid_fast_forward(state: State, name: Name) -> State | None:
    started = _kill_before_fast_forward(state, name)
    return None if started is None else dataclasses.replace(started, half=name)


def _close(state: State, name: Name) -> State:
    ticket = state.of(name)
    if unrecorded(ticket):
        return state.with_ticket(name, recorded(ticket))
    if says_merged(ticket):
        return state.with_ticket(name, finished(ticket))
    return state.with_ticket(name, closed(ticket, Outcome.CLOSED))


def cleaned(state: State, *, everything: bool) -> State:
    state = recovered(state)
    for name, ticket in state.each():
        settled = recorded(ticket) if unrecorded(ticket) else ticket
        swept = ticket.tree is not Tree.NONE and (ticket.merged or everything)
        if swept or everything:
            settled = finished(settled)
        if swept:
            settled = discard(settled)
        note = settled.note
        unfinished = ticket.tree is Tree.REBASING or not settled.ahead
        if everything and note is not None and note.rebasing and unfinished:
            settled = settled._replace(note=note._replace(rebasing=False))
        state = state.with_ticket(name, settled)
    return state


def _report(state: State) -> State:
    for name, ticket in state.each():
        settled = recorded(ticket) if unrecorded(ticket) else ticket
        if settled.note is not None and settled.note.fresh:
            settled = settled._replace(note=settled.note._replace(fresh=False))
        state = state.with_ticket(name, settled)
    return state


def _edit(state: State, name: Name) -> State | None:
    ticket = state.of(name)
    if state.edited or ticket.merged or state.half is not None:
        return None
    shifted = moved(state)
    return shifted.with_ticket(name, shifted.of(name)._replace(text=1))


def _line(state: State, name: Name) -> State | None:
    if state.flag is not Flag.MISSING or (edited := _edit(state, name)) is None:
        return None
    return dataclasses.replace(edited, flag=Flag.LANDED)


def _upgrade(state: State) -> State | None:
    first = state.of(Name.A)
    unasked = state.flag is Flag.UNASKED and state.split is Split.WHOLE
    if not unasked or first.ahead or first.merged or state.half is not None:
        return None
    return dataclasses.replace(moved(state), flag=Flag.MISSING)


def _split(state: State, name: Name) -> State | None:
    ticket = state.of(name)
    if state.split is not Split.WHOLE or ticket.tree is not Tree.CLEAN:
        return None
    if ticket.branch is not Branch.BARE or ticket.merged:
        return None
    parted = moved(state.with_ticket(name, closed(ticket, Outcome.SPLIT)))
    return dataclasses.replace(parted, split=Split.SPLIT)


def _resolve(state: State, name: Name) -> State | None:
    if state.split is not Split.SPLIT or (edited := _edit(state, name)) is None:
        return None
    return dataclasses.replace(edited, split=Split.CLOSED)


def _lock(state: State, name: Name) -> State | None:
    ticket = state.of(name)
    if ticket.tree is not Tree.DIRTY:
        return None
    return state.with_ticket(name, ticket._replace(tree=Tree.LOCKED))


def _unlock(state: State, name: Name) -> State | None:
    ticket = state.of(name)
    if ticket.tree is not Tree.LOCKED:
        return None
    return state.with_ticket(name, ticket._replace(tree=Tree.DIRTY))


def recovering(act: Callable[[State, Name], State | None]) -> Callable[[State, Name], State | None]:
    def run(state: State, name: Name) -> State | None:
        return act(recovered(state), name)

    return run


ON_TICKET: dict[Kind, Callable[[State, Name], State | None]] = {
    Kind.OPEN: recovering(_open),
    Kind.WORK: _work,
    Kind.COMMIT: _commit,
    Kind.RECORD: _record,
    Kind.PUBLISH: _publish,
    Kind.CHECK: recovering(_check),
    Kind.CHECK_NOT_FINAL: recovering(functools.partial(_check, final=False)),
    Kind.CHECK_UNREADABLE: recovering(_check_unreadable),
    Kind.CHECK_HALTED: recovering(_check_halted),
    Kind.CHECK_HALTED_NOT_FINAL: recovering(functools.partial(_check_halted, final=False)),
    Kind.CLOSE: recovering(_close),
    Kind.SPLIT: recovering(_split),
    Kind.UPDATE: recovering(_update),
    Kind.UPDATE_UNREADABLE: recovering(_update_unreadable),
    Kind.EDIT: _edit,
    Kind.LINE: _line,
    Kind.RESOLVE: _resolve,
    Kind.LAND: recovering(_land),
    Kind.LOCK: _lock,
    Kind.UNLOCK: _unlock,
    Kind.KILL_MID_REBASE: recovering(_kill_mid_rebase),
    Kind.KILL_AFTER_REBASE: recovering(_kill_after_rebase),
    Kind.KILL_BEFORE_DISCARD: recovering(_kill_before_discard),
    Kind.KILL_AFTER_STASH: recovering(_kill_after_stash),
    Kind.KILL_LAND_BEFORE_NOTE: recovering(_kill_land_before_note),
    Kind.KILL_LAND_BEFORE_DISCARD: recovering(_kill_land_before_discard),
    Kind.KILL_LAND_AFTER_DISCARD: recovering(_kill_land_after_discard),
    Kind.KILL_BEFORE_FAST_FORWARD: recovering(_kill_before_fast_forward),
    Kind.KILL_MID_FAST_FORWARD: recovering(_kill_mid_fast_forward),
}
WHOLE: dict[Kind, Callable[[State], State | None]] = {
    Kind.CLEAN: lambda state: cleaned(state, everything=False),
    Kind.CLEAN_ALL: lambda state: cleaned(state, everything=True),
    Kind.REPORT: lambda state: _report(recovered(state)),
    Kind.UPGRADE: _upgrade,
}


A_AND_C = (Name.A, Name.C)
BUILT = (Name.A, *PARTS[Name.A])
PLAYS: dict[Kind, tuple[Name, ...]] = {
    Kind.COMMIT: BUILT,
    Kind.RECORD: BUILT,
    Kind.LAND: (*A_AND_C, *PARTS[Name.A]),
    Kind.SPLIT: (Name.A,),
    Kind.RESOLVE: (Name.A,),
    Kind.PUBLISH: (Name.A,),
    Kind.CHECK: BUILT,
    Kind.CHECK_NOT_FINAL: (Name.A,),
    Kind.CHECK_UNREADABLE: (Name.A,),
    Kind.CHECK_HALTED: (Name.A,),
    Kind.CHECK_HALTED_NOT_FINAL: (Name.A,),
    Kind.UPDATE_UNREADABLE: (Name.A,),
    Kind.WORK: (Name.C,),
    Kind.LOCK: (Name.C,),
    Kind.UNLOCK: (Name.C,),
    Kind.UPDATE: A_AND_C,
    Kind.CLOSE: A_AND_C,
    Kind.EDIT: A_AND_C,
    Kind.LINE: (Name.A,),
    Kind.KILL_MID_REBASE: (Name.A,),
    Kind.KILL_AFTER_REBASE: (Name.A,),
    Kind.KILL_BEFORE_DISCARD: (Name.C,),
    Kind.KILL_AFTER_STASH: (Name.C,),
    Kind.KILL_LAND_BEFORE_NOTE: (Name.A,),
    Kind.KILL_LAND_BEFORE_DISCARD: (Name.A,),
    Kind.KILL_LAND_AFTER_DISCARD: (Name.A,),
    Kind.KILL_BEFORE_FAST_FORWARD: (Name.A,),
    Kind.KILL_MID_FAST_FORWARD: (Name.A,),
}


KILLED = {kind.value: ON_TICKET[command] for kind, command in KILLS.items()}
ON_A_TICKET = [
    (Action(kind, name), act, name)
    for kind, act in ON_TICKET.items()
    for name in PLAYS.get(kind, NAMES)
]
ON_THE_QUEUE = [(Action(kind), act) for kind, act in WHOLE.items()]


def on_a_split(state: State, kind: Kind) -> bool:
    return state.split is not Split.WHOLE and kind is not Kind.RESOLVE


def moves(state: State) -> list[Move]:
    found: list[Move] = []
    for action, act, name in ON_A_TICKET:
        if not state.filed(name) or (name in PARTS and on_a_split(state, action.kind)):
            continue
        if name in PARTED and not guarded(state, action):
            continue
        after = act(state, name)
        if after is not None:
            found.append((action, after))
    found += [(action, after) for action, act in ON_THE_QUEUE if (after := act(state)) is not None]
    return found


Edge = tuple[State, str, State]


def graph(
    keep: Callable[[State, Action], bool] = lambda _state, _action: True,
) -> tuple[frozenset[State], list[Edge]]:
    seen = {INITIAL: INITIAL}
    frontier = deque([INITIAL])
    edges: list[Edge] = []
    while frontier:
        state = frontier.popleft()
        for action, after in moves(state):
            if not keep(state, action):
                continue
            known = seen.get(after)
            if known is None:
                seen[after] = known = after
                frontier.append(after)
            edges.append((state, action.name, known))
    return frozenset(seen), edges


def ticket_of(action: str) -> Name | None:
    last = action.rsplit(" ", 1)[-1]
    return BY_VALUE.get(last)


def within_cap(before: State, _: str, after: State) -> bool:
    """No step makes more tickets build at once than the cap allows."""
    return building(after) <= CAP or building(after) <= building(before)


def kept(before: State, action: str, after: State) -> bool:
    """Uncommitted work in a queue work tree is never lost: it stays in the tree, goes into a stash, or the agent commits it."""  # noqa: E501
    for name, was, now in zip(NAMES, before.tickets, after.tickets, strict=True):
        if was.tree not in HOLDS_WORK or now.tree in HOLDS_WORK or now.stashes > was.stashes:
            continue
        if action != f"{Kind.COMMIT.value} {name.value}":
            return False
    return True


def judged(before: State, _: str, after: State) -> bool:
    """The default branch moves only to the merge of a branch whose verdict record is its last commit."""  # noqa: E501
    return all(
        was.merged or not now.merged or was.branch is Branch.JUDGED
        for was, now in zip(before.tickets, after.tickets, strict=True)
    )


def not_stale(before: State, _: str, after: State) -> bool:
    """A branch lands only on a review of the default tip it lands on, never on a review of an older one."""  # noqa: E501
    return all(
        was.merged or not now.merged or was.reviewed
        for was, now in zip(before.tickets, after.tickets, strict=True)
    )


def merge_said(ticket: Ticket) -> tuple[bool, bool]:
    note = ticket.note
    if note is None or note.outcome is not Outcome.MERGED:
        return False, False
    return True, note.fresh


def reported_once(before: State, action: str, after: State) -> bool:
    """A ticket's merge is recorded when it lands, or left by a kill where the drain records it, is never closed as another outcome, and is printed by exactly one report."""  # noqa: E501
    killed = action.rpartition(" ")[0] in KILLED
    for was, now in zip(before.tickets, after.tickets, strict=True):
        said, unprinted = merge_said(was)
        says, unprinting = merge_said(now)
        left = killed and unrecorded(now)
        lost = now.merged and not says and not unrecorded(now)
        if lost or (now.merged and not was.merged and not unprinting and not left):
            return False
        if says and not said and not unprinting and action != Kind.REPORT.value:
            return False
        closes = now.note is not None and now.note.outcome is Outcome.CLOSED
        if now.merged and closes and now.note != was.note:
            return False
        if said and not says:
            return False
        printed = unprinted and action == Kind.REPORT.value
        if said and unprinted != unprinting and not printed:
            return False
    return True


def report_writes(before: State, action: str, after: State) -> bool:
    """A report changes a ticket only to print its close, or to record a merge no note says and print it."""  # noqa: E501
    if action != Kind.REPORT.value:
        return True
    for was, now in zip(before.tickets, after.tickets, strict=True):
        if unrecorded(was):
            if not says_merged(now) or now.note is None or now.note.fresh:
                return False
        elif now != was and (
            was.note is None or now != was._replace(note=was.note._replace(fresh=False))
        ):
            return False
    return True


def stays_held(before: State, _: str, after: State) -> bool:
    """A held ticket stays held, and unmerged, until the owner changes its text."""
    now = classify(after)
    return all(
        now.get(name) is Status.HELD or before.of(name).text != after.of(name).text
        for name, status in classify(before).items()
        if status is Status.HELD
    )


def released_by_the_owner(ticket: Ticket) -> bool:
    note = ticket.note
    if note is None or not note.hold:
        return True
    return note.text is not None and 0 <= note.text < ticket.text


def held_until_edited(before: State, _: str, after: State) -> bool:
    """A ticket its note holds merges only once the owner has changed the text it was held on."""
    return all(
        was.merged or not now.merged or released_by_the_owner(was)
        for was, now in zip(before.tickets, after.tickets, strict=True)
    )


def awaited(state: State, name: Name) -> tuple[Name, ...]:
    blocker = BLOCKER.get(name)
    if blocker is None:
        return ()
    split = blocker in PARTS and state.split is not Split.WHOLE
    return PARTS[blocker] if split else (blocker,)


def refused(_: State, __: str, after: State) -> bool:
    """A ticket whose blocker waits on the owner is refused, never left waiting for it."""
    statuses = classify(after)
    return all(
        statuses.get(name) is not Status.WAITING or not held_by_note(after.of(blocker))
        for name in BLOCKER
        for blocker in awaited(after, name)
    )


def on_a_split_blocker(state: State) -> list[Name]:
    return [name for name in BLOCKER if awaited(state, name) != (BLOCKER[name],)]


def parts_first(before: State, _: str, after: State) -> bool:
    """A ticket that waits on a split ticket is never opened before every part of it has landed."""
    return all(
        after.of(name).tree is Tree.NONE
        or before.of(name).tree is not Tree.NONE
        or all(before.of(part).merged for part in awaited(before, name))
        for name in on_a_split_blocker(after)
    )


def can_land(state: State, name: Name) -> bool:
    return state.of(name).merged or classify(state).get(name) in {*LIVE, Status.WAITING}


def parts_awaited(_: State, __: str, after: State) -> bool:
    """A ticket that waits on a split ticket is not refused while every part can still land, and is buildable once every part has landed."""  # noqa: E501
    statuses = classify(after)
    for name in on_a_split_blocker(after):
        status = statuses.get(name)
        parts = awaited(after, name)
        if all(after.of(part).merged for part in parts):
            if status in {Status.WAITING, Status.REFUSED}:
                return False
        elif status is Status.REFUSED and all(can_land(after, part) for part in parts):
            return False
    return True


def behind(ticket: Ticket) -> bool:
    marked = ticket.note is not None and ticket.note.rebasing
    return ticket.ahead and (not ticket.current or marked)


def next_drain(state: State, name: Name) -> State:
    swept = cleaned(state, everything=True)
    return _update(swept, name) if behind(swept.of(name)) else swept


def recovers(before: State, action: str, after: State) -> bool:
    """A command killed part-way and run again ends where it would have ended, and the next drain's ``clean --all``, with the ``update`` its plan then asks for, ends where it would have after the whole command or none of it."""  # noqa: E501
    said, _, last = action.rpartition(" ")
    command, name = KILLED.get(said), BY_VALUE.get(last)
    if command is None or name is None:
        return True
    whole = command(before, name)
    if whole is None or command(after, name) != whole:
        return False
    return next_drain(after, name) in {next_drain(whole, name), next_drain(before, name)}


def refused_unopened(before: State, _: str, after: State) -> bool:
    """A ticket the plan refuses is never given a work tree."""
    return all(
        after.of(name).tree is Tree.NONE or before.of(name).tree is not Tree.NONE
        for name, status in classify(before).items()
        if status is Status.REFUSED
    )


def released_by_the_line(_: State, action: str, after: State) -> bool:
    """Once the owner lands a refused ticket's Flag: line, neither it nor a ticket waiting on it is refused."""  # noqa: E501
    if action != f"{Kind.LINE.value} {Name.A.value}":
        return True
    statuses = classify(after)
    waiting = [name for name, blocker in BLOCKER.items() if blocker is Name.A]
    return all(statuses.get(name) is not Status.REFUSED for name in [Name.A, *waiting])


def no_tree_left(_: State, action: str, after: State) -> bool:
    """After ``clean --all`` no queue work tree is left."""
    return action != Kind.CLEAN_ALL.value or all(t.tree is Tree.NONE for t in after.tickets)


def unlanded(before: State, _: str, after: State) -> bool:
    """A branch a role of its run committed on is never landed."""
    return not any(
        was.published and now.merged for was, now in zip(before.tickets, after.tickets, strict=True)
    )


AGENT_COMMITS = frozenset({Kind.COMMIT, Kind.RECORD})
COMMITS = frozenset({*AGENT_COMMITS, Kind.PUBLISH})


def untouched(before: State, action: str, _: State) -> bool:
    """A branch a role of its run committed on is never committed on after the run."""
    return not any(
        before.of(name).published
        and action in {f"{kind.value} {name.value}" for kind in AGENT_COMMITS}
        for name in NAMES
    )


def guarded(state: State, action: Action) -> bool:
    return (
        action.kind not in COMMITS
        or action.ticket is None
        or not commit_refused(state.of(action.ticket))
    )


RULES: dict[str, Callable[[State, str, State], bool]] = {
    "1. no more tickets build at once than the cap": within_cap,
    "2. uncommitted work is never lost": kept,
    "3. the default branch moves only to a judged merge": judged,
    "4. a branch does not land on a review of an older base": not_stale,
    "5. a merge is recorded and reported exactly once": reported_once,
    "5a. a report writes only what it prints, and a merge no note says": report_writes,
    "6. a held ticket stays held until its text changes": stays_held,
    "6a. a ticket its note holds merges only once the owner edits it": held_until_edited,
    "7. a ticket whose blocker waits on the owner is refused": refused,
    "7a. a ticket the plan refuses is never opened": refused_unopened,
    "7b. the owner's Flag: line releases the refused ticket and those waiting on it": (
        released_by_the_line
    ),
    "7c. a ticket that waits on a split ticket is opened only once every part has landed": (
        parts_first
    ),
    "7d. a ticket that waits on a split ticket waits for its parts, then starts": parts_awaited,
    "8. a killed command recovers by running again or by clean --all": recovers,
    "10. clean --all leaves no queue work tree": no_tree_left,
    "11a. a branch a role committed on is never landed": unlanded,
    "11b. a branch a role committed on is never committed on after its run": untouched,
}


class Drain(Fact):
    ONE = "drain 1"
    TWO = "drain 2"


class Merge(Fact):
    NONE = "not merged"
    UNSAID = "merged, and no note says so"
    SAID = "merged, and a note no report has printed says so"
    PRINTED = "merged, and a report has printed it"


class Lease(NamedTuple):
    shared: bool | None = None
    holder: Drain | None = None
    alive: tuple[bool, bool] = (True, True)
    draining: tuple[bool, bool] = (False, False)
    took: tuple[bool, bool] = (False, False)
    tree: Drain | None = None
    draft: bool = False
    running: bool = False
    stashed: bool = False
    landing: Drain | None = None
    merge: Merge = Merge.NONE
    built: bool = False
    orphan: Drain | None = None
    doomed: bool = False
    held: bool = False


def seen_by(lease: Lease) -> str | None:
    return "landing" if lease.landing is not None else "merge" if lease.orphan is not None else None


def landing_seen(lease: Lease) -> Drain | None:
    return lease.landing if seen_by(lease) == "landing" else lease.orphan


def landing_alive(lease: Lease) -> Drain | None:
    return lease.landing if lease.landing is not None else lease.orphan


def merges_that_can_land(lease: Lease) -> list[Drain | None]:
    owners: list[Drain | None] = []
    if lease.landing is not None and lease.merge is Merge.NONE:
        owners.append(lease.landing)
    if lease.orphan is not None:
        owners.append(None)
    return owners


def of(pair: tuple[bool, bool], drain: Drain) -> bool:
    return pair[drain is Drain.TWO]


def put(pair: tuple[bool, bool], drain: Drain, *, value: bool) -> tuple[bool, bool]:
    return (pair[0], value) if drain is Drain.TWO else (value, pair[1])


class Step(Fact):
    SEPARATE = "each drain runs in a session of its own"
    SHARED = "one long-lived process runs both drains"
    CLEAN_ALL = "drain.py clean --all"
    RESUME = "drain.py clean --all --drain with its token"
    TAKE_OVER = "drain.py clean --all --take-over"
    OPEN = "drain.py open a"
    DRAFT = "its agent leaves a draft in its tree"
    PROGRAM = "its agent starts a program built in its tree"
    PROGRAM_ENDS = "the program built in the tree ends"
    REPORT = "drain.py report"
    REPORT_OWN = "drain.py report --drain with its token"
    CLEAN = "drain.py clean"
    UPDATE = "drain.py update a"
    PARK = "drain.py close --outcome parked a"
    LAND = "drain.py land a, up to the merge.py it runs"
    MERGES = "its merge.py lands the merge"
    LAND_REST = "drain.py land a, the close after the merge"
    MERGE_KILLED = "a kill stops its land and its merge.py before the merge"
    KILLED_AT_START = "a kill stops its land alone before its merge.py probes it"
    DOOMED_STOPS = "the merge.py of a land killed before its probe finds the land gone and stops"
    LOCKED_OUT = "its merge.py meets the merge lock of a merge whose land is gone, and both stop"
    ORPHANED = (
        "a kill stops its land alone once its merge.py has probed it, the merge.py running on"
    )
    ORPHAN_MERGES = "the merge.py its killed land started lands the merge"
    ORPHAN_ENDS = "the merge.py its killed land started ends and lands nothing"
    LAND_KILLED = "a kill stops its land after the merge, before the close"
    STOP = "stops, its session still open"
    DIE = "its session ends"


CLEANS = frozenset({Step.CLEAN_ALL, Step.RESUME, Step.TAKE_OVER})
REPORTS = frozenset({Step.REPORT, Step.REPORT_OWN})
OWN = frozenset({Step.RESUME, Step.REPORT_OWN})


def rival(lease: Lease, drain: Drain) -> bool:
    holder = lease.holder
    chain = holder is not None and of(lease.alive, holder) and not lease.shared
    return (chain and holder is not drain) or lease.running


def other_drain(lease: Lease, drain: Drain, step: Step) -> bool:
    ours = step in OWN and lease.holder is drain
    return not ours and rival(lease, drain)


def without_tree(lease: Lease) -> Lease:
    if lease.tree is None:
        return lease
    stashed = lease.stashed or lease.draft
    return lease._replace(tree=None, draft=False, running=False, stashed=stashed)


def merge_recorded(lease: Lease) -> Lease:
    return without_tree(lease)._replace(merge=Merge.SAID, held=False)


def _clean_all(lease: Lease, drain: Drain, step: Step) -> Lease:
    ours = step is Step.RESUME and lease.holder is drain
    if step is not Step.TAKE_OVER and not ours and rival(lease, drain):
        return lease
    taken = lease._replace(
        holder=drain,
        took=put(lease.took, drain, value=True),
        draining=put(lease.draining, drain, value=True),
    )
    if landing_seen(taken) is not None:
        return taken
    return merge_recorded(taken) if taken.merge is Merge.UNSAID else without_tree(taken)


def _report_and_release(lease: Lease, drain: Drain, step: Step) -> Lease:
    busy = other_drain(lease, drain, step)
    if not busy and lease.merge is Merge.UNSAID and landing_seen(lease) is None:
        lease = merge_recorded(lease)
    merge = Merge.PRINTED if lease.merge is Merge.SAID else lease.merge
    holder = lease.holder if busy else None
    return lease._replace(
        holder=holder, merge=merge, draining=put(lease.draining, drain, value=False)
    )


def _clean(lease: Lease, drain: Drain, _: Step) -> Lease:
    busy = landing_seen(lease) is not None or other_drain(lease, drain, Step.CLEAN)
    if lease.merge is not Merge.UNSAID or busy:
        return lease
    return merge_recorded(lease)


def _land(lease: Lease, drain: Drain, _: Step) -> Lease | None:
    if landing_seen(lease) is not None:
        return lease
    ready = lease.tree is drain and of(lease.draining, drain) and lease.merge is Merge.NONE
    if not ready or lease.draft or lease.running:
        return None
    return lease._replace(landing=drain, built=True)


def _merges(lease: Lease, _: Drain, __: Step) -> Lease | None:
    return lease._replace(merge=Merge.UNSAID) if lease.merge is Merge.NONE else None


def _land_rest(lease: Lease, _: Drain, __: Step) -> Lease | None:
    if lease.merge is Merge.NONE:
        return None
    done = merge_recorded(lease) if lease.merge is Merge.UNSAID else lease
    return done._replace(landing=None)


def _orphaned(lease: Lease, drain: Drain, _: Step) -> Lease | None:
    if lease.merge is not Merge.NONE or lease.orphan is not None:
        return None
    return lease._replace(landing=None, orphan=drain)


def _killed_at_start(lease: Lease, _: Drain, __: Step) -> Lease | None:
    if lease.merge is not Merge.NONE or lease.doomed:
        return None
    return lease._replace(landing=None, doomed=True)


def _locked_out(lease: Lease, _: Drain, __: Step) -> Lease | None:
    if not lease.doomed or lease.merge is not Merge.NONE:
        return None
    return lease._replace(landing=None, doomed=False)


def _orphan_merges(lease: Lease) -> Lease | None:
    return lease._replace(merge=Merge.UNSAID, orphan=None) if lease.merge is Merge.NONE else None


def _orphan_ends(lease: Lease) -> Lease:
    return lease._replace(orphan=None)


ORPHAN_STEPS: dict[Step, Callable[[Lease], Lease | None]] = {
    Step.ORPHAN_MERGES: _orphan_merges,
    Step.ORPHAN_ENDS: _orphan_ends,
}


def _doomed_stops(lease: Lease) -> Lease | None:
    return lease._replace(doomed=False) if lease.doomed else None


def _land_killed(lease: Lease, _: Drain, __: Step) -> Lease | None:
    return lease._replace(landing=None) if lease.merge is not Merge.NONE else None


def _merge_killed(lease: Lease, _: Drain, __: Step) -> Lease | None:
    return lease._replace(landing=None) if lease.merge is Merge.NONE else None


def _open_tree(lease: Lease, drain: Drain, _: Step) -> Lease | None:
    if landing_seen(lease) is not None:
        return lease
    ready = lease.holder is drain and of(lease.draining, drain) and lease.tree is None
    if not ready or lease.merge is not Merge.NONE:
        return None
    return lease if lease.built else lease._replace(tree=drain)


def in_flight(lease: Lease) -> bool:
    return landing_alive(lease) is not None or lease.doomed


def _update_in_flight(lease: Lease, _: Drain, __: Step) -> Lease | None:
    if not in_flight(lease):
        return None
    if landing_seen(lease) is None and lease.merge is Merge.UNSAID:
        return merge_recorded(lease)
    return lease


def _park_in_flight(lease: Lease, _: Drain, __: Step) -> Lease | None:
    if not in_flight(lease):
        return None
    if landing_seen(lease) is not None:
        return lease
    if lease.merge is Merge.UNSAID:
        return merge_recorded(lease)
    if lease.merge is not Merge.NONE:
        return lease
    return without_tree(lease)._replace(held=True)


def _leave_draft(lease: Lease, drain: Drain, _: Step) -> Lease | None:
    free = lease.tree is drain and not lease.draft and not lease.stashed
    return lease._replace(draft=True) if free else None


def _start_program(lease: Lease, drain: Drain, _: Step) -> Lease | None:
    free = lease.tree is drain and not lease.running and not lease.stashed
    return lease._replace(running=True, draft=True) if free else None


def _stop(lease: Lease, drain: Drain, _: Step) -> Lease | None:
    stopped = put(lease.draining, drain, value=False)
    return lease._replace(draining=stopped) if of(lease.draining, drain) else None


def _die(lease: Lease, drain: Drain, _: Step) -> Lease:
    if lease.shared:
        return lease._replace(alive=(False, False), draining=(False, False), landing=None)
    dead = put(lease.alive, drain, value=False)
    landing = None if lease.landing is drain else lease.landing
    return lease._replace(
        alive=dead, draining=put(lease.draining, drain, value=False), landing=landing
    )


DrainAct = Callable[[Lease, Drain, Step], Lease | None]
BY_A_DRAIN: dict[Step, DrainAct] = {
    Step.CLEAN_ALL: _clean_all,
    Step.RESUME: _clean_all,
    Step.TAKE_OVER: _clean_all,
    Step.OPEN: _open_tree,
    Step.DRAFT: _leave_draft,
    Step.PROGRAM: _start_program,
    Step.REPORT: _report_and_release,
    Step.REPORT_OWN: _report_and_release,
    Step.CLEAN: _clean,
    Step.UPDATE: _update_in_flight,
    Step.PARK: _park_in_flight,
    Step.LAND: _land,
    Step.STOP: _stop,
    Step.DIE: _die,
}
INSIDE_LAND: dict[Step, DrainAct] = {
    Step.OPEN: _open_tree,
    Step.UPDATE: _update_in_flight,
    Step.PARK: _park_in_flight,
    Step.LAND: _land,
    Step.MERGES: _merges,
    Step.LAND_REST: _land_rest,
    Step.MERGE_KILLED: _merge_killed,
    Step.KILLED_AT_START: _killed_at_start,
    Step.LOCKED_OUT: _locked_out,
    Step.ORPHANED: _orphaned,
    Step.LAND_KILLED: _land_killed,
    Step.DIE: _die,
}
LANDING_ITSELF = frozenset(
    {
        Step.MERGES,
        Step.LAND_REST,
        Step.MERGE_KILLED,
        Step.KILLED_AT_START,
        Step.LOCKED_OUT,
        Step.LAND_KILLED,
        Step.ORPHANED,
        *ORPHAN_STEPS,
    }
)


def _by_a_drain(lease: Lease, drain: Drain, step: Step) -> Lease | None:
    acts = INSIDE_LAND if lease.landing is drain else BY_A_DRAIN
    act = acts.get(step)
    if act is None or (step in OWN and not of(lease.took, drain)) or not of(lease.alive, drain):
        return None
    return act(lease, drain, step)


DRAIN_STEPS = tuple(
    step
    for step in Step
    if step not in {Step.SEPARATE, Step.SHARED, Step.PROGRAM_ENDS, Step.DOOMED_STOPS, *ORPHAN_STEPS}
)
LEASE_INITIAL = Lease()
LeaseEdge = tuple[Lease, str, Lease]


def lease_moves(lease: Lease) -> list[tuple[str, Lease]]:
    if lease.shared is None:
        return [
            (Step.SEPARATE.value, lease._replace(shared=False)),
            (Step.SHARED.value, lease._replace(shared=True)),
        ]
    found = []
    for drain in Drain:
        for step in DRAIN_STEPS:
            after = _by_a_drain(lease, drain, step)
            if after is not None:
                found.append((f"{drain.value}: {step.value}", after))
    if lease.running:
        found.append((Step.PROGRAM_ENDS.value, lease._replace(running=False)))
    if (stopped := _doomed_stops(lease)) is not None:
        found.append((Step.DOOMED_STOPS.value, stopped))
    if lease.orphan is not None:
        for step, act in ORPHAN_STEPS.items():
            if (after := act(lease)) is not None:
                found.append((f"{lease.orphan.value}: {step.value}", after))
    return found


LEASE_STEPS = {f"{drain.value}: {step.value}": (drain, step) for drain in Drain for step in Step}


@functools.cache
def lease_graph() -> tuple[frozenset[Lease], list[LeaseEdge]]:
    seen = {LEASE_INITIAL}
    frontier = deque([LEASE_INITIAL])
    edges: list[LeaseEdge] = []
    while frontier:
        lease = frontier.popleft()
        for said, after in lease_moves(lease):
            edges.append((lease, said, after))
            if after not in seen:
                seen.add(after)
                frontier.append(after)
    return frozenset(seen), edges


def taken_from(before: Lease, said: str, after: Lease) -> Drain | None:
    drain, step = LEASE_STEPS.get(said, (None, None))
    owner = before.tree
    if step not in {Step.CLEAN_ALL, Step.RESUME} or owner is None or owner is drain:
        return None
    if after.tree is not None or not of(before.alive, owner):
        return None
    return owner if of(before.draining, owner) else None


def spares_the_live(before: Lease, said: str, after: Lease) -> bool:
    """A second drain's ``clean --all`` never removes a work tree a live first drain still uses."""
    return taken_from(before, said, after) is None


def spares_the_told(before: Lease, said: str, after: Lease) -> bool:
    """A second drain's ``clean --all`` removes a live first drain's work tree only when one process runs both and nothing runs from that tree, and then it keeps its draft as a stash."""  # noqa: E501
    if taken_from(before, said, after) is None:
        return True
    return bool(before.shared) and not before.running and (after.stashed or not before.draft)


def refused_by(before: Lease, said: str, after: Lease) -> Drain | None:
    drain, step = LEASE_STEPS.get(said, (None, None))
    if step not in {Step.CLEAN_ALL, Step.RESUME} or after.holder is drain:
        return None
    return before.holder


def stopped_blocks_not(before: Lease, said: str, after: Lease) -> bool:
    """A drain that has stopped draining never blocks the next one's ``clean --all``, unless a program runs from its tree."""  # noqa: E501
    holder = refused_by(before, said, after)
    return holder is None or of(before.draining, holder) or before.running


def gone_blocks_not(before: Lease, said: str, after: Lease) -> bool:
    """A drain whose processes are all gone never blocks the next one's ``clean --all``, unless a program runs from its tree."""  # noqa: E501
    holder = refused_by(before, said, after)
    return holder is None or of(before.alive, holder) or before.running


def report_spares(before: Lease, said: str, after: Lease) -> bool:
    """A drain's report never ends the lease of another drain that is still alive."""
    drain, step = LEASE_STEPS.get(said, (None, None))
    holder = before.holder
    if step not in REPORTS or holder in {None, drain} or after.holder is not None:
        return True
    return not of(before.alive, holder) and not before.running


def printed_once(before: Lease, said: str, after: Lease) -> bool:
    """A merge is printed by exactly one report across both drains."""
    _, step = LEASE_STEPS.get(said, (None, None))
    if before.merge is Merge.PRINTED:
        return after.merge is Merge.PRINTED
    return after.merge is not Merge.PRINTED or step in REPORTS


def spares_the_landing(before: Lease, said: str, after: Lease) -> bool:
    """No drain discards the work tree of a landing another live drain is still running, not even on ``--take-over``."""  # noqa: E501
    drain, _ = LEASE_STEPS.get(said, (None, None))
    landing = landing_alive(before)
    if landing is None or landing is drain:
        return True
    return before.tree is not landing or after.tree is not None


def keeps_the_record(before: Lease, said: str, after: Lease) -> bool:
    """The close a landing ends with never rewrites a merge another command recorded meanwhile."""
    _, step = LEASE_STEPS.get(said, (None, None))
    if step is not Step.LAND_REST or before.merge in {Merge.NONE, Merge.UNSAID}:
        return True
    return after.merge is before.merge


def note_of(lease: Lease) -> tuple[Merge | None, bool]:
    return (lease.merge if lease.merge in {Merge.SAID, Merge.PRINTED} else None), lease.held


def leaves_the_landing_alone(before: Lease, said: str, after: Lease) -> bool:
    """No command but the landing itself removes the work tree or writes the note of a ticket whose landing is alive."""  # noqa: E501
    drain, step = LEASE_STEPS.get(said, (None, None))
    landing = landing_alive(before)
    if landing is None or (drain is landing and step in LANDING_ITSELF):
        return True
    removed = before.tree is not None and after.tree is None
    return not removed and note_of(after) == note_of(before)


def hold_not_merged(before: Lease, _: str, after: Lease) -> bool:
    """A ticket whose note holds it is never merged by the drain, since no step of the lease graph is the owner's edit that frees it or the owner's own merge."""  # noqa: E501
    return not before.held or before.merge is not Merge.NONE or after.merge is Merge.NONE


def seen_while_alive(_: Lease, __: str, after: Lease) -> bool:
    """A landing is seen, so the ticket's commands refuse it and report and clean leave it alone, from its land's start until its land and every merge of it that can still land have ended."""  # noqa: E501
    return landing_alive(after) is None or landing_seen(after) is not None


def unseen_once_ended(_: Lease, __: str, after: Lease) -> bool:
    """A landing is not seen once its land and every merge of the branch have ended."""
    return landing_seen(after) is None or landing_alive(after) is not None


def closed_by_the_owner(before: Lease, said: str, after: Lease) -> bool:
    """No close is written for the ticket while a merge of it that can still land is alive, except by the landing that owns that merge."""  # noqa: E501
    if note_of(after) == note_of(before):
        return True
    drain, step = LEASE_STEPS.get(said, (None, None))
    writer = drain if step in LANDING_ITSELF and before.landing is drain else None
    return all(owner is not None and owner is writer for owner in merges_that_can_land(before))


def one_merge_can_land(_: Lease, __: str, after: Lease) -> bool:
    """At most one merge of the ticket that can still land is alive at a time, so the ticket is merged once."""  # noqa: E501
    return len(merges_that_can_land(after)) <= 1


LEASE_RULES: dict[str, Callable[[Lease, str, Lease], bool]] = {
    "9. a second drain never removes a work tree a live first drain uses": spares_the_live,
    "9a. a second drain removes it only where the docstring says the two look alike": (
        spares_the_told
    ),
    "9b. a drain that has stopped never blocks the next": stopped_blocks_not,
    "9c. a drain that is gone never blocks the next": gone_blocks_not,
    "9d. a report ends no lease but its own drain's": report_spares,
    "9e. a merge is printed by exactly one report across both drains": printed_once,
    "9f. no drain discards the tree of a landing another live drain runs": spares_the_landing,
    "9g. a landing's close keeps a merge another command recorded meanwhile": keeps_the_record,
    "9h. no command but the landing itself removes the tree or writes the note of a live landing": (
        leaves_the_landing_alone
    ),
    "9i. a ticket whose note holds it is not merged by the drain": hold_not_merged,
    "9j. a landing is seen until its land and every merge of it that can still land have ended": (
        seen_while_alive
    ),
    "9k. a landing is not seen once its land and every merge of the branch have ended": (
        unseen_once_ended
    ),
    "9l. no close is written while a merge that can still land is alive, but by its own landing": (
        closed_by_the_owner
    ),
    "9m. at most one merge of the ticket that can still land is alive at a time": (
        one_merge_can_land
    ),
}


SIBLINGS = (Name.A, Name.C)
OWNERS_LINE = 1


class Keep(Fact):
    UPDATE_EXPIRE = "update runs expire once its rebase is done"
    CARRY = "update takes the incoming edit of a unit file it deleted, then runs expire"
    ONCE = "expire carries only a line the brief does not hold yet"
    RESOLVE = "update resolves a modify/delete conflict in the files of a unit expire lists"
    JUDGE = "the judge refuses a unit the change left expired or an Answers line it dropped"
    HISTORY = "a judge-again run reads the spec from the parent of the commit that deleted it"
    NOTE = "a command on a ticket finds it by the note written when it opened"


KEEPS = frozenset(Keep)


class Files(NamedTuple):
    unit: bool = True
    answers: frozenset[int] = frozenset({0})
    brief: tuple[int, ...] = ()
    resolved: frozenset[Name] = frozenset()


class Stage(Fact):
    BUILT = "the content commit, expire not yet run"
    EXPIRED = "expire ran, the verdict record not yet written"
    JUDGED = "the verdict record last"
    STOPPED = "a rebase stopped on a conflict, left for Reconcile"


class Change(NamedTuple):
    base: Files
    stage: Stage = Stage.BUILT
    dropped: bool = False
    carried: tuple[int, ...] = ()
    history: bool = False
    rebased: bool = False
    merged: bool = False
    recorded: bool = False
    held: bool = False
    blind: bool = False


class Expiry(NamedTuple):
    tip: Files = Files()
    changes: tuple[Change | None, ...] = (None, None)
    answered: bool = False

    def change(self, name: Name) -> Change | None:
        return self.changes[SIBLINGS.index(name)]

    def with_change(self, name: Name, change: Change) -> Expiry:
        changes = list(self.changes)
        changes[SIBLINGS.index(name)] = change
        return self._replace(changes=tuple(changes))


EXPIRY_INITIAL = Expiry()


def expired(files: Files) -> bool:
    return files.unit and files.resolved >= frozenset(SIBLINGS)


def head(change: Change, name: Name) -> Files:
    base = change.base
    brief = base.brief + change.carried
    if change.dropped:
        return base._replace(unit=False, brief=brief, resolved=frozenset())
    return base._replace(brief=brief, resolved=base.resolved | {name})


def expire(change: Change, name: Name, keeps: frozenset[Keep]) -> Change:
    files = head(change, name)
    ran = change._replace(
        stage=Stage.EXPIRED, history=change.history or change.stage is Stage.BUILT
    )
    if not expired(files):
        return ran
    fresh = Keep.ONCE not in keeps
    lines = tuple(line for line in sorted(files.answers) if fresh or line not in files.brief)
    return ran._replace(dropped=True, carried=change.carried + lines)


def unit_edited(base: Files, tip: Files) -> bool:
    return (base.unit, base.answers, base.resolved) != (tip.unit, tip.answers, tip.resolved)


def refused_for_expiry(base: Files, after: Files) -> bool:
    left = expired(after) and not expired(base)
    lost = base.unit and not after.unit and not base.answers <= set(after.brief)
    return left or lost


def live(state: Expiry, name: Name) -> Change | None:
    change = state.change(name)
    return None if change is None or change.merged or change.held else change


def _sibling_build(state: Expiry, name: Name, _: frozenset[Keep]) -> Expiry | None:
    tip = state.tip
    if state.change(name) is not None or not tip.unit or name in tip.resolved:
        return None
    return state.with_change(name, Change(base=tip))


def _sibling_expire(state: Expiry, name: Name, keeps: frozenset[Keep]) -> Expiry | None:
    change = live(state, name)
    if change is None or change.stage is not Stage.BUILT:
        return None
    return state.with_change(name, expire(change, name, keeps))


def _sibling_record(state: Expiry, name: Name, _: frozenset[Keep]) -> Expiry | None:
    change = live(state, name)
    if change is None or change.stage is not Stage.EXPIRED or change.rebased:
        return None
    return state.with_change(name, change._replace(stage=Stage.JUDGED))


def _sibling_rejudge(state: Expiry, name: Name, keeps: frozenset[Keep]) -> Expiry | None:
    change = live(state, name)
    if change is None or change.stage is not Stage.EXPIRED or not change.rebased:
        return None
    found = change.history and Keep.HISTORY in keeps
    return state.with_change(name, change._replace(stage=Stage.JUDGED, blind=not found))


def _sibling_update(state: Expiry, name: Name, keeps: frozenset[Keep]) -> Expiry | None:
    change = live(state, name)
    tip = state.tip
    if change is None or change.base == tip or change.stage not in {Stage.EXPIRED, Stage.JUDGED}:
        return None
    moved = change._replace(base=tip, stage=Stage.EXPIRED, rebased=True)
    if change.dropped and unit_edited(change.base, tip):
        if Keep.RESOLVE not in keeps:
            return state.with_change(name, moved._replace(stage=Stage.STOPPED))
        if Keep.CARRY in keeps:
            return state.with_change(name, expire(moved._replace(dropped=False), name, keeps))
        return state.with_change(name, moved)
    if Keep.UPDATE_EXPIRE in keeps:
        moved = expire(moved, name, keeps)
    return state.with_change(name, moved)


def recorded_again(state: Expiry, name: Name, keeps: frozenset[Keep]) -> Expiry | None:
    change = state.change(name)
    if change is None or change.recorded:
        return None
    if Keep.NOTE not in keeps and not state.tip.unit:
        return state
    return state.with_change(name, change._replace(recorded=True))


def _sibling_land(
    state: Expiry, name: Name, keeps: frozenset[Keep], *, killed: bool = False
) -> Expiry | None:
    change = state.change(name)
    if change is not None and change.merged:
        return None if killed else recorded_again(state, name, keeps)
    if change is None or change.held or change.stage is not Stage.JUDGED:
        return None
    if change.base != state.tip:
        return None
    after = head(change, name)
    if Keep.JUDGE in keeps and refused_for_expiry(change.base, after):
        return None if killed else state.with_change(name, change._replace(held=True))
    merged = change._replace(merged=True, recorded=not killed)
    return state.with_change(name, merged)._replace(tip=after)


def _sibling_kill_land(state: Expiry, name: Name, keeps: frozenset[Keep]) -> Expiry | None:
    return _sibling_land(state, name, keeps, killed=True)


def _sibling_squash(state: Expiry, name: Name, _: frozenset[Keep]) -> Expiry | None:
    change = live(state, name)
    if change is None or change.stage is not Stage.EXPIRED or not change.history:
        return None
    return state.with_change(name, change._replace(history=False))


def _owner_answers(state: Expiry, _: frozenset[Keep]) -> Expiry | None:
    if state.answered or not state.tip.unit:
        return None
    tip = state.tip._replace(answers=state.tip.answers | {OWNERS_LINE})
    return state._replace(tip=tip, answered=True)


class Deed(Fact):
    COMMIT = Kind.COMMIT.value
    EXPIRE = "documents.py expire runs on"
    RECORD = Kind.RECORD.value
    UPDATE = Kind.UPDATE.value
    REJUDGE = "a judge-again run commits the verdict record of"
    LAND = Kind.LAND.value
    KILL_LAND = Kind.KILL_LAND_BEFORE_NOTE.value
    SQUASH = "the owner squashes by hand the branch of"
    ANSWER = "the owner lands an Answers line on a ticket of the unit"


ON_A_SIBLING: dict[Deed, Callable[[Expiry, Name, frozenset[Keep]], Expiry | None]] = {
    Deed.COMMIT: _sibling_build,
    Deed.EXPIRE: _sibling_expire,
    Deed.RECORD: _sibling_record,
    Deed.UPDATE: _sibling_update,
    Deed.REJUDGE: _sibling_rejudge,
    Deed.SQUASH: _sibling_squash,
    Deed.LAND: _sibling_land,
    Deed.KILL_LAND: _sibling_kill_land,
}


def expiry_moves(state: Expiry, keeps: frozenset[Keep]) -> list[tuple[str, Expiry]]:
    found = [
        (f"{deed.value} {name.value}", after)
        for deed, act in ON_A_SIBLING.items()
        for name in SIBLINGS
        if (after := act(state, name, keeps)) is not None
    ]
    if (answered := _owner_answers(state, keeps)) is not None:
        found.append((Deed.ANSWER.value, answered))
    return found


ExpiryEdge = tuple[Expiry, str, Expiry]


@functools.cache
def expiry_graph(keeps: frozenset[Keep] = KEEPS) -> tuple[frozenset[Expiry], list[ExpiryEdge]]:
    seen = {EXPIRY_INITIAL: EXPIRY_INITIAL}
    frontier = deque([EXPIRY_INITIAL])
    edges: list[ExpiryEdge] = []
    while frontier:
        state = frontier.popleft()
        for said, after in expiry_moves(state, keeps):
            known = seen.get(after)
            if known is None:
                seen[after] = known = after
                frontier.append(after)
            edges.append((state, said, known))
    return frozenset(seen), edges


def sibling_of(said: str) -> Name | None:
    name = ticket_of(said)
    return name if name in SIBLINGS else None


def kept_once(files: Files) -> bool:
    once = len(set(files.brief)) == len(files.brief)
    return once and (files.unit or files.answers <= set(files.brief))


def heads(state: Expiry) -> list[Files]:
    return [
        head(change, name)
        for name, change in zip(SIBLINGS, state.changes, strict=True)
        if change is not None and not change.merged and change.stage is not Stage.STOPPED
    ]


def newly(before: Expiry, after: Expiry, fact: Callable[[Change], bool]) -> bool:
    return any(
        now is not None and fact(now) and (was is None or not fact(was))
        for was, now in zip(before.changes, after.changes, strict=True)
    )


def deleted_by_the_last(_: Expiry, __: str, after: Expiry) -> bool:
    return not expired(after.tip)


def never_refused_for_expiry(before: Expiry, _: str, after: Expiry) -> bool:
    return not newly(before, after, lambda change: change.held)


def answers_carried(_: Expiry, __: str, after: Expiry) -> bool:
    return all(map(kept_once, [after.tip, *heads(after)]))


def unit_conflicts_resolved(_: Expiry, said: str, after: Expiry) -> bool:
    name = sibling_of(said)
    if name is None or not said.startswith(Deed.UPDATE.value):
        return True
    change = after.change(name)
    return change is None or change.stage is not Stage.STOPPED


def spec_found(before: Expiry, _: str, after: Expiry) -> bool:
    return not newly(before, after, lambda change: change.blind)


def land_recovers(before: Expiry, said: str, after: Expiry) -> bool:
    name = sibling_of(said)
    if name is None or said != f"{Deed.LAND.value} {name.value}":
        return True
    was, now = before.change(name), after.change(name)
    owed = was is not None and was.merged and not was.recorded
    return not owed or (now is not None and now.recorded)


EXPIRY_RULES: dict[str, Callable[[Expiry, str, Expiry], bool]] = {
    "12. no step leaves the default branch holding a unit expired at its tip": deleted_by_the_last,
    "12a. no ticket's merge is refused for a unit it left or an Answers line it dropped": (
        never_refused_for_expiry
    ),
    "12b. wherever the unit is gone, each of its Answers lines is in the brief once": (
        answers_carried
    ),
    "12c. no update stops on a conflict whose every path is in a unit expire lists": (
        unit_conflicts_resolved
    ),
    "12d. a judge-again run finds the spec": spec_found,
    "12e. a land killed after its merge records the merge when run again": land_recovers,
}


class Run(NamedTuple):
    states: tuple[object, ...]
    edges: list[tuple[object, str, object]]


def run(
    taken: Callable[[Action], bool],
    keep: Callable[[State, Action], bool] = lambda _state, _action: True,
) -> Run:
    seen = {INITIAL: INITIAL}
    frontier = deque([INITIAL])
    edges: list[tuple[object, str, object]] = []
    while frontier:
        state = frontier.popleft()
        for action, after in moves(state):
            if not taken(action) or not keep(state, action):
                continue
            known = seen.get(after)
            if known is None:
                seen[after] = known = after
                frontier.append(after)
            edges.append((state, action.name, known))
    return Run(tuple(seen), edges)


def landing_run() -> Run:
    return run(
        lambda action: action.ticket is not Name.C and action.kind not in TICKET_LIFE | UNRUN,
        guarded,
    )


TICKET_LIFE = frozenset(
    {Kind.PUBLISH, Kind.UPGRADE, Kind.LINE, Kind.CHECK_UNREADABLE, Kind.CHECK_NOT_FINAL}
)
UNRUN = frozenset(
    {
        Kind.SPLIT,
        Kind.RESOLVE,
        Kind.CHECK_HALTED,
        Kind.CHECK_HALTED_NOT_FINAL,
        Kind.UPDATE_UNREADABLE,
    }
)
UNSPLIT = (Name.A, Name.B, Name.C)


def life_run(*, gate: bool) -> Run:
    return run(
        lambda action: action.ticket is not Name.C and action.kind not in KILLS.keys() | UNRUN,
        lambda state, action: (
            (action.kind is not Kind.UPGRADE or not any(map(tip_matters, state.tickets)))
            and (not gate or guarded(state, action))
        ),
    )


def work_run() -> Run:
    return run(
        lambda action: (
            (action.ticket in {Name.C, None} and action.kind is not Kind.UPGRADE)
            or (action.ticket is Name.A and action.kind is Kind.CLOSE)
        )
    )


def plan_word(state: State, name: Name) -> str:
    status = classify(state).get(name)
    return "resolved" if status is None else status.value


def note_fact(read: Callable[[Note], object], none: object) -> Callable[[Ticket], object]:
    return lambda ticket: none if ticket.note is None else read(ticket.note)


TICKET_FACTS: dict[str, Callable[[Ticket], object]] = {
    "tree": lambda ticket: ticket.tree.name.lower(),
    "branch": lambda ticket: ticket.branch.name.lower(),
    "current": lambda ticket: ticket.current,
    "merged": lambda ticket: ticket.merged,
    "note": note_fact(lambda note: note.outcome.name.lower(), "none"),
    "hold": note_fact(lambda note: note.hold, none=False),
    "fresh": note_fact(lambda note: note.fresh, none=False),
    "attempts": note_fact(lambda note: note.attempts, 0),
    "rebasing": note_fact(lambda note: note.rebasing, none=False),
    "discarding": note_fact(lambda note: note.discarding, none=False),
    "edited": lambda ticket: ticket.text > 0,
    "stashes": lambda ticket: ticket.stashes,
    "published": lambda ticket: ticket.published,
    "since": lambda ticket: ticket.since,
    "passed": note_fact(lambda note: note.passed, none=False),
}


def state_facts(names: tuple[Name, ...]) -> dict[str, Callable[[State], object]]:
    facts: dict[str, Callable[[State], object]] = {}
    for name in names:
        letter = name.value.upper()
        facts[f"status{letter}"] = lambda state, name=name: plan_word(state, name)
        for fact, read in TICKET_FACTS.items():
            facts[f"{fact}{letter}"] = lambda state, name=name, read=read: read(state.of(name))
    return {
        **facts,
        "scratch": lambda state: state.scratch,
        "half": lambda state: "none" if state.half is None else state.half.value,
        "flag": lambda state: state.flag.name.lower(),
    }


def rule_name(name: str) -> str:
    return model_tla.identifier(f"Rule {name.partition('.')[0]}")


def step_rules(rules: Mapping[str, Callable[[Any, str, Any], bool]]) -> dict[str, model_tla.Rule]:
    return {
        rule_name(name): model_tla.Step((holds.__doc__ or "").strip(), holds)
        for name, holds in rules.items()
    }


def drain_id(drain: Drain | None) -> str:
    return "none" if drain is None else drain.value.rsplit(" ", 1)[-1]


LEASE_FACTS: dict[str, Callable[[Lease], object]] = {
    "processes": lambda lease: (
        "undecided" if lease.shared is None else "one" if lease.shared else "separate"
    ),
    "holder": lambda lease: drain_id(lease.holder),
    **{
        f"{fact}{drain_id(drain)}": lambda lease, fact=fact, drain=drain: of(
            getattr(lease, fact), drain
        )
        for fact in ("alive", "draining", "took")
        for drain in Drain
    },
    "tree": lambda lease: drain_id(lease.tree),
    "draft": lambda lease: lease.draft,
    "running": lambda lease: lease.running,
    "stashed": lambda lease: lease.stashed,
    "landing": lambda lease: drain_id(lease.landing),
    "merge": lambda lease: lease.merge.name.lower(),
    "built": lambda lease: lease.built,
    "orphan": lambda lease: drain_id(lease.orphan),
    "doomed": lambda lease: lease.doomed,
    "held": lambda lease: lease.held,
}


def lease_run() -> Run:
    _, edges = lease_graph()
    return Run(tuple(dict.fromkeys([LEASE_INITIAL, *(after for _, _, after in edges)])), edges)


SHOWN_LANDING = ("statusA", *(f"{fact}A" for fact in TICKET_FACTS))


def holder_is(lease: Lease) -> str:
    if lease.holder is None:
        return "none"
    if of(lease.draining, lease.holder):
        return "draining"
    return "stopped" if of(lease.alive, lease.holder) else "gone"


SHOWN_LEASE: dict[str, Callable[[Lease], object]] = {
    "processes": LEASE_FACTS["processes"],
    "holder": LEASE_FACTS["holder"],
    "holderIs": holder_is,
    **{fact: LEASE_FACTS[fact] for fact in ("landing", "merge", "orphan", "doomed")},
}


def expiry_run() -> Run:
    _, edges = expiry_graph()
    return Run(tuple(dict.fromkeys([EXPIRY_INITIAL, *(after for _, _, after in edges)])), edges)


CHANGE_FACTS: dict[str, tuple[Callable[[Change, Files], object], object]] = {
    "stage": (lambda change, _: change.stage.name.lower(), "none"),
    "behind": (lambda change, tip: change.base != tip, False),
    "dropped": (lambda change, _: change.dropped, False),
    "carried": (lambda change, _: change.carried, ()),
    "history": (lambda change, _: change.history, False),
    "rebased": (lambda change, _: change.rebased, False),
    "merged": (lambda change, _: change.merged, False),
    "recorded": (lambda change, _: change.recorded, False),
    "held": (lambda change, _: change.held, False),
    "blind": (lambda change, _: change.blind, False),
}


def expiry_facts() -> dict[str, Callable[[Expiry], object]]:
    facts: dict[str, Callable[[Expiry], object]] = {
        "unit": lambda state: state.tip.unit,
        "answers": lambda state: state.tip.answers,
        "brief": lambda state: state.tip.brief,
        "resolved": lambda state: frozenset(name.value for name in state.tip.resolved),
        "answered": lambda state: state.answered,
    }
    for name in SIBLINGS:
        for fact, (read, none) in CHANGE_FACTS.items():
            facts[f"{fact}{name.value.upper()}"] = lambda state, name=name, read=read, none=none: (
                none if (change := state.change(name)) is None else read(change, state.tip)
            )
    return facts


def short_of_a_line(change: Change, tip: Files) -> bool:
    return change.dropped and not tip.answers <= change.base.answers


SHOWN_EXPIRY: dict[str, Callable[[Expiry], object]] = {
    "unit": lambda state: state.tip.unit,
    "landed": lambda state: sum(change is not None and change.merged for change in state.changes),
    "deletes": lambda state: any(
        change is not None and not change.merged and change.dropped for change in state.changes
    ),
    "short": lambda state: any(
        change is not None and not change.merged and short_of_a_line(change, state.tip)
        for change in state.changes
    ),
}


def spoken(rules: Mapping[str, Callable[[Any, str, Any], bool]]) -> dict[str, model_tla.Rule]:
    sentences = {name: name.partition(" ")[2] for name in rules}
    return {
        rule_name(name): model_tla.Step(said[:1].upper() + said[1:] + ".", rules[name])
        for name, said in sentences.items()
    }


def seen_through(
    name: str, graph: Run, shown: Mapping[str, Callable[[Any], object]], *, deadlock: bool = True
) -> model_tla.Export:
    summary = model_tla.summary(
        graph, lambda state: tuple(fact(state) for fact in shown.values()), model_tla.identifier
    )
    return model_tla.export(
        name,
        summary,
        {},
        fields=(),
        derived={fact: (lambda values, at=at: values[at]) for at, fact in enumerate(shown)},
        deadlock=deadlock,
    )


def exports() -> tuple[model_tla.Export, ...]:
    landing, life, lease = landing_run(), life_run(gate=False), lease_run()
    gated, expiry = life_run(gate=True), expiry_run()
    facts = state_facts(UNSPLIT)
    main = step_rules(RULES)
    return (
        model_tla.export(
            "DrainLanding", landing, main, fields=(), derived=facts, action=model_tla.identifier
        ),
        model_tla.export(
            "DrainWork", work_run(), main, fields=(), derived=facts, action=model_tla.identifier
        ),
        model_tla.export(
            "DrainTicketLife", life, main, fields=(), derived=facts, action=model_tla.identifier
        ),
        model_tla.export(
            "DrainTicketLifeGated",
            gated,
            main,
            fields=(),
            derived=facts,
            action=model_tla.identifier,
        ),
        model_tla.export(
            "DrainLease",
            lease,
            step_rules(LEASE_RULES),
            fields=(),
            derived=LEASE_FACTS,
            action=model_tla.identifier,
            deadlock=False,
        ),
        seen_through("DrainLife", gated, {fact: facts[fact] for fact in SHOWN_LANDING}),
        seen_through("DrainLeaseShown", lease, SHOWN_LEASE, deadlock=False),
        model_tla.export(
            "DrainExpiry",
            expiry,
            spoken(EXPIRY_RULES),
            fields=(),
            derived=expiry_facts(),
            action=model_tla.identifier,
            deadlock=False,
        ),
        seen_through("DrainExpiryShown", expiry, SHOWN_EXPIRY, deadlock=False),
    )
