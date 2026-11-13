#!/usr/bin/env python3
"""The feature preflight: what must be true before the pipeline spawns its first agent.

The input of ``/ratchet:feature`` is a tracker ticket (``mattpocock-skills:to-spec``,
``to-tickets`` or ``triage`` wrote it): a number on GitHub or GitLab, or the path of a local
ticket file. The preflight is a pure check over that ticket and the checkout, red with the exact
failure and the skill that produces the missing piece:

- the ticket exists and is open, and a local one is ``.scratch/<feature>/issues/<name>.md``, the
  one file the guard asks the owner about before an agent writes the agent-ready label;
- it carries a ``## Conjecture`` section that states something (the method's one addition to
  the ticket: the approach, the observation that would refute it, the alternatives and the
  criticism that kills each), on the ticket itself or on its parent spec, since ``to-tickets``
  cuts one spec into several tickets that share one approach. The parent is the one a
  ``Parent:`` line or a ``## Parent`` section names, else, for a local ticket under
  ``<feature>/issues/``, the ``<feature>/spec.md`` beside that directory;
- no ticket its ``Blocked by`` line or section names is still open;
- it carries the owner's agent-ready label, the ``ready-for-agent`` role as
  ``docs/agents/triage-labels.md`` maps it (a local ticket: a ``Status:`` line with that label);
- the working tree is clean;
- in an adopted repository the ticket, or its parent, says how the change is operated under
  ``## Operability``: a ``Flag:``, ``Signal:``, ``Load:`` and ``Security:`` line, each asked only
  when the binding declares an application type its question is asked of
  (``skills/method/application-types.toml``) and its row is not bound ``later: ...`` (the
  ``Flag:`` line waits for the ``deployed`` life event instead), and each a statement or ``none``
  with the reason (ADR 0028);
- with ``--branch``, ``HEAD`` is that branch and it is not the base branch;
- in an adopted repository (``docs/process/binding.md``), a commit would run tier 0:
  ``hooks_installed.py`` proves it by running the commit hooks, and its failure carries the
  owner's command as the ``produce``, since the guard refuses hook installs from an agent.

The output is JSON: ``ok``, ``failures`` (each with ``check``, ``detail`` and ``produce``, the
skill or action that supplies it), the ticket's ``number``, ``title`` and ``slug``, and the
``branch`` derived from them (``feature/<number>-<slug>``), and the ``base`` it starts from:
``--base``, else the default branch as ``gatelib`` reads it. ``critic`` is true only when the
binding declares the ``depended-on`` life event, and ``critic_reason`` says why either way: the
pipeline runs its critique and fix phases only then (ADR 0028). The ``/ratchet:feature`` skill runs
it before the workflow, so a red preflight spawns nothing; the workflow runs it again as its
first stage, so the pipeline cannot be entered around the skill.

Usage: ``feature_preflight.py <ref> [--root DIR] [--base BRANCH] [--branch NAME]``.
"""

from __future__ import annotations

import argparse
import json
import posixpath
import re
import subprocess
import sys
from collections.abc import Callable, Sequence
from dataclasses import asdict, dataclass, field
from pathlib import Path

import application_types
import gatelib
import hooks_installed

# Why: a local ticket is the file the guard asks about the label on; both read its shape here.
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "hooks"))
from ratchet_guards.tickets import TICKET_SUFFIX, TICKETS_DIRECTORY, TRACKER_DIRECTORY

CONJECTURE = "## Conjecture"
OPERABILITY = "## Operability"
# Why: ADR 0012 asks telemetry, flags, security and load of a change. A ticket's line is
# asked when the catalogue asks its question of a type the binding declares, so a library's
# ticket is asked none and a command's only its flag; and when its row is not bound `later:`
# (ADR 0028), since a row that waits judges nothing yet.
OPERABILITY_LINES = (
    ("Flag", "changed flags off and on", "the flag the behaviour ships behind"),
    ("Signal", "telemetry emitted", "the span, metric or log that shows it working"),
    ("Load", "load smoke", "the route or input the load gates gain"),
    ("Security", "dynamic security scan", "the surface it adds: routes, inputs, who may call"),
)
# Why: the Flag line is asked of the owner once others can reach what ships, not on its row.
FLAG_LINE = "Flag"
FLAG_EVENT = "deployed"
CRITIC_EVENT = "depended-on"
LATER = re.compile(r"^\|\s*([^|]+?)\s*\|\s*`?later:", re.MULTILINE | re.IGNORECASE)
GATES = "## Gates"
READY_ROLE = "ready-for-agent"
LABELS_RECORD = Path("docs/agents/triage-labels.md")
ROLE_COLUMN = "label in mattpocock/skills"
TRACKER_COLUMN = "label in our tracker"
EMPTY_BODIES = frozenset({"", "tbd", "todo", "n/a", "none", "-", "..."})
PLACEHOLDER = re.compile(r"<[^<>\n]{2,}>")
MIN_BODY = 40
SLUG_WIDTH = 40
CLOSED_STATUSES = frozenset({"resolved", "closed", "done", "wontfix"})
STATUS_LINE = re.compile(
    r"^\*{0,2}status:?\*{0,2}:?\s*`?([^`\n]+?)`?\s*$", re.IGNORECASE | re.MULTILINE
)
PARENT_LINE = re.compile(
    r"^\*{0,2}parent:?\*{0,2}:?\s*`?(#?\d+|[^`\s]+\.md)`?", re.IGNORECASE | re.MULTILINE
)
PARENT_SECTION = "## Parent"
SECTION_REF = re.compile(r"(?:#|/issues/)(\d+)|([^`\s()\[\]]+\.md)")
BLOCKED_LINE = re.compile(r"^\*{0,2}blocked by:?\*{0,2}:?[ \t]*(.*)$", re.IGNORECASE | re.MULTILINE)
BLOCKED_SECTION = "## Blocked by"
BLOCKER_REF = re.compile(r"[^`\s,()]+\.md|#?\d+")
Runner = Callable[[Sequence[str]], str | None]

PRODUCES = {
    "ticket": "mattpocock-skills:to-spec, to-tickets or triage writes the ticket on the tracker",
    "open": "a closed ticket is done or rejected; pick an open one",
    "conjecture": (
        "the owner adds `## Conjecture` to the ticket, or to its parent spec:"
        " `/ratchet:conjecture <ticket>` drafts it (the approach, the observation that would"
        " refute it, the alternatives and the criticism that kills each)"
    ),
    "operability": (
        "`/ratchet:conjecture <ticket>` writes `## Operability` on the ticket, or on its parent"
        " spec for all its tickets, from the binding and the record"
    ),
    "label": "the owner applies the agent-ready label (mattpocock-skills:triage, or by hand)",
    "blocked": (
        "build the blocking tickets first; when the order no longer holds, the owner edits the"
        " ticket's Blocked by"
    ),
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
        if match is not None:
            parent = match.group(1)
        else:
            found = SECTION_REF.search(section(self.body, PARENT_SECTION))
            if found is None:
                return None
            parent = f"#{found.group(1)}" if found.group(1) else found.group(2)
        if parent.endswith(".md") and not parent.startswith("#"):
            return str(Path(self.ref).parent / parent) if "/" in self.ref else parent
        return parent

    @property
    def blockers(self) -> tuple[str, ...]:
        """The refs the ``Blocked by`` line or section names, parentheses and "none" ignored."""
        match = BLOCKED_LINE.search(self.body)
        text = match.group(1) if match else section(self.body, BLOCKED_SECTION)
        refs = BLOCKER_REF.findall(re.sub(r"\([^)]*\)", " ", text))
        if self.ref.endswith(".md"):
            return tuple(refs)
        # Why: on a forge only `#N` names an issue; a bare number is part of a title.
        return tuple(ref for ref in refs if ref.startswith("#"))

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
    base: str = ""
    label: str = READY_ROLE
    critic: bool = False
    critic_reason: str = ""

    def fail(self, check: str, detail: str, produce: str = "") -> None:
        """Record one failed check."""
        self.ok = False
        self.failures.append(Failure(check, detail, produce))


# --- the ticket ------------------------------------------------------------------------------


def run_gh(args: Sequence[str]) -> str | None:
    """``gh args`` stdout, or ``None`` when gh fails or is missing."""
    try:
        completed = subprocess.run(["gh", *args], capture_output=True, text=True, check=False)
    except OSError:
        return None
    return completed.stdout if completed.returncode == 0 else None


def numbered_locally(ref: str, root: Path) -> list[str]:
    """The local ticket files whose name starts with the number ``ref`` gives (``03``, ``#3``)."""
    number = ref.lstrip("#")
    if not number.isdigit():
        return []
    return sorted(
        path.relative_to(root).as_posix()
        for path in root.glob(".scratch/*/issues/*.md")
        if (lead := re.match(r"\d+", path.name)) and int(lead.group()) == int(number)
    )


def at_a_ticket_path(ref: str, root: Path) -> bool:
    """Whether the local file ``ref`` is ``.scratch/<feature>/issues/<name>.md`` under ``root``.

    The guard asks the owner before an agent writes the agent-ready label only on such a file,
    so on any other file the label may be the agent's own without an ask. ``ref`` must have that
    shape both as written, with ``..`` folded, and as the filesystem resolves it, so no link
    leads elsewhere, and no hard link may give it a second name the label could be written
    through. The ask is a courtesy on the honest path; catching a label the owner did not give
    is the merge judge's job (``ratchet_guards.tickets``).
    """
    if (root / ref).stat().st_nlink > 1:
        return False
    readings = (
        (Path(posixpath.normpath(root / ref)), root),
        ((root / ref).resolve(), root.resolve()),
    )
    for path, base in readings:
        if not path.is_relative_to(base):
            return False
        parts = path.relative_to(base).parts
        if not (
            len(parts) == 4  # noqa: PLR2004 # -- .scratch, the feature, issues, the ticket
            and parts[0] == TRACKER_DIRECTORY
            and parts[2] == TICKETS_DIRECTORY
            and parts[3].endswith(TICKET_SUFFIX)
        ):
            return False
    return True


def fetch(ref: str, root: Path, gh: Runner) -> Ticket | None:
    """The ticket behind ``ref``: a local file, or an issue by number through ``gh``.

    Where the tracker answers nothing, a number is the one local file whose name it starts.
    """
    path = root / Path(ref)
    if path.is_file():
        return local_ticket(Path(ref).as_posix(), path.read_text(encoding="utf-8"))
    number = ref.lstrip("#")
    if not number.isdigit():
        return None
    output = gh(["issue", "view", number, "--json", "number,title,body,labels,state"])
    if output is None:
        # Why: with no tracker a number can only mean a local file; a second match is ambiguous
        # and the caller names the candidates (see numbered_locally).
        found = numbered_locally(ref, root)
        return fetch(found[0], root, gh) if len(found) == 1 else None
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
        is_open=not any(label.lower() in CLOSED_STATUSES for label in labels),
    )


# --- the checks ------------------------------------------------------------------------------


def section(text: str, heading: str) -> str:
    """The body under ``heading`` up to the next heading of the same or higher level."""
    level = len(heading) - len(heading.lstrip("#"))
    pattern = rf"^{re.escape(heading)}\s*$\n(.*?)(?=^#{{1,{level}}}\s|\Z)"
    match = re.search(pattern, text, re.MULTILINE | re.DOTALL)
    return match.group(1) if match else ""


def unfilled(text: str, template: str = "") -> str | None:
    """Why ``text`` is still a template, or ``None`` when it states something.

    Why: real text quotes angle brackets (a span named ``<METHOD> <route template>``, a body
    ``{"text": "<string>"}``), so a placeholder is a line that holds nothing else, or that repeats
    the template's own words; a blank, a stub word or a bare "none" says nothing.
    """
    text = text.strip()
    if text.lower().rstrip(".") in EMPTY_BODIES:
        return "nothing"
    if template and text.rstrip(".").lower() == template.lower():
        return "the template's own words"
    first = PLACEHOLDER.search(text)
    if first and not re.search(r"\w", PLACEHOLDER.sub("", text)):
        return f"only the placeholder `{first.group()}`"
    return None


def says_something(text: str) -> bool:
    """Whether ``text`` is a statement: not blank, not only placeholders, not a bare "none"."""
    return unfilled(text) is None


def states_a_conjecture(body: str) -> bool:
    """Whether the Conjecture section says something: not blank, placeholder or a stub."""
    return says_something(body) and len(body.strip()) >= MIN_BODY


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
    words = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")
    if len(words) > SLUG_WIDTH:
        cut = words[: SLUG_WIDTH + 1]
        words = cut.rsplit("-", 1)[0] if "-" in cut else cut[:SLUG_WIDTH]
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
    parent_ref = parent_of(ticket, root)
    if parent_ref is None:
        return f"{ticket.ref} has no `{CONJECTURE}` section and names no parent"
    parent = fetch(parent_ref, root, gh)
    if parent is None:
        return f"{ticket.ref} has no `{CONJECTURE}` section; its parent {parent_ref} was not found"
    inherited = parent.conjecture
    if inherited is not None and states_a_conjecture(inherited):
        return None
    return f"{ticket.ref} has no `{CONJECTURE}` section and its parent {parent_ref} states none"


def binding_text(root: Path) -> str | None:
    """The binding in the checkout, or ``None`` before adoption."""
    binding = root / gatelib.BINDING
    return binding.read_text(encoding="utf-8") if binding.is_file() else None


def operability_asked(root: Path) -> list[tuple[str, str]]:
    """The ``## Operability`` lines asked here, each with what it says; none before adoption.

    A line is asked when its question is asked of a declared type and its row is not waiting
    (``later: ...``); the ``Flag`` line is asked once ``deployed`` is declared.
    """
    text = binding_text(root)
    if text is None:
        return []
    kinds = frozenset(application_types.declared(text) or ())
    asked_of = application_types.load().asked_of
    waiting = {question.casefold() for question in LATER.findall(section(text, GATES))}
    live = FLAG_EVENT in application_types.life_events(text)
    return [
        (name, says)
        for name, question, says in OPERABILITY_LINES
        if asked_of(question) & kinds
        and (live if name == FLAG_LINE else question.casefold() not in waiting)
    ]


def operability_failure(ticket: Ticket, root: Path, gh: Runner) -> str | None:
    """Which asked lines neither the ticket nor its parent states; ``None`` when none is."""
    asked = operability_asked(root)
    if not asked:
        return None
    block = section(ticket.body, OPERABILITY)
    parent_ref = parent_of(ticket, root)
    if not block.strip() and parent_ref is not None:
        parent = fetch(parent_ref, root, gh)
        block = section(parent.body, OPERABILITY) if parent is not None else ""
    lacking, templated = [], []
    for name, says in asked:
        cause = unfilled("".join(operability_line(name).findall(block)[:1]), says)
        if cause == "nothing":
            lacking.append(f"`{name}:` ({says})")
        elif cause is not None:
            templated.append(f"has `{name}:` with {cause}")
    parts = ([f"lacks {', '.join(lacking)}"] if lacking else []) + templated
    if not parts:
        return None
    return f"{ticket.ref}: `{OPERABILITY}` {'; '.join(parts)}; write each, or `none` and why"


def operability_line(name: str) -> re.Pattern[str]:
    """One line of the block, as a list item or bare, bold or plain, its value captured."""
    return re.compile(
        rf"^[-* \t]*\*{{0,2}}{name}:?\*{{0,2}}:?[ \t]*(.*)$", re.IGNORECASE | re.MULTILINE
    )


def parent_of(ticket: Ticket, root: Path) -> str | None:
    """The ref of the spec ``ticket`` inherits its conjecture and operability from, if any."""
    return ticket.parent or beside_spec(ticket.ref, root)


def reads(ticket: Ticket, root: Path) -> tuple[frozenset[str], frozenset[str]]:
    """Every local file the preflight reads to decide whether, and what, to build ``ticket``.

    The first set it reads whole: the ticket and the spec it inherits from. The second it reads
    for the ``Status:`` line alone: each ticket it is blocked by. A ref is normalised, and a
    number is every local ticket it could name. ADR 0031 holds both still while the ticket
    carries the label, and a test holds the preflight's readers to them.
    """

    def local(ref: str | None) -> frozenset[str]:
        if ref is None or not ref.endswith(".md"):
            return frozenset(numbered_locally(ref or "", root))
        return frozenset({posixpath.normpath(ref)})

    whole = {posixpath.normpath(ticket.ref), *local(parent_of(ticket, root))}
    status = frozenset().union(*(local(blocker_ref(ticket, ref, root)) for ref in ticket.blockers))
    return frozenset(whole), status - whole


def beside_spec(ref: str, root: Path) -> str | None:
    """The ``spec.md`` of a local ticket filed under ``<feature>/issues/``, when it exists."""
    path = Path(ref)
    if path.parent.name != "issues":
        return None
    spec = path.parent.parent / "spec.md"
    return spec.as_posix() if (root / spec).is_file() else None


def blocker_ref(ticket: Ticket, ref: str, root: Path) -> str:
    """A blocker as a ref ``fetch`` reads: a sibling file for a local ticket, else a number."""
    folder = Path(ticket.ref).parent
    if ref.endswith(".md"):
        return (folder / ref).as_posix()
    if ref.startswith("#"):
        return ref
    siblings = sorted((root / folder).glob(f"{int(ref):02d}-*.md"))
    return siblings[0].relative_to(root).as_posix() if siblings else ref


def open_blockers(ticket: Ticket, root: Path, gh: Runner) -> list[str]:
    """Each blocker that is still open or cannot be found, as a sentence."""
    problems = []
    for ref in ticket.blockers:
        resolved = blocker_ref(ticket, ref, root)
        blocker = fetch(resolved, root, gh)
        if blocker is None:
            problems.append(f"blocker {ref} was not found")
        elif blocker.is_open:
            problems.append(f"blocker {resolved} is still open")
    return problems


def check_ticket(report: Report, ticket: Ticket, label: str, *, root: Path, gh: Runner) -> None:
    report.number, report.title = ticket.number, ticket.title
    report.slug = slug_of(ticket.title, ticket.number)
    report.branch = branch_of(ticket, report.slug)
    if not ticket.is_open:
        report.fail("open", f"{ticket.ref} is not open")
    failure = conjecture_failure(ticket, root, gh)
    if failure is not None:
        report.fail("conjecture", failure)
    failure = operability_failure(ticket, root, gh)
    if failure is not None:
        report.fail("operability", failure)
    if label not in ticket.labels:
        report.fail(
            "label", f"{ticket.ref} does not carry `{label}`; labels: {list(ticket.labels)}"
        )
    for problem in open_blockers(ticket, root, gh):
        report.fail("blocked", f"{ticket.ref}: {problem}")


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


def check_critic(report: Report, root: Path) -> None:
    """Say whether the pipeline critiques: only a project people depend on (ADR 0028)."""
    text = binding_text(root)
    report.critic = text is not None and CRITIC_EVENT in application_types.declared_or_all(text)
    report.critic_reason = (
        f"the binding declares `{CRITIC_EVENT}`: others rely on it, so the change is attacked"
        " before review"
        if report.critic
        else f"the binding declares no `{CRITIC_EVENT}` life event: nobody relies on it yet, so"
        " the change goes spec, ticket check, implement, review"
    )


def check_hooks(report: Report, root: Path) -> None:
    """Red when the repository is adopted and a commit would skip tier 0."""
    if not (root / gatelib.BINDING).is_file():
        return
    problem = hooks_installed.check(root)
    if problem is not None:
        report.fail("hooks", problem.detail, problem.produce)


def preflight(ref: str, root: Path, *, base: str, branch: str | None, gh: Runner) -> Report:
    report = Report(ref=ref, base=base, label=ready_label(root))
    ticket = fetch(ref, root, gh)
    if ticket is None:
        report.fail("ticket", f"{ref}: no such ticket (a number on the tracker, or a file path)")
    elif (root / ticket.ref).is_file() and not at_a_ticket_path(ticket.ref, root):
        report.fail(
            "ticket",
            f"{ref}: a local ticket is a file at .scratch/<feature>/issues/<name>.md with no"
            " second name (a hard link), the one file the guard asks the owner about before an"
            " agent writes the agent-ready label",
            "/mattpocock-skills:to-tickets",
        )
    else:
        check_ticket(report, ticket, report.label, root=root, gh=gh)
    check_checkout(report, root, base=base, branch=branch)
    check_hooks(report, root)
    check_critic(report, root)
    return report


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "ref",
        help="the ticket: a number, #number, or a local file .scratch/<feature>/issues/<name>.md"
        " (a number may start its name)",
    )
    parser.add_argument("--root", default=".")
    parser.add_argument("--base", help="the branch features start from (the default branch)")
    parser.add_argument("--branch", help="the working branch HEAD must be on")
    options = parser.parse_args(argv[1:])
    root = Path(options.root).resolve()
    base = options.base or gatelib.default_branches(root)[0].removeprefix("origin/")
    report = preflight(
        options.ref,
        root,
        base=base,
        branch=options.branch,
        gh=run_gh,
    )
    print(json.dumps(asdict(report), indent=2))
    for failure in report.failures:
        print(f"preflight: {failure.check}: {failure.detail}", file=sys.stderr)
        print(f"  produce: {failure.produce}", file=sys.stderr)
    print(f"preflight: {'green' if report.ok else 'red'}", file=sys.stderr)
    return 0 if report.ok else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
