"""The route's reference model made real: each model action done on a git repository.

``apply`` leaves a scratch repository as the documented command leaves it: ``/ratchet:init``
commits ``AGENTS.md``, ``/ratchet:adopt`` commits the binding and the manifest on its own branch
and merges it, the spec skills leave records uncommitted on the default branch, ``merge --records``
commits them on a ``records/<time>`` branch and merges it or leaves it parked, and the drain
writes its note through ``drain.close``. An owner's edit of a ticket is committed on the default
branch at once, as the model has it. ``classify`` reads a real ``route.Step`` back as the model's
advice label.

``apply`` never runs ``scripts/merge.py``. Each merge it makes, of a working branch, of a records
branch, or the drain's, is written with git: the merge that leaves the default branch checked out,
the one that leaves the merged branch checked out, and the merge of a branch already merged, which
changes nothing. The sweep's binding names ``just merge``, which nothing here can run. A repository
made by ``Repo.merging`` names ``merge.py`` and green gates instead, and the tests run the real
entry point there, from a clean working branch and from the adoption's branch, and again on the
branch it merged; ``merge --records`` is never run.

``/ratchet:adopt`` writes the architecture decision record and the binding's ``## Architecture``
section, as the adoption has since ADR 0049; an adoption by a plugin from before it writes
neither. An upgrade runs the plugin's own code for the ``architecture`` check: it makes the edit
the check offers and writes down in ``quality/owed.toml`` what the check still finds, which is
nothing where the architecture is recorded. ``/ratchet:architecture`` writes the record and the
parts, settles the ledger, and leaves its branch for the owner to merge.

A spec and its tickets live under ``.scratch/f<n>/``, where ``n`` counts the specs written, and
each ticket's title names ``n``, so the branch ``feature/<number>-<slug>`` is new for every spec
and a branch merged for an earlier spec never matches a later ticket.
"""

from __future__ import annotations

import os
import shutil
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from typing import TYPE_CHECKING, Final

import application_types
import architecture
import conformance
import drain
import feature_preflight as preflight
import gatelib
import markdown_text
import merge
import merge_ready
import owed
import queue_notes
import route
import triggers
import upgrade
from tests.repos import branch, git, install_hooks, repository, snapshot, write
from tests.route_model import (
    STATED,
    ActionName,
    Advice,
    Architecture,
    Head,
    Setup,
    Ticket,
    Tree,
    World,
    runnable_slots,
)
from tests.test_drain import MERGE

if TYPE_CHECKING:
    from pathlib import Path

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
    "# Binding\n\n{pin}- Default branch: `main`\n- Life events: none\n\n"
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
    "\n" + architecture.SECTION + "\n\n- Decision: `{record}`\n\n| Part | Path | May depend on |\n"
    "| --- | --- | --- |\n| rules | `src/rules/**` | nothing |\n| web | `src/**` | rules |\n"
)
ARCHITECTURE_BRANCH = "chore/architecture"
FILLED: Final = owed.Entry("docs/process/binding.md", "pins", "has no section", "")
REVIEW_ROW = (
    f"| {triggers.REVIEW_QUESTION} | `ratchet` plugin `scripts/triggers.py` | 1 | |"
    " `just triggers` |\n"
)
# Why each arrival takes a kind of its own: a kind reviewed once is reviewed, so the next arrival
# that reopens the architecture is the first of another kind.
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
GLOSSARY = "# Glossary\n\n**Customer**: who pays.\n"
PLACEHOLDER = "# <project>\n"
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
LETTERS: Final = "ab"


def share_objects(source: str, target: str) -> None:
    """Copy one file of a repository, linking it when it is one of git's objects."""
    if f"{os.sep}.git{os.sep}objects{os.sep}" in source:
        os.link(source, target)
    else:
        shutil.copy2(source, target)


@dataclass
class Repo:
    """A scratch repository, and the counters that name what the actions write into it."""

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
    entries: dict[str, str] = field(default_factory=lambda: dict(ENTRY_POINTS))
    manifest: str = MANIFEST

    @classmethod
    def start(cls, parent: Path) -> Repo:
        """A repository on ``main`` holding a README, where nothing names the method."""
        return cls(repository(parent / "repo", {"README.md": "# A\n"}))

    @classmethod
    def merging(cls, parent: Path) -> Repo:
        """``start``, adopted with entry points the real merge runs: ``merge.py`` and green gates.

        Why the template's manifest: the merge judges the adoption by its own gates, and those
        refuse a manifest that guards less than the template.
        """
        return replace(
            cls.start(parent),
            entries={"check": "true", "check-deep": "true", "merge": MERGE},
            manifest=merge_ready.TEMPLATE.read_text(encoding="utf-8"),
        )

    def copy(self, root: Path) -> Repo:
        """The same repository at ``root``, its counters with it.

        Why the objects are linked, not copied: git never changes an object file once written,
        and they are most of the files a copy would write.
        """
        shutil.copytree(self.root, root, symlinks=True, copy_function=share_objects)
        return replace(self, root=root, tools=list(self.tools), reopened=list(self.reopened))

    def feature(self) -> str:
        """The directory of the current spec, relative to the root."""
        return f".scratch/f{self.spec}"

    def head(self) -> str:
        """The branch HEAD is on."""
        return git(self.root, "rev-parse", "--abbrev-ref", "HEAD")

    def binding(self, *, pinned: bool = True) -> str:
        """The binding: each safeguard row owed (``later:``) or bound to a tool, and the review's.

        The review's row is there from the adoption, so the reviews start with it (ADR 0052). The
        pin names the plugin of the last upgrade, and ``architecture`` is the binding's
        ``## Architecture`` section, empty while it has none.
        """
        gates = "".join(
            f"| q{n} | {tool or f'later: code:kind{n}'} | 1 | | `just q{n}` |\n"
            for n, tool in enumerate(self.tools, 1)
        )
        rows = "".join(
            f"| kind{n} | `src/kind{n}.py` | |\n" for n, _tool in enumerate(self.tools, 1)
        ) + "".join(f"| {kind} | `src/{kind}.py` | |\n" for kind in self.reopened)
        return BINDING.format(
            pin=PIN.format(version=self.version()) if pinned else UNPINNED,
            entries="".join(
                f"| {name} | `{command}` |\n" for name, command in self.entries.items()
            ),
            gates=gates + REVIEW_ROW,
            triggers=rows,
            architecture=self.architecture,
        )

    def version(self) -> str:
        """The plugin version the binding pins: one more minor version for each upgrade."""
        return f"999.{self.upgrades}.0"

    def next_record(self, slug: str) -> str:
        """The path of a new decision record, under the next free number in ``docs/adr/``."""
        number = len(list((self.root / "docs/adr").glob("*.md"))) + 1
        return f"docs/adr/{number:04d}-{slug}.md"

    def record_architecture(self) -> None:
        """Write the architecture decision record and the binding's section that names it."""
        record = self.next_record("the-architecture")
        write(self.root, record, ARCHITECTURE_RECORD)
        self.architecture = ARCHITECTURE_SECTION.format(record=record)
        write(self.root, "docs/process/binding.md", self.binding())

    def settle(self, done: Callable[[owed.Entry], bool]) -> None:
        """Delete the ledger's entries ``done`` picks, as the step that settles them does."""
        left = [entry for entry in owed.read(self.root).entries if not done(entry)]
        write(self.root, str(owed.LEDGER), owed.render(left))

    def ticket_path(self, slot: int) -> str:
        """The local ticket in ``slot``: ``.scratch/f<n>/issues/NN-<letter><n>.md``."""
        return f"{self.feature()}/issues/{slot + 1:02d}-{LETTERS[slot]}{self.spec}.md"

    def ticket_branch(self, slot: int) -> str:
        """The branch the feature skill builds the ticket in ``slot`` on."""
        return f"feature/{slot + 1}-{LETTERS[slot]}{self.spec}"

    def ticket_text(self, slot: int, ticket: Ticket, *, inherits: bool) -> str:
        """The ticket file: title, blocker, status and, when it has one, its own conjecture."""
        blocker = "Blocked by: 01\n\n" if slot == 1 else ""
        head = f"# {slot + 1:02d}: {LETTERS[slot].upper()}{self.spec}\n\n{blocker}"
        own = CONJECTURE if ticket in OWN_CONJECTURE and not inherits else ""
        status = STATUS.get(ticket, "ready-for-agent")
        return f"{head}Status: {status}\n" + (f"\n{own}" if own else "")

    def write_ticket(self, slot: int, ticket: Ticket, *, inherits: bool) -> None:
        """Write the ticket in ``slot`` in its new state, leaving it uncommitted."""
        write(self.root, self.ticket_path(slot), self.ticket_text(slot, ticket, inherits=inherits))

    def add_to_ticket(self, slot: int, text: str) -> None:
        """Append ``text`` to the ticket in ``slot`` and commit it: its digest changes."""
        self.edits += 1
        path = self.root / self.ticket_path(slot)
        path.write_text(f"{path.read_text(encoding='utf-8')}\n{text} ({self.edits})\n", "utf-8")
        snapshot(self.root, f"docs(tracker): {text.lower()}")

    def next_records_branch(self) -> str:
        """A fresh ``records/<time>`` name; a counter stands for the time."""
        self.records += 1
        return f"{merge.RECORDS}20261113-{self.records:06d}"


@dataclass(frozen=True)
class Change:
    """The two worlds an action takes the repository between."""

    before: World
    after: World

    @property
    def slot(self) -> int:
        """The ticket the action moved."""
        pairs = zip(self.before.tickets, self.after.tickets, strict=True)
        return next(slot for slot, (old, new) in enumerate(pairs) if old != new)


Handler = Callable[[Repo, Change], None]
DO: Final[dict[ActionName, Handler]] = {}


def does(name: ActionName) -> Callable[[Handler], Handler]:
    """Register the function that performs action ``name`` on a repository."""

    def register(handler: Handler) -> Handler:
        DO[name] = handler
        return handler

    return register


def apply(repo: Repo, name: ActionName, change: Change) -> None:
    """Perform ``name`` on ``repo``, as the model says it takes ``change.before`` to ``.after``."""
    DO[name](repo, change)


@does(ActionName.OWNER_INITS)
def _owner_inits(repo: Repo, _change: Change) -> None:
    write(repo.root, "AGENTS.md", STANZA)
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
    """Check out the adoption's branch, made off the default branch if it is new."""
    if repo.head() != ADOPTION:
        branch(repo.root, ADOPTION)


def adoption(repo: Repo, change: Change, *, pinned: bool) -> None:
    """The binding, the manifest and the glossary's template; the architecture, where asked."""
    write(repo.root, "docs/process/binding.md", repo.binding(pinned=pinned))
    write(repo.root, "guards/manifest.toml", repo.manifest)
    write(repo.root, "GLOSSARY.md", PLACEHOLDER)
    if change.after.architecture is Architecture.RECORDED:
        repo.record_architecture()


def adopt(repo: Repo, change: Change) -> None:
    """A whole adoption, committed on its branch and merged."""
    on_adoption_branch(repo)
    adoption(repo, change, pinned=True)
    snapshot(repo.root, "chore(method): adopt")
    git(repo.root, "switch", "-q", "main")
    git(repo.root, "merge", "-q", "--no-ff", "--no-edit", ADOPTION)
    repo.merged = ADOPTION


DO[ActionName.OWNER_ADOPTS] = adopt
DO[ActionName.OLDER_PLUGIN_ADOPTS] = adopt


@does(ActionName.ADOPTION_STOPS)
def _adoption_stops(repo: Repo, change: Change) -> None:
    on_adoption_branch(repo)
    pinned = change.after.setup is Setup.BOUND
    adoption(repo, change, pinned=pinned)
    if not pinned:
        snapshot(repo.root, "chore(method): adopt, the templates")


@does(ActionName.AGENT_INSTALLS_HOOKS)
def _agent_installs_hooks(repo: Repo, _change: Change) -> None:
    install_hooks(repo.root)


@does(ActionName.OWNER_GRILLS)
def _owner_grills(repo: Repo, _change: Change) -> None:
    write(repo.root, "GLOSSARY.md", GLOSSARY)
    write(repo.root, "docs/design-brief.md", "# Brief\n")


@does(ActionName.OWNER_WRITES_SPEC)
def _owner_writes_spec(repo: Repo, _change: Change) -> None:
    repo.spec += 1
    write(repo.root, f"{repo.feature()}/spec.md", "# Settle\n")


@does(ActionName.OWNER_STATES_SPEC_CONJECTURE)
def _owner_states_spec_conjecture(repo: Repo, _change: Change) -> None:
    write(repo.root, f"{repo.feature()}/spec.md", f"# Settle\n\n{CONJECTURE}")


@does(ActionName.OWNER_SPLITS_SPEC)
def _owner_splits_spec(repo: Repo, _change: Change) -> None:
    for slot in (0, 1):
        repo.write_ticket(slot, Ticket.TRIAGE, inherits=False)


def rewrites_ticket(repo: Repo, change: Change) -> None:
    """The owner edits the ticket's file; committed when the model has the tree clean after."""
    repo.write_ticket(
        change.slot, change.after.tickets[change.slot], inherits=change.after.inherits
    )
    if change.after.tree is Tree.CLEAN:
        snapshot(repo.root, "docs(tracker): the owner's edit of a ticket")


for _name in (
    ActionName.OWNER_AGREES_TICKET,
    ActionName.OWNER_WORKS_TICKET_THEMSELF,
    ActionName.OWNER_STATES_CONJECTURE,
    ActionName.OWNER_RESOLVES_TICKET,
):
    DO[_name] = rewrites_ticket


def build_on_branch(repo: Repo, slot: int, files: dict[str, str] | None = None) -> str:
    """Switch to the ticket's feature branch, off the default branch, with one commit of code."""
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
    """The build stops with its code uncommitted, or just after ``/ratchet:feature`` cut the branch.

    Why the first ticket that can start: a stop before anything is written changes no ticket.
    """
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
    """The drain's close of a ticket it parked, as ``drain.py close --result`` writes it."""
    ref = repo.ticket_path(slot)
    ticket = preflight.local_ticket(ref, (repo.root / ref).read_text(encoding="utf-8"))
    drain.close(
        repo.root, ticket, outcome=drain.PARKED, reasons=drain.reasons_of(result), result=result
    )


GUARD_PATH = "docs/process/layers.md"
BLOCKS: Final[dict[Ticket, dict[str, object]]] = {
    Ticket.ASKED: {"openQuestions": [{"kind": "direction", "question": "which way?"}]},
    Ticket.GUARDED: {"guardEdits": [{"path": GUARD_PATH, "edit": "allow it"}]},
    Ticket.PARKED: {"guardEdits": [{"path": GUARD_PATH, "edit": "allow it"}]},
}
NEWER: Final = frozenset({"base"})


@does(ActionName.DRAIN_PARKS_TICKET)
def _drain_parks_ticket(repo: Repo, change: Change) -> None:
    kind = change.after.tickets[change.slot]
    park(repo, change.slot, {"verdict": "blocked", **BLOCKS[kind]})
    if kind is Ticket.PARKED:
        # Why: a note an older drain wrote names the guard hold and its cause but no base commit,
        # so landing the edits cannot release it.
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
    owed = f"triggers: '{kind}' gained 1 instance(s) (src/b.py:2) while the base already held 1"
    park(repo, 1, {"verdict": "blocked", "checkOutput": owed})


@does(ActionName.OWNER_ANSWERS_ON_THE_TICKET)
def _owner_answers_on_the_ticket(repo: Repo, change: Change) -> None:
    repo.add_to_ticket(change.slot, "## Answers\n\nQ: which way? A: that one")


@does(ActionName.OWNER_LANDS_GUARD_EDITS)
def _owner_lands_guard_edits(repo: Repo, _change: Change) -> None:
    repo.edits += 1
    write(repo.root, GUARD_PATH, f"The guard edit the run named ({repo.edits}).\n")
    snapshot(repo.root, "chore(guards): land the guard edits the run named")


@does(ActionName.OWNER_CHANGES_TICKET)
def _owner_changes_ticket(repo: Repo, change: Change) -> None:
    repo.add_to_ticket(change.slot, "Narrowed: the first half only")


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


@does(ActionName.UPGRADE_LEAVES_ROWS_TO_FILL)
def _upgrade_leaves_rows_to_fill(repo: Repo, _change: Change) -> None:
    """What ``upgrade.py --apply`` leaves: the pin moved, a row to fill, the architecture owed.

    The architecture's part is the plugin's own code: the edit the ``architecture`` check offers
    is made as ``upgrade.pass_over`` makes it, and what the check still finds is written down
    through ``owed.recorded``, as ``upgrade.owing`` writes it.
    """
    repo.upgrades += 1
    write(repo.root, "docs/process/binding.md", repo.binding())
    check = conformance.check_architecture
    offered = [conformance.tried(repo.root, check, found) for found in check(repo.root)]
    upgrade.pass_over(repo.root, offered, {})
    text = gatelib.read_text(repo.root / route.BINDING) or ""
    if markdown_text.has_section(text, architecture.SECTION):
        body = markdown_text.section(text, architecture.SECTION).rstrip()
        repo.architecture = f"\n{architecture.SECTION}{body}\n"
    named = conformance.rule_of(check)
    keys = [replace(found, rule=named).key() for found in check(repo.root)]
    added = [replace(FILLED, since=repo.version())] + [
        owed.Entry(path, rule, problem, repo.version()) for rule, path, problem in keys
    ]
    ledger = gatelib.read_text(repo.root / owed.LEDGER)
    write(repo.root, str(owed.LEDGER), owed.recorded(added)(ledger))
    snapshot(repo.root, f"chore(method): upgrade the plugin to {repo.version()}")


@does(ActionName.AGENT_FILLS_ROWS)
def _agent_fills_rows(repo: Repo, _change: Change) -> None:
    repo.settle(lambda entry: entry.rule in owed.FILL_FIRST)
    snapshot(repo.root, "docs(process): fill the rows the upgrade left")


@does(ActionName.OWNER_RECORDS_ARCHITECTURE)
def _owner_records_architecture(repo: Repo, _change: Change) -> None:
    """What ``/ratchet:architecture`` leaves with no forge: its branch, which the owner merges."""
    git(repo.root, "switch", "-q", "-C", ARCHITECTURE_BRANCH)
    repo.record_architecture()
    repo.settle(lambda entry: entry.rule == route.ARCHITECTURE_RULE)
    snapshot(repo.root, "chore(method): record the architecture")


@does(ActionName.AGENT_BINDS_SAFEGUARD)
def _agent_binds_safeguard(repo: Repo, _change: Change) -> None:
    repo.tools = [tool or "`just safe`" for tool in repo.tools]
    write(repo.root, "docs/process/binding.md", repo.binding())
    snapshot(repo.root, "chore: bind the safeguard")


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
    """Merge the branch checked out into the default branch, which is left checked out."""
    name = repo.head()
    git(repo.root, "switch", "-q", "main")
    git(repo.root, "merge", "-q", "--no-ff", "--no-edit", name)
    repo.merged = name
    return name


@does(ActionName.BRANCH_MERGES)
def _branch_merges(repo: Repo, change: Change) -> None:
    """The merge, as the fixed entry point leaves it: merged, and the default branch checked out.

    On a branch already merged it does nothing: the entry point's judge refuses that branch as
    behind, and the tests that run the real entry point show it changes nothing.
    """
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
        branch(repo.root, repo.next_records_branch())
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
    """Delete the repository's commit hooks, as a clone or a reset of ``.git`` leaves it."""
    for name in ("pre-commit", "commit-msg"):
        (repo.root / ".git/hooks" / name).unlink()


def reinstall_hooks(repo: Repo) -> None:
    """Install the commit hooks again."""
    install_hooks(repo.root)


STAGES: Final = {
    "init": Advice.INIT,
    "tracker": Advice.TRACKER,
    "adopt": Advice.ADOPT,
    "hooks": Advice.HOOKS,
    "change": Advice.COMMIT,
    "switch": Advice.SWITCH,
    "merge": Advice.MERGE,
    "fill": Advice.FILL,
    "architecture-review": Advice.REVIEW,
    "safeguard": Advice.SAFEGUARD,
    "architecture": Advice.ARCHITECTURE,
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


def classify(step: route.Step) -> Advice | None:
    """The model's advice label for a real step, or ``None`` when the model has none for it.

    One stage names several labels: ``records`` for the glossary, a stopped run, records to land
    and a branch to merge; ``specify`` for the spec, its conjecture and its split; ``blocked-run``
    for a feature run's stash and each kind of hold the drain's note records. The command tells
    them apart, and the why where two labels share one (``merge --records``).
    """
    if step.stage == "records":
        return _records(step)
    if step.stage == "specify":
        return _specify(step)
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
    return Advice.MERGE_RECORDS if merge.RECORDS in step.command else None


def _specify(step: route.Step) -> Advice | None:
    if step.command == route.SPEC:
        return Advice.SPECIFY
    if step.command.startswith(route.CONJECTURE):
        return Advice.CONJECTURE_SPEC
    return Advice.SPLIT if step.command.startswith(route.TICKETS) else None


def read(repo: Repo) -> route.Step:
    """What the real route says for ``repo``, offline."""
    return route.decide(repo.root, None)
