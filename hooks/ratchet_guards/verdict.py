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
ALLOW = "Allow it?"
NAME_THE_DIRECTORY = (
    "Give the file a path the guard can follow, an absolute one or one after a `cd` to a literal "
    "directory, and it is judged where it lands."
)
WITHOUT_REWRITING = (
    "Bring the branch up to date without rewriting it: merge the default branch into it, or "
    "add a new commit. The merge job lands it, or with no forge the `merge` entry point does."
)
EXPANDED_BODY = (
    "The command sits in a heredoc body whose delimiter `{0}` is unquoted, so bash runs it; "
    "quote the delimiter (`'{0}'`) and bash keeps the body as text."
)
OWNERS_HAND = (
    "A merge the judge leaves to the owner is the owner's to make, in the forge's interface or "
    "their own terminal: report its reasons to them."
)
WAITS = "`just merge-ready --head <branch>` prints each reason one waits, one a line."
LOCALLY = (
    "Where the default branch tracks a remote, `git pull --ff-only` brings it up to date. To try "
    "a merge, a cherry-pick or a read-tree, first move to a detached `HEAD` (`git switch "
    "--detach`) or a scratch branch in a tool call of its own, and run it there."
)
SWITCH_ALONE = (
    "Run the branch switch in a tool call of its own and make the change in the next one: the "
    "guard reads the branch then."
)


class Decision(IntEnum):
    ASK = 1
    DENY = 2


@dataclass(frozen=True, slots=True)
class Rule:
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
    "guard-path-unseen-switch": Rule(
        Decision.DENY,
        "changes a guard path after a command that may switch branches in a way the guard cannot "
        "read, so the change may land on the default branch",
        SWITCH_ALONE,
    ),
    "no-verify": Rule(Decision.DENY, "skips the git hooks"),
    "queue-final-check": Rule(
        Decision.DENY,
        "a commit in a queue work tree before `drain.py check <ticket> --final` has passed, or on "
        "a one-ticket run's branch before `publish_check.py` has passed, leaves nothing to tell "
        "the orchestrator's commit from a role's",
        "First run `drain.py check <ticket> --final` in a drain, or `publish_check.py` in a "
        "one-ticket run, and commit when it is green.",
    ),
    "skip-hooks": Rule(Decision.DENY, "skips or uninstalls the pre-commit hooks"),
    "hooks-path": Rule(Decision.DENY, "redirects git hooks or overrides git config"),
    "force-push": Rule(
        Decision.DENY, "force-pushes, which rewrites published history", WITHOUT_REWRITING
    ),
    "push-main": Rule(
        Decision.DENY,
        "pushes to the default branch directly, where the method lands a change only through a "
        "merged pull request",
        "Push a branch and open a pull request instead.",
    ),
    "reset-main": Rule(Decision.DENY, "moves main back or sideways with a reset"),
    "default-ref": Rule(
        Decision.DENY,
        "moves, deletes or re-points the default branch, or a ref or setting it is read through",
    ),
    "amend-pushed": Rule(
        Decision.DENY, "amends a commit that is already pushed", WITHOUT_REWRITING
    ),
    "rewrite-history": Rule(Decision.DENY, "rewrites history"),
    "merge-pr": Rule(
        Decision.DENY,
        "merges a pull request, which in a project on the method only the merge job or the owner "
        "does",
        "Leave it to the merge job: it merges a pull request by itself once its checks pass and "
        "nothing in it needs the owner, and `just merge-ready --head <branch>` prints each reason "
        f"one waits, one a line. {OWNERS_HAND}",
    ),
    "merge-pr-unadopted": Rule(
        Decision.ASK,
        "merges a pull request",
        "The merge job merges a pull request by itself once its checks pass and nothing in it "
        "needs you. Once they pass, it comments on the pull request with each reason it waits for "
        "you, one a line, and `just merge-ready --head <branch>` prints the same reasons here.",
    ),
    "merge-default": Rule(
        Decision.DENY,
        "merges another branch into the default branch, which in a project on the method only "
        "the merge entry point, the merge job or the owner does",
        "Leave it to what merges by itself: with no forge `just merge <branch>` merges a branch "
        "when the merge judge and `check` pass it, and on a forge the merge job merges its pull "
        f"request. {WAITS} {OWNERS_HAND} {LOCALLY}",
    ),
    "merge-pr-no-job": Rule(
        Decision.DENY,
        "merges a pull request, which in a project on the method with no merge job only the "
        "owner does",
        "The default branch holds no merge job, so nothing merges a pull request by itself: the "
        "owner merges it, in the forge's interface or their own terminal. `just merge-ready "
        "--head <branch>` prints each reason the merge judge gives, one a line: report them to "
        "the owner.",
    ),
    "merge-default-no-job": Rule(
        Decision.DENY,
        "merges another branch into the default branch, which in a project on the method with "
        "no merge job only the merge entry point or the owner does",
        "With no forge `just merge <branch>` merges a branch when the merge judge and `check` "
        "pass it. The default branch holds no merge job, so on a forge the owner merges. "
        f"{WAITS} {OWNERS_HAND} {LOCALLY}",
    ),
    "merge-default-unadopted": Rule(
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
        "This marks a ticket ready for agents to build without you, which is yours to decide",
        ALLOW,
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
    "unreadable": Rule(
        Decision.ASK,
        "The guard could not check this tool call",
        "Allow it only if you know what it does.",
    ),
    "unresolved-directory": Rule(
        Decision.DENY,
        "writes a relative path after a cd the guard cannot follow; it may be a guard path that "
        "only the owner changes, or one on the default branch",
        NAME_THE_DIRECTORY,
    ),
}


ACTING_RULES = {Kind.WORKFLOW: "workflow-write", Kind.INSTRUCTIONS: "instructions-write"}
UNADOPTED = {"merge-pr": "merge-pr-unadopted", "merge-default": "merge-default-unadopted"}
NO_MERGE_JOB = {"merge-pr": "merge-pr-no-job", "merge-default": "merge-default-no-job"}
OWNER_ASKS = frozenset({*ACTING_RULES.values(), "agent-ready-label", "unreadable"})


@dataclass(frozen=True, slots=True)
class Verdict:
    rule: str
    detail: str
    source: str = ""
    heredoc: str = ""

    @property
    def decision(self) -> Decision:
        return RULES[self.rule].decision

    @property
    def because(self) -> str:
        return RULES[self.rule].because

    @property
    def way_out(self) -> str:
        way_out = RULES[self.rule].way_out
        return f"{EXPANDED_BODY.format(self.heredoc)} {way_out}" if self.heredoc else way_out


def strictest(verdicts: Iterable[Verdict]) -> Verdict | None:
    return max(verdicts, key=lambda verdict: verdict.decision, default=None)
