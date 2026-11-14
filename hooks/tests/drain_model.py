"""A pure model of the queue drain: how tickets go from agreed to merged, and the default branch.

Three tickets, ``a``, ``b`` and ``c``, where ``b`` is blocked by ``a``, under a cap of one. Each
ticket is what ``drain.classify`` reads: its queue work tree, its branch, whether that branch holds
the tip of the default branch, whether it is merged, the drain's note on it, its text and the
stashes that hold its uncommitted work, and what a killed ``merge.py`` left. The actions are the
drain's commands in any order, what the agent does in a work tree, what the owner does, a stale
index lock that git leaves, and a kill of ``update``, ``close``, ``land`` or the ``merge.py`` it
runs between two of their effects. ``graph()`` is every reachable state and step, ``RULES`` what
a reader of ADR 0023 and the feature skill expects to stay true over any history. Every rule is a
predicate over a step: ``(before, action name, after)``.

The transitions are the code's: a command does here what ``drain.py`` and ``merge.py`` do on a
repository, refusals included, so a rule the code does not keep breaks here too. The merge judge
passes a branch only when its verdict record is its last commit, certifies the tree the branch
has, and the branch holds the default tip; the binding's ``check`` is always green; no two tickets
touch one file, so nothing conflicts.

Bounds keep the graph small: the owner edits a ticket at most once, the agent leaves work
uncommitted once, and the retry count of a ticket left waiting stops at ``RETRIES``, past which
nothing reads it.
"""

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
    """An enum whose members hash as they compare, by identity.

    Why: the search hashes a few million of them, and ``Enum`` hashes each by its name in Python.
    """

    __hash__ = object.__hash__


class Name(Fact):
    A = "a"
    B = "b"
    C = "c"


NAMES = tuple(Name)
INDEX = {name: index for index, name in enumerate(NAMES)}
BY_VALUE = {name.value: name for name in NAMES}
BLOCKER: dict[Name, Name] = {Name.B: Name.A}


class Tree(Fact):
    NONE = "no work tree"
    CLEAN = "a clean work tree"
    DIRTY = "uncommitted work in its tree"
    LOCKED = "uncommitted work behind a stale index lock"
    REBASING = "a rebase under way, the tree detached at the default tip"


HOLDS_WORK = frozenset({Tree.DIRTY, Tree.LOCKED})


class Branch(Fact):
    NONE = "no branch"
    BARE = "a branch with no commit of its own"
    DRAFT = "commits, the verdict record not last"
    JUDGED = "commits, the verdict record last"
    STALE = "commits, the verdict record last and certifying a tree the branch no longer has"


RECORDED = frozenset({Branch.JUDGED, Branch.STALE})
AHEAD = frozenset({Branch.DRAFT, *RECORDED})


class Outcome(Fact):
    """What a note says. ``waits`` and ``parked`` differ only in the report's heading."""

    OPENED = "opened"
    MERGED = "merged"
    CLOSED = "waits or parked"


class Note(NamedTuple):
    """``.ratchet/queue/tickets/<branch>.json``: what the drain keeps on a ticket.

    ``open`` writes one with no hold, digest or report flag, unless it keeps a close no report
    has printed yet; a close writes all of them.
    """

    outcome: Outcome
    hold: bool = False
    text: int | None = None
    """The ticket's text the note was written on (its digest); ``None`` names none."""
    fresh: bool = False
    """A close no report has printed yet."""
    attempts: int = 0
    rebasing: bool = False
    """``update``'s mark that it began a rebase.

    ``update`` clears it when it finishes, and a close or an ``open`` drops it. ``clean --all``
    keeps it only on a branch whose rebase finished and that has a commit of its own.
    """
    discarding: bool = False
    """``settle``'s mark that it wrote the note and has not yet removed the tree.

    ``settle`` drops it once the tree is gone, and ``finish_close`` drops it, first removing a tree
    that stands; ``open``, ``update`` and ``clean --all`` drop it too. While it is set, ``close``
    does not count its refusal again.
    """


class Ticket(NamedTuple):
    tree: Tree = Tree.NONE
    branch: Branch = Branch.NONE
    current: bool = True
    """Whether the branch holds the tip of the default branch."""
    reviewed: bool = False
    """Whether the verdict record was written while the branch held the tip it holds now.

    No file holds it: it is the history a rule reads, not a fact the code reads.
    """
    merged: bool = False
    note: Note | None = None
    text: int = 0
    stashes: int = 0

    @property
    def ahead(self) -> bool:
        """Whether the branch holds a commit the default branch does not."""
        return self.branch in AHEAD and not self.merged


def tip_matters(ticket: Ticket) -> bool:
    """Whether anything the code does reads if the branch holds the default tip.

    A branch with no commit of its own and no work tree is judged and refused the same either way,
    and ``open`` starts it at the tip again. Nothing reads the tip of a merged one.
    """
    if ticket.branch is Branch.NONE or ticket.merged:
        return False
    return ticket.branch is not Branch.BARE or ticket.tree is not Tree.NONE


def normal(ticket: Ticket) -> Ticket:
    """``ticket`` with what nothing reads set one way, so two states that differ only there are one.

    ``settle``'s mark on a ticket with no tree is read only by the count of a retry. Only a branch
    whose verdict record is last is retried, and any other gets one only in a tree, which ``open``
    and ``update`` make and which drops the mark.
    """
    current = ticket.current or not tip_matters(ticket)
    reviewed = ticket.reviewed and ticket.branch is Branch.JUDGED and not ticket.merged
    note = ticket.note
    retried = ticket.branch is Branch.JUDGED and not ticket.merged
    if note is not None and note.discarding and ticket.tree is Tree.NONE and not retried:
        ticket = ticket._replace(note=note._replace(discarding=False))
    if (current, reviewed) == (ticket.current, ticket.reviewed):
        return ticket
    return ticket._replace(current=current, reviewed=reviewed)


@dataclass(frozen=True)
class State:
    tickets: tuple[Ticket, Ticket, Ticket]
    worked: bool = False
    """Whether the agent has left work uncommitted yet: it does so once in a history."""
    scratch: bool = False
    """Whether a ``merge.py`` that was killed left its scratch work tree."""
    half: Name | None = None
    """The ticket whose fast-forward of the default branch was cut off half-way.

    Files of its merge are in the default branch's work tree, one part-written, its index is
    locked and the landing intent is kept.
    """

    @property
    def edited(self) -> bool:
        """Whether the owner has edited a ticket yet: they do so once in a history."""
        return any(ticket.text for ticket in self.tickets)

    def of(self, name: Name) -> Ticket:
        return self.tickets[INDEX[name]]

    def with_ticket(self, name: Name, ticket: Ticket) -> State:
        tickets = list(self.tickets)
        tickets[INDEX[name]] = normal(ticket)
        return State((tickets[0], tickets[1], tickets[2]), self.worked, self.scratch, self.half)

    def __hash__(self) -> int:
        # Why: the search hashes each state it reaches many times, and a state never changes.
        cached = self.__dict__.get("hashed")
        if cached is None:
            cached = hash((self.tickets, self.worked, self.scratch, self.half))
            object.__setattr__(self, "hashed", cached)
        return cached

    def each(self) -> Iterator[tuple[Name, Ticket]]:
        return zip(NAMES, self.tickets, strict=True)


INITIAL = State(tickets=(Ticket(), Ticket(), Ticket()))


class Status(Fact):
    """What ``drain.plan`` calls a ticket; a merged one is resolved and not in the plan."""

    BUILDABLE = "buildable"
    BUILDING = "building"
    BUILT = "built"
    WAITING = "waiting"
    HELD = "held"
    REFUSED = "refused"


LIVE = frozenset({Status.BUILDABLE, Status.BUILDING, Status.BUILT})


def held_by_note(ticket: Ticket) -> bool:
    """Whether the note holds the ticket: a hold written on the text the ticket has now."""
    note = ticket.note
    return note is not None and note.hold and note.text == ticket.text


@functools.cache
def classify(state: State) -> dict[Name, Status]:
    """Each ticket in the plan, as ``drain.classify`` and then ``drain.settled`` call it.

    The plan reads the tickets from the default branch's work tree. While a fast-forward is cut
    off half-way that holds the merge's ticket file, so the ticket being landed reads as resolved.
    """
    found: dict[Name, Status] = {}
    resolved = {name for name, ticket in state.each() if ticket.merged or state.half is name}
    for name, ticket in state.each():
        if name in resolved:
            continue
        if held_by_note(ticket):
            found[name] = Status.HELD
        elif ticket.ahead:
            found[name] = Status.BUILT
        elif ticket.tree is not Tree.NONE:
            found[name] = Status.BUILDING
        elif name in BLOCKER and BLOCKER[name] not in resolved:
            found[name] = Status.WAITING
        else:
            found[name] = Status.BUILDABLE
    for name, status in list(found.items()):
        if status is Status.WAITING and found.get(BLOCKER[name]) not in LIVE:
            found[name] = Status.REFUSED
    return found


def building(state: State) -> int:
    """The tickets in the plan that hold a queue work tree: what the cap counts."""
    return sum(ticket.tree is not Tree.NONE for ticket in state.tickets if not ticket.merged)


def start(state: State) -> tuple[Name, ...]:
    """The tickets ``plan`` starts: the buildable, in order, up to the cap less those in a tree.

    None while a fast-forward cut off half-way leaves the checkout unfit to build on.
    """
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
    UPDATE = "drain.py update"
    LAND = "drain.py land"
    CLOSE = "drain.py close --outcome parked"
    CLEAN = "drain.py clean"
    CLEAN_ALL = "drain.py clean --all"
    REPORT = "drain.py report"
    EDIT = "the owner edits ticket"
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
    """``state`` after the default branch moved: no branch holds its tip any longer."""
    for name, ticket in state.each():
        state = state.with_ticket(name, ticket._replace(current=False, reviewed=False))
    return state


def discard(ticket: Ticket) -> Ticket:
    """The ticket after ``drain.discard``: its tree gone, its uncommitted work a stash first.

    A tree whose work git cannot stash stays, work and all.
    """
    if ticket.tree is Tree.LOCKED:
        return ticket
    stashes = ticket.stashes + (ticket.tree is Tree.DIRTY)
    return ticket._replace(tree=Tree.NONE, stashes=stashes)


def noted(ticket: Ticket, outcome: Outcome, *, retry: bool = False) -> Ticket:
    """The ticket once ``drain.settle`` has written its note marked as being discarded.

    ``retry`` says every reason is one the drain resolves itself: the ticket is retried, not
    held, until it has waited ``RETRIES`` times in a row on one text. A refusal is not counted
    again while the note it follows is still marked: a run of that close was cut off.
    """
    prior = ticket.note
    same = prior is not None and prior.text == ticket.text and prior.outcome is not Outcome.OPENED
    counted = 0 if prior is not None and prior.discarding else 1
    attempts = min(RETRIES, (prior.attempts if same and prior else 0) + counted) if retry else 0
    merged = outcome is Outcome.MERGED
    hold = not merged and (attempts >= RETRIES or not retry)
    note = Note(outcome, hold, ticket.text, fresh=True, attempts=attempts, discarding=True)
    return ticket._replace(note=note)


def closed(ticket: Ticket, outcome: Outcome, *, retry: bool = False) -> Ticket:
    """The ticket after ``drain.close``: its note written, its tree discarded, the mark dropped."""
    settled = discard(noted(ticket, outcome, retry=retry))
    note = settled.note
    return settled._replace(note=note._replace(discarding=False)) if note else settled


def says_merged(ticket: Ticket) -> bool:
    return ticket.note is not None and ticket.note.outcome is Outcome.MERGED


def recorded(ticket: Ticket) -> Ticket:
    """A merge no note says, recorded: ``drain.record_merge``."""
    return closed(ticket, Outcome.MERGED)


def unrecorded(ticket: Ticket) -> bool:
    """``drain.recorded_merge``: merged, and no note (one the drain wrote) says so."""
    note = ticket.note
    return ticket.merged and note is not None and note.outcome is not Outcome.MERGED


def finished(ticket: Ticket) -> Ticket:
    """``drain.finish_close``: the discard a close was cut off before, its note kept as written.

    The mark is dropped whether the tree stands or not: a close cut off after its discard is owed
    no rerun once something else runs on the ticket.
    """
    note = ticket.note
    if note is None or not note.discarding:
        return ticket
    return discard(ticket)._replace(note=note._replace(discarding=False))


def _open(state: State, name: Name) -> State:
    if name not in start(state):
        return state
    ticket = state.of(name)
    prior = ticket.note
    # Why: a close no report has printed is kept for the report, its hold and retries dropped.
    kept = prior is not None and prior.fresh and prior.outcome is Outcome.CLOSED
    opened = Ticket(
        tree=Tree.CLEAN,
        branch=Branch.BARE,
        note=(
            prior._replace(hold=False, attempts=0, rebasing=False, discarding=False)
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
    committed = ticket._replace(tree=Tree.CLEAN, branch=Branch.DRAFT, reviewed=False)
    return state.with_ticket(name, committed)


def _record(state: State, name: Name) -> State | None:
    ticket = state.of(name)
    if ticket.merged or ticket.tree is not Tree.CLEAN or ticket.branch is not Branch.DRAFT:
        return None
    judged = ticket._replace(branch=Branch.JUDGED, reviewed=ticket.current)
    return state.with_ticket(name, judged)


def rebased(ticket: Ticket) -> Ticket:
    """The branch rebased onto the default tip, a verdict record left last dropped."""
    branch = Branch.DRAFT if ticket.branch in RECORDED else ticket.branch
    return ticket._replace(current=True, branch=branch, reviewed=False)


def marked(ticket: Ticket, *, rebasing: bool) -> Ticket:
    """The note as ``update`` writes it, which drops ``settle``'s mark too."""
    note = ticket.note or Note(Outcome.OPENED)
    return ticket._replace(note=note._replace(rebasing=rebasing, discarding=False))


def trees(state: State) -> int:
    """Every queue work tree, a merged ticket's too: what ``update`` counts before it makes one."""
    return sum(ticket.tree is not Tree.NONE for ticket in state.tickets)


def revived(state: State, name: Name) -> Ticket | None:
    """The ticket as ``update`` has it once it has a clean tree to work in; ``None`` when not.

    A built branch with no work tree gets one, under the cap, when it is behind or its note
    says a rebase was under way; a rebase a killed run left is abandoned.
    """
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
    """``state`` as ``update`` has it when it turns to the rebase: ``settle``'s mark dropped.

    ``None`` on a branch whose merge landed: ``update`` records that merge and does not rebase it.
    """
    ticket = state.of(name)
    if unrecorded(ticket) or says_merged(ticket):
        return None
    note = ticket.note
    if note is None or not note.discarding:
        return state
    return state.with_ticket(name, ticket._replace(note=note._replace(discarding=False)))


def to_rebase(state: State, name: Name) -> Ticket | None:
    """The ticket as ``update`` has it when its rebase starts; ``None`` when it starts none."""
    ticket = revived(state, name)
    if ticket is None or ticket.current or ticket.branch is Branch.NONE:
        return None
    return marked(ticket, rebasing=True)


def finishing(ticket: Ticket) -> bool:
    """Whether ``update`` finds a rebase a killed run finished: the tip is current and marked."""
    return ticket.current and ticket.note is not None and ticket.note.rebasing


def _update(state: State, name: Name) -> State:
    ticket = state.of(name)
    ready = unmarked(state, name)
    if ready is None:
        # Why: a rebase would move the branch off its merge, and the merge would no longer be
        # seen as landed. The merge is recorded instead, or a discard its close was cut off
        # before is finished.
        return state.with_ticket(name, recorded(ticket) if unrecorded(ticket) else finished(ticket))
    working = revived(ready, name)
    if working is None:
        return ready
    if finishing(working):
        return ready.with_ticket(name, rebased(marked(working, rebasing=False)))
    began = to_rebase(ready, name)
    if began is None:
        return ready
    return ready.with_ticket(name, marked(rebased(began), rebasing=False))


def _kill_mid_rebase(state: State, name: Name) -> State | None:
    ready = unmarked(state, name)
    if ready is None or (began := to_rebase(ready, name)) is None:
        return None
    return ready.with_ticket(name, began._replace(tree=Tree.REBASING))


def _kill_after_rebase(state: State, name: Name) -> State | None:
    """Where ``update`` is about to drop the record: after its rebase, or finishing one.

    A run that finishes what a killed run left drops the record before it clears the mark, so
    a kill there leaves the mark.
    """
    ready = unmarked(state, name)
    if ready is None:
        return None
    began = to_rebase(ready, name)
    if began is not None:
        branch = Branch.STALE if began.branch in RECORDED else began.branch
        return ready.with_ticket(name, began._replace(current=True, branch=branch, reviewed=False))
    ticket = revived(ready, name)
    if ticket is None or not finishing(ticket) or ticket.branch not in RECORDED:
        return None
    return ready.with_ticket(name, ticket)


def parkable(ticket: Ticket) -> bool:
    """Whether ``close --outcome parked`` gets as far as its own note.

    A merge is refused, and one no note says yet is recorded first: the kills of ``close`` are
    taken on a ticket that never merges, and do not follow that record or ``finish_close``.
    """
    return not says_merged(ticket) and not unrecorded(ticket)


def _kill_after_stash(state: State, name: Name) -> State | None:
    ticket = state.of(name)
    if ticket.tree is not Tree.DIRTY or not parkable(ticket):
        return None
    stashed = noted(ticket, Outcome.CLOSED)._replace(tree=Tree.CLEAN, stashes=ticket.stashes + 1)
    return state.with_ticket(name, stashed)


def _kill_before_discard(state: State, name: Name) -> State | None:
    """``close`` with its note written and its tree not yet discarded; none where that is all of it.

    A kill after the discard leaves the whole close's note still marked, and only the count of a
    retry reads the mark, which a parked close never is; so ``close`` is killed only here.
    """
    ticket = state.of(name)
    if not parkable(ticket):
        return None
    killed = state.with_ticket(name, noted(ticket, Outcome.CLOSED))
    return None if killed == _close(state, name) else killed


def judge(ticket: Ticket) -> list[str]:
    """The reason codes ``merge.py`` prints for the branch; none when it lands."""
    if ticket.merged:
        return ["behind", "record"]
    reasons = [] if ticket.current else ["behind"]
    return reasons if ticket.branch is Branch.JUDGED else [*reasons, "record"]


class Landing(NamedTuple):
    """What ``land`` does before it settles the ticket, and how it settles it."""

    judged: State
    """The repository once ``merge.py`` is done: the default branch moved when it merged."""
    outcome: Outcome
    retry: bool


def recovered(state: State) -> State:
    """``merge.recover``: the scratch work tree swept, a fast-forward cut off half-way put back."""
    if not state.scratch and state.half is None:
        return state
    return dataclasses.replace(state, scratch=False, half=None)


@functools.cache
def landing(state: State, name: Name) -> Landing | None:
    """``land`` up to its close, from what ``merge.recover`` puts back; ``None`` when it closes nothing."""  # noqa: E501
    state = recovered(state)
    ticket = state.of(name)
    if unrecorded(ticket):
        return Landing(state, Outcome.MERGED, retry=False)
    # Why: a held ticket is refused before merge.py runs, and a merged one by the close after it.
    if ticket.branch is Branch.NONE or held_by_note(ticket) or says_merged(ticket):
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
        # Why: a merge recorded already is closed again, which finishes a cut-off discard, and a
        # held ticket is refused once that discard is finished.
        return state.with_ticket(name, finished(ticket))
    run = landing(state, name)
    if run is None:
        return state
    ticket = closed(run.judged.of(name), run.outcome, retry=run.retry)
    return run.judged.with_ticket(name, ticket)


def _kill_land_before_note(state: State, name: Name) -> State | None:
    """The merge landed and nothing after it ran: no note says so and the tree is there."""
    run = landing(state, name)
    if run is None or run.judged == recovered(state):
        return None
    return run.judged


def _kill_land_before_discard(state: State, name: Name) -> State | None:
    """``land`` with its note written and its tree not yet discarded."""
    run = landing(state, name)
    if run is None:
        return None
    ticket = noted(run.judged.of(name), run.outcome, retry=run.retry)
    return run.judged.with_ticket(name, ticket)


def _kill_land_after_discard(state: State, name: Name) -> State | None:
    """``land`` with its tree discarded and its note still marked; none where discard does nothing."""  # noqa: E501
    run = landing(state, name)
    if run is None or discard(run.judged.of(name)) == run.judged.of(name):
        return None
    ticket = discard(noted(run.judged.of(name), run.outcome, retry=run.retry))
    return run.judged.with_ticket(name, ticket)


def _kill_before_fast_forward(state: State, name: Name) -> State | None:
    """``merge.py`` killed in ``check`` on the merge result: only a passed branch gets that far.

    A kill earlier in ``merge.py`` leaves the same: its scratch work tree and nothing else.
    """
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
        # Why: ``close`` records a merge no note says before it reads the note, and then refuses.
        return state.with_ticket(name, recorded(ticket))
    if says_merged(ticket):
        return state.with_ticket(name, finished(ticket))
    return state.with_ticket(name, closed(ticket, Outcome.CLOSED))


def cleaned(state: State, *, everything: bool) -> State:
    """``drain.clean``: every tree of a merged branch removed, with ``everything`` every tree.

    A merge no note says is recorded first, a tree or not, and a close cut off before its discard
    is finished before its tree goes, once ``merge.recover`` has put back what a killed
    ``merge.py`` left. ``everything`` then clears the mark of a rebase that did not finish (it was
    under way in the tree) or of a branch with no commit of its own, ``drain.unmark``, and the mark
    of a close whose tree is gone, ``drain.finish_close``. One drain runs here, so ``clean`` finds
    no other drain alive and records each merge.
    """
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
    """``drain.report``: each merge no note says recorded, then each close not yet printed, printed.

    One drain runs here, so the report finds no other drain alive and records each merge.
    """
    for name, ticket in state.each():
        settled = recorded(ticket) if unrecorded(ticket) else ticket
        if settled.note is not None and settled.note.fresh:
            settled = settled._replace(note=settled.note._replace(fresh=False))
        state = state.with_ticket(name, settled)
    return state


def _edit(state: State, name: Name) -> State | None:
    ticket = state.of(name)
    # Why: git refuses the owner's commit while the cut-off fast-forward holds the index lock.
    if state.edited or ticket.merged or state.half is not None:
        return None
    shifted = moved(state)
    return shifted.with_ticket(name, shifted.of(name)._replace(text=1))


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
    """``act`` once ``merge.recover`` has run: ``drain.main`` runs it before every command.

    So no command reads a ticket's text while a fast-forward cut off half-way holds the merge's.
    """

    def run(state: State, name: Name) -> State | None:
        return act(recovered(state), name)

    return run


# Why: where two paths to a state are as short, the search keeps the one whose actions come
# first here; a landing runs merge.py, the dearest step to replay, so it comes last.
ON_TICKET: dict[Kind, Callable[[State, Name], State | None]] = {
    Kind.OPEN: recovering(_open),
    Kind.WORK: _work,
    Kind.COMMIT: _commit,
    Kind.RECORD: _record,
    Kind.CLOSE: recovering(_close),
    Kind.UPDATE: recovering(_update),
    Kind.EDIT: _edit,
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
WHOLE: dict[Kind, Callable[[State], State]] = {
    Kind.CLEAN: lambda state: cleaned(state, everything=False),
    Kind.CLEAN_ALL: lambda state: cleaned(state, everything=True),
    Kind.REPORT: lambda state: _report(recovered(state)),
}


# Why: a bound. Each ticket plays one part so the graph stays small: the agent commits only in
# a, whose branch goes the whole way to merged, and leaves work uncommitted only in c, the ticket
# the cap holds back; b, which waits on a, is only opened. A kill stops ``update`` and ``land``
# only in a, whose branch carries a verdict record, and ``close`` only in c, whose tree holds
# work. The drain's other commands and the owner reach a and c, and the commands of the whole
# queue reach all three.
A_AND_C = (Name.A, Name.C)
PLAYS: dict[Kind, tuple[Name, ...]] = {
    Kind.COMMIT: (Name.A,),
    Kind.RECORD: (Name.A,),
    Kind.WORK: (Name.C,),
    Kind.LOCK: (Name.C,),
    Kind.UNLOCK: (Name.C,),
    Kind.UPDATE: A_AND_C,
    Kind.LAND: A_AND_C,
    Kind.CLOSE: A_AND_C,
    Kind.EDIT: A_AND_C,
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


def moves(state: State) -> list[Move]:
    """Every action open to the drain, the agent, the owner and git in ``state``."""
    found: list[Move] = []
    for action, act, name in ON_A_TICKET:
        after = act(state, name)
        if after is not None:
            found.append((action, after))
    found += [(action, act(state)) for action, act in ON_THE_QUEUE]
    return found


Edge = tuple[State, str, State]


def graph() -> tuple[frozenset[State], list[Edge]]:
    """Every state reachable from ``INITIAL`` and every step between them, breadth first.

    The first step into each state ends a shortest path to it. Each step leads to the one copy of
    its state the search keeps, so a search over the steps finds each state by identity.
    """
    seen = {INITIAL: INITIAL}
    frontier = deque([INITIAL])
    edges: list[Edge] = []
    while frontier:
        state = frontier.popleft()
        for action, after in moves(state):
            known = seen.get(after)
            if known is None:
                seen[after] = known = after
                frontier.append(after)
            edges.append((state, action.name, known))
    return frozenset(seen), edges


def ticket_of(action: str) -> Name | None:
    """The ticket an action name ends in, if it names one."""
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
    """Whether the note says the ticket merged, and whether no report has printed that yet."""
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
    """Whether no note holds the ticket, or the owner has changed the text it was held on."""
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


def refused(_: State, __: str, after: State) -> bool:
    """A ticket whose blocker waits on the owner is refused, never left waiting for it."""
    statuses = classify(after)
    return all(
        statuses.get(name) is not Status.WAITING or not held_by_note(after.of(blocker))
        for name, blocker in BLOCKER.items()
    )


def behind(ticket: Ticket) -> bool:
    """Whether ``drain.plan`` calls the ticket behind, now or once its hold ends: ``update`` it."""
    marked = ticket.note is not None and ticket.note.rebasing
    return ticket.ahead and (not ticket.current or marked)


def next_drain(state: State, name: Name) -> State:
    """``clean --all``, then the ``update`` the plan asks for when it calls the ticket behind."""
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


def no_tree_left(_: State, action: str, after: State) -> bool:
    """After ``clean --all`` no queue work tree is left."""
    return action != Kind.CLEAN_ALL.value or all(t.tree is Tree.NONE for t in after.tickets)


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
    "8. a killed command recovers by running again or by clean --all": recovers,
    "10. clean --all leaves no queue work tree": no_tree_left,
}


# --- the lease: one drain at a time -----------------------------------------------------------


class Drain(Fact):
    ONE = "drain 1"
    TWO = "drain 2"


class Merge(Fact):
    """Where the ticket's merge is, as ``drain.recorded_merge`` and ``drain.report`` read it."""

    NONE = "not merged"
    UNSAID = "merged, and no note says so"
    SAID = "merged, and a note no report has printed says so"
    PRINTED = "merged, and a report has printed it"


class Lease(NamedTuple):
    """Two drains on one repository as ``drain.lease`` reads them, and one ticket's work tree.

    ``shared`` is ``None`` until the history says how the drains run: each in a session of its
    own, or both under one long-lived process. The pairs hold drain 1's fact, then drain 2's.
    """

    shared: bool | None = None
    holder: Drain | None = None
    """Whose chain and token the lease file holds; ``None`` when there is no lease."""
    alive: tuple[bool, bool] = (True, True)
    """Whether the drain's session process is alive."""
    draining: tuple[bool, bool] = (False, False)
    """Whether the drain took the lease and has neither reported nor stopped. No file holds it."""
    took: tuple[bool, bool] = (False, False)
    """Whether the drain holds a token: its ``clean --all`` took the lease once."""
    tree: Drain | None = None
    """The drain that opened the ticket's work tree, while it is there."""
    draft: bool = False
    running: bool = False
    """Whether a program the drain built runs from the tree. Its file is uncommitted work too."""
    stashed: bool = False
    """Whether a draft was kept as a stash. A history leaves one draft, so the graph ends."""
    landing: Drain | None = None
    """The drain whose ``land a`` is past its checks and has not yet closed: its landing is alive.

    ``land`` holds the drain lock for its checks and again for its close, and not while
    ``merge.py`` runs: every other command runs there. While ``merge`` is ``NONE``, ``merge.py``
    judges and checks; after it, the merge has landed. ``land`` holds the ticket's landing lock
    the whole time, which ends with its process. ``report``, ``clean`` and ``clean --all`` read it
    and leave the ticket alone; a second ``land``, ``update`` and ``close`` read it and refuse,
    changing nothing. ``open`` does not read it, and the plan does not start the ticket.
    ``settle`` runs whole under the drain lock, so between its note and its discard no other
    command runs, and that cut is not taken.
    """
    merge: Merge = Merge.NONE
    held: bool = False
    """Whether the ticket's note holds it.

    No step writes a hold: the graph has ``close --outcome parked`` only while a landing is alive,
    which refuses it. The world reads the note's hold back after every step, so a hold written
    under a landing shows there.
    """


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
    LAND_KILLED = "a kill stops its land after the merge, before the close"
    STOP = "stops, its session still open"
    DIE = "its session ends"


CLEANS = frozenset({Step.CLEAN_ALL, Step.RESUME, Step.TAKE_OVER})
REPORTS = frozenset({Step.REPORT, Step.REPORT_OWN})
OWN = frozenset({Step.RESUME, Step.REPORT_OWN})
"""The steps that pass the drain's token: a drain that never took the lease has none."""


def rival(lease: Lease, drain: Drain) -> bool:
    """``drain.rival``: whether the lease names a drain still running, as ``drain`` sees it.

    A live process of the holder's chain is evidence only when it is not above ``drain``'s
    command too: in a session of its own. A program running from a queue tree always is, with a
    lease or with none.
    """
    holder = lease.holder
    chain = holder is not None and of(lease.alive, holder) and not lease.shared
    return (chain and holder is not drain) or lease.running


def other_drain(lease: Lease, drain: Drain, step: Step) -> bool:
    """``drain.other_drain``: whether a drain other than the one the step's token names runs."""
    ours = step in OWN and lease.holder is drain
    return not ours and rival(lease, drain)


def without_tree(lease: Lease) -> Lease:
    """``drain.discard`` of the ticket's tree: its draft kept as a stash, its program ended."""
    if lease.tree is None:
        return lease
    stashed = lease.stashed or lease.draft
    return lease._replace(tree=None, draft=False, running=False, stashed=stashed)


def merge_recorded(lease: Lease) -> Lease:
    """``drain.settle`` of a merge: the note says merged, unprinted, and the tree is discarded."""
    return without_tree(lease)._replace(merge=Merge.SAID)


def _clean_all(lease: Lease, drain: Drain, step: Step) -> Lease:
    ours = step is Step.RESUME and lease.holder is drain
    if step is not Step.TAKE_OVER and not ours and rival(lease, drain):
        return lease
    taken = lease._replace(
        holder=drain,
        took=put(lease.took, drain, value=True),
        draining=put(lease.draining, drain, value=True),
    )
    if taken.landing is not None:
        # Why: ``drain.in_flight`` keeps the tree of a live landing, ``--take-over`` or not.
        return taken
    return merge_recorded(taken) if taken.merge is Merge.UNSAID else without_tree(taken)


def _report_and_release(lease: Lease, drain: Drain, step: Step) -> Lease:
    """``drain.report``: a merge no note says recorded, then printed.

    The merge is left alone while another drain runs, or while its landing does. ``drain.release``
    ends the lease on the drains alone: the token is the holder's, or no rival is alive.
    """
    busy = other_drain(lease, drain, step)
    if not busy and lease.merge is Merge.UNSAID and lease.landing is None:
        lease = merge_recorded(lease)
    merge = Merge.PRINTED if lease.merge is Merge.SAID else lease.merge
    holder = lease.holder if busy else None
    return lease._replace(
        holder=holder, merge=merge, draining=put(lease.draining, drain, value=False)
    )


def _clean(lease: Lease, drain: Drain, _: Step) -> Lease:
    """``drain.clean``: a merge no note says recorded, its tree with it, unless another drain or its landing runs."""  # noqa: E501
    busy = lease.landing is not None or other_drain(lease, drain, Step.CLEAN)
    if lease.merge is not Merge.UNSAID or busy:
        return lease
    return merge_recorded(lease)


def _land(lease: Lease, drain: Drain, _: Step) -> Lease | None:
    """``land`` up to the ``merge.py`` it runs, on a tree whose work is all committed.

    A bound: the agent lands only a tree with nothing uncommitted and no program running from it.
    """
    if lease.landing is not None:
        # Why: ``drain.landing_lock`` refuses a second landing of the ticket, which changes nothing.
        return lease
    ready = lease.tree is drain and of(lease.draining, drain) and lease.merge is Merge.NONE
    if not ready or lease.draft or lease.running:
        return None
    return lease._replace(landing=drain)


def _merges(lease: Lease, _: Drain, __: Step) -> Lease | None:
    """``merge.py`` judges, checks and lands the merge: it reads no note, so a hold does not stop it."""  # noqa: E501
    return lease._replace(merge=Merge.UNSAID) if lease.merge is Merge.NONE else None


def _land_rest(lease: Lease, _: Drain, __: Step) -> Lease | None:
    """``land``'s close: ``settle`` on a merge no note says; nothing on one another command recorded."""  # noqa: E501
    if lease.merge is Merge.NONE:
        return None
    done = merge_recorded(lease) if lease.merge is Merge.UNSAID else lease
    return done._replace(landing=None)


def _land_killed(lease: Lease, _: Drain, __: Step) -> Lease | None:
    """A kill of ``land`` once its merge has landed: the merge stays, and no note says it."""
    return lease._replace(landing=None) if lease.merge is not Merge.NONE else None


def _merge_killed(lease: Lease, _: Drain, __: Step) -> Lease | None:
    """A kill of ``land`` and its ``merge.py`` together before the merge, as a timeout ends both."""
    return lease._replace(landing=None) if lease.merge is Merge.NONE else None


def _open_tree(lease: Lease, drain: Drain, _: Step) -> Lease | None:
    if lease.landing is not None:
        # Why: the plan calls the ticket of a live landing built, held or resolved, and ``open``
        # starts only a buildable one.
        return lease
    ready = lease.holder is drain and of(lease.draining, drain) and lease.tree is None
    if not ready:
        return None
    return lease._replace(tree=drain) if lease.merge is Merge.NONE else None


def _refused_in_flight(lease: Lease, _: Drain, __: Step) -> Lease | None:
    """``update`` or ``close`` while a landing of the ticket is alive: ``drain.act_on`` refuses it.

    It changes nothing, before the merge lands and after. A bound: the step is taken only while a
    landing of the ticket is alive.
    """
    return lease if lease.landing is not None else None


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
    Step.UPDATE: _refused_in_flight,
    Step.PARK: _refused_in_flight,
    Step.LAND: _land,
    Step.STOP: _stop,
    Step.DIE: _die,
}
# Why: a drain inside ``land`` takes the land's own steps, a kill of it, or its death; and the
# ticket's commands, which its agent types when its shell gave up waiting on the land.
INSIDE_LAND: dict[Step, DrainAct] = {
    Step.OPEN: _open_tree,
    Step.UPDATE: _refused_in_flight,
    Step.PARK: _refused_in_flight,
    Step.LAND: _land,
    Step.MERGES: _merges,
    Step.LAND_REST: _land_rest,
    Step.MERGE_KILLED: _merge_killed,
    Step.LAND_KILLED: _land_killed,
    Step.DIE: _die,
}
LANDING_ITSELF = frozenset({Step.MERGES, Step.LAND_REST, Step.MERGE_KILLED, Step.LAND_KILLED})


def _by_a_drain(lease: Lease, drain: Drain, step: Step) -> Lease | None:
    acts = INSIDE_LAND if lease.landing is drain else BY_A_DRAIN
    act = acts.get(step)
    if act is None or (step in OWN and not of(lease.took, drain)) or not of(lease.alive, drain):
        return None
    return act(lease, drain, step)


DRAIN_STEPS = tuple(
    step for step in Step if step not in {Step.SEPARATE, Step.SHARED, Step.PROGRAM_ENDS}
)
LEASE_INITIAL = Lease()
LeaseEdge = tuple[Lease, str, Lease]


def lease_moves(lease: Lease) -> list[tuple[str, Lease]]:
    """Every step open to the two drains, their agents and the program in ``lease``."""
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
    return found


LEASE_STEPS = {f"{drain.value}: {step.value}": (drain, step) for drain in Drain for step in Step}


def lease_graph() -> tuple[frozenset[Lease], list[LeaseEdge]]:
    """Every lease state reachable from ``LEASE_INITIAL`` and every step, breadth first."""
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
    """The live, draining drain whose tree another drain's ``clean --all`` took in this step."""
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
    """The lease's holder, when this step is a ``clean --all`` the lease refused."""
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
    landing = before.landing
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
    """What the ticket's note says: the merge it records, printed or not, and whether it holds."""
    return (lease.merge if lease.merge in {Merge.SAID, Merge.PRINTED} else None), lease.held


def leaves_the_landing_alone(before: Lease, said: str, after: Lease) -> bool:
    """No command but the landing itself removes the work tree or writes the note of a ticket whose landing is alive."""  # noqa: E501
    drain, step = LEASE_STEPS.get(said, (None, None))
    if before.landing is None or (drain is before.landing and step in LANDING_ITSELF):
        return True
    removed = before.tree is not None and after.tree is None
    return not removed and note_of(after) == note_of(before)


def hold_not_merged(before: Lease, _: str, after: Lease) -> bool:
    """A ticket whose note holds it is never merged, since no step of the lease graph is the owner's edit that frees it."""  # noqa: E501
    return not before.held or before.merge is not Merge.NONE or after.merge is Merge.NONE


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
    "9i. a ticket whose note holds it is not merged": hold_not_merged,
}


# --- the export: the graphs as TLA+ modules, for a model checker and the charts ------------------


class Run(NamedTuple):
    """States from a start, the start first, and the steps between them, as ``model_tla`` reads."""

    states: tuple[object, ...]
    edges: list[tuple[object, str, object]]


def run(taken: Callable[[Action], bool]) -> Run:
    """The part of ``graph()`` its start reaches by the actions ``taken`` keeps, breadth first.

    Each of its states and steps is one of ``graph()``, so a rule breaks on a step of it exactly
    where it breaks on that step of the graph.
    """
    seen = {INITIAL: INITIAL}
    frontier = deque([INITIAL])
    edges: list[tuple[object, str, object]] = []
    while frontier:
        state = frontier.popleft()
        for action, after in moves(state):
            if not taken(action):
                continue
            known = seen.get(after)
            if known is None:
                seen[after] = known = after
                frontier.append(after)
            edges.append((state, action.name, known))
    return Run(tuple(seen), edges)


def landing_run() -> Run:
    """Every step but those on ``c``: ``a`` built, judged, landed or parked, ``b`` waiting on it."""
    return run(lambda action: action.ticket is not Name.C)


def life_run() -> Run:
    """The landing run with no kill: a ticket's life as the drain's commands lead it."""
    return run(lambda action: action.ticket is not Name.C and action.kind not in KILLS)


def work_run() -> Run:
    """The steps on ``c`` and on the queue, and ``a`` parked, which lets the cap start ``c``."""
    return run(
        lambda action: (
            action.ticket in {Name.C, None}
            or (action.ticket is Name.A and action.kind is Kind.CLOSE)
        )
    )


def plan_word(state: State, name: Name) -> str:
    """What ``drain.plan`` calls the ticket; ``resolved`` when it is not in the plan."""
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
}
"""What a module reads of each ticket, named with the ticket's letter: ``treeA``."""


def state_facts(names: tuple[Name, ...]) -> dict[str, Callable[[State], object]]:
    """The variables of a module of the main graph: the plan's word and the facts of each ticket."""
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
    }


def rule_name(name: str) -> str:
    """A rule's name in a module, by its number: ``Rule6a``."""
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
    "held": lambda lease: lease.held,
}
"""What a module reads of the lease, a drain named by its number: ``alive1``."""


def lease_run() -> Run:
    _, edges = lease_graph()
    return Run(tuple(dict.fromkeys([LEASE_INITIAL, *(after for _, _, after in edges)])), edges)


SHOWN_LANDING = ("statusA", *(f"{fact}A" for fact in TICKET_FACTS))
"""What the chart of a ticket's life reads: ticket ``a``."""
# Why: the statechart tool draws a whole chart only of a module of at most 500 states, and
# neither chart reads the tree, the program or the hold, which no step writes.
SHOWN_LEASE = tuple(
    fact
    for fact in LEASE_FACTS
    if fact not in {"took1", "took2", "tree", "draft", "running", "stashed", "held"}
)
"""What the charts of the lease read: the drains, the lease, the landing and its merge."""


def seen_through(
    name: str, graph: Run, shown: Mapping[str, Callable[[Any], object]], *, deadlock: bool = True
) -> model_tla.Export:
    """The summary of ``graph`` whose states are the values of the facts ``shown``."""
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
    """The modules ``docs/model/drain.md`` was checked and drawn from.

    TLC checks the rules on the landing run, the work run and the whole lease graph; the search
    in the tests proves them on the whole main graph. The charts are drawn from those three, and
    from summaries of the life run and the lease graph.
    """
    landing, lease = landing_run(), lease_run()
    facts = state_facts(NAMES)
    main = step_rules(RULES)
    return (
        model_tla.export(
            "DrainLanding", landing, main, fields=(), derived=facts, action=model_tla.identifier
        ),
        model_tla.export(
            "DrainWork", work_run(), main, fields=(), derived=facts, action=model_tla.identifier
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
        seen_through("DrainLife", life_run(), {fact: facts[fact] for fact in SHOWN_LANDING}),
        seen_through(
            "DrainLeaseShown",
            lease,
            {fact: LEASE_FACTS[fact] for fact in SHOWN_LEASE},
            deadlock=False,
        ),
    )
