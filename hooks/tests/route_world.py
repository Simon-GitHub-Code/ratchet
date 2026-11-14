"""The route's reference model made real: each model action done on a git repository.

``apply`` leaves a scratch repository as the documented command leaves it: ``/ratchet:init``
commits ``AGENTS.md``, ``/ratchet:adopt`` commits the binding and the manifest on its own branch
and merges it, the spec skills leave records uncommitted on the default branch, ``merge --records``
commits them on a ``records/<time>`` branch and merges it or leaves it parked, and the drain
writes its note through ``drain.close``. ``scripts/merge.py`` is not run: its two outcomes are
written with git. An owner's edit of a ticket is committed on the default branch at once, as the
model has it. ``classify`` reads a real ``route.Step`` back as the model's advice label.

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

import drain
import feature_preflight as preflight
import merge
import queue_notes
import route
from tests.repos import branch, git, install_hooks, repository, snapshot, write
from tests.route_model import ActionName, Advice, Head, Setup, Ticket, Tree, World

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
PIN = f"- Ratchet plugin: 999.0.0 at {'0' * 40}\n"
UNPINNED = "- Ratchet plugin: <X.Y.Z> at <ratchet-sha>\n"
BINDING = (
    "# Binding\n\n{pin}- Default branch: `main`\n- Life events: none\n\n"
    "| Entry point | Command |\n| --- | --- |\n| check | `just check` |\n"
    "| merge | `just merge` |\n\n"
    "## Gates\n\n| Question | Tool | Tier | Threshold source | Entry point |\n"
    "| --- | --- | --- | --- | --- |\n{gates}\n"
    "## Triggers\n\n| Kind | Paths | Pattern |\n| --- | --- | --- |\n{triggers}"
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

    @classmethod
    def start(cls, parent: Path) -> Repo:
        """A repository on ``main`` holding a README, where nothing names the method."""
        return cls(repository(parent / "repo", {"README.md": "# A\n"}))

    def copy(self, root: Path) -> Repo:
        """The same repository at ``root``, its counters with it.

        Why the objects are linked, not copied: git never changes an object file once written,
        and they are most of the files a copy would write.
        """
        shutil.copytree(self.root, root, symlinks=True, copy_function=share_objects)
        return replace(self, root=root, tools=list(self.tools))

    def feature(self) -> str:
        """The directory of the current spec, relative to the root."""
        return f".scratch/f{self.spec}"

    def head(self) -> str:
        """The branch HEAD is on."""
        return git(self.root, "rev-parse", "--abbrev-ref", "HEAD")

    def binding(self, *, pinned: bool = True) -> str:
        """The binding: each safeguard row owed (``later:``) or bound to a tool."""
        gates = "".join(
            f"| q{n} | {tool or f'later: code:kind{n}'} | 1 | | `just q{n}` |\n"
            for n, tool in enumerate(self.tools, 1)
        )
        triggers = "".join(
            f"| kind{n} | `src/kind{n}.py` | |\n" for n, _tool in enumerate(self.tools, 1)
        )
        return BINDING.format(pin=PIN if pinned else UNPINNED, gates=gates, triggers=triggers)

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


def adoption(repo: Repo, *, pinned: bool) -> None:
    write(repo.root, "docs/process/binding.md", repo.binding(pinned=pinned))
    write(repo.root, "guards/manifest.toml", MANIFEST)
    write(repo.root, "GLOSSARY.md", PLACEHOLDER)


@does(ActionName.OWNER_ADOPTS)
def _owner_adopts(repo: Repo, _change: Change) -> None:
    on_adoption_branch(repo)
    adoption(repo, pinned=True)
    snapshot(repo.root, "chore(method): adopt")
    git(repo.root, "switch", "-q", "main")
    git(repo.root, "merge", "-q", "--no-ff", "--no-edit", ADOPTION)


@does(ActionName.ADOPTION_STOPS)
def _adoption_stops(repo: Repo, change: Change) -> None:
    on_adoption_branch(repo)
    pinned = change.after.setup is Setup.BOUND
    adoption(repo, pinned=pinned)
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


def park(repo: Repo, slot: int, result: dict[str, object]) -> None:
    """The drain's close of a ticket it parked, as ``drain.py close --result`` writes it."""
    ref = repo.ticket_path(slot)
    ticket = preflight.local_ticket(ref, (repo.root / ref).read_text(encoding="utf-8"))
    drain.close(
        repo.root, ticket, outcome=drain.PARKED, reasons=drain.reasons_of(result), result=result
    )


BLOCKS: Final[dict[Ticket, dict[str, object]]] = {
    Ticket.ASKED: {"openQuestions": [{"kind": "direction", "question": "which way?"}]},
    Ticket.GUARDED: {"guardEdits": [{"path": "docs/process/layers.md", "edit": "allow it"}]},
    Ticket.PARKED: {},
}


@does(ActionName.DRAIN_PARKS_TICKET)
def _drain_parks_ticket(repo: Repo, change: Change) -> None:
    kind = change.after.tickets[change.slot]
    park(repo, change.slot, {"verdict": "blocked", **BLOCKS[kind]})
    if kind is Ticket.PARKED:
        # Why: a note an older drain wrote names no kind of hold at all.
        path = queue_notes.note_path(repo.root, repo.ticket_branch(change.slot))
        note = queue_notes.read_note(repo.root, repo.ticket_branch(change.slot))
        queue_notes.write_note(path, {k: v for k, v in note.items() if k != "holds"})


@does(ActionName.DRAIN_PARKS_FOR_A_SAFEGUARD)
def _drain_parks_for_a_safeguard(repo: Repo, _change: Change) -> None:
    repo.tools.append(None)
    kind = f"kind{len(repo.tools)}"
    files = {"docs/process/binding.md": repo.binding(), f"src/{kind}.py": "import requests\n"}
    name = build_on_branch(repo, 0, files)
    git(repo.root, "switch", "-q", "main")
    git(repo.root, "merge", "-q", "--no-ff", "--no-edit", name)
    owed = f"triggers: '{kind}' gained 1 instance(s) (src/b.py:2) while the base already held 1"
    park(repo, 1, {"verdict": "blocked", "checkOutput": owed})


@does(ActionName.OWNER_ANSWERS_ON_THE_TICKET)
def _owner_answers_on_the_ticket(repo: Repo, change: Change) -> None:
    repo.add_to_ticket(change.slot, "## Answers\n\nQ: which way? A: that one")


@does(ActionName.OWNER_LANDS_GUARD_EDITS)
def _owner_lands_guard_edits(repo: Repo, _change: Change) -> None:
    repo.edits += 1
    write(repo.root, f"docs/process/edit{repo.edits}.md", "The guard edit the run named.\n")
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


@does(ActionName.BRANCH_MERGES)
def _branch_merges(repo: Repo, _change: Change) -> None:
    name = repo.head()
    git(repo.root, "switch", "-q", "main")
    git(repo.root, "merge", "-q", "--no-ff", "--no-edit", name)


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
    "merge": Advice.MERGE,
    "safeguard": Advice.SAFEGUARD,
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
