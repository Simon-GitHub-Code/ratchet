#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import posixpath
import re
import subprocess
import sys
from collections.abc import Callable, Sequence
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Literal

import application_types
import change_size
import gatelib
import hooks_installed
import owed
import project_bite
import publish_check
import queue_notes
import run_time

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "hooks"))
from ratchet_guards import splits
from ratchet_guards.tickets import TICKET_SUFFIX, TICKETS_DIRECTORY, TRACKER_DIRECTORY

CONJECTURE = "## Conjecture"
OPERABILITY = "## Operability"
SIZE = "## Size"
SIZE_LINE = re.compile(
    r"^\s*[-*]\s*\*{0,2}Product (lines|files):?\*{0,2}:?\s*~?(\d[\d,]*)",
    re.MULTILINE | re.IGNORECASE,
)
OPERABILITY_LINES = (
    ("Flag", "changed flags off and on", "the flag the behaviour ships behind"),
    ("Signal", "telemetry emitted", "the span, metric or log that shows it working"),
    ("Load", "load smoke", "the route or input the load gates gain"),
    ("Security", "dynamic security scan", "the surface it adds: routes, inputs, who may call"),
)
FLAG_LINE = "Flag"
FLAG_SWITCH = re.compile(
    r"[*_\s]*`([^`\s]+)`[*_]*\W+off until the owner switches it on\W*", re.IGNORECASE
)
FLAG_APPEARS = re.findall(
    r"\w+",
    "none: the owner chose to have it appear as soon as it is finished, while the project is not"
    " deployed",
)
FLAG_KEY_WIDTH = 80
FENCE = re.compile(
    r"^[ \t]*(?P<fence>(?P<mark>[`~])(?P=mark){2,})[^\n]*$"
    r".*?(?:^[ \t]*(?P=fence)(?P=mark)*[ \t]*$|\Z)",
    re.MULTILINE | re.DOTALL,
)
CRITIC_EVENT = "depended-on"
DEPLOYED_EVENT = "deployed"
CRITERIA = "## Acceptance criteria"
CRITERION_ITEM = re.compile(
    r"^(?P<indent>[ \t]*)(?:[-*+]|\d+[.)])[ \t]+(?:\[[ xX]\][ \t]*)?(?P<text>.*)$"
)
CODE_SPAN = re.compile(r"`([^`\n]+)`")
LATER = re.compile(r"^\|\s*([^|]+?)\s*\|\s*`?later:", re.MULTILINE | re.IGNORECASE)
GATES = "## Gates"
READY_ROLE = "ready-for-agent"
LABELS_RECORD = Path("docs/agents/triage-labels.md")
ROLE_COLUMN = "label in mattpocock/skills"
TRACKER_COLUMN = "label in our tracker"
EMPTY_BODIES = frozenset({"", "tbd", "todo", "n/a"})
NO_REASON = frozenset({"", "none", "n/a"})
INVISIBLE = re.compile(r"[\u00ad\u200b-\u200f\u2060-\u2064\ufeff]")
EDGES = re.compile(
    r"^(?:<br\s*/?>|&#?\w+;|\d+[.)]\s|[\W_])+|(?:<br\s*/?>|&#?\w+;|[\W_])+$", re.IGNORECASE
)
NONE_AND = re.compile(r"none\b(.*)", re.IGNORECASE | re.DOTALL)
PLACEHOLDER = re.compile(r"<[^<>\n]{2,}>")
MIN_BODY = 40
SLUG_WIDTH = 40
CLOSED_STATUSES = frozenset({"resolved", "closed", "done", "wontfix"})
STATUS_LINE = re.compile(
    r"^\*{0,2}status:?\*{0,2}:?\s*`?([^`\n]+?)`?\s*$", re.IGNORECASE | re.MULTILINE
)
PARENT_LINE = re.compile(
    r"^\*{0,2}parent:?\*{0,2}:?\s*`?([^`\s]+\.md|#?\d+)`?", re.IGNORECASE | re.MULTILINE
)
PARENT_TO_LINE_END = re.compile(f"{PARENT_LINE.pattern}.*", PARENT_LINE.flags)
PARENT_SECTION = "## Parent"
SECTION_REF = re.compile(r"(?:#|/issues/)(\d+)|([^`\s()\[\]]+\.md)")
BLOCKED_LINE = re.compile(r"^\*{0,2}blocked by:?\*{0,2}:?[ \t]*(.*)$", re.IGNORECASE | re.MULTILINE)
BLOCKED_SECTION = "## Blocked by"
BLOCKER_REF = re.compile(r"[^`\s,()]+\.md|#?\d+")
FORMAL_LINE = re.compile(
    r"^\*{0,2}formal model:?\*{0,2}:?[ \t]*(.*)$", re.IGNORECASE | re.MULTILINE
)
FORMAL_DEFECT = re.compile(r"defect\s+`([^`\n]+)`", re.IGNORECASE)
FORMAL_ROUND = re.compile(r"round\b", re.IGNORECASE)
FORMAL_KEPT = re.compile(r"kept on\s+`([^`\n]+)`", re.IGNORECASE)
Runner = Callable[[Sequence[str]], str | None]

PRODUCES = {
    "ticket": (
        "/mattpocock-skills:to-spec, /mattpocock-skills:to-tickets or /mattpocock-skills:triage"
        " writes the ticket on the tracker"
    ),
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
    "parent": (
        "the owner points the ticket's `Parent:` line at its spec inside the repository, or at"
        " a ticket on the tracker"
    ),
    "size": (
        "the orchestrator splits the ticket itself: the parts as tickets that block each other in"
        " the tracker, the original marked split (`/ratchet:feature`, step 4, `tooLarge`)"
    ),
    "split": "build the parts it was split into; nothing is left to do for the ticket itself",
    "label": "the owner applies the agent-ready label (/mattpocock-skills:triage, or by hand)",
    "blocked": (
        "build the blocking tickets first; when the order no longer holds, the owner edits the"
        " ticket's Blocked by"
    ),
    "life-events": (
        "the owner writes the binding's `- Life events:` line in the spellings the refusal names"
    ),
    "clean": "commit or stash the working tree; the pipeline's agents leave their work uncommitted",
    "branch": "`git switch -c <branch> <base>`; the `/ratchet:feature` skill does this",
}


@dataclass(frozen=True)
class FormalModel:
    defect: str | None
    kept: str | None

    @property
    def round(self) -> bool:
        return self.defect is None


@dataclass(frozen=True)
class Ticket:
    ref: str
    number: int | None
    title: str
    body: str
    labels: tuple[str, ...]
    is_open: bool

    @property
    def parent(self) -> str | None:
        match = PARENT_LINE.search(self.body)
        if match is not None:
            parent = match.group(1)
        else:
            found = SECTION_REF.search(section(self.body, PARENT_SECTION))
            if found is None:
                return None
            parent = f"#{found.group(1)}" if found.group(1) else found.group(2)
        if parent.endswith(".md") and not parent.startswith("#") and "://" not in parent:
            folder = posixpath.dirname(self.ref) if "/" in self.ref else ""
            return posixpath.normpath(posixpath.join(folder, parent))
        return parent

    @property
    def blockers(self) -> tuple[str, ...]:
        match = BLOCKED_LINE.search(self.body)
        text = match.group(1) if match else section(self.body, BLOCKED_SECTION)
        refs = BLOCKER_REF.findall(re.sub(r"\([^)]*\)", " ", text))
        if self.ref.endswith(".md"):
            return tuple(refs)
        return tuple(ref for ref in refs if ref.startswith("#"))

    @property
    def formal_model(self) -> FormalModel | None:
        match = FORMAL_LINE.search(self.body)
        if match is None:
            return None
        text = match.group(1)
        kept = FORMAL_KEPT.search(text)
        defect = FORMAL_DEFECT.match(text)
        if defect is None and FORMAL_ROUND.match(text) is None:
            return None
        return FormalModel(
            defect.group(1).strip() if defect else None, kept.group(1).strip() if kept else None
        )

    @property
    def conjecture(self) -> str | None:
        return section(self.body, CONJECTURE) if CONJECTURE in self.body else None


@dataclass(frozen=True)
class Flag:
    form: Literal["hidden", "appears", "unasked"] = "unasked"
    key: str | None = None


@dataclass(frozen=True)
class NamedGate:
    gate: str
    tier: str
    criterion: str


@dataclass
class Failure:
    check: str
    detail: str
    produce: str = ""

    def __post_init__(self) -> None:
        self.produce = self.produce or PRODUCES[self.check]


@dataclass
class Report:
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
    parent: str | None = None
    deployed: bool = False
    flag: Flag = field(default_factory=Flag)
    start: publish_check.Start | None = None
    named_gates: list[NamedGate] = field(default_factory=list)

    def fail(self, check: str, detail: str, produce: str = "") -> None:
        self.ok = False
        self.failures.append(Failure(check, detail, produce))


def run_gh(args: Sequence[str]) -> str | None:
    try:
        completed = subprocess.run(["gh", *args], capture_output=True, text=True, check=False)
    except OSError:
        return None
    return completed.stdout if completed.returncode == 0 else None


def numbered_locally(ref: str, root: Path) -> list[str]:
    number = ref.lstrip("#")
    if not number.isdigit():
        return []
    return sorted(
        path.relative_to(root).as_posix()
        for path in root.glob(".scratch/*/issues/*.md")
        if (lead := re.match(r"\d+", path.name)) and int(lead.group()) == int(number)
    )


def at_a_ticket_path(ref: str, root: Path) -> bool:
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
    path = root / Path(ref)
    if path.is_file():
        return local_ticket(Path(ref).as_posix(), path.read_text(encoding="utf-8"))
    number = ref.lstrip("#")
    if not number.isdigit():
        return None
    output = gh(["issue", "view", number, "--json", "number,title,body,labels,state"])
    if output is None:
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


def section(text: str, heading: str) -> str:
    level = len(heading) - len(heading.lstrip("#"))
    pattern = rf"^{re.escape(heading)}\s*$\n(.*?)(?=^#{{1,{level}}}\s|\Z)"
    match = re.search(pattern, text, re.MULTILINE | re.DOTALL)
    return match.group(1) if match else ""


def bare(text: str) -> str:
    return EDGES.sub("", INVISIBLE.sub("", text)).lower()


def unfilled(text: str, template: str = "") -> str | None:
    value = bare(text)
    reason = NONE_AND.fullmatch(value)
    if value in EMPTY_BODIES or (reason is not None and bare(reason.group(1)) in NO_REASON):
        return "nothing"
    if template and value == bare(template):
        return "the template's own words"
    first = PLACEHOLDER.search(text)
    if first and not re.search(r"\w", PLACEHOLDER.sub("", text)):
        return f"only the placeholder `{first.group()}`"
    return None


def says_something(text: str) -> bool:
    return unfilled(text) is None


def states_a_conjecture(body: str) -> bool:
    return says_something(body) and len(body.strip()) >= MIN_BODY


def ready_label(root: Path) -> str:
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


FEATURE_BRANCHES = "feature/"


def branch_of(ticket: Ticket, slug: str) -> str:
    if ticket.number:
        return f"{FEATURE_BRANCHES}{ticket.number}-{slug}"
    return f"{FEATURE_BRANCHES}{slug}"


def conjecture_failure(ticket: Ticket, root: Path, gh: Runner) -> str | None:
    own = ticket.conjecture
    if own is not None:
        if states_a_conjecture(own):
            return None
        return f"{ticket.ref}: `{CONJECTURE}` states no conjecture"
    parent_ref = parent_of(ticket, root)
    if parent_ref is None:
        return f"{ticket.ref} has no `{CONJECTURE}` section and names no parent"
    parent = read_parent(parent_ref, root, gh)
    if parent is None:
        why = unread(parent_ref, root, "was not found")
        return f"{ticket.ref} has no `{CONJECTURE}` section; its parent {parent_ref} {why}"
    inherited = parent.conjecture
    if inherited is not None and states_a_conjecture(inherited):
        return None
    return f"{ticket.ref} has no `{CONJECTURE}` section and its parent {parent_ref} states none"


def binding_text(root: Path) -> str | None:
    binding = root / gatelib.BINDING
    return binding.read_text(encoding="utf-8") if binding.is_file() else None


def criteria(body: str) -> list[str]:
    block = re.search(
        rf"^{re.escape(CRITERIA)}[ \t]*$\n(.*?)(?=^#{{1,2}}\s|\Z)",
        body,
        re.MULTILINE | re.DOTALL | re.IGNORECASE,
    )
    items: list[str] = []
    for line in (block.group(1) if block else "").splitlines():
        item = CRITERION_ITEM.match(line)
        if item:
            items.append(item.group("text").strip())
        elif items and line.strip() and line[:1] in " \t":
            items[-1] += " " + line.strip()
    return items


def named_gates(body: str, binding: str | None) -> list[NamedGate]:
    if binding is None:
        return []
    held = {
        span: row
        for row in reversed(project_bite.gate_rows(binding))
        if row.command
        for span in (row.gate, row.command)
    }
    found: dict[tuple[str, str], NamedGate] = {}
    for criterion in criteria(body):
        for span in CODE_SPAN.findall(criterion):
            row = held.get(span.strip())
            if row is not None:
                found.setdefault((row.gate, criterion), NamedGate(row.gate, row.tier, criterion))
    return list(found.values())


def ticket_named_gates(ref: str, root: Path, gh: Runner) -> list[NamedGate]:
    ticket = fetch(ref, root, gh)
    return [] if ticket is None else named_gates(ticket.body, binding_text(root))


def operability_asked(root: Path) -> list[tuple[str, str]]:
    text = binding_text(root)
    if text is None:
        return []
    kinds = frozenset(application_types.declared(text) or ())
    asked_of = application_types.load().asked_of
    waiting = {question.casefold() for question in LATER.findall(section(text, GATES))}
    return [
        (name, says)
        for name, question, says in OPERABILITY_LINES
        if asked_of(question) & kinds and (name == FLAG_LINE or question.casefold() not in waiting)
    ]


def operability_lines(ticket: Ticket, parent: Ticket | None) -> dict[str, tuple[str, ...]]:
    lines: dict[str, tuple[str, ...]] = {}
    for record in (ticket, parent):
        block = "" if record is None else FENCE.sub("", section(record.body, OPERABILITY))
        for name, _question, _says in OPERABILITY_LINES:
            found = operability_line(name).findall(block)
            if found:
                lines.setdefault(name, tuple(found))
    return lines


def operability_failure(ticket: Ticket, root: Path, gh: Runner) -> str | None:
    asked = operability_asked(root)
    if not asked:
        return None
    lines = operability_lines(ticket, None)
    parent_ref = parent_of(ticket, root)
    lacking, unreadable, templated = [], [], []
    if parent_ref is not None and any(name not in lines for name, _says in asked):
        parent = read_parent(parent_ref, root, gh)
        if parent is None:
            why = unread(parent_ref, root, "could not be read")
            unreadable.append(f"its parent {parent_ref} {why}")
        lines = operability_lines(ticket, parent)
    for name, says in asked:
        cause = unfilled(next(iter(lines.get(name, ())), ""), says)
        if cause == "nothing":
            lacking.append(f"`{name}:` ({says})")
        elif cause is not None:
            templated.append(f"has `{name}:` with {cause}")
        elif name == FLAG_LINE and (problem := flag_reading(lines[name])[1]):
            templated.append(problem)
    if lacking and parent_ref is None and (unlinked := unlinked_part(ticket, root, gh)):
        return unlinked
    parts = ([f"lacks {', '.join(lacking)}"] if lacking else []) + unreadable + templated
    if not parts:
        return None
    return f"{ticket.ref}: `{OPERABILITY}` {'; '.join(parts)}; write each, or `none` and why"


def unlinked_part(ticket: Ticket, root: Path, gh: Runner) -> str | None:
    origin_ref = splits.split_from(ticket.body)
    origin = None if origin_ref is None else read_parent(origin_ref, root, gh)
    link = None if origin is None else PARENT_TO_LINE_END.search(origin.body)
    if link is None:
        return None
    return (
        f"{ticket.ref}: the part names no parent, and the ticket it was split from, {origin_ref},"
        f' names {link.group(1)}; add its line "{link.group()}" to the part, or run'
        f" `/ratchet:conjecture {ticket.ref}`, which writes it"
    )


def flag_of(ticket: Ticket, root: Path, gh: Runner) -> Flag:
    if FLAG_LINE not in dict(operability_asked(root)):
        return Flag()
    lines = operability_lines(ticket, None)
    parent_ref = parent_of(ticket, root)
    if FLAG_LINE not in lines and parent_ref is not None:
        lines = operability_lines(ticket, read_parent(parent_ref, root, gh))
    return flag_reading(lines.get(FLAG_LINE, ()))[0]


def flag_reading(lines: Sequence[str]) -> tuple[Flag, str | None]:
    forms = set(map(flag_form, lines))
    if len(forms) > 1:
        quoted = ", ".join(f'"{line.strip()}"' for line in lines)
        return Flag(), f"has `Flag:` lines that disagree, {quoted}: keep the owner's answer alone"
    flag = next(iter(forms), Flag())
    if flag.key is not None and len(flag.key) > FLAG_KEY_WIDTH:
        width = f"a key of {len(flag.key)} characters, and a key is at most {FLAG_KEY_WIDTH}"
        return Flag(), f"has `Flag:` with {width}"
    return flag, None


def flag_form(line: str) -> Flag:
    text = INVISIBLE.sub("", line)
    if switch := FLAG_SWITCH.fullmatch(text):
        return Flag("hidden", switch.group(1))
    return Flag("appears") if re.findall(r"\w+", text.lower()) == FLAG_APPEARS else Flag()


def size_failure(ticket: Ticket, root: Path) -> str | None:
    limits = change_size.ceilings(root)
    stated = {
        kind.lower(): int(number.replace(",", ""))
        for kind, number in SIZE_LINE.findall(section(ticket.body, SIZE))
    }
    over = [
        f"{stated[kind]} product {kind}, ceiling {limit}"
        for kind, limit in (("lines", limits.product_lines), ("files", limits.product_files))
        if limit is not None and stated.get(kind, 0) > limit
    ]
    if not over:
        return None
    return f"{ticket.ref}: `{SIZE}` estimates {'; '.join(over)}: one change would stop as too large"


def operability_line(name: str) -> re.Pattern[str]:
    return re.compile(
        rf"^[-* \t]*\*{{0,2}}{name}(?::\*{{0,2}}|\*{{1,2}}:)[ \t]*(.*)$",
        re.IGNORECASE | re.MULTILINE,
    )


def parent_of(ticket: Ticket, root: Path) -> str | None:
    return ticket.parent or beside_spec(ticket.ref, root)


def outside(ref: str, root: Path) -> bool:
    place = root / ref
    try:
        place.stat()
    except FileNotFoundError:
        pass
    except (OSError, ValueError):
        return True
    real = Path(os.path.realpath(place))
    return Path(ref).is_absolute() or not real.is_relative_to(root.resolve())


def unread(ref: str, root: Path, why: str) -> str:
    return "is outside the repository" if outside(ref, root) else why


def read_parent(ref: str, root: Path, gh: Runner) -> Ticket | None:
    return None if outside(ref, root) else fetch(ref, root, gh)


def reads(ticket: Ticket, root: Path) -> tuple[frozenset[str], frozenset[str]]:
    def local(ref: str | None) -> frozenset[str]:
        if ref is None or not ref.endswith(".md"):
            return frozenset(numbered_locally(ref or "", root))
        return frozenset({posixpath.normpath(ref)})

    whole = {posixpath.normpath(ticket.ref), *local(parent_of(ticket, root))}
    status = frozenset().union(*(local(blocker_ref(ticket, ref, root)) for ref in ticket.blockers))
    return frozenset(whole), status - whole


def beside_spec(ref: str, root: Path) -> str | None:
    path = Path(ref)
    if path.parent.name != "issues":
        return None
    spec = path.parent.parent / "spec.md"
    return spec.as_posix() if (root / spec).is_file() else None


def blocker_ref(ticket: Ticket, ref: str, root: Path) -> str:
    folder = Path(ticket.ref).parent
    if ref.endswith(".md"):
        return (folder / ref).as_posix()
    if ref.startswith("#"):
        return ref
    siblings = sorted((root / folder).glob(f"{int(ref):02d}-*.md"))
    return siblings[0].relative_to(root).as_posix() if siblings else ref


@dataclass(frozen=True)
class Blocker:
    named: str
    ref: str
    ticket: Ticket | None
    split_from: str = ""


def blockers(ticket: Ticket, root: Path, gh: Runner) -> list[Blocker]:
    found: list[Blocker] = []
    for named in ticket.blockers:
        ref = blocker_ref(ticket, named, root)
        blocker = fetch(ref, root, gh)
        parts = None if blocker is None else split_parts(blocker, root)
        if parts is None:
            found.append(Blocker(named, ref, blocker))
        elif not parts:
            found.append(Blocker(named, "", None, ref))
        else:
            found += [Blocker(part, part, fetch(part, root, gh), ref) for part in parts]
    return found


def unmet(blocker: Blocker) -> str | None:
    if blocker.ticket is not None and not blocker.ticket.is_open:
        return None
    if blocker.split_from and not blocker.ref:
        return f"blocker {blocker.split_from} was split into no parts"
    if blocker.split_from:
        return f"blocker {blocker.split_from} was split, and its part {blocker.ref} is not done"
    if blocker.ticket is None:
        return f"blocker {blocker.named} was not found"
    return f"blocker {blocker.ref} is still open"


def open_blockers(ticket: Ticket, root: Path, gh: Runner) -> list[str]:
    return [why for blocker in blockers(ticket, root, gh) if (why := unmet(blocker)) is not None]


def split_parts(ticket: Ticket, root: Path) -> list[str] | None:
    branch = branch_of(ticket, slug_of(ticket.title, ticket.number))
    return queue_notes.split_into(root, branch, ticket)


def check_ticket(report: Report, ticket: Ticket, label: str, *, root: Path, gh: Runner) -> None:
    report.number, report.title = ticket.number, ticket.title
    report.slug = slug_of(ticket.title, ticket.number)
    report.branch = branch_of(ticket, report.slug)
    report.parent = parent_of(ticket, root)
    if report.parent is not None and outside(report.parent, root):
        report.fail(
            "parent",
            f"{ticket.ref}: its parent {report.parent} is outside the repository, so the"
            " preflight does not read it",
        )
    elif report.parent is not None and report.parent.startswith("../"):
        real = Path(os.path.realpath(root / report.parent))
        report.parent = real.relative_to(root.resolve()).as_posix()
    if not ticket.is_open:
        report.fail("open", f"{ticket.ref} is not open")
    if (parts := split_parts(ticket, root)) is not None:
        report.fail("split", f"{ticket.ref} was split by size into {', '.join(parts)}")
    failure = conjecture_failure(ticket, root, gh)
    if failure is not None:
        report.fail("conjecture", failure)
    failure = operability_failure(ticket, root, gh)
    if failure is not None:
        report.fail("operability", failure)
    report.flag = flag_of(ticket, root, gh)
    failure = size_failure(ticket, root)
    if failure is not None:
        report.fail("size", failure)
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


def check_fill_first(report: Report, root: Path) -> None:
    owing = owed.fill_first(root)
    if owing:
        rules = ", ".join(sorted({entry.rule for entry in owing}))
        report.fail(
            "fill",
            f"{len(owing)} owed items an agent writes from the repository are not written"
            f" yet ({rules})",
            "/ratchet:fill",
        )


def check_life_events(report: Report, root: Path) -> None:
    text = binding_text(root)
    declared = frozenset() if text is None else application_types.declared_or_all(text)
    events = application_types.load().events
    if declared - events.keys():
        written = application_types.EVENTS.findall(text or "")[0].strip()
        spelled = ", ".join(f"`{name}`" for name in events)
        report.fail(
            "life-events",
            f"the binding's `- Life events:` line reads `{written}`, which the preflight cannot"
            f" read: it takes `none`, or one or more of {spelled} joined by commas",
        )
    report.deployed = DEPLOYED_EVENT in declared
    report.critic = CRITIC_EVENT in declared
    report.critic_reason = (
        f"the binding declares `{CRITIC_EVENT}`: others rely on it, so the change is attacked"
        " before review"
        if report.critic
        else f"the binding declares no `{CRITIC_EVENT}` life event: nobody relies on it yet, so"
        " the change goes spec, ticket check, implement, review"
    )


def check_hooks(report: Report, root: Path) -> None:
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
        report.named_gates = named_gates(ticket.body, binding_text(root))
    check_checkout(report, root, base=base, branch=branch)
    check_hooks(report, root)
    check_fill_first(report, root)
    check_life_events(report, root)
    if branch is not None and report.ok:
        record_start(report, root, branch=branch, gh=gh)
    return report


def record_start(report: Report, root: Path, *, branch: str, gh: Runner) -> None:
    try:
        report.start = publish_check.start_of(root, branch, gh)
        queue_notes.record_feature_start(root, branch, asdict(report.start))
    except publish_check.UnreadableError as error:
        report.fail("start", str(error), "the forge reachable again, or the branch made again")
        return
    run_time.stamp_start(root, branch)


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
