from __future__ import annotations

import atexit
import functools
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import zlib
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Final

import application_types
import architecture
import comments
import conformance
import documents
import drain
import feature_preflight as preflight
import formal_model
import gatelib
import markdown_text
import merge
import merge_ready
import owed
import plugin_pin
import queue_notes
import route
import triggers
from tests.repos import (
    branch,
    commit,
    git,
    install_hooks,
    repository,
    skeleton,
    snapshot,
    write,
)
from tests.route_model import (
    STATED,
    ActionName,
    Advice,
    Architecture,
    Defect,
    Flag,
    Head,
    Setup,
    Ticket,
    Tree,
    Upkeep,
    World,
    expires,
    runnable_slots,
)
from tests.test_drain import MERGE

STANZA = "# P\n\nLoad the `ratchet:method` skill first.\n"
PURPOSE = f"\n## What this is\n\n{route.PURPOSE_PLACEHOLDER}: purpose, who it is for.>\n"
CONJECTURE = (
    "## Conjecture\n\nOne script decides the route; the refuter is a wrong command on a known"
    " state. Alternatives: the model guesses (killed: it drifts).\n"
)
MANIFEST = (
    '[[group]]\nname = "records"\nreason = "Added to, never changed."\nedit = "append"\n'
    'paths = ["docs/adr/**", "GLOSSARY.md"]\n\n'
    '[[group]]\nname = "decisions"\nreason = "The owner\'s."\npaths = ["docs/process/**"]\n'
)
PIN = "- Ratchet plugin: {version} at " + "0" * 40 + "\n"
UNPINNED = "- Ratchet plugin: <X.Y.Z> at <ratchet-sha>\n"
BINDING = (
    "# Binding\n\n{pin}- Default branch: `main`\n- Life events: none\n{types}\n"
    "| Entry point | Command |\n| --- | --- |\n{entries}\n"
    "## Gates\n\n| Question | Tool | Tier | Threshold source | Entry point |\n"
    "| --- | --- | --- | --- | --- |\n{gates}\n"
    "## Triggers\n\n| Kind | Paths | Pattern |\n| --- | --- | --- |\n{triggers}{architecture}"
)
ENTRY_POINTS: Final = {"check": "just check", "merge": "just merge"}
ARCHITECTURE_RECORD = (
    "# The architecture\n\n## Decision\n\nOne service, its rules apart from its web layer.\n\n"
    "- Chosen by: the owner\n\n## Considered options\n\n"
    "- **Two services.** Offered; not chosen: one team deploys it.\n"
    "- **A rewrite.** Set aside: the code works.\n\n"
    "## Reversal cost\n\nThe rules stay; the web layer is written again.\n\n"
    "## Refutation criterion\n\nA second team needs to deploy on its own.\n"
)
ARCHITECTURE_SECTION = (
    "\n" + architecture.SECTION + "\n\n- Decision: `{record}`\n"
    f"- {comments.PUBLISHED}: {comments.NOT_A_LIBRARY}\n\n| Part | Path | May depend on |\n"
    "| --- | --- | --- |\n| rules | `src/rules/**` | nothing |\n| web | `src/**` | rules |\n"
)
ARCHITECTURE_BRANCH = "chore/architecture"
GATES = "## Gates"
SECTIONS: Final = re.compile(r"\n(?=## )")
REVIEW_ROW = (
    f"| {triggers.REVIEW_QUESTION} | `ratchet` plugin `scripts/triggers.py` | 1 | |"
    " `just triggers` |\n"
)
REOPENING: Final = tuple(
    kind
    for key in application_types.load().architecture
    for kind in application_types.code_kinds(key)
)
REVIEW = (
    "# The architecture holds\n\n- Architecture review: {keys}\n- Outcome: holds\n"
    "- Decided: 2026-11-14\n\n## Decision\n\nThe new kind sits in the part that owns it.\n\n"
    "## Refutation criterion\n\nAn instance outside that part.\n"
)
ASKED_TYPES = "- Application types: cli\n"
ASKED_KINDS: Final = application_types.load().kinds_required(
    set(application_types.declared(ASKED_TYPES) or [])
)
FLAG_ANSWER = (
    "## Answers\n\n- `conjecture.flag`: it settles one route: should it stay hidden until you"
    " switch it on, or appear as soon as it is finished? Appear as soon as it is finished.\n\n"
    "## Operability\n\n- Flag: none: the owner chose to have it appear as soon as it is finished,"
    " while the project is not deployed\n"
)
CRITERIA = "## Acceptance criteria"
STORIES = "## User Stories"
LEFT_SPEC = f"# Settle\n\n{STORIES}\n\n1. As the owner, I am told the next command.\n"
SPEC = f"{LEFT_SPEC}\n## Answers\n\n- `spec.scope`: one command, not a list of them.\n"
ANSWERS = "## Answers"
OWNERS_WORDS = "## In the owner's words"
BRIEF = f"# Brief\n\n{OWNERS_WORDS}\n\n- `adopt.purpose`: tell me the next command.\n"
EXPIRED_ON = "2026-11-17"
CONJECTURE_SKILL: Final = plugin_pin.PLUGIN / "skills/conjecture/SKILL.md"
CRITERIA_LINE: Final = re.compile(r"^- Criteria: .*$", re.MULTILINE)
DIGEST: Final = re.compile(r"[0-9a-f]{40}")
GLOSSARY = "# Glossary\n\n**Customer**: who pays.\n"
PLACEHOLDER = "# <project>\n"
ADOPT_TEMPLATES: Final = plugin_pin.PLUGIN / "skills/adopt/templates"
QUALITY: Final = ("quality/thresholds.toml", "quality/mutation-register.toml")
ADOPTION = "adopt-ratchet"
STATUS: Final = {
    Ticket.TRIAGE: "needs-triage",
    Ticket.RESOLVED: "resolved",
}
OWN_CONJECTURE: Final = frozenset(Ticket) - {
    Ticket.NONE,
    Ticket.TRIAGE,
    Ticket.READY,
    Ticket.RESOLVED,
}
FORMAL_QUESTIONS: Final = {
    application_types.code_kinds(application_types.load().trigger_of(question))[0]: question
    for question, proofs in application_types.load().proofs.items()
    if conformance.FORMAL_PROOF in proofs
}
DEFECT_KINDS: Final = ("state-machine",)
FORMAL_GROUP = (
    '\n[[group]]\nname = "formal"\nreason = "The model the code must mirror."\n'
    'paths = ["formal/**"]\n'
)
MIRRORED = "src/mirrored"
FORMAL_SECTION = (
    f"\n{formal_model.SECTION}\n\n- Model: `formal/**`\n- Mirrors: `{MIRRORED}/**`\n"
    "- Synced: {synced}\n"
)
SYNCED_LINE: Final = re.compile(r"^- Synced: .*$", re.MULTILINE)
LETTERS: Final = "ab"
TICKETS_BRANCH: Final = "docs/tickets-"
STATUS_LINE: Final = re.compile(r"^Status: .*$", re.MULTILINE)
COMMENTED = "src/commented.py"
COMMENT = "one, as the owner asked"
CITED = "src/cited.py"
README = "README.md"
CUT_README = f"# A\n\nThe rule lives in `{CITED}`.\n"
STALE_README = f"# A\n\nThe rule says one thing.\n\nThe rule lives in `{CITED}`.\n"


@functools.cache
def digest_command() -> str:
    line = next(
        line
        for line in CONJECTURE_SKILL.read_text(encoding="utf-8").splitlines()
        if "**The body is a file**" in line
    )
    return re.findall(r"`([^`]+)`", line)[-1]


@functools.cache
def criteria_digest(body: str, heading: str) -> str:
    with tempfile.TemporaryDirectory(prefix="ratchet-criteria-") as scratch:
        path = Path(scratch) / "body.md"
        path.write_text(body, encoding="utf-8")
        command = digest_command().replace("<file>", shlex.quote(str(path)))
        ran = subprocess.run(  # noqa: S603 # -- the conjecture skill's own command
            ["sh", "-c", command.replace("<heading>", heading)],  # noqa: S607 # -- as the skill runs it
            cwd=scratch,
            capture_output=True,
            text=True,
            check=True,
        )
    digest = ran.stdout.strip()
    assert DIGEST.fullmatch(digest), f"the digest command printed {ran.stdout!r}"
    return digest


def criteria_line(body: str, heading: str) -> str:
    return f"- Criteria: `{heading}` `{criteria_digest(body, heading)}`\n"


def redraft(path: Path, heading: str) -> None:
    text = path.read_text(encoding="utf-8")
    line = criteria_line(text, heading).rstrip("\n")
    path.write_text(CRITERIA_LINE.sub(lambda _old: line, text), encoding="utf-8")


def add_criterion(path: Path, heading: str, criterion: str) -> None:
    text = path.read_text(encoding="utf-8")
    held = markdown_text.section(text, heading).strip("\n")
    path.write_text(with_body(text, heading, f"{held}\n{criterion}"), encoding="utf-8")


def share_objects(source: str, target: str) -> None:
    if f"{os.sep}.git{os.sep}objects{os.sep}" in source:
        os.link(source, target)
    else:
        shutil.copy2(source, target)


@dataclass
class Repo:
    root: Path
    spec: int = 0
    records: int = 0
    edits: int = 0
    tools: list[str | None] = field(default_factory=list)
    parked: str | None = None
    reopened: list[str] = field(default_factory=list)
    reviewed: int = 0
    upgrades: int = 0
    architecture: str = ""
    merged: str | None = None
    asked: bool = False
    entries: dict[str, str] = field(default_factory=lambda: dict(ENTRY_POINTS))
    manifest: str = MANIFEST
    named: dict[int, str] = field(default_factory=dict)
    defect: str | None = None
    rounds: int = 0
    waiting: str | None = None
    prunes: int = 0
    guarded: str | None = None

    @classmethod
    def start(cls, parent: Path) -> Repo:
        return cls(repository(parent / "repo", {"README.md": "# A\n"}))

    @classmethod
    def merging(cls, parent: Path) -> Repo:
        return replace(
            cls.start(parent),
            entries={"check": "true", "check-deep": "true", "merge": MERGE},
            manifest=merge_ready.TEMPLATE.read_text(encoding="utf-8"),
        )

    def copy(self, root: Path) -> Repo:
        shutil.copytree(self.root, root, symlinks=True, copy_function=share_objects)
        return replace(
            self,
            root=root,
            tools=list(self.tools),
            reopened=list(self.reopened),
            named=dict(self.named),
        )

    def feature(self) -> str:
        return f".scratch/f{self.spec}"

    def head(self) -> str:
        return git(self.root, "rev-parse", "--abbrev-ref", "HEAD")

    def binding(self, *, pinned: bool = True) -> str:
        gates = "".join(
            f"| {self.question(n)} | {tool or self.later(n)} | 1 | | `just q{n}` |\n"
            for n, tool in enumerate(self.tools, 1)
        )
        rows = "".join(
            f"| {self.kind(n)} | `src/kind{n}.py`, `src/kind{n}-*.py` | |\n"
            for n, _tool in enumerate(self.tools, 1)
        ) + "".join(f"| {kind} | `src/{kind}.py` | |\n" for kind in self.reopened)
        if self.asked:
            rows += "".join(
                f"| {kind} | none: the project has none yet | |\n"
                for kind in sorted(ASKED_KINDS - set(self.reopened) - set(self.named.values()))
            )
        made = BINDING.format(
            pin=PIN.format(version=self.version()) if pinned else UNPINNED,
            types=ASKED_TYPES if self.asked else "",
            entries="".join(
                f"| {name} | `{command}` |\n" for name, command in self.entries.items()
            ),
            gates=gates + REVIEW_ROW + self.kept_gates(),
            triggers=rows,
            architecture=self.architecture,
        )
        return made + self.kept_sections(made)

    def kept_sections(self, made: str) -> str:
        text = gatelib.read_text(self.root / route.BINDING) or ""
        return "".join(
            f"\n{block.rstrip()}\n"
            for block in SECTIONS.split(text)[1:]
            if not markdown_text.has_section(made, block.split("\n", 1)[0])
        )

    def kind(self, n: int) -> str:
        return self.named.get(n, f"kind{n}")

    def later(self, n: int) -> str:
        return f"later: code:{self.kind(n)}"

    def question(self, n: int) -> str:
        return FORMAL_QUESTIONS[self.named[n]] if n in self.named else f"q{n}"

    def kept_gates(self) -> str:
        found = markdown_text.table(gatelib.read_text(self.root / route.BINDING) or "", GATES)
        if found is None:
            return ""
        own = {self.question(n) for n in range(1, len(self.tools) + 1)} | {triggers.REVIEW_QUESTION}
        rows = zip(found.rows, found.lines[2:], strict=True)
        return "".join(f"{line}\n" for row, line in rows if row[0] not in own)

    def version(self) -> str:
        return f"999.{self.upgrades}.0"

    def next_record(self, slug: str) -> str:
        number = len(list((self.root / "docs/adr").glob("*.md"))) + 1
        return f"docs/adr/{number:04d}-{slug}.md"

    def record_architecture(self) -> None:
        record = self.next_record("the-architecture")
        write(self.root, record, ARCHITECTURE_RECORD)
        self.architecture = ARCHITECTURE_SECTION.format(record=record)
        write(self.root, "docs/process/binding.md", self.binding())

    def settle(self, done: Callable[[owed.Entry], bool]) -> None:
        left = [entry for entry in owed.read(self.root).entries if not done(entry)]
        write(self.root, str(owed.LEDGER), owed.render(left))

    def spec_path(self) -> str:
        return f"{self.feature()}/spec.md"

    def ticket_path(self, slot: int) -> str:
        return f"{self.feature()}/issues/{slot + 1:02d}-{LETTERS[slot]}{self.spec}.md"

    def ticket_branch(self, slot: int) -> str:
        return f"feature/{slot + 1}-{LETTERS[slot]}{self.spec}"

    def ticket_text(self, slot: int, ticket: Ticket, *, inherits: bool) -> str:
        blocker = "Blocked by: 01\n\n" if slot == 1 else ""
        name = f"{LETTERS[slot].upper()}{self.spec}"
        head = f"# {slot + 1:02d}: {name}\n\n{blocker}"
        status = STATUS.get(ticket, "ready-for-agent")
        line = f"{self.defect}\n\n" if self.defect and slot == 0 else ""
        criteria = f"{CRITERIA}\n\n- [ ] The route names the next command for {name}.\n"
        text = f"{head}Status: {status}\n\n{line}{criteria}"
        if ticket in OWN_CONJECTURE and not inherits:
            text = f"{text}\n{CONJECTURE}"
            stated = f"{text}\n{criteria_line(text, CRITERIA)}"
            return f"{stated}\n{FLAG_ANSWER}" if line and self.asked else stated
        return text

    def write_ticket(self, slot: int, ticket: Ticket, *, inherits: bool) -> None:
        write(self.root, self.ticket_path(slot), self.ticket_text(slot, ticket, inherits=inherits))

    def add_to_ticket(self, slot: int, text: str) -> None:
        self.edits += 1
        path = self.root / self.ticket_path(slot)
        path.write_text(f"{path.read_text(encoding='utf-8')}\n{text} ({self.edits})\n", "utf-8")
        snapshot(self.root, f"docs(tracker): {text.lower()}")

    def next_records_branch(self, *, named: bool = False) -> str:
        self.records += 1
        if named:
            return f"{TICKETS_BRANCH}{self.records}"
        return f"{merge.RECORDS}20261113-{self.records:06d}"


@dataclass(frozen=True)
class Change:
    before: World
    after: World

    @property
    def slot(self) -> int:
        pairs = zip(self.before.tickets, self.after.tickets, strict=True)
        return next(slot for slot, (old, new) in enumerate(pairs) if old != new)


def named_by_an_agent(change: Change) -> bool:
    return (
        change.before.head is Head.MAIN
        and change.after.parked
        and zlib.crc32(repr(change.after).encode()) % 2 == 1
    )


Handler = Callable[[Repo, Change], None]
DO: Final[dict[ActionName, Handler]] = {}


def does(name: ActionName) -> Callable[[Handler], Handler]:
    def register(handler: Handler) -> Handler:
        DO[name] = handler
        return handler

    return register


def apply(repo: Repo, name: ActionName, change: Change) -> None:
    DO[name](repo, change)


@does(ActionName.OWNER_INITS)
def _owner_inits(repo: Repo, _change: Change) -> None:
    write(repo.root, "AGENTS.md", STANZA)
    ignored = plugin_pin.PLUGIN / "skills/init/templates/gitignore"
    write(repo.root, ".gitignore", ignored.read_text(encoding="utf-8"))
    snapshot(repo.root, "chore: init")


@does(ActionName.INIT_STOPS)
def _init_stops(repo: Repo, _change: Change) -> None:
    write(repo.root, "AGENTS.md", STANZA + PURPOSE)
    snapshot(repo.root, "chore: init, its questions not yet answered")


@does(ActionName.OWNER_SETS_UP_TRACKER)
def _owner_sets_up_tracker(repo: Repo, _change: Change) -> None:
    write(repo.root, "docs/agents/issue-tracker.md", "GitHub\n")
    snapshot(repo.root, "chore: the tracker")


def on_adoption_branch(repo: Repo) -> None:
    if repo.head() != ADOPTION:
        branch(repo.root, ADOPTION)


def adoption(repo: Repo, change: Change, *, pinned: bool) -> None:
    write(repo.root, "docs/process/binding.md", repo.binding(pinned=pinned))
    write(repo.root, "guards/manifest.toml", repo.manifest)
    write(repo.root, "GLOSSARY.md", PLACEHOLDER)
    for path in QUALITY:
        write(repo.root, path, (ADOPT_TEMPLATES / path).read_text(encoding="utf-8"))
    if change.after.architecture is Architecture.RECORDED and not repo.architecture:
        repo.record_architecture()


def adopt(repo: Repo, change: Change) -> None:
    if change.before.setup is Setup.PINNED and repo.merged:
        return
    on_adoption_branch(repo)
    adoption(repo, change, pinned=True)
    if change.after.comments:
        write(repo.root, COMMENTED, f"x = 1  # {COMMENT}\n")
        entry = owed.Entry(COMMENTED, owed.COMMENTS, COMMENT, repo.version())
        write(repo.root, str(owed.LEDGER), owed.render([entry]))
    snapshot(repo.root, "chore(method): adopt")
    git(repo.root, "switch", "-q", "main")
    git(repo.root, "merge", "-q", "--no-ff", "--no-edit", ADOPTION)
    repo.merged = ADOPTION


DO[ActionName.OWNER_ADOPTS] = adopt
DO[ActionName.OLDER_PLUGIN_ADOPTS] = adopt


@does(ActionName.ADOPTION_STOPS)
def _adoption_stops(repo: Repo, change: Change) -> None:
    on_adoption_branch(repo)
    pinned = change.after.setup is Setup.PINNED
    adoption(repo, change, pinned=pinned)
    if not pinned:
        snapshot(repo.root, "chore(method): adopt, the templates")


@does(ActionName.AGENT_INSTALLS_HOOKS)
def _agent_installs_hooks(repo: Repo, _change: Change) -> None:
    install_hooks(repo.root)


@does(ActionName.OWNER_GRILLS)
def _owner_grills(repo: Repo, _change: Change) -> None:
    write(repo.root, "GLOSSARY.md", GLOSSARY)
    write(repo.root, "docs/design-brief.md", BRIEF)


@does(ActionName.OWNER_WRITES_SPEC)
def _owner_writes_spec(repo: Repo, _change: Change) -> None:
    repo.defect = None
    repo.spec += 1
    write(repo.root, repo.spec_path(), SPEC)


@does(ActionName.OWNER_STATES_SPEC_CONJECTURE)
def _owner_states_spec_conjecture(repo: Repo, change: Change) -> None:
    flag = f"\n{FLAG_ANSWER}" if change.after.flag is not Flag.WAITS else ""
    stated = f"{SPEC}\n{CONJECTURE}"
    write(repo.root, repo.spec_path(), f"{stated}\n{criteria_line(stated, STORIES)}{flag}")


def answers_the_flag_on_the_spec(repo: Repo) -> None:
    path = repo.root / repo.feature() / "spec.md"
    text = gatelib.read_text(path)
    if text is not None and FLAG_ANSWER not in text:
        path.write_text(f"{text}\n{FLAG_ANSWER}", encoding="utf-8")


@does(ActionName.OWNER_STATES_CONJECTURE)
def _owner_states_conjecture(repo: Repo, change: Change) -> None:
    moved = list(enumerate(zip(change.before.tickets, change.after.tickets, strict=True)))
    for slot, pair in moved:
        if pair == (Ticket.READY, Ticket.STATED):
            repo.write_ticket(slot, Ticket.STATED, inherits=change.after.inherits)
    redrafts = [slot for slot, pair in moved if pair == (Ticket.STALE, Ticket.STATED)]
    if redrafts and change.after.inherits:
        redraft(repo.root / repo.spec_path(), STORIES)
    elif redrafts:
        for slot in redrafts:
            redraft(repo.root / repo.ticket_path(slot), CRITERIA)
    if change.after.flag is not Flag.WAITS:
        answers_the_flag_on_the_spec(repo)
    if change.before.upkeep is Upkeep.UNSTATED:
        unstated = (repo.root / round_ticket_path(repo)).read_text(encoding="utf-8")
        write(repo.root, round_ticket_path(repo), stated_round_ticket(repo, unstated))
    snapshot(repo.root, "docs(tracker): the owner's conjecture of a ticket")


@does(ActionName.OWNER_EDITS_CRITERIA)
def _owner_edits_criteria(repo: Repo, change: Change) -> None:
    repo.edits += 1
    if change.after.inherits:
        story = f"2. As the owner, I am told why ({repo.edits})."
        add_criterion(repo.root / repo.spec_path(), STORIES, story)
    else:
        criterion = f"- [ ] The route says why ({repo.edits})."
        add_criterion(repo.root / repo.ticket_path(change.slot), CRITERIA, criterion)
    snapshot(repo.root, "docs(tracker): the owner's edit of the criteria")


@does(ActionName.OWNER_SPLITS_SPEC)
def _owner_splits_spec(repo: Repo, _change: Change) -> None:
    for slot in (0, 1):
        repo.write_ticket(slot, Ticket.TRIAGE, inherits=False)


def rewrites_ticket(repo: Repo, change: Change) -> None:
    repo.write_ticket(
        change.slot, change.after.tickets[change.slot], inherits=change.after.inherits
    )
    if change.after.tree is Tree.CLEAN:
        snapshot(repo.root, "docs(tracker): the owner's edit of a ticket")


DO[ActionName.OWNER_AGREES_TICKET] = rewrites_ticket


def expire(repo: Repo) -> None:
    repo.edits += 1
    unit = repo.root / repo.feature()
    said = [
        f"- {EXPIRED_ON}: {line.removeprefix('- ')}"
        for path in sorted(unit.rglob("*.md"))
        for line in markdown_text.section(path.read_text(encoding="utf-8"), ANSWERS).split("\n")
        if line.strip()
    ]
    if said:
        brief = repo.root / route.DESIGN_BRIEF
        text = brief.read_text(encoding="utf-8").rstrip("\n")
        held = markdown_text.section(text, OWNERS_WORDS).strip("\n")
        words = "\n".join([held, *said]).strip("\n")
        brief.write_text(with_body(text, OWNERS_WORDS, words), encoding="utf-8")
    git(repo.root, "rm", "-r", "-q", "-f", "--", repo.feature())


def resolves_ticket(repo: Repo, change: Change) -> None:
    path = repo.root / repo.ticket_path(change.slot)
    status = f"Status: {STATUS[Ticket.RESOLVED]}"
    path.write_text(STATUS_LINE.sub(status, path.read_text(encoding="utf-8")), encoding="utf-8")
    if expires(change.before, change.after):
        expire(repo)
    if change.after.tree is Tree.CLEAN:
        snapshot(repo.root, "docs(tracker): the owner's edit of a ticket")


DO[ActionName.OWNER_WORKS_TICKET_THEMSELF] = resolves_ticket
DO[ActionName.OWNER_RESOLVES_TICKET] = resolves_ticket


def build_on_branch(repo: Repo, slot: int, files: dict[str, str] | None = None) -> str:
    name = repo.ticket_branch(slot)
    git(repo.root, "switch", "-q", "-C", name)
    for path, text in (files or {f"src/{name.split('/')[1]}.py": "x = 1\n"}).items():
        write(repo.root, path, text)
    snapshot(repo.root, f"feat: {name}")
    return name


@does(ActionName.AGENT_BUILDS_TICKET)
def _agent_builds_ticket(repo: Repo, change: Change) -> None:
    build_on_branch(repo, change.slot)


@does(ActionName.BUILD_STOPS)
def _build_stops(repo: Repo, change: Change) -> None:
    if change.after.head is Head.MERGED:
        before = change.before
        slot = next(s for s in runnable_slots(before) if before.tickets[s] in STATED)
        git(repo.root, "switch", "-q", "-C", repo.ticket_branch(slot))
        return
    name = repo.ticket_branch(change.slot)
    git(repo.root, "switch", "-q", "-C", name)
    write(repo.root, f"src/{name.split('/')[1]}.py", "x = 1\n")


@does(ActionName.FEATURE_RUN_BLOCKS)
def _feature_run_blocks(repo: Repo, change: Change) -> None:
    name = repo.ticket_branch(change.slot)
    git(repo.root, "switch", "-q", "-C", name)
    write(repo.root, "docs/features/draft/spec.md", "draft\n")
    git(repo.root, "stash", "push", "-q", "-u", "-m", f"{name}: blocked run")
    git(repo.root, "switch", "-q", "main")


@does(ActionName.OWNER_ANSWERS_BLOCKED_RUN)
def _owner_answers_blocked_run(repo: Repo, change: Change) -> None:
    repo.add_to_ticket(change.slot, "## Answers\n\nQ: which way? A: this one")


@does(ActionName.DRAIN_MERGES_TICKET)
def _drain_merges_ticket(repo: Repo, change: Change) -> None:
    name = build_on_branch(repo, change.slot)
    git(repo.root, "switch", "-q", "main")
    git(repo.root, "merge", "-q", "--no-ff", "--no-edit", name)
    repo.merged = name


def park(repo: Repo, slot: int, result: dict[str, object]) -> None:
    ref = repo.ticket_path(slot)
    ticket = preflight.local_ticket(ref, (repo.root / ref).read_text(encoding="utf-8"))
    drain.close(
        repo.root, ticket, outcome=drain.PARKED, reasons=drain.reasons_of(result), result=result
    )


GUARD_PATH = "docs/process/layers.md"
QUESTIONS: Final = {"openQuestions": [{"kind": "direction", "question": "which way?"}]}
GUARD_EDITS: Final = {"guardEdits": [{"path": GUARD_PATH, "edit": "allow it"}]}
BLOCKS: Final[dict[Ticket, dict[str, object]]] = {
    Ticket.ASKED: QUESTIONS,
    Ticket.GUARDED: GUARD_EDITS,
    Ticket.ASKED_GUARDED: {**QUESTIONS, **GUARD_EDITS},
    Ticket.PARKED: GUARD_EDITS,
}
NEWER: Final = frozenset({"base"})


@does(ActionName.DRAIN_PARKS_TICKET)
def _drain_parks_ticket(repo: Repo, change: Change) -> None:
    kind = change.after.tickets[change.slot]
    if kind is Ticket.OWES_ITS_ROW:
        (n,) = repo.named
        output = second_instance_judged(repo, f"kind{n}")
        park(repo, change.slot, {"verdict": "blocked", "checkOutput": output})
        return
    park(repo, change.slot, {"verdict": "blocked", **BLOCKS[kind]})
    if kind is Ticket.PARKED:
        path = queue_notes.note_path(repo.root, repo.ticket_branch(change.slot))
        note = queue_notes.read_note(repo.root, repo.ticket_branch(change.slot))
        queue_notes.write_note(path, {k: v for k, v in note.items() if k not in NEWER})


@does(ActionName.DRAIN_PARKS_FOR_A_SAFEGUARD)
def _drain_parks_for_a_safeguard(repo: Repo, _change: Change) -> None:
    repo.tools.append(None)
    kind = f"kind{len(repo.tools)}"
    files = {"docs/process/binding.md": repo.binding(), f"src/{kind}.py": "import requests\n"}
    name = build_on_branch(repo, 0, files)
    git(repo.root, "switch", "-q", "main")
    git(repo.root, "merge", "-q", "--no-ff", "--no-edit", name)
    repo.merged = name
    park(repo, 1, {"verdict": "blocked", "checkOutput": second_instance_judged(repo, kind)})


TRIGGERS: Final = Path(triggers.__file__)


def second_instance_judged(repo: Repo, kind: str) -> str:
    tree = repo.root.parent / f"{repo.root.name}.queue"
    git(repo.root, "worktree", "add", "-q", "--detach", str(tree), "main")
    try:
        write(tree, f"src/{kind}-b.py", "import requests\n")
        snapshot(tree, "feat: a second instance of the kind")
        ran = subprocess.run(  # noqa: S603 # -- the plugin's own gate, no shell
            [sys.executable, str(TRIGGERS), "--root", str(tree)],
            capture_output=True,
            text=True,
            check=False,
        )
    finally:
        git(repo.root, "worktree", "remove", "--force", str(tree))
    assert ran.returncode == 1, f"the triggers gate is red on a second instance:\n{ran.stdout}"
    return ran.stdout


@does(ActionName.OWNER_ANSWERS_ON_THE_TICKET)
def _owner_answers_on_the_ticket(repo: Repo, change: Change) -> None:
    repo.add_to_ticket(change.slot, "## Answers\n\nQ: which way? A: that one")


@does(ActionName.OWNER_LANDS_GUARD_EDITS)
def _owner_lands_guard_edits(repo: Repo, change: Change) -> None:
    repo.edits += 1
    if change.after.waits:
        repo.guarded = f"fix/guard-edits-{repo.edits}"
        branch(repo.root, repo.guarded)
        write(repo.root, change_description(repo.guarded), CHANGE_DESCRIPTION)
    write(repo.root, GUARD_PATH, f"The guard edit the run named ({repo.edits}).\n")
    snapshot(repo.root, "chore(guards): land the guard edits the run named")
    git(repo.root, "switch", "-q", "main")


def change_description(name: str) -> str:
    return f"{route.CHANGES}/{name.replace('/', '-')}.md"


CHANGE_DESCRIPTION = "## Summary\n\nThe guard edits the run named.\n"


@does(ActionName.OWNER_MERGES_THE_WAITING_BRANCH)
def _owner_merges_the_waiting_branch(repo: Repo, _change: Change) -> None:
    git(repo.root, "merge", "-q", "--no-ff", "--no-edit", str(repo.guarded))
    repo.guarded = None


@does(ActionName.OWNER_CHANGES_TICKET)
def _owner_changes_ticket(repo: Repo, change: Change) -> None:
    repo.add_to_ticket(change.slot, "## Scope\n\nNarrowed: the first half only")


@does(ActionName.KIND_OF_CODE_ARRIVES)
def _kind_of_code_arrives(repo: Repo, _change: Change) -> None:
    repo.tools.append(None)
    write(repo.root, "docs/process/binding.md", repo.binding())
    write(repo.root, f"src/kind{len(repo.tools)}.py", "import requests\n")
    snapshot(repo.root, "feat: the first of a kind")


@does(ActionName.KIND_THAT_REOPENS_THE_ARCHITECTURE_ARRIVES)
def _kind_that_reopens_the_architecture_arrives(repo: Repo, _change: Change) -> None:
    assert len(repo.reopened) < len(REOPENING), "every kind that reopens it has arrived"
    kind = REOPENING[len(repo.reopened)]
    repo.reopened.append(kind)
    write(repo.root, "docs/process/binding.md", repo.binding())
    write(repo.root, f"src/{kind}.py", "x = 1\n")
    snapshot(repo.root, "feat: the first of a kind that reopens the architecture")


@does(ActionName.AGENT_LANDS_THE_REVIEW)
def _agent_lands_the_review(repo: Repo, _change: Change) -> None:
    keys = ", ".join(f"`code:{kind}`" for kind in repo.reopened[repo.reviewed :])
    repo.reviewed = len(repo.reopened)
    write(repo.root, repo.next_record("review"), REVIEW.format(keys=keys))
    snapshot(repo.root, "docs(adr): architecture review")


PLUGIN_PARTS: Final = (".claude-plugin", "scripts", "skills", "hooks/ratchet_guards")


@functools.cache
def plugin_at(version: str) -> Path:
    root = Path(tempfile.mkdtemp(prefix="ratchet-plugin-"))
    atexit.register(shutil.rmtree, root, ignore_errors=True)
    for part in PLUGIN_PARTS:
        ignore = shutil.ignore_patterns("__pycache__")
        shutil.copytree(plugin_pin.PLUGIN / part, root / part, symlinks=True, ignore=ignore)
    manifest = root / plugin_pin.MANIFEST
    data = json.loads(manifest.read_text(encoding="utf-8"))
    manifest.write_text(json.dumps({**data, "version": version}), encoding="utf-8")
    shutil.copytree(skeleton(), root / ".git", symlinks=True)
    snapshot(root, f"chore: release {version}")
    return root


def run_upgrade(repo: Repo) -> str:
    plugin = plugin_at(repo.version())
    ran = subprocess.run(  # noqa: S603 # -- the plugin's own upgrade, no shell
        [sys.executable, str(plugin / "scripts/upgrade.py"), "--root", str(repo.root), "--apply"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert ran.returncode == 0, f"upgrade.py exited {ran.returncode}:\n{ran.stdout}"
    return ran.stdout


def landed(repo: Repo, name: str, subject: str) -> None:
    snapshot(repo.root, subject)
    git(repo.root, "switch", "-q", "main")
    git(repo.root, "merge", "-q", "--no-ff", "--no-edit", name)


@does(ActionName.UPGRADE_LEAVES_ROWS_TO_FILL)
def _upgrade_leaves_rows_to_fill(repo: Repo, _change: Change) -> None:
    repo.upgrades += 1
    name = f"chore/upgrade-{repo.version()}"
    git(repo.root, "switch", "-q", "-C", name)
    run_upgrade(repo)
    text = gatelib.read_text(repo.root / route.BINDING) or ""
    if markdown_text.has_section(text, architecture.SECTION):
        body = markdown_text.section(text, architecture.SECTION).rstrip()
        repo.architecture = f"\n{architecture.SECTION}{body}\n"
    landed(repo, name, f"chore(method): upgrade the plugin to {repo.version()}")


FILLED: Final = {
    application_types.SIGNS_SECTION: (
        "| Event | Paths |\n| --- | --- |\n| deployed | none: it runs nowhere yet |\n"
        "| irreplaceable-data | none: it keeps no records |\n"
    ),
    "## Install": "- Install: none: the tools arrive on demand\n",
    "## Dependencies": (
        "- Manifests: none: it declares no package\n- Lockfiles: none: it locks no package\n"
        "- Entry point: none: there is nothing to read\n- Registries: none\n"
    ),
    "## Image pins": "| Files | Reference |\n| --- | --- |\n| none: no file names an image | |\n",
    "## Suppressions": (
        "| Kind | Pattern | Rule |\n| --- | --- | --- |\n\n| Question | Kinds |\n| --- | --- |\n"
    ),
    comments.SECTION: (
        "| Files | Comments |\n| --- | --- |\n| `**` | none: nobody writes it by hand |\n"
    ),
    documents.SECTION: (
        "| Kind | Paths | Prune |\n| --- | --- | --- |\n"
        "| forge templates | `.github/**` | `never` |\n"
    ),
}


def with_body(text: str, heading: str, body: str) -> str:
    lines = text.split("\n")
    if heading not in lines:
        return text
    start = lines.index(heading)
    end = next((n for n in range(start + 1, len(lines)) if lines[n].startswith("## ")), len(lines))
    return "\n".join([*lines[: start + 1], "", *body.rstrip("\n").split("\n"), "", *lines[end:]])


def with_changed_tests(repo: Repo, text: str) -> str:
    table = (
        "| Suite | Gate | Files | Run files |\n| --- | --- | --- | --- |\n"
        f"{conformance.SELF_TEST_SUITES}\n"
    )
    row = conformance.template_gate_row("changed tests run")
    text = text.replace("\n## Tests\n", f"\n## Tests\n\n{table}", 1)
    if "\n| changed tests run |" not in text:
        text = text.replace("| test count did not fall |", f"{row}\n| test count did not fall |", 1)
    path = repo.root / "justfile"
    if path.exists():
        listed = (gatelib.read_text(path) or "").replace(
            'tier1_gates := "test coverage', 'tier1_gates := "test coverage tests-changed', 1
        )
        write(repo.root, "justfile", listed)
    return text


@does(ActionName.UPGRADE_ASKS_THE_FLAG_LINE)
def _upgrade_asks_the_flag_line(repo: Repo, _change: Change) -> None:
    repo.asked = True
    tree = repo.root
    if repo.head() != "main":
        tree = repo.root.parent / f"{repo.root.name}.upgrade"
        git(repo.root, "worktree", "add", "-q", str(tree), "main")
    pin = re.compile(r"^- Ratchet plugin: .*$", re.MULTILINE)
    kept = pin.search(gatelib.read_text(tree / route.BINDING) or "")
    made = repo.binding()
    write(tree, str(route.BINDING), pin.sub(lambda _line: kept.group(), made) if kept else made)
    snapshot(tree, "chore(method): the project is a command, not yet deployed")
    if tree != repo.root:
        git(repo.root, "worktree", "remove", str(tree))


@does(ActionName.AGENT_FILLS_ROWS)
def _agent_fills_rows(repo: Repo, _change: Change) -> None:
    name = "chore/fill-owed"
    git(repo.root, "switch", "-q", "-C", name)
    text = gatelib.read_text(repo.root / route.BINDING) or ""
    for heading, body in FILLED.items():
        text = with_body(text, heading, body)
    write(repo.root, str(route.BINDING), with_changed_tests(repo, text))
    run_upgrade(repo)
    landed(repo, name, "docs(process): fill the rows the upgrade left")


@does(ActionName.OWNER_RECORDS_ARCHITECTURE)
def _owner_records_architecture(repo: Repo, _change: Change) -> None:
    git(repo.root, "switch", "-q", "-C", ARCHITECTURE_BRANCH)
    repo.record_architecture()
    repo.settle(lambda entry: entry.rule == route.ARCHITECTURE_RULE)
    snapshot(repo.root, "chore(method): record the architecture")


@does(ActionName.UPGRADE_FINDS_AN_EXPIRED_UNIT)
def _upgrade_finds_an_expired_unit(repo: Repo, _change: Change) -> None:
    write(repo.root, repo.spec_path(), LEFT_SPEC)
    for slot in (0, 1):
        repo.write_ticket(slot, Ticket.RESOLVED, inherits=False)
    snapshot(repo.root, "docs(tracker): a unit a plugin from before ADR 0064 left")


@does(ActionName.AGENT_DELETES_THE_EXPIRED_UNIT)
def _agent_deletes_the_expired_unit(repo: Repo, _change: Change) -> None:
    expire(repo)


def kept_branch(kind: str) -> str:
    return f"{formal_model.NAMESPACE}/kept-{kind}"


def finds_a_defect(repo: Repo) -> None:
    n = len(repo.tools)
    assert n not in repo.named, "the defect is found binding the row of the last arrival"
    (kind,) = (kind for kind in DEFECT_KINDS if kind not in repo.named.values())
    repo.named[n] = kind
    branch(repo.root, kept_branch(kind))
    write(repo.root, f"formal/{kind}.md", "The kept model, which the code breaks.\n")
    snapshot(repo.root, "chore(formal): the model the safeguard kept")
    git(repo.root, "switch", "-q", "main")
    repo.defect = f"Formal model: defect `{repo.question(n)}`, kept on `{kept_branch(kind)}`"
    repo.spec += 1
    write(repo.root, "docs/process/binding.md", repo.binding())
    repo.write_ticket(0, Ticket.READY, inherits=False)
    snapshot(repo.root, "chore: the safeguard leaves its row owed and files the defect")


def binds_from_the_kept_model(repo: Repo) -> None:
    (kind,) = repo.named.values()
    git(repo.root, "merge", "-q", "--no-ff", "--no-edit", kept_branch(kind))
    in_sync(repo)


@does(ActionName.AGENT_BINDS_SAFEGUARD)
def _agent_binds_safeguard(repo: Repo, change: Change) -> None:
    if change.after.defect is Defect.OPEN:
        finds_a_defect(repo)
        return
    if change.before.defect is Defect.KEPT:
        binds_from_the_kept_model(repo)
    repo.tools = [tool or "`just safe`" for tool in repo.tools]
    write(repo.root, "docs/process/binding.md", repo.binding())
    snapshot(repo.root, "chore: bind the safeguard")


def in_sync(repo: Repo) -> None:
    text = gatelib.read_text(repo.root / route.BINDING) or ""
    if markdown_text.has_section(text, formal_model.SECTION):
        return
    if FORMAL_GROUP not in repo.manifest and 'name = "formal"' not in repo.manifest:
        repo.manifest += FORMAL_GROUP
        write(repo.root, "guards/manifest.toml", repo.manifest)
    write(repo.root, "formal/model.md", "The model of the mirrored code.\n")
    write(repo.root, f"{MIRRORED}/__init__.py", "")
    tip = commit(repo.root, "chore(formal): the model and the code it mirrors")
    write(repo.root, str(route.BINDING), text + FORMAL_SECTION.format(synced=tip))
    snapshot(repo.root, "chore(formal): the binding names what the model mirrors")


def mirrored_change(repo: Repo, name: str) -> None:
    repo.edits += 1
    write(repo.root, f"{MIRRORED}/{name}{repo.edits}.py", "x = 1\n")


@does(ActionName.MIRRORED_CODE_LANDS)
def _mirrored_code_lands(repo: Repo, _change: Change) -> None:
    in_sync(repo)
    mirrored_change(repo, "change")
    snapshot(repo.root, "feat: a change to the code the model mirrors")


ROUND_TICKET = (
    "# 01: R{k}\n\nStatus: ready-for-agent\n\nFormal model: round, kept on `{kept}`\n\n"
    f"{CRITERIA}\n\n- [ ] The code keeps the property the round found broken.\n"
)


def stated_round_ticket(repo: Repo, unstated: str) -> str:
    text = f"{unstated}\n{CONJECTURE}"
    stated = f"{text}\n{criteria_line(text, CRITERIA)}"
    return f"{stated}\n{FLAG_ANSWER}" if repo.asked else stated


def round_unit(repo: Repo) -> str:
    return f".scratch/formal-{repo.rounds}"


def round_ticket_path(repo: Repo) -> str:
    return f"{round_unit(repo)}/issues/01-r{repo.rounds}.md"


@does(ActionName.AGENT_RUNS_A_ROUND)
def _agent_runs_a_round(repo: Repo, change: Change) -> None:
    repo.rounds += 1
    tip = git(repo.root, "rev-parse", "main")
    checked = tip[: formal_model.SHORT]
    if change.after.upkeep in {Upkeep.TICKET, Upkeep.UNSTATED}:
        kept = f"{formal_model.NAMESPACE}/kept-upkeep-{checked}"
        branch(repo.root, kept)
        write(repo.root, "formal/model.md", f"The model, updated at {checked}.\n")
        snapshot(repo.root, "chore(formal): the round's update, which the code breaks")
        git(repo.root, "switch", "-q", "main")
        text = ROUND_TICKET.format(k=repo.rounds, kept=kept)
        if change.after.upkeep is Upkeep.TICKET:
            text = stated_round_ticket(repo, text)
        write(repo.root, round_ticket_path(repo), text)
        snapshot(repo.root, "docs(tracker): the bug the round found")
        return
    repo.waiting = f"{formal_model.UPKEEP}{checked}"
    branch(repo.root, repo.waiting)
    path = repo.root / route.BINDING
    synced = SYNCED_LINE.sub(f"- Synced: {tip}", path.read_text(encoding="utf-8"))
    path.write_text(synced, encoding="utf-8")
    snapshot(repo.root, "chore(formal): the round checked the model against the code")
    git(repo.root, "switch", "-q", "main")


@does(ActionName.OWNER_MERGES_THE_ROUND)
def _owner_merges_the_round(repo: Repo, _change: Change) -> None:
    git(repo.root, "merge", "-q", "--no-ff", "--no-edit", str(repo.waiting))
    repo.waiting = None


@does(ActionName.AGENT_FIXES_THE_ROUND_TICKET)
def _agent_fixes_the_round_ticket(repo: Repo, _change: Change) -> None:
    git(repo.root, "switch", "-q", "-c", f"feature/round-{repo.rounds}")
    mirrored_change(repo, "fix")
    git(repo.root, "rm", "-r", "-q", "--", round_unit(repo))
    snapshot(repo.root, "fix: the bug the round found")
    merge_branch(repo)


@does(ActionName.MERGES_LEAVE_A_PAGE_STALE)
def _merges_leave_a_page_stale(repo: Repo, _change: Change) -> None:
    if (gatelib.read_text(repo.root / README) or "") != STALE_README:
        write(repo.root, README, STALE_README)
        write(repo.root, CITED, "rule = 1\n")
        snapshot(repo.root, "docs: the readme says where the rule lives")
    for _ in range(documents.PRUNE_AFTER_DEFAULT):
        repo.edits += 1
        git(repo.root, "switch", "-q", "-c", f"change-{repo.edits}")
        path = repo.root / CITED
        path.write_text(f"{path.read_text(encoding='utf-8')}rule{repo.edits} = 1\n", "utf-8")
        snapshot(repo.root, "feat: the rule changes")
        merge_branch(repo)


def prune_branch(repo: Repo, subject: str) -> str:
    repo.prunes += 1
    name = f"chore/prune-{subject}-{repo.prunes}"
    git(repo.root, "switch", "-q", "-c", name)
    return name


@does(ActionName.AGENT_PRUNES_COMMENTS)
def _agent_prunes_comments(repo: Repo, _change: Change) -> None:
    prune_branch(repo, "comments")
    write(repo.root, COMMENTED, "x = 1\n")
    repo.settle(lambda entry: entry.rule == owed.COMMENTS)
    snapshot(repo.root, f"refactor(prune): {COMMENTED}")
    merge_branch(repo)


def cuts_a_page(change: Change) -> bool:
    return zlib.crc32(repr(change.before).encode()) % 2 == 1


@does(ActionName.AGENT_PRUNES_PAGES)
def _agent_prunes_pages(repo: Repo, change: Change) -> None:
    if not cuts_a_page(change):
        assert documents.passed(repo.root), "the default branch has a commit to count from"
        return
    prune_branch(repo, "pages")
    write(repo.root, README, CUT_README)
    snapshot(repo.root, f"docs(prune): {README}")
    merge_branch(repo)


@does(ActionName.SOMEONE_EDITS_CODE)
def _someone_edits_code(repo: Repo, _change: Change) -> None:
    repo.edits += 1
    write(repo.root, f"src/edit{repo.edits}.py", "x = 1\n")


@does(ActionName.AGENT_COMMITS_ON_A_BRANCH)
def _agent_commits_on_a_branch(repo: Repo, change: Change) -> None:
    if change.before.head is Head.MAIN:
        branch(repo.root, f"change-{repo.edits}")
    snapshot(repo.root, "feat: a change")


def merge_branch(repo: Repo) -> str:
    name = repo.head()
    git(repo.root, "switch", "-q", "main")
    git(repo.root, "merge", "-q", "--no-ff", "--no-edit", name)
    repo.merged = name
    return name


@does(ActionName.BRANCH_MERGES)
def _branch_merges(repo: Repo, change: Change) -> None:
    if change.before.head is Head.WORK:
        merge_branch(repo)


@does(ActionName.MERGE_LEAVES_THE_BRANCH_CHECKED_OUT)
def _merge_leaves_the_branch_checked_out(repo: Repo, _change: Change) -> None:
    git(repo.root, "switch", "-q", merge_branch(repo))


@does(ActionName.SOMEONE_CHECKS_OUT_A_MERGED_BRANCH)
def _someone_checks_out_a_merged_branch(repo: Repo, _change: Change) -> None:
    assert repo.merged, "a repository at rest has merged a branch, the adoption's at least"
    git(repo.root, "switch", "-q", repo.merged)


@does(ActionName.AGENT_SWITCHES_TO_THE_DEFAULT_BRANCH)
def _agent_switches_to_the_default_branch(repo: Repo, _change: Change) -> None:
    git(repo.root, "switch", "-q", "main")


@does(ActionName.RECORDS_RUN_STOPS)
def _records_run_stops(repo: Repo, _change: Change) -> None:
    branch(repo.root, repo.next_records_branch())
    git(repo.root, "add", "--all")
    if repo.records % 2 == 0:
        snapshot(repo.root, "docs(records): land what the owner's conversation wrote")


@does(ActionName.AGENT_LANDS_RECORDS)
def _agent_lands_records(repo: Repo, change: Change) -> None:
    if change.before.head is Head.MAIN:
        branch(repo.root, repo.next_records_branch(named=named_by_an_agent(change)))
    name = repo.head()
    if git(repo.root, "status", "--porcelain"):
        snapshot(repo.root, "docs(records): land what the owner's conversation wrote")
    git(repo.root, "switch", "-q", "main")
    if change.after.parked:
        repo.parked = name
    else:
        git(repo.root, "merge", "-q", "--no-ff", "--no-edit", name)


@does(ActionName.OWNER_MERGES_RECORDS)
def _owner_merges_records(repo: Repo, _change: Change) -> None:
    git(repo.root, "merge", "-q", "--no-ff", "--no-edit", str(repo.parked))
    repo.parked = None


def remove_hooks(repo: Repo) -> None:
    for name in ("pre-commit", "commit-msg"):
        (repo.root / ".git/hooks" / name).unlink()


def reinstall_hooks(repo: Repo) -> None:
    install_hooks(repo.root)


STAGES: Final = {
    "init": Advice.INIT,
    "tracker": Advice.TRACKER,
    "adopt": Advice.ADOPT,
    "hooks": Advice.HOOKS,
    "change": Advice.COMMIT,
    "switch": Advice.SWITCH,
    "merge": Advice.MERGE,
    "owner-merge": Advice.OWNER_MERGE,
    "fill": Advice.FILL,
    "architecture-review": Advice.REVIEW,
    "safeguard": Advice.SAFEGUARD,
    "architecture": Advice.ARCHITECTURE,
    "formal-ticket": Advice.FORMAL_TICKET,
    "formal-round": Advice.FORMAL_ROUND,
    "formal-upkeep": Advice.FORMAL_UPKEEP,
    "expire": Advice.EXPIRE,
    "triage": Advice.TRIAGE,
    "resolve": Advice.RESOLVE,
    "blocked": Advice.BLOCKED,
    "drain": Advice.DRAIN,
    "build": Advice.BUILD,
    "conjecture": Advice.CONJECTURE,
}
RELEASES: Final = {
    "answer the questions or land the guard edits the blocked run": Advice.BLOCKED_RUN,
    "answer the open questions the run of": Advice.ANSWER,
    "land the guard edits the run of": Advice.LAND_GUARD,
    "change ticket": Advice.CHANGE_TICKET,
}
WAITING: Final = "its change holds only records"
PRUNES: Final = {
    f"{route.PRUNE} comments": Advice.PRUNE_COMMENTS,
    f"{route.PRUNE} pages": Advice.PRUNE_PAGES,
}


def classify(step: route.Step) -> Advice | None:
    if step.stage == "records":
        return _records(step)
    if step.stage == "specify":
        return _specify(step)
    if step.stage == "prune":
        return PRUNES.get(step.command)
    if step.stage == "blocked-run":
        return next(
            (label for lead, label in RELEASES.items() if step.command.startswith(lead)), None
        )
    return STAGES.get(step.stage)


def _records(step: route.Step) -> Advice | None:
    if step.command == route.GRILL:
        return Advice.GRILL
    if "--records" in step.command:
        return Advice.FINISH_RECORDS if "stopped on branch" in step.why else Advice.LAND_RECORDS
    return Advice.MERGE_RECORDS if WAITING in step.why else None


def _specify(step: route.Step) -> Advice | None:
    if step.command == route.SPEC:
        return Advice.SPECIFY
    if step.command.startswith(route.CONJECTURE):
        return Advice.CONJECTURE_SPEC
    return Advice.SPLIT if step.command.startswith(route.TICKETS) else None


def read(repo: Repo) -> route.Step:
    return route.decide(repo.root, None)
