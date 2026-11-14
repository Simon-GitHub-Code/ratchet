"""A pure model of one run of the feature pipeline: what the workflow does with any answer.

The workflow is a function of its arguments and of its agents' structured answers, so one run is a
machine whose inputs are those answers. Each answer is abstracted to the few values the script
branches on: the spec role asks the owner or not, measures its change inside the ceilings or over
one, or estimates that the whole change cannot fit; the ticket check finds a gap or not; a builder's
check is green or red; the critic finds nothing, owes a fix, or tries nothing; the review approves,
refuses, or approves against its own evidence; and any agent can die.

The state is the agent the run waits on, what the answers so far established (whether the critic
runs, the spec's last answer, a promise the run kept from the ticket, the last builder's check,
what the last critique owes), the agents and fix rounds spent, and how the run ended. ``graph()``
is every run the abstract answers allow: the fix loop's own bound keeps it finite, so it is every
run, not a sample. ``RULES`` is what a reader of the method expects to stay true whatever the
agents answer; every rule is a predicate over a step ``(before, action name, after)``.

The transitions follow the documents: skills/method/pipeline.md, skills/feature/SKILL.md and the
ADRs they cite (0023 on judging a rebased branch again, 0028 on the critic, 0036 on the ticket
check, 0040 and 0045 on the size stops, 0046 on splitting a ticket).
"""

from __future__ import annotations

import functools
import re
from dataclasses import dataclass, replace
from enum import Enum
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator

MAX_FIX_ROUNDS = 2
MOST_YOUNG = 7
MOST_DEPENDED_ON = 12
MOST_REJUDGE = 2


class Role(Enum):
    """An agent of the run, by the label the script gives its call."""

    PREFLIGHT = "preflight"
    SPEC = "spec"
    CHECK = "ticket-check"
    AMEND = "spec-amend"
    RECHECK = "ticket-recheck"
    IMPLEMENT = "implement"
    CRITIQUE = "critique"
    FIX = "fix"
    REVIEW = "review"
    RECONCILE = "reconcile"
    REVIEW_AGAIN = "review-again"


class Phase(Enum):
    """The documented stages, in their documented order."""

    PREFLIGHT = "Preflight"
    SPEC = "Spec"
    TICKET_CHECK = "Ticket check"
    IMPLEMENT = "Implement"
    CRITIQUE = "Critique"
    FIX = "Fix"
    RECONCILE = "Reconcile"
    REVIEW = "Review"


PHASES = list(Phase)
PHASE_OF = {
    Role.PREFLIGHT: Phase.PREFLIGHT,
    Role.SPEC: Phase.SPEC,
    Role.CHECK: Phase.TICKET_CHECK,
    Role.AMEND: Phase.TICKET_CHECK,
    Role.RECHECK: Phase.TICKET_CHECK,
    Role.IMPLEMENT: Phase.IMPLEMENT,
    Role.CRITIQUE: Phase.CRITIQUE,
    Role.FIX: Phase.FIX,
    Role.REVIEW: Phase.REVIEW,
    Role.RECONCILE: Phase.RECONCILE,
    Role.REVIEW_AGAIN: Phase.REVIEW,
}
REVIEWS = frozenset({Role.REVIEW, Role.REVIEW_AGAIN})


class Start(Enum):
    """What the orchestrator passes the workflow."""

    BUILD = "starts a build"
    NO_TICKET = "starts with no ticket"
    REJUDGE = "judges a rebased branch again"
    REJUDGE_CONFLICT = "judges a branch whose rebase conflicted again"
    REJUDGE_UNNAMED = "judges a branch again without the first run's slug"


class Preflight(Enum):
    DIES = "dies"
    RED = "is red"
    NO_SLUG = "is green with no slug"
    YOUNG = "is green for a project nobody depends on"
    DEPENDED_ON = "is green for a project that is depended-on"


class Spec(Enum):
    """The spec role's answer, written or amended."""

    DIES = "dies"
    ASKS = "asks the owner a question"
    GUARD_EDIT = "needs a guard path changed"
    PRODUCT_OVER = "measures product code over its ceiling"
    TESTS_OVER = "measures tests over their ceiling"
    TESTS_MARGIN = "measures tests over two thirds of their ceiling"
    SPLIT = "estimates the change cannot fit and proposes parts"
    FITS = "fits"
    WRONG_REASON = "fits with tests that fail for the wrong reason"


class Check(Enum):
    """The ticket check's answer, first or second."""

    DIES = "dies"
    NO_GAP = "finds no gap"
    GAP = "finds a gap"


class Build(Enum):
    """A builder's answer: the implementer, a fixer or the reconcile agent."""

    DIES = "dies"
    PRODUCT_OVER = "measures product code over its ceiling"
    TESTS_OVER = "measures tests over their ceiling"
    TESTS_MARGIN = "measures tests over two thirds of their ceiling"
    GREEN = "is green"
    RED = "is red"


class Critique(Enum):
    DIES = "dies"
    CLEAR = "finds nothing"
    SILENT = "finds nothing and tried nothing"
    OWED = "finds a major it does not see fixed"


class Review(Enum):
    DIES = "dies"
    APPROVES = "approves"
    REFUSES = "requests changes"
    APPROVES_RED = "approves on a red check"
    APPROVES_ISSUE = "approves with a blocker of its own open"
    APPROVES_DROPPED = "approves a change that drops a promise of the ticket"
    APPROVES_GUARD = "approves a guard path change nothing covers"


Answer = Preflight | Spec | Check | Build | Critique | Review
Said = Start | Answer

# Why: the script reads a size only after the spec and the implementation, so one answer over a
# ceiling shows all a fixer or a reconcile agent can bring; the test ceiling takes the same branch.
LATE_BUILD = (Build.DIES, Build.GREEN, Build.RED, Build.PRODUCT_OVER)
ANSWERS: dict[Role, tuple[Answer, ...]] = {
    Role.PREFLIGHT: tuple(Preflight),
    Role.SPEC: tuple(Spec),
    Role.CHECK: tuple(Check),
    Role.AMEND: tuple(Spec),
    Role.RECHECK: tuple(Check),
    Role.IMPLEMENT: tuple(Build),
    Role.CRITIQUE: tuple(Critique),
    Role.FIX: LATE_BUILD,
    Role.REVIEW: tuple(Review),
    Role.RECONCILE: LATE_BUILD,
    Role.REVIEW_AGAIN: tuple(Review),
}
FAILED = frozenset({Preflight.DIES, Spec.DIES, Check.DIES, Build.DIES, Critique.DIES, Review.DIES})
MALFORMED = frozenset({Preflight.NO_SLUG})
REFUSED = "refused"
DIES_AS = {
    Role.CHECK: "ticket check",
    Role.AMEND: "spec amend",
    Role.RECHECK: "ticket recheck",
    Role.REVIEW_AGAIN: "review",
}


class Outcome(Enum):
    APPROVE = "approve"
    REQUEST_CHANGES = "request_changes"
    BLOCKED = "blocked"
    ERROR = "the script's own error"


VERDICTS = frozenset({Outcome.APPROVE, Outcome.REQUEST_CHANGES})


class Stop(Enum):
    """Why a blocked run stopped."""

    NO_TICKET = "no ticket was passed"
    PREFLIGHT = "the preflight is red"
    QUESTION = "a question for the owner"
    GUARD_EDIT = "a guard path the owner must change"
    TOO_LARGE = "too large"


class Ceiling(Enum):
    PRODUCT = "product"
    TESTS = "tests"


class Objection(Enum):
    """A reason the script forces ``request_changes`` whatever the review said."""

    SPEC_EVIDENCE = "the spec's tests do not fail for the right reason"
    BUILDER_RED = "the last implementer or fixer reported a red check"
    OWED = "a critic finding is still owed a fix"
    SILENT = "the critic found nothing and tried nothing"
    RECONCILE_RED = "the reconcile agent reported a red check"
    REVIEW_RED = "the review's own check was red"
    REVIEW_ISSUE = "the review holds a blocker or major issue"
    GUARD = "a guard path changed with nothing covering it"


REVIEW_OBJECTIONS: dict[Said, frozenset[Objection]] = {
    Review.APPROVES: frozenset(),
    Review.REFUSES: frozenset(),
    Review.APPROVES_RED: frozenset({Objection.REVIEW_RED}),
    Review.APPROVES_ISSUE: frozenset({Objection.REVIEW_ISSUE}),
    Review.APPROVES_DROPPED: frozenset({Objection.REVIEW_ISSUE}),
    Review.APPROVES_GUARD: frozenset({Objection.GUARD}),
}
# Why: the amend returns the whole spec as it now stands, a fix round's check is "the last
# implement or fix", and the re-critique re-checks every finding by title (pipeline.md, the loop).
REPAIRS: dict[Role, frozenset[Objection]] = {
    Role.AMEND: frozenset({Objection.SPEC_EVIDENCE}),
    Role.FIX: frozenset({Objection.BUILDER_RED}),
    Role.CRITIQUE: frozenset({Objection.OWED}),
}

READS = frozenset(
    {"git rev-parse", "git diff", "git log", "git status", "git show", "git merge-base"}
    | {"gh issue view"}
)
ON_THE_BRANCH = READS | {"git switch"}
PUBLISHING = frozenset({"git commit", "git push", "gh pr"})
# Why: every agent leaves its change uncommitted and the orchestrator commits and publishes
# (pipeline.md); the run's branch is the one SKILL.md switches to or creates; the reconcile agent
# finishes the rebase (SKILL.md, the drain, step 4.4); the preflight runs a script and no git.
COMMANDS: dict[Role, frozenset[str]] = {
    **dict.fromkeys(Role, ON_THE_BRANCH),
    Role.PREFLIGHT: frozenset(),
    Role.CHECK: READS,
    Role.RECHECK: READS,
    Role.RECONCILE: ON_THE_BRANCH | {"git rebase", "git add"},
}


@dataclass(frozen=True)
class Ending:
    """What the orchestrator gets back from a finished run."""

    outcome: Outcome
    stop: Stop | None = None
    ceiling: Ceiling | None = None
    measured_after: Role | None = None
    parts: bool = False
    """Whether a run stopped too large names the parts to split the ticket into."""
    error: str = ""
    """The agent the script's error names, or ``REFUSED`` for one it raises on a bad input."""
    objections: frozenset[Objection] = frozenset()
    rounds: int = 0
    unresolved: bool = False
    lost: bool = False
    """Whether the result returns the ticket's lost promises for the next run."""
    kept: bool = False
    """Whether the result's assumptions list the promise the run kept from the ticket."""
    record: Outcome | None = None
    rejudged: bool = False


@dataclass(frozen=True)
class State:
    """One run between two answers; ``awaiting`` is ``None`` before the start and after the end."""

    started: bool = False
    awaiting: Role | None = None
    rejudge: bool = False
    critic: bool | None = None
    phase: Phase | None = None
    agents: int = 0
    rounds: int = 0
    spec: Spec | None = None
    kept: bool = False
    built: bool = False
    green: bool | None = None
    owed: bool = False
    silent: bool = False
    ending: Ending | None = None


@dataclass(frozen=True)
class Action:
    """The orchestrator's start, or the answer of the agent the run waits on."""

    answer: Said
    role: Role | None = None
    rounds: int = 0

    @property
    def label(self) -> str:
        if self.role is Role.FIX:
            return f"fix-{self.rounds + 1}"
        if self.role is Role.CRITIQUE and self.rounds:
            return f"critique-{self.rounds}"
        return self.role.value if self.role else ""

    @property
    def phase(self) -> Phase | None:
        if self.role is Role.CRITIQUE and self.rounds:
            return Phase.FIX
        return PHASE_OF[self.role] if self.role else None

    @property
    def name(self) -> str:
        who = f"{self.label} " if self.role else "the orchestrator "
        return f"{who}{self.answer.value}"

    @property
    def fails_as(self) -> str:
        if self.answer in MALFORMED:
            return REFUSED
        return DIES_AS.get(self.role, self.label) if self.role else REFUSED


Move = tuple[Action, State]
INITIAL = State()
STARTS: dict[Start, State] = {
    Start.BUILD: State(started=True, awaiting=Role.PREFLIGHT),
    Start.NO_TICKET: State(started=True, ending=Ending(Outcome.BLOCKED, stop=Stop.NO_TICKET)),
    Start.REJUDGE: State(started=True, rejudge=True, awaiting=Role.REVIEW_AGAIN),
    Start.REJUDGE_CONFLICT: State(started=True, rejudge=True, awaiting=Role.RECONCILE),
    Start.REJUDGE_UNNAMED: State(
        started=True, rejudge=True, ending=Ending(Outcome.ERROR, error=REFUSED)
    ),
}
SPEC_STOPS = {Spec.ASKS: Stop.QUESTION, Spec.GUARD_EDIT: Stop.GUARD_EDIT}
OVER: dict[Said, Ceiling] = {
    Spec.PRODUCT_OVER: Ceiling.PRODUCT,
    Spec.TESTS_OVER: Ceiling.TESTS,
    Spec.SPLIT: Ceiling.PRODUCT,
    Build.PRODUCT_OVER: Ceiling.PRODUCT,
    Build.TESTS_OVER: Ceiling.TESTS,
}
MARGINS = frozenset({Spec.TESTS_MARGIN, Build.TESTS_MARGIN})


def stops_on(answer: Said, *, critic: bool | None) -> Ceiling | None:
    """The ceiling a measure stops the run on: one it passes, or the critic's room (ADR 0040)."""
    if answer in MARGINS and critic:
        return Ceiling.TESTS
    return OVER.get(answer)


def standing(state: State) -> frozenset[Objection]:
    """The objections the answers so far raise, which would force ``request_changes`` now."""
    red = Objection.RECONCILE_RED if state.rejudge else Objection.BUILDER_RED
    raised = {
        Objection.SPEC_EVIDENCE: state.spec is Spec.WRONG_REASON,
        red: state.green is False,
        Objection.OWED: state.owed,
        Objection.SILENT: state.silent,
    }
    return frozenset(objection for objection, on in raised.items() if on)


def _ended(state: State, ending: Ending) -> State:
    return replace(state, awaiting=None, ending=ending)


def _too_large(state: State, ceiling: Ceiling, after: Role, *, parts: bool) -> State:
    stop = Ending(Outcome.BLOCKED, Stop.TOO_LARGE, ceiling, after, parts=parts)
    return _ended(state, stop)


def _preflight(state: State, action: Action) -> State:
    if action.answer is Preflight.RED:
        return _ended(state, Ending(Outcome.BLOCKED, stop=Stop.PREFLIGHT))
    return replace(state, critic=action.answer is Preflight.DEPENDED_ON, awaiting=Role.SPEC)


def _spec(state: State, action: Action) -> State:
    answer = action.answer
    assert isinstance(answer, Spec)
    ceiling = stops_on(answer, critic=state.critic)
    if ceiling is not None:
        return _too_large(state, ceiling, Role.SPEC, parts=answer is Spec.SPLIT)
    if answer in SPEC_STOPS:
        return _ended(state, Ending(Outcome.BLOCKED, stop=SPEC_STOPS[answer]))
    after = Role.CHECK if action.role is Role.SPEC else Role.RECHECK
    return replace(state, spec=answer, awaiting=after)


def _check(state: State, action: Action) -> State:
    if action.answer is Check.NO_GAP:
        return replace(state, awaiting=Role.IMPLEMENT)
    if action.role is Role.CHECK:
        return replace(state, awaiting=Role.AMEND)
    return replace(state, kept=True, awaiting=Role.IMPLEMENT)


def _implement(state: State, action: Action) -> State:
    ceiling = stops_on(action.answer, critic=state.critic)
    if ceiling is not None:
        return _too_large(state, ceiling, Role.IMPLEMENT, parts=False)
    after = Role.CRITIQUE if state.critic else Role.REVIEW
    return replace(state, built=True, green=action.answer is not Build.RED, awaiting=after)


def _critique(state: State, action: Action) -> State:
    owed = action.answer is Critique.OWED
    again = owed and state.rounds < MAX_FIX_ROUNDS
    silent = action.answer is Critique.SILENT
    return replace(state, owed=owed, silent=silent, awaiting=Role.FIX if again else Role.REVIEW)


def _fix(state: State, action: Action) -> State:
    green = action.answer is not Build.RED
    return replace(state, rounds=state.rounds + 1, green=green, awaiting=Role.CRITIQUE)


def _reconcile(state: State, action: Action) -> State:
    return replace(state, green=action.answer is not Build.RED, awaiting=Role.REVIEW_AGAIN)


def _review(state: State, action: Action) -> State:
    objections = standing(state) | REVIEW_OBJECTIONS[action.answer]
    approved = action.answer is not Review.REFUSES and not objections
    outcome = Outcome.APPROVE if approved else Outcome.REQUEST_CHANGES
    ending = Ending(
        outcome,
        objections=objections,
        rounds=state.rounds,
        unresolved=state.owed,
        lost=action.answer is Review.APPROVES_DROPPED and not state.rejudge,
        kept=state.kept,
        record=outcome,
        rejudged=state.rejudge,
    )
    return _ended(state, ending)


STEPS: dict[Role, Callable[[State, Action], State]] = {
    Role.PREFLIGHT: _preflight,
    Role.SPEC: _spec,
    Role.CHECK: _check,
    Role.AMEND: _spec,
    Role.RECHECK: _check,
    Role.IMPLEMENT: _implement,
    Role.CRITIQUE: _critique,
    Role.FIX: _fix,
    Role.REVIEW: _review,
    Role.RECONCILE: _reconcile,
    Role.REVIEW_AGAIN: _review,
}


def answered(state: State, action: Action) -> State:
    """``state`` once the agent it waits on gives ``action``'s answer."""
    assert action.role is not None
    ran = replace(state, agents=state.agents + 1, phase=action.phase)
    if action.answer in FAILED or action.answer in MALFORMED:
        return _ended(ran, Ending(Outcome.ERROR, error=action.fails_as))
    return STEPS[action.role](ran, action)


def _start(state: State) -> Iterator[Move]:
    if not state.started:
        for start in Start:
            yield Action(start), STARTS[start]


def _answer(state: State) -> Iterator[Move]:
    if state.awaiting is not None:
        for answer in ANSWERS[state.awaiting]:
            action = Action(answer, state.awaiting, state.rounds)
            yield action, answered(state, action)


ACTIONS: dict[str, Callable[[State], Iterator[Move]]] = {
    "the orchestrator starts the run": _start,
    "the agent the run waits on answers": _answer,
}


def moves(state: State) -> list[Move]:
    """Every action open in ``state``; none once the run has ended."""
    return [move for actions in ACTIONS.values() for move in actions(state)]


Edge = tuple[State, str, State]


def graph() -> tuple[frozenset[State], list[Edge]]:
    """Every state reachable from ``INITIAL`` and every step between them."""
    seen = {INITIAL}
    frontier = [INITIAL]
    edges: list[Edge] = []
    while frontier:
        state = frontier.pop()
        for action, after in moves(state):
            edges.append((state, action.name, after))
            if after not in seen:
                seen.add(after)
                frontier.append(after)
    return frozenset(seen), edges


ROUND = re.compile(r"-(\d+)$")


@functools.cache
def named(name: str) -> Action:
    """The action called ``name``, in whatever round of the fix loop it is taken."""
    who, said = name.split(" ", 1)
    if who == "the":
        return Action(Start(said.removeprefix("orchestrator ")))
    role = Role(ROUND.sub("", who))
    numbered = ROUND.search(who)
    number = int(numbered.group(1)) if numbered else 0
    rounds = number - 1 if role is Role.FIX else number
    answer = next(answer for answer in type(ANSWERS[role][0]) if answer.value == said)
    return Action(answer, role, rounds)


def most(state: State) -> int:
    if state.rejudge:
        return MOST_REJUDGE
    return MOST_YOUNG if state.critic is False else MOST_DEPENDED_ON


def evidenced(before: State, name: str, after: State) -> bool:
    """A run ends approved only when the review that runs last approves with its own check green and nothing open, and the last builder's check was green."""  # noqa: E501
    action = named(name)
    if action.role in REVIEWS and after.ending is None:
        return False
    if after.ending is None or after.ending.outcome is not Outcome.APPROVE:
        return True
    return action.role in REVIEWS and action.answer is Review.APPROVES and before.green is not False


def bounded(_: State, __: str, after: State) -> bool:
    """A run never spawns more agents than the documents allow, seven before the project is depended-on, twelve after, and two to judge a rebased branch again."""  # noqa: E501
    return after.agents <= most(after)


def ordered(before: State, name: str, _: State) -> bool:
    """Agents run in the documented order of stages, and the implementer runs only after a spec with no open question has been through the ticket check."""  # noqa: E501
    action = named(name)
    if action.phase is None:
        return True
    if before.phase is not None and PHASES.index(action.phase) < PHASES.index(before.phase):
        return False
    if action.role is not Role.IMPLEMENT:
        return True
    asked = before.spec is None or before.spec in SPEC_STOPS
    return not asked and before.phase is Phase.TICKET_CHECK


def stricter(before: State, name: str, after: State) -> bool:
    """No answer lifts an objection the run has raised but the repair the documents name for it, and a run with an objection standing never ends approved."""  # noqa: E501
    role = named(name).role
    repairs = REPAIRS.get(role, frozenset()) if role else frozenset()
    if not standing(before) - standing(after) <= repairs:
        return False
    approved = after.ending is not None and after.ending.outcome is Outcome.APPROVE
    return not approved or not standing(before)


def ceilinged(_: State, name: str, after: State) -> bool:
    """A change measured or estimated over the product ceiling or the test ceiling stops the run as too large on that ceiling, so it is never approved."""  # noqa: E501
    ceiling = OVER.get(named(name).answer)
    if ceiling is None:
        return True
    ending = after.ending
    return ending is not None and ending.stop is Stop.TOO_LARGE and ending.ceiling is ceiling


def loop_ends(before: State, name: str, after: State) -> bool:
    """The fix loop runs at most two rounds, and a finding still owed after the last one reaches the result as unresolved."""  # noqa: E501
    action = named(name)
    if after.rounds > MAX_FIX_ROUNDS:
        return False
    if action.role not in REVIEWS or not before.owed or after.ending is None:
        return True
    return after.ending.outcome not in VERDICTS or after.ending.unresolved


def unpublished(_: State, name: str, __: State) -> bool:
    """The run never commits, pushes or opens a pull request, and asks each agent only for the git and forge commands the documents give its role."""  # noqa: E501
    role = named(name).role
    return role is None or not COMMANDS[role] & PUBLISHING


def reported(_: State, name: str, after: State) -> bool:
    """A failed or malformed agent answer ends in a result that says so, never in an approval and never in an unhandled error."""  # noqa: E501
    answer = named(name).answer
    if answer not in FAILED and answer not in MALFORMED:
        return True
    ending = after.ending
    return ending is not None and ending.outcome in {Outcome.BLOCKED, Outcome.REQUEST_CHANGES}


def judged_again(_: State, name: str, after: State) -> bool:
    """Judging a rebased branch again always ends on a second review, and only that review's own approval approves the branch."""  # noqa: E501
    action = named(name)
    ending = after.ending
    if not after.rejudge or ending is None or ending.outcome not in VERDICTS:
        return True
    approval = ending.outcome is not Outcome.APPROVE or action.answer is Review.APPROVES
    return action.role is Role.REVIEW_AGAIN and ending.record is ending.outcome and approval


def split(before: State, name: str, after: State) -> bool:
    """A run that stops too large names the parts to split the ticket into, and stops before any implementer runs."""  # noqa: E501
    ending = after.ending
    if ending is None or ending.stop is not Stop.TOO_LARGE:
        return True
    return ending.parts and named(name).role is not Role.IMPLEMENT and not before.built


def assumed(_: State, __: str, after: State) -> bool:
    """A promise of the ticket the run keeps after the amend left it out reaches the result's assumptions."""  # noqa: E501
    ending = after.ending
    if not after.kept or ending is None or ending.outcome not in VERDICTS:
        return True
    return ending.kept


def critic_waits(before: State, name: str, _: State) -> bool:
    """The critic and its fix loop run only once the project is depended-on."""
    return named(name).role not in {Role.CRITIQUE, Role.FIX} or before.critic is True


def check_asks_nothing(_: State, name: str, after: State) -> bool:
    """The ticket check never stops the run unless its agent dies, so no question reaches the owner from it."""  # noqa: E501
    action = named(name)
    if action.role not in {Role.CHECK, Role.RECHECK} or action.answer is Check.DIES:
        return True
    return after.ending is None


def never_crashes(_: State, name: str, after: State) -> bool:
    """A failed or malformed agent answer is never approved and never crashes the run, which ends in the script's own error naming that agent."""  # noqa: E501
    action = named(name)
    if action.answer not in FAILED and action.answer not in MALFORMED:
        return True
    ending = after.ending
    return (
        ending is not None and ending.outcome is Outcome.ERROR and ending.error == action.fails_as
    )


def lost(_: State, name: str, after: State) -> bool:
    """A change the review finds dropping a promise of the ticket is refused, and a first build returns what it lost for the next run."""  # noqa: E501
    ending = after.ending
    if named(name).answer is not Review.APPROVES_DROPPED or ending is None:
        return True
    return ending.outcome is Outcome.REQUEST_CHANGES and ending.lost == (not after.rejudge)


RULES: dict[str, Callable[[State, str, State], bool]] = {
    "1. a run is approved only on the last review's clean approval of a green tree": evidenced,
    "2. a run spawns no more agents than the documents allow": bounded,
    "3. stages run in order, and nothing is built before the spec asks nothing": ordered,
    "4. a verdict only gets stricter, but by a documented repair": stricter,
    "5. a change over a ceiling stops as too large and is never approved": ceilinged,
    "6. the fix loop ends, and what it leaves owed is in the result": loop_ends,
    "7. the run never commits, pushes or opens a pull request": unpublished,
    "8. a failed or malformed answer ends in a result that says so": reported,
    "9. a rebased branch is approved only by its own second review": judged_again,
    "10. a run that stops too large names its parts and is unbuilt": split,
    "11. a promise the run kept from the ticket is in its assumptions": assumed,
    "12. the critic waits for a project that is depended-on": critic_waits,
    "13. the ticket check never stops the run": check_asks_nothing,
    "14. a failed answer is never approved and never crashes the run": never_crashes,
    "15. a dropped promise is refused and handed to the next run": lost,
}
