"""Rules for the GitHub CLI: merges, branch protection, repository settings, labels and runs.

The agent's ``gh`` runs as the owner, so GitHub cannot tell the two apart; these rules are
where the difference is drawn. Reads pass: ``gh label list`` and a ``GET`` on a labels
endpoint are how an agent learns what the owner has set up.

Label creation has one sanctioned path. ``gh label create <name>`` on the current checkout's
own repository passes when ``docs/agents/triage-labels.md`` at the checkout root lists
``<name>`` in its "Label in our tracker" column: the record the mattpocock-skills setup writes
from the owner's answer, and a guard path in the decisions group once the repository is
adopted. Creating a label routes nothing by itself; the list is the owner's; and a label named
nowhere in the record is still refused. ``--force`` edits an existing label, ``-R`` names
another repository, and both stay refused.
"""

import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path

from ratchet_guards.context import Context, Guard
from ratchet_guards.options import Spec, scan
from ratchet_guards.verdict import Verdict

_REPO_FLAGS = frozenset({"-R", "--repo", "--hostname"})
# Why: `gh label list` is a read and `gh label` alone prints help; only the verbs that change a
# label are the owner's.
_LABEL_WRITES = frozenset({"create", "edit", "delete", "clone"})
_FORCE = frozenset({"-f", "--force"})
_LABEL_CREATE = Spec("Rcd", _REPO_FLAGS | {"--color", "--description"})
LABELS_RECORD = Path("docs/agents/triage-labels.md")
_TRACKER_COLUMN = "label in our tracker"
_RULE_CELLS = frozenset("-: ")
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


def _cells(line: str) -> list[str]:
    return [cell.strip().strip("`").strip() for cell in line.strip().strip("|").split("|")]


def listed_labels(record: str) -> frozenset[str]:
    """The "Label in our tracker" column of the triage-labels record, backticks removed."""
    column: int | None = None
    labels: set[str] = set()
    for line in record.splitlines():
        if not line.lstrip().startswith("|"):
            continue
        cells = _cells(line)
        if column is None:
            headers = [cell.lower() for cell in cells]
            column = headers.index(_TRACKER_COLUMN) if _TRACKER_COLUMN in headers else None
        elif column < len(cells) and not set(cells[column]) <= _RULE_CELLS:
            labels.add(cells[column])
    return frozenset(labels)


def _recorded(name: str, context: Context, guard: Guard) -> bool:
    """Whether the checkout holding ``context.cwd`` records ``name`` as a triage label."""
    filesystem = guard.locator.filesystem
    root = filesystem.checkout(filesystem.canonical(context.cwd))
    if root is None:
        return False
    record = filesystem.read_text(root / LABELS_RECORD)
    return record is not None and name in listed_labels(record)


def _label_create(words: Sequence[str], context: Context, guard: Guard) -> bool:
    """Whether this ``gh label create`` is the sanctioned one: a recorded label, here, no force."""
    options = scan(words[1:], _LABEL_CREATE)
    names = options.operands[2:]
    if len(names) != 1 or options.flags & (_REPO_FLAGS | _FORCE):
        return False
    return _recorded(names[0], context, guard)


def check(words: Sequence[str], context: Context, guard: Guard) -> list[Verdict]:
    """Verdicts for one ``gh`` command run in ``context``."""
    operands = _operands(words[1:])
    if not operands:
        return []
    group = operands[0]
    if group == "api":
        return _api(words[words.index("api") + 1 :])
    action = operands[1] if len(operands) > 1 else ""
    if group == "label":
        rule = "labels" if action in _LABEL_WRITES else None
        if action == "create" and _label_create(words, context, guard):
            rule = None
    else:
        rule = _COMMANDS.get((group, action))
    return [] if rule is None else [Verdict(rule, f"gh {group} {action}")]
