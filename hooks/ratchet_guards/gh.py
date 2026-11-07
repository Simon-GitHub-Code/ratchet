"""Rules for the GitHub CLI: merges, branch protection, repository settings, labels and runs.

The agent's ``gh`` runs as the owner, so GitHub cannot tell the two apart; these rules are
where the difference is drawn.
"""

import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass

from ratchet_guards.options import Spec, scan
from ratchet_guards.verdict import Verdict

_REPO_FLAGS = frozenset({"-R", "--repo", "--hostname"})
_COMMANDS = {
    ("pr", "merge"): "merge-pr",
    ("repo", "edit"): "repo-settings",
    ("repo", "delete"): "repo-settings",
    ("repo", "rename"): "repo-settings",
    ("repo", "archive"): "repo-settings",
    ("repo", "unarchive"): "repo-settings",
    ("secret", "set"): "repo-settings",
    ("secret", "delete"): "repo-settings",
    ("secret", "remove"): "repo-settings",
    ("variable", "set"): "repo-settings",
    ("variable", "delete"): "repo-settings",
    ("workflow", "disable"): "repo-settings",
    ("run", "delete"): "workflow-runs",
    ("run", "cancel"): "workflow-runs",
}
_API = Spec(
    "XfFHqtp",
    frozenset(
        {"--method", "--field", "--raw-field", "--header", "--input", "--jq", "--template"}
        | {"--preview", "--hostname", "--cache"}
    ),
)
_BODY_FLAGS = frozenset({"-f", "-F", "--field", "--raw-field", "--input"})
_PROTECTION = re.compile(r"branches/[^/\s]+/protection|rulesets?\b|branchprotectionrule")
_MERGE_ENDPOINT = re.compile(r"pulls/\d+/merge/?$")
_MERGE_MUTATION = re.compile(r"mergepullrequest|enablepullrequestautomerge")
_LABELS = re.compile(r"(^|/)labels(/|$)")
_REPOSITORY = re.compile(r"^/?repos/[^/]+/[^/]+/?$")
_SETTINGS = re.compile(r"actions/(secrets|variables|permissions)|actions/workflows/.+/disable")
_RUN_CANCEL = re.compile(r"actions/runs/.*cancel/?$")


def _operands(words: Sequence[str]) -> list[str]:
    operands: list[str] = []
    remaining = iter(words)
    for word in remaining:
        if word in _REPO_FLAGS:
            next(remaining, None)
        elif not word.startswith("-"):
            operands.append(word)
    return operands


def _method(values: dict[str, str], flags: frozenset[str]) -> str:
    method = values.get("-X") or values.get("--method")
    if method:
        return method.upper()
    return "POST" if flags & _BODY_FLAGS else "GET"


@dataclass(frozen=True, slots=True)
class _Call:
    endpoint: str
    method: str
    text: str

    @property
    def writes(self) -> bool:
        return self.method != "GET"


_API_RULES: list[tuple[str, Callable[[_Call], bool]]] = [
    ("branch-protection", lambda c: bool(_PROTECTION.search(c.endpoint + " " + c.text))),
    (
        "merge-pr",
        lambda c: bool(
            (c.writes and _MERGE_ENDPOINT.search(c.endpoint)) or _MERGE_MUTATION.search(c.text)
        ),
    ),
    (
        "workflow-runs",
        lambda c: (
            "actions/runs" in c.endpoint
            and (c.method == "DELETE" or bool(_RUN_CANCEL.search(c.endpoint)))
        ),
    ),
    ("labels", lambda c: c.writes and bool(_LABELS.search(c.endpoint))),
    (
        "repo-settings",
        lambda c: c.writes and bool(_REPOSITORY.search(c.endpoint) or _SETTINGS.search(c.endpoint)),
    ),
]


def _api_rule(endpoint: str, method: str, text: str) -> str | None:
    call = _Call(endpoint, method, text)
    return next((rule for rule, fires in _API_RULES if fires(call)), None)


def _api(args: Sequence[str]) -> list[Verdict]:
    options = scan(args, _API)
    if not options.operands:
        return []
    endpoint = options.operands[0].lower()
    method = _method(options.values, options.flags)
    text = " ".join(args).lower()
    rule = _api_rule(endpoint, method, text)
    return [] if rule is None else [Verdict(rule, f"gh api {method} {endpoint}")]


def check(words: Sequence[str]) -> list[Verdict]:
    """Verdicts for one ``gh`` command."""
    operands = _operands(words[1:])
    if not operands:
        return []
    group = operands[0]
    if group == "api":
        return _api(words[words.index("api") + 1 :])
    action = operands[1] if len(operands) > 1 else ""
    rule = "labels" if group == "label" else _COMMANDS.get((group, action))
    return [] if rule is None else [Verdict(rule, f"gh {group} {action}")]
