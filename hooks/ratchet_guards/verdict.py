"""Verdicts: what the guard decides about one tool call, and why.

Every rule has an id, a decision, a sentence and a way out: what the agent does next without the
owner. A tool call collects the verdicts of every rule that fired and the strictest wins: deny
over ask. No verdict at all means the guard has no opinion, and the harness's own permission rules
decide.

A guard path is no rule of its own. A change to one on a branch gets no verdict: it reaches the
default branch only through a merge, and the merge judge reads it there (ADR 0033). What would take
effect before any merge is refused: a guard path changed where its commit lands on the default
branch, and the paths the owner changes by hand.
"""

from collections.abc import Iterable
from dataclasses import dataclass
from enum import IntEnum

from ratchet_guards.early import Kind

DISPUTE = (
    "Do not work around it. If you believe the gate is wrong, stop, write a gate dispute "
    "(docs/disputes/NNNN-<slug>.md) and report it to whoever started you."
)
ON_A_BRANCH = (
    "Make the change on a branch (`git switch -c <name>`) and commit it there: the merge judge "
    "reads it and sends it to the owner when it must."
)
# Why: the owner answers a file that acts before the merge at the prompt, in plain words.
ALLOW = "Allow it?"
NAME_THE_DIRECTORY = (
    "Give the file a path the guard can follow, an absolute one or one after a `cd` to a literal "
    "directory, and it is judged where it lands."
)


class Decision(IntEnum):
    """How strict a verdict is; a higher value wins."""

    ASK = 1
    DENY = 2


@dataclass(frozen=True, slots=True)
class Rule:
    """One guard rule: what it decides, the sentence that explains it, and the way out."""

    decision: Decision
    because: str
    way_out: str = DISPUTE


RULES: dict[str, Rule] = {
    "guard-path-default": Rule(
        Decision.DENY,
        "changes a guard path where the commit lands on the default branch, which no merge judge "
        "reads",
        ON_A_BRANCH,
    ),
    "guard-path-locked": Rule(
        Decision.DENY,
        "edits a guard path that only the owner changes, by hand, outside Claude Code",
    ),
    "guard-path-write": Rule(
        Decision.DENY,
        "writes, moves or deletes, from the shell, a guard path that only the owner changes",
    ),
    "no-verify": Rule(Decision.DENY, "skips the git hooks"),
    "skip-hooks": Rule(Decision.DENY, "skips or uninstalls the pre-commit hooks"),
    "hooks-path": Rule(Decision.DENY, "redirects git hooks or overrides git config"),
    "force-push": Rule(Decision.DENY, "force-pushes, which rewrites published history"),
    "push-main": Rule(Decision.DENY, "pushes to main; changes reach main through a reviewed PR"),
    "reset-main": Rule(Decision.DENY, "moves main back or sideways with a reset"),
    "default-ref": Rule(
        Decision.DENY,
        "moves, deletes or re-points the default branch, or a ref or setting it is read through",
    ),
    "amend-pushed": Rule(Decision.DENY, "amends a commit that is already pushed"),
    "rewrite-history": Rule(Decision.DENY, "rewrites history"),
    "merge-pr": Rule(
        Decision.ASK,
        "merges a pull request",
        "The merge judge merges a pull request by itself when the checks pass and nothing in it "
        "needs you, so one still open is waiting for you for a reason: the judge's comment on the "
        "pull request gives each one in a line.",
    ),
    "merge-default": Rule(
        Decision.ASK,
        "merges another branch into the default branch",
        "The `merge` command merges a branch by itself when the checks pass and nothing in it "
        "needs you; `just merge-ready --head <branch>` prints each reason this one would wait "
        "for you, one a line.",
    ),
    "branch-protection": Rule(Decision.DENY, "reads or changes branch protection or rulesets"),
    "repo-settings": Rule(
        Decision.DENY, "changes repository, secret, variable or workflow settings"
    ),
    "labels": Rule(Decision.DENY, "manages labels"),
    "agent-ready-label": Rule(
        Decision.ASK,
        "applies the agent-ready label, the owner's agreement that a ticket is ready to build",
    ),
    "workflow-runs": Rule(Decision.DENY, "deletes or cancels workflow runs"),
    "nested-settings": Rule(
        Decision.DENY,
        "starts Claude Code with other settings sources or with hooks or plugins off",
    ),
    "plugin-removal": Rule(
        Decision.DENY, "disables or removes a harness plugin, package or marketplace"
    ),
    "workflow-write": Rule(Decision.ASK, Kind.WORKFLOW.value, ALLOW),
    "instructions-write": Rule(Decision.ASK, Kind.INSTRUCTIONS.value, ALLOW),
    "unreadable": Rule(Decision.ASK, "could not be checked by the guard"),
    "unresolved-directory": Rule(
        Decision.DENY,
        "writes a relative path after a cd the guard cannot follow; it may be a guard path that "
        "only the owner changes, or one on the default branch",
        NAME_THE_DIRECTORY,
    ),
}


ACTING_RULES = {Kind.WORKFLOW: "workflow-write", Kind.INSTRUCTIONS: "instructions-write"}


@dataclass(frozen=True, slots=True)
class Verdict:
    """A rule that fired on a tool call, with the detail that made it fire."""

    rule: str
    detail: str

    @property
    def decision(self) -> Decision:
        """The rule's decision."""
        return RULES[self.rule].decision

    @property
    def because(self) -> str:
        """The rule's sentence."""
        return RULES[self.rule].because

    @property
    def way_out(self) -> str:
        """What the agent, or the owner at a prompt, does next."""
        return RULES[self.rule].way_out


def strictest(verdicts: Iterable[Verdict]) -> Verdict | None:
    """The first verdict with the strictest decision, or ``None`` when no rule fired."""
    return max(verdicts, key=lambda verdict: verdict.decision, default=None)
