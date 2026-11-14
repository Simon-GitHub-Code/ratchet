"""A pure model of the queue drain: how tickets go from agreed to merged, and the default branch.

Three tickets, ``a``, ``b`` and ``c``, where ``b`` is blocked by ``a``, under a cap of one. Each
ticket is what ``drain.classify`` reads: its queue work tree, its branch, whether that branch holds
the tip of the default branch, whether it is merged, the drain's note on it, its text and the
stashes that hold its uncommitted work. The actions are the drain's commands in any order, what
the agent does in a work tree, what the owner does, and a stale index lock that git leaves.
``graph()`` is every reachable state and step, ``RULES`` what a reader of ADR 0023 and the feature
skill expects to stay true over any history. Every rule is a predicate over a step:
``(before, action name, after)``.

The transitions are the code's: a command does here what ``drain.py`` and ``merge.py`` do on a
repository, refusals included, so a rule the code does not keep breaks here too. The merge judge
passes a branch only when its verdict record is its last commit and it holds the default tip; the
binding's ``check`` is always green; no two tickets touch one file, so nothing conflicts.

Bounds keep the graph small: the owner edits a ticket at most once, the agent leaves work
uncommitted in a ticket's tree only while no stash holds that ticket's work, and the retry count
of a ticket left waiting stops at ``RETRIES``, past which nothing reads it.
"""

from __future__ import annotations

import functools
from collections import deque
from dataclasses import dataclass, replace
from enum import Enum
from typing import TYPE_CHECKING

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


INDEX = {name: index for index, name in enumerate(Name)}
BLOCKER: dict[Name, Name] = {Name.B: Name.A}


class Tree(Fact):
    NONE = "no work tree"
    CLEAN = "a clean work tree"
    DIRTY = "uncommitted work in its tree"
    LOCKED = "uncommitted work behind a stale index lock"


HOLDS_WORK = frozenset({Tree.DIRTY, Tree.LOCKED})


class Branch(Fact):
    NONE = "no branch"
    BARE = "a branch with no commit of its own"
    DRAFT = "commits, the verdict record not last"
    JUDGED = "commits, the verdict record last"


class Outcome(Fact):
    """What a note says. ``waits`` and ``parked`` differ only in the report's heading."""

    OPENED = "opened"
    MERGED = "merged"
    CLOSED = "waits or parked"


@dataclass(frozen=True)
class Note:
    """``.ratchet/queue/tickets/<branch>.json``: what the drain keeps on a ticket.

    ``open`` writes one with no hold, digest or report flag; a close writes all of them.
    """

    outcome: Outcome
    hold: bool = False
    text: int | None = None
    """The ticket's text the note was written on (its digest); ``None`` names none."""
    fresh: bool = False
    """A merge no report has printed yet; only a merge's flag is kept, as only rule 5 reads it."""
    attempts: int = 0


@dataclass(frozen=True)
class Ticket:
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
        return self.branch in {Branch.DRAFT, Branch.JUDGED} and not self.merged


def tip_matters(ticket: Ticket) -> bool:
    """Whether anything the code does reads if the branch holds the default tip.

    A merged branch, or one with no commit of its own and no work tree, is judged and refused the
    same either way; ``open`` starts its branch at the tip again.
    """
    if ticket.merged or ticket.branch is Branch.NONE:
        return False
    return ticket.branch is not Branch.BARE or ticket.tree is not Tree.NONE


def normal(ticket: Ticket) -> Ticket:
    """``ticket`` with what nothing reads set one way, so two states that differ only there are one."""  # noqa: E501
    current = ticket.current or not tip_matters(ticket)
    reviewed = ticket.reviewed and ticket.branch is Branch.JUDGED and not ticket.merged
    if (current, reviewed) == (ticket.current, ticket.reviewed):
        return ticket
    return replace(ticket, current=current, reviewed=reviewed)


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
        tickets[INDEX[name]] = normal(ticket)
        return State((tickets[0], tickets[1], tickets[2]), self.worked)

    def __hash__(self) -> int:
        # Why: the search hashes each state it reaches many times, and a state never changes.
        cached = self.__dict__.get("hashed")
        if cached is None:
            cached = hash((self.tickets, self.worked))
            object.__setattr__(self, "hashed", cached)
        return cached

    def each(self) -> Iterator[tuple[Name, Ticket]]:
        return zip(Name, self.tickets, strict=True)


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
    buildable = tuple(name for name in Name if statuses.get(name) is Status.BUILDABLE)
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


@dataclass(frozen=True)
class Action:
    kind: Kind
    ticket: Name | None = None

    @property
    def name(self) -> str:
        return self.kind.value if self.ticket is None else f"{self.kind.value} {self.ticket.value}"


Move = tuple[Action, State]


def moved(state: State) -> State:
    """``state`` after the default branch moved: no branch holds its tip any longer."""
    for name, ticket in state.each():
        state = state.with_ticket(name, replace(ticket, current=False, reviewed=False))
    return state


def discard(ticket: Ticket) -> Ticket:
    """The ticket after ``drain.discard``: its tree gone, its uncommitted work a stash first.

    A tree whose work git cannot stash stays, work and all.
    """
    if ticket.tree is Tree.LOCKED:
        return ticket
    stashes = ticket.stashes + (ticket.tree is Tree.DIRTY)
    return replace(ticket, tree=Tree.NONE, stashes=stashes)


def closed(ticket: Ticket, outcome: Outcome, *, retry: bool = False) -> Ticket:
    """The ticket after ``drain.close``: its tree discarded and its note written.

    ``retry`` says every reason is one the drain resolves itself: the ticket is retried, not
    held, until it has waited ``RETRIES`` times in a row on one text.
    """
    prior = ticket.note
    same = prior is not None and prior.text == ticket.text and prior.outcome is not Outcome.OPENED
    attempts = min(RETRIES, (prior.attempts if same and prior else 0) + 1) if retry else 0
    merged = outcome is Outcome.MERGED
    hold = not merged and (attempts >= RETRIES or not retry)
    note = Note(outcome, hold=hold, text=ticket.text, fresh=merged, attempts=attempts)
    return replace(discard(ticket), note=note)


def recorded(ticket: Ticket) -> Ticket:
    """A merge no note says, recorded: ``drain.record_merge``."""
    return closed(ticket, Outcome.MERGED)


def unrecorded(ticket: Ticket) -> bool:
    """``drain.recorded_merge``: merged, and the note (one the drain wrote) does not say so."""
    note = ticket.note
    return ticket.merged and note is not None and note.outcome is not Outcome.MERGED


def _open(state: State, name: Name) -> State:
    if name not in start(state):
        return state
    ticket = state.of(name)
    opened = Ticket(
        tree=Tree.CLEAN,
        branch=Branch.BARE,
        note=Note(Outcome.OPENED),
        text=ticket.text,
        stashes=ticket.stashes,
    )
    return state.with_ticket(name, opened)


def _work(state: State, name: Name) -> State | None:
    ticket = state.of(name)
    if state.worked or ticket.merged or ticket.tree is not Tree.CLEAN:
        return None
    return replace(state.with_ticket(name, replace(ticket, tree=Tree.DIRTY)), worked=True)


def _commit(state: State, name: Name) -> State | None:
    ticket = state.of(name)
    usable = ticket.tree in {Tree.CLEAN, Tree.DIRTY} and not ticket.merged
    if not usable or (ticket.branch is not Branch.BARE and ticket.tree is not Tree.DIRTY):
        return None
    committed = replace(ticket, tree=Tree.CLEAN, branch=Branch.DRAFT, reviewed=False)
    return state.with_ticket(name, committed)


def _record(state: State, name: Name) -> State | None:
    ticket = state.of(name)
    if ticket.merged or ticket.tree is not Tree.CLEAN or ticket.branch is not Branch.DRAFT:
        return None
    judged = replace(ticket, branch=Branch.JUDGED, reviewed=ticket.current)
    return state.with_ticket(name, judged)


def rebased(ticket: Ticket) -> Ticket:
    """The branch rebased onto the default tip, a verdict record left last dropped."""
    branch = Branch.DRAFT if ticket.branch is Branch.JUDGED else ticket.branch
    return replace(ticket, current=True, branch=branch, reviewed=False)


def _update(state: State, name: Name) -> State:
    ticket = state.of(name)
    if ticket.tree is Tree.NONE:
        if not ticket.ahead or ticket.current:
            return state
        return state.with_ticket(name, rebased(replace(ticket, tree=Tree.CLEAN)))
    if ticket.tree is not Tree.CLEAN or ticket.current or ticket.branch is Branch.NONE:
        return state
    return state.with_ticket(name, rebased(ticket))


def judge(ticket: Ticket) -> list[str]:
    """The reason codes ``merge.py`` prints for the branch; none when it lands."""
    if ticket.merged:
        return ["behind", "record"]
    reasons = [] if ticket.current else ["behind"]
    return reasons if ticket.branch is Branch.JUDGED else [*reasons, "record"]


def _land(state: State, name: Name) -> State:
    ticket = state.of(name)
    if unrecorded(ticket):
        return state.with_ticket(name, recorded(ticket))
    if ticket.branch is Branch.NONE:
        return state
    reasons = judge(ticket)
    if not reasons:
        landed = moved(state).with_ticket(
            name, replace(ticket, merged=True, current=False, reviewed=False)
        )
        return landed.with_ticket(name, closed(landed.of(name), Outcome.MERGED))
    retry = reasons == ["behind"]
    return state.with_ticket(name, closed(ticket, Outcome.CLOSED, retry=retry))


def _close(state: State, name: Name) -> State:
    return state.with_ticket(name, closed(state.of(name), Outcome.CLOSED))


def cleaned(state: State, *, everything: bool) -> State:
    """``drain.clean``: every tree of a merged branch removed, with ``everything`` every tree."""
    for name, ticket in state.each():
        if ticket.tree is Tree.NONE or not (ticket.merged or everything):
            continue
        settled = recorded(ticket) if unrecorded(ticket) else ticket
        state = state.with_ticket(name, discard(settled))
    return state


def _report(state: State) -> State:
    for name, ticket in state.each():
        if ticket.note is not None and ticket.note.fresh:
            state = state.with_ticket(name, replace(ticket, note=replace(ticket.note, fresh=False)))
    return state


def _edit(state: State, name: Name) -> State | None:
    ticket = state.of(name)
    if state.edited or ticket.merged:
        return None
    return moved(state).with_ticket(name, replace(moved(state).of(name), text=1))


def _lock(state: State, name: Name) -> State | None:
    ticket = state.of(name)
    if ticket.tree is not Tree.DIRTY:
        return None
    return state.with_ticket(name, replace(ticket, tree=Tree.LOCKED))


def _unlock(state: State, name: Name) -> State | None:
    ticket = state.of(name)
    if ticket.tree is not Tree.LOCKED:
        return None
    return state.with_ticket(name, replace(ticket, tree=Tree.DIRTY))


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
}
WHOLE: dict[Kind, Callable[[State], State]] = {
    Kind.CLEAN: lambda state: cleaned(state, everything=False),
    Kind.CLEAN_ALL: lambda state: cleaned(state, everything=True),
    Kind.REPORT: _report,
}


# Why: a bound. Each ticket plays one part so the graph stays small: the agent commits only in
# a, whose branch goes the whole way to merged, and leaves work uncommitted only in c, the ticket
# the cap holds back; b, which waits on a, is only opened. The drain's other commands and the
# owner reach a and c, and the commands of the whole queue reach all three.
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
}


def moves(state: State) -> list[Move]:
    """Every action open to the drain, the agent, the owner and git in ``state``."""
    found: list[Move] = []
    for kind, act in ON_TICKET.items():
        for name in PLAYS.get(kind, tuple(Name)):
            after = act(state, name)
            if after is not None:
                found.append((Action(kind, name), after))
    found += [(Action(kind), act(state)) for kind, act in WHOLE.items()]
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
    return next((name for name in Name if name.value == last), None)


def within_cap(before: State, _: str, after: State) -> bool:
    """No step makes more tickets build at once than the cap allows."""
    return building(after) <= CAP or building(after) <= building(before)


def kept(before: State, action: str, after: State) -> bool:
    """Uncommitted work in a queue work tree is never lost: it stays in the tree, goes into a stash, or the agent commits it."""  # noqa: E501
    for name, was, now in zip(Name, before.tickets, after.tickets, strict=True):
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
    """A ticket's merge is recorded when it lands and is printed by exactly one report."""
    for was, now in zip(before.tickets, after.tickets, strict=True):
        said, unprinted = merge_said(was)
        says, unprinting = merge_said(now)
        if now.merged and not was.merged and not unprinting:
            return False
        if said and not says:
            return False
        printed = unprinted and action == Kind.REPORT.value
        if said and unprinted != unprinting and not printed:
            return False
    return True


def stays_held(before: State, _: str, after: State) -> bool:
    """A held ticket stays held, and unmerged, until the owner changes its text."""
    was_held = {name for name, status in classify(before).items() if status is Status.HELD}
    now_held = {name for name, status in classify(after).items() if status is Status.HELD}
    return all(name in now_held or before.of(name).text != after.of(name).text for name in was_held)


def refused(_: State, __: str, after: State) -> bool:
    """A ticket whose blocker waits on the owner is refused, never left waiting for it."""
    statuses = classify(after)
    return all(
        statuses.get(name) is not Status.WAITING or not held_by_note(after.of(blocker))
        for name, blocker in BLOCKER.items()
    )


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
    "10. clean --all leaves no queue work tree": no_tree_left,
}
