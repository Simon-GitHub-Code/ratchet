"""Verdicts: what the guard decides about one tool call, and why.

Every rule has an id, a decision and a sentence. A tool call collects the verdicts of every
rule that fired and the strictest wins: deny over ask over allow. No verdict at all means the
guard has no opinion, and Claude Code's own permission rules decide.
"""

from collections.abc import Iterable
from dataclasses import dataclass
from enum import IntEnum


class Decision(IntEnum):
    """How strict a verdict is; a higher value wins."""

    ASK = 1
    DENY = 2


@dataclass(frozen=True, slots=True)
class Rule:
    """One guard rule: what it decides and the sentence that explains it."""

    decision: Decision
    because: str


RULES: dict[str, Rule] = {
    "guard-path-edit": Rule(
        Decision.ASK,
        "edits a guard path; only the owner approves a gate change",
    ),
    "guard-path-locked": Rule(
        Decision.DENY,
        "edits a guard path that only the owner changes, by hand, outside Claude Code",
    ),
    "guard-path-write": Rule(
        Decision.DENY,
        "writes, moves or deletes a guard path from the shell",
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
        "merges a pull request; the merge job or the owner merges one",
    ),
    "merge-default": Rule(
        Decision.ASK,
        "merges into the default branch; the `merge` entry point or the owner does",
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
    "unreadable": Rule(Decision.ASK, "could not be checked by the guard"),
}


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


def strictest(verdicts: Iterable[Verdict]) -> Verdict | None:
    """The first verdict with the strictest decision, or ``None`` when no rule fired."""
    return max(verdicts, key=lambda verdict: verdict.decision, default=None)
