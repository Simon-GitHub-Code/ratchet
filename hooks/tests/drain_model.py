"""A pure model of the queue drain: how tickets go from agreed to merged, and the default branch.

Three tickets, ``a``, ``b`` and ``c``, where ``b`` is blocked by ``a``, under a cap of one. Each
ticket is what ``drain.classify`` reads: its queue work tree, its branch, whether that branch holds
the tip of the default branch, whether it is merged, the drain's note on it, its text and the
stashes that hold its uncommitted work. The actions are the drain's commands in any order, what
the agent does in a work tree, what the owner does, a stale index lock that git leaves, and a
kill of ``update``, ``close`` or ``land`` between two of their effects. ``graph()`` is every
reachable state and step, ``RULES`` what a reader of ADR 0023 and the feature skill expects to
stay true over any history. Every rule is a predicate over a step: ``(before, action name,
after)``.

The transitions are the code's: a command does here what ``drain.py`` and ``merge.py`` do on a
repository, refusals included, so a rule the code does not keep breaks here too. The merge judge
passes a branch only when its verdict record is its last commit, certifies the tree the branch
has, and the branch holds the default tip; the binding's ``check`` is always green; no two tickets
touch one file, so nothing conflicts.

Bounds keep the graph small: the owner edits a ticket at most once, the agent leaves work
uncommitted once, and the retry count of a ticket left waiting stops at ``RETRIES``, past which
nothing reads it. ``settle``'s mark that it has not yet discarded the tree is kept only on the
ticket a landing retries, the one ticket whose mark a close reads.
"""

from __future__ import annotations

import functools
from collections import deque
from dataclasses import dataclass
from enum import Enum
from typing import TYPE_CHECKING, NamedTuple

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator

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

    ``settle`` drops it once the tree is gone, and ``update`` drops it when it writes the note.
    While it is set, ``close`` does not count its refusal again. A merged note never reads it.
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

    A merged branch, or one with no commit of its own and no work tree, is judged and refused the
    same either way; ``open`` starts its branch at the tip again.
    """
    if ticket.merged or ticket.branch is Branch.NONE:
        return False
    return ticket.branch is not Branch.BARE or ticket.tree is not Tree.NONE


def normal(name: Name, ticket: Ticket) -> Ticket:
    """``ticket`` with what nothing reads set one way, so two states that differ only there are one.

    Only a close that counts a retry reads ``settle``'s mark: never on a merged note, and never
    on a ticket whose branch no verdict record is committed on, which is a bound.
    """
    current = ticket.current or not tip_matters(ticket)
    reviewed = ticket.reviewed and ticket.branch is Branch.JUDGED and not ticket.merged
    note = ticket.note
    unread = note is not None and (note.outcome is Outcome.MERGED or name not in JUDGES)
    if unread and note.discarding:
        ticket = ticket._replace(note=note._replace(discarding=False))
    if (current, reviewed) == (ticket.current, ticket.reviewed):
        return ticket
    return ticket._replace(current=current, reviewed=reviewed)


@dataclass(frozen=True)
class State:
    tickets: tuple[Ticket, Ticket, Ticket]
    worked: bool = False
    """Whether the agent has left work uncommitted yet: it does so once in a history."""

    @property
    def edited(self) -> bool:
        """Whether the owner has edited a ticket yet: they do so once in a history."""
        return any(ticket.text for ticket in self.tickets)

    def of(self, name: Name) -> Ticket:
        return self.tickets[INDEX[name]]

    def with_ticket(self, name: Name, ticket: Ticket) -> State:
        tickets = list(self.tickets)
        tickets[INDEX[name]] = normal(name, ticket)
        return State((tickets[0], tickets[1], tickets[2]), self.worked)

    def __hash__(self) -> int:
        # Why: the search hashes each state it reaches many times, and a state never changes.
        cached = self.__dict__.get("hashed")
        if cached is None:
            cached = hash((self.tickets, self.worked))
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
    """Each ticket in the plan, as ``drain.classify`` and then ``drain.settled`` call it."""
    found: dict[Name, Status] = {}
    for name, ticket in state.each():
        if ticket.merged:
            continue
        if held_by_note(ticket):
            found[name] = Status.HELD
        elif ticket.ahead:
            found[name] = Status.BUILT
        elif ticket.tree is not Tree.NONE:
            found[name] = Status.BUILDING
        elif name in BLOCKER and not state.of(BLOCKER[name]).merged:
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
    """The tickets ``plan`` starts: the buildable, in order, up to the cap less those in a tree."""
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


KILLS = {
    Kind.KILL_MID_REBASE: Kind.UPDATE,
    Kind.KILL_AFTER_REBASE: Kind.UPDATE,
    Kind.KILL_BEFORE_DISCARD: Kind.CLOSE,
    Kind.KILL_AFTER_STASH: Kind.CLOSE,
    Kind.KILL_LAND_BEFORE_NOTE: Kind.LAND,
    Kind.KILL_LAND_BEFORE_DISCARD: Kind.LAND,
    Kind.KILL_LAND_AFTER_DISCARD: Kind.LAND,
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
    again while the note it follows is still marked.
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
    """``drain.recorded_merge``: merged, and the run that landed it did not finish.

    The note (one the drain wrote) does not say so, or it does and the tree still stands.
    """
    note = ticket.note
    if not ticket.merged or note is None:
        return False
    return note.outcome is not Outcome.MERGED or ticket.tree is not Tree.NONE


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
            prior._replace(hold=False, attempts=0, rebasing=False)
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
    return State(state.with_ticket(name, ticket._replace(tree=Tree.DIRTY)).tickets, worked=True)


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
    ticket = revived(state, name)
    if ticket is None:
        return state
    if finishing(ticket):
        return state.with_ticket(name, rebased(marked(ticket, rebasing=False)))
    began = to_rebase(state, name)
    if began is None:
        return state
    return state.with_ticket(name, marked(rebased(began), rebasing=False))


def _kill_mid_rebase(state: State, name: Name) -> State | None:
    began = to_rebase(state, name)
    if began is None:
        return None
    return state.with_ticket(name, began._replace(tree=Tree.REBASING))


def _kill_after_rebase(state: State, name: Name) -> State | None:
    """Where ``update`` is about to drop the record: after its rebase, or finishing one.

    A run that finishes what a killed run left drops the record before it clears the mark, so
    a kill there leaves the mark. A merged branch is left out: after a ``--no-ff`` merge
    ``update`` rebases it again rather than finish, and the model does not follow that run.
    """
    began = to_rebase(state, name)
    if began is not None:
        branch = Branch.STALE if began.branch in RECORDED else began.branch
        return state.with_ticket(name, began._replace(current=True, branch=branch, reviewed=False))
    ticket = revived(state, name)
    if ticket is None or ticket.merged or not finishing(ticket) or ticket.branch not in RECORDED:
        return None
    return state.with_ticket(name, ticket)


def parkable(ticket: Ticket) -> bool:
    """Whether ``close --outcome parked`` gets as far as its own note.

    A merge is refused, and one no note says yet is recorded first: the kills of ``close`` are
    taken on a ticket that never merges, and do not follow that record.
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

    On a ticket whose mark nothing reads, a kill after the discard leaves what the whole close
    leaves, so ``close`` is killed only here.
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


def landing(state: State, name: Name) -> Landing | None:
    """``land`` up to its close; ``None`` when it closes nothing."""
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
    run = landing(state, name)
    if run is None:
        return state
    ticket = closed(run.judged.of(name), run.outcome, retry=run.retry)
    return run.judged.with_ticket(name, ticket)


def _kill_land_before_note(state: State, name: Name) -> State | None:
    """The merge landed and nothing after it ran: no note says so and the tree is there."""
    run = landing(state, name)
    if run is None or run.judged == state:
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


def _close(state: State, name: Name) -> State:
    ticket = state.of(name)
    if unrecorded(ticket):
        # Why: ``close`` records a merge no note says before it reads the note, and then refuses.
        return state.with_ticket(name, recorded(ticket))
    if says_merged(ticket):
        return state
    return state.with_ticket(name, closed(ticket, Outcome.CLOSED))


def cleaned(state: State, *, everything: bool) -> State:
    """``drain.clean``: every tree of a merged branch removed, with ``everything`` every tree.

    A merge no note says is recorded first, a tree or not. ``everything`` then clears the mark
    of a rebase that did not finish (it was under way in the tree) or of a branch with no commit
    of its own: ``drain.unmark``.
    """
    for name, ticket in state.each():
        settled = recorded(ticket) if unrecorded(ticket) else ticket
        if ticket.tree is not Tree.NONE and (ticket.merged or everything):
            settled = discard(settled)
        note = settled.note
        unfinished = ticket.tree is Tree.REBASING or not settled.ahead
        if everything and note is not None and note.rebasing and unfinished:
            settled = settled._replace(note=note._replace(rebasing=False))
        state = state.with_ticket(name, settled)
    return state


def _report(state: State) -> State:
    for name, ticket in state.each():
        if ticket.note is not None and ticket.note.fresh:
            state = state.with_ticket(name, ticket._replace(note=ticket.note._replace(fresh=False)))
    return state


def _edit(state: State, name: Name) -> State | None:
    ticket = state.of(name)
    if state.edited or ticket.merged:
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


# Why: where two paths to a state are as short, the search keeps the one whose actions come
# first here; a landing runs merge.py, the dearest step to replay, so it comes last.
ON_TICKET: dict[Kind, Callable[[State, Name], State | None]] = {
    Kind.OPEN: _open,
    Kind.WORK: _work,
    Kind.COMMIT: _commit,
    Kind.RECORD: _record,
    Kind.CLOSE: _close,
    Kind.UPDATE: _update,
    Kind.EDIT: _edit,
    Kind.LAND: _land,
    Kind.LOCK: _lock,
    Kind.UNLOCK: _unlock,
    Kind.KILL_MID_REBASE: _kill_mid_rebase,
    Kind.KILL_AFTER_REBASE: _kill_after_rebase,
    Kind.KILL_BEFORE_DISCARD: _kill_before_discard,
    Kind.KILL_AFTER_STASH: _kill_after_stash,
    Kind.KILL_LAND_BEFORE_NOTE: _kill_land_before_note,
    Kind.KILL_LAND_BEFORE_DISCARD: _kill_land_before_discard,
    Kind.KILL_LAND_AFTER_DISCARD: _kill_land_after_discard,
}
WHOLE: dict[Kind, Callable[[State], State]] = {
    Kind.CLEAN: lambda state: cleaned(state, everything=False),
    Kind.CLEAN_ALL: lambda state: cleaned(state, everything=True),
    Kind.REPORT: _report,
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
}
JUDGES = PLAYS[Kind.RECORD]
"""The tickets a landing can refuse only as behind, and so retry: ``normal`` keeps their mark."""


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

    The first step into each state ends a shortest path to it.
    """
    seen = {INITIAL}
    frontier = deque([INITIAL])
    edges: list[Edge] = []
    while frontier:
        state = frontier.popleft()
        for action, after in moves(state):
            edges.append((state, action.name, after))
            if after not in seen:
                seen.add(after)
                frontier.append(after)
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
    """A ticket's merge is recorded when it lands, or left to recovery by a kill, is never closed as another outcome, and is printed by exactly one report."""  # noqa: E501
    killed = action.rpartition(" ")[0] in KILLED
    for was, now in zip(before.tickets, after.tickets, strict=True):
        said, unprinted = merge_said(was)
        says, unprinting = merge_said(now)
        left = killed and unrecorded(now)
        if now.merged and not was.merged and not unprinting and not left:
            return False
        if says and not said and not unprinting:
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


def stays_held(before: State, _: str, after: State) -> bool:
    """A held ticket stays held, and unmerged, until the owner changes its text."""
    now = classify(after)
    return all(
        now.get(name) is Status.HELD or before.of(name).text != after.of(name).text
        for name, status in classify(before).items()
        if status is Status.HELD
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
    "6. a held ticket stays held until its text changes": stays_held,
    "7. a ticket whose blocker waits on the owner is refused": refused,
    "8. a killed command recovers by running again or by clean --all": recovers,
    "10. clean --all leaves no queue work tree": no_tree_left,
}
