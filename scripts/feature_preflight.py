#!/usr/bin/env python3
"""The feature preflight: what must be true before the pipeline spawns its first agent.

The input of ``/ratchet:feature`` is a tracker ticket (``mattpocock-skills:to-spec``,
``to-tickets`` or ``triage`` wrote it): a number on GitHub or GitLab, or the path of a local
ticket file. The preflight is a pure check over that ticket and the checkout, red with the exact
failure and the skill that produces the missing piece:

- the ticket exists and is open;
- it carries a ``## Conjecture`` section that states something (the method's one addition to
  the ticket: the approach, the observation that would refute it, the alternatives and the
  criticism that kills each), on the ticket itself or on the parent spec its ``Parent:`` line
  names, since ``to-tickets`` cuts one spec into several tickets that share one approach;
- it carries the owner's agent-ready label, the ``ready-for-agent`` role as
  ``docs/agents/triage-labels.md`` maps it (a local ticket: a ``Status:`` line with that label);
- the working tree is clean;
- with ``--branch``, ``HEAD`` is that branch and it is not the base branch.

The output is JSON: ``ok``, ``failures`` (each with ``check``, ``detail`` and ``produce``, the
skill or action that supplies it), the ticket's ``number``, ``title`` and ``slug``, and the
``branch`` derived from them (``feature/<number>-<slug>``). The ``/ratchet:feature`` skill runs
it before the workflow, so a red preflight spawns nothing; the workflow runs it again as its
first stage, so the pipeline cannot be entered around the skill.

Usage: ``feature_preflight.py <ref> [--root DIR] [--base BRANCH] [--branch NAME]``.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from collections.abc import Callable, Sequence
from dataclasses import asdict, dataclass, field
from pathlib import Path

import gatelib

CONJECTURE = "## Conjecture"
READY_ROLE = "ready-for-agent"
LABELS_RECORD = Path("docs/agents/triage-labels.md")
ROLE_COLUMN = "label in mattpocock/skills"
TRACKER_COLUMN = "label in our tracker"
EMPTY_BODIES = frozenset({"", "tbd", "todo", "n/a", "none", "-", "..."})
MIN_BODY = 40
SLUG_WIDTH = 40
STATUS_LINE = re.compile(
    r"^\*{0,2}status:?\*{0,2}:?\s*`?([^`\n]+?)`?\s*$", re.IGNORECASE | re.MULTILINE
)
PARENT_LINE = re.compile(
    r"^\*{0,2}parent:?\*{0,2}:?\s*`?(#?\d+|[^`\s]+\.md)`?", re.IGNORECASE | re.MULTILINE
)
Runner = Callable[[Sequence[str]], str | None]

PRODUCES = {
    "ticket": "mattpocock-skills:to-spec, to-tickets or triage writes the ticket on the tracker",
    "open": "a closed ticket is done or rejected; pick an open one",
    "conjecture": (
        "the owner adds `## Conjecture` to the ticket, or to the spec its Parent line names:"
        " `/ratchet:conjecture <ticket>` drafts it (the approach, the observation that would"
        " refute it, the alternatives and the criticism that kills each)"
    ),
    "label": "the owner applies the agent-ready label (mattpocock-skills:triage, or by hand)",
    "clean": "commit or stash the working tree; the pipeline's agents leave their work uncommitted",
    "branch": "`git switch -c <branch> <base>`; the `/ratchet:feature` skill does this",
}


@dataclass(frozen=True)
class Ticket:
    """What the preflight knows about the input."""

    ref: str
    number: int | None
    title: str
    body: str
    labels: tuple[str, ...]
    is_open: bool

    @property
    def parent(self) -> str | None:
        """The ref of the parent spec the ticket names, if any."""
        match = PARENT_LINE.search(self.body)
        if match is None:
            return None
        parent = match.group(1)
        if parent.endswith(".md") and not parent.startswith("#"):
            return str(Path(self.ref).parent / parent) if "/" in self.ref else parent
        return parent

    @property
    def conjecture(self) -> str | None:
        """The body of the ticket's own ``## Conjecture`` section, or ``None`` without one."""
        return section(self.body, CONJECTURE) if CONJECTURE in self.body else None


@dataclass
class Failure:
    """One precondition that does not hold."""

    check: str
    detail: str
    produce: str = ""

    def __post_init__(self) -> None:
        """Default ``produce`` from the check's name."""
        self.produce = self.produce or PRODUCES[self.check]


@dataclass
class Report:
    """The preflight's result, printed as JSON."""

    ref: str
    ok: bool = True
    failures: list[Failure] = field(default_factory=list)
    number: int | None = None
    title: str = ""
    slug: str = ""
    branch: str = ""
    label: str = READY_ROLE

    def fail(self, check: str, detail: str) -> None:
        """Record one failed check."""
        self.ok = False
        self.failures.append(Failure(check, detail))


# --- the ticket ------------------------------------------------------------------------------


def run_gh(args: Sequence[str]) -> str | None:
    """``gh args`` stdout, or ``None`` when gh fails or is missing."""
    try:
        completed = subprocess.run(["gh", *args], capture_output=True, text=True, check=False)
    except OSError:
        return None
    return completed.stdout if completed.returncode == 0 else None


def fetch(ref: str, root: Path, gh: Runner) -> Ticket | None:
    """The ticket behind ``ref``: a local file, or an issue by number through ``gh``."""
    path = root / Path(ref)
    if path.is_file():
        return local_ticket(Path(ref).as_posix(), path.read_text(encoding="utf-8"))
    number = ref.lstrip("#")
    if not number.isdigit():
        return None
    output = gh(["issue", "view", number, "--json", "number,title,body,labels,state"])
    if output is None:
        return None
    try:
        data = json.loads(output)
    except ValueError:
        return None
    if not isinstance(data, dict):
        return None
    labels = data.get("labels")
    names = tuple(
        str(label.get("name", ""))
        for label in (labels if isinstance(labels, list) else [])
        if isinstance(label, dict)
    )
    return Ticket(
        ref=ref,
        number=int(number),
        title=str(data.get("title", "")),
        body=str(data.get("body", "")),
        labels=names,
        is_open=str(data.get("state", "")).upper() == "OPEN",
    )


def local_ticket(ref: str, text: str) -> Ticket:
    """A ticket file: the title is its first heading, its status line is its label."""
    heading = re.search(r"^#\s+(?:\d+:\s*)?(.+)$", text, re.MULTILINE)
    title = heading.group(1).strip() if heading else Path(ref).stem
    status = STATUS_LINE.search(text)
    labels = (status.group(1).strip(),) if status else ()
    number = re.match(r"(\d+)", Path(ref).stem)
    return Ticket(
        ref=ref,
        number=int(number.group(1)) if number else None,
        title=title,
        body=text,
        labels=labels,
        is_open=not any(label.lower() in {"resolved", "closed", "done"} for label in labels),
    )


# --- the checks ------------------------------------------------------------------------------


def section(text: str, heading: str) -> str:
    """The body under ``heading`` up to the next heading of the same or higher level."""
    level = len(heading) - len(heading.lstrip("#"))
    pattern = rf"^{re.escape(heading)}\s*$\n(.*?)(?=^#{{1,{level}}}\s|\Z)"
    match = re.search(pattern, text, re.MULTILINE | re.DOTALL)
    return match.group(1) if match else ""


def states_a_conjecture(body: str) -> bool:
    """Whether the Conjecture section says something: not blank, placeholder or a stub."""
    text = body.strip()
    if "<" in text and ">" in text and re.search(r"<[^>\n]{2,}>", text):
        return False
    return text.lower().rstrip(".") not in EMPTY_BODIES and len(text) >= MIN_BODY


def ready_label(root: Path) -> str:
    """The tracker's label for the ``ready-for-agent`` role, per the triage-labels record."""
    path = root / LABELS_RECORD
    if not path.is_file():
        return READY_ROLE
    columns: dict[str, int] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.lstrip().startswith("|"):
            continue
        cells = [c.strip().strip("`").strip() for c in line.strip().strip("|").split("|")]
        if not columns:
            columns = {cell.lower(): index for index, cell in enumerate(cells)}
            continue
        role, tracker = columns.get(ROLE_COLUMN), columns.get(TRACKER_COLUMN)
        if role is None or tracker is None or max(role, tracker) >= len(cells):
            continue
        if cells[role] == READY_ROLE and cells[tracker]:
            return cells[tracker]
    return READY_ROLE


def slug_of(title: str, number: int | None) -> str:
    words = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")[:SLUG_WIDTH].strip("-")
    return words or (f"ticket-{number}" if number else "feature")


def branch_of(ticket: Ticket, slug: str) -> str:
    return f"feature/{ticket.number}-{slug}" if ticket.number else f"feature/{slug}"


def conjecture_failure(ticket: Ticket, root: Path, gh: Runner) -> str | None:
    """Why the ticket states no conjecture, itself or through its parent; ``None`` when it does."""
    own = ticket.conjecture
    if own is not None:
        if states_a_conjecture(own):
            return None
        return f"{ticket.ref}: `{CONJECTURE}` states no conjecture"
    parent_ref = ticket.parent
    if parent_ref is None:
        return f"{ticket.ref} has no `{CONJECTURE}` section and names no parent"
    parent = fetch(parent_ref, root, gh)
    if parent is None:
        return f"{ticket.ref} has no `{CONJECTURE}` section; its parent {parent_ref} was not found"
    inherited = parent.conjecture
    if inherited is not None and states_a_conjecture(inherited):
        return None
    return f"{ticket.ref} has no `{CONJECTURE}` section and its parent {parent_ref} states none"


def check_ticket(report: Report, ticket: Ticket, label: str, *, root: Path, gh: Runner) -> None:
    report.number, report.title = ticket.number, ticket.title
    report.slug = slug_of(ticket.title, ticket.number)
    report.branch = branch_of(ticket, report.slug)
    if not ticket.is_open:
        report.fail("open", f"{ticket.ref} is not open")
    failure = conjecture_failure(ticket, root, gh)
    if failure is not None:
        report.fail("conjecture", failure)
    if label not in ticket.labels:
        report.fail(
            "label", f"{ticket.ref} does not carry `{label}`; labels: {list(ticket.labels)}"
        )


def check_checkout(report: Report, root: Path, *, base: str, branch: str | None) -> None:
    status = gatelib.git(root, "status", "--porcelain", "--untracked-files=all")
    if status is None:
        report.fail("clean", f"{root} is not a git checkout")
        return
    if status.strip():
        report.fail("clean", f"the working tree has changes:\n{status.strip()}")
    if branch is None:
        return
    head = gatelib.git(root, "rev-parse", "--abbrev-ref", "HEAD")
    if head != branch:
        report.fail("branch", f"HEAD is `{head}`, not `{branch}`")
    if branch == base:
        report.fail("branch", f"`{branch}` is the base branch")


def preflight(
    ref: str, root: Path, *, base: str, branch: str | None, gh: Runner = run_gh
) -> Report:
    report = Report(ref=ref, label=ready_label(root))
    ticket = fetch(ref, root, gh)
    if ticket is None:
        report.fail("ticket", f"{ref}: no such ticket (a number on the tracker, or a file path)")
    else:
        check_ticket(report, ticket, report.label, root=root, gh=gh)
    check_checkout(report, root, base=base, branch=branch)
    return report


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("ref", help="the ticket: a number, #number, or a local ticket file")
    parser.add_argument("--root", default=".")
    parser.add_argument("--base", default="main")
    parser.add_argument("--branch", help="the working branch HEAD must be on")
    options = parser.parse_args(argv[1:])
    report = preflight(
        options.ref, Path(options.root).resolve(), base=options.base, branch=options.branch
    )
    print(json.dumps(asdict(report), indent=2))
    for failure in report.failures:
        print(f"preflight: {failure.check}: {failure.detail}", file=sys.stderr)
        print(f"  produce: {failure.produce}", file=sys.stderr)
    print(f"preflight: {'green' if report.ok else 'red'}", file=sys.stderr)
    return 0 if report.ok else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
