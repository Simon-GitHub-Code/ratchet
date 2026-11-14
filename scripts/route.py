#!/usr/bin/env python3
"""The route: the one command the owner types next, decided from the checkout, never guessed.

Four plugins build software on the method, and most of their skills are the owner's
keystrokes: the harness refuses them to an agent. So the method's job at every stage is to say
which keystroke comes next. This script reads the checkout (and the tracker, unless
``--offline``) and prints that command with its reason and the step after it. The
``/ratchet:next`` skill relays it, every ratchet skill ends with it, and the session hook prints
its offline form, so a session never opens without its next step.

The stages, in order; the first that does not hold is the answer:

1. a git repository;
2. ``AGENTS.md`` naming the method (``/ratchet:init``);
3. the tracker record ``docs/agents/issue-tracker.md`` (the mattpocock-skills setup);
4. the binding ``docs/process/binding.md`` with its plugin pin written, which adopt does last of
   what it binds (``/ratchet:adopt``, again to resume one that stopped);
5. the commit hooks, which run tier 0 on every commit (``hooks_installed.py`` proves they would;
   the step is the ``hooks-install`` entry point, which an agent runs, or the owner's command
   where git config must change, since the guard refuses that from an agent);
6. a clean tree (finish the change: ``check``, commit on a branch, pull request; nothing is
   committed on the default branch itself, and records alone left there land through the
   ``merge`` entry point's ``--records``, which an agent runs, or a pull request on a forge);
   with no forge, a ``records/<time>`` branch checked out is a ``--records`` run that stopped,
   and the same command finishes it;
7. on a working branch: the pull request is open, else open it, and its merge job merges what
   the merge judge passes (ADR 0021); with no forge (no ``origin``) the binding's ``merge``
   entry point judges the branch, runs ``check`` on the merge result and merges it, or says
   why the merge is the owner's;
8. on the default branch: a ``records/<time>`` branch not yet merged, which the merge judge is
   asked about (the owner's merge command with its first reason, or the ``merge`` entry point
   for an agent when it passes); the glossary and design brief (``grill-with-docs``), then an
   agent-ready ticket (``to-spec`` and ``to-tickets``) whose blockers are done, its conjecture
   (``/ratchet:conjecture``), and then the build (``/ratchet:feature`` when the owner steps
   away, ``/pstack:poteto-mode`` when they stay). With more than one ticket ready the build is
   the drain: ``/ratchet:feature`` with no ticket builds them all (ADR 0023). Before any of
   these, a safeguard the repository owes (``triggers.owed``: a kind of code has arrived, or a
   life event is declared, and a row it fires is still ``later:``) is ``/ratchet:safeguard``,
   with the step it would otherwise name offered as the alternative (ADR 0028). Before that,
   an architecture review the repository owes (``triggers.reviews_owed``: a kind that reopens
   the architecture has arrived for the first time, or such a life event is declared, and no
   decision record names it) is ``/ratchet:architecture``, with ``/ratchet:safeguard`` after it
   when rows are owed too, since the review may move where the safeguards go (ADR 0052).

After the three lines come the ``also:`` lines, when there are any: the upgrade, when the plugin
in use is newer than the one the binding pins (ADR 0027); each question the owner has answered
with the recommendation three times running, offered as a standing answer (``questions.py``, ADR
0022); and each life event the tree shows a sign of and the binding does not declare. They are
proposals and change nothing. ``--decline EVENT`` records that the owner said no to an event, in
``.ratchet/events.tsv``, so it is not proposed again until a sign of it appears that was not
there when they said no.

Usage: ``route.py [--root DIR] [--offline] [--json] [--decline EVENT]``.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import asdict, dataclass
from pathlib import Path

import application_types
import feature_preflight as preflight
import gatelib
import hooks_installed
import merge
import merge_ready
import plugin_pin
import questions
import queue_notes
import triggers

INSTRUCTION_FILES = ("AGENTS.md", "CLAUDE.md")
STANZA = "ratchet:method"
TRACKER_RECORD = Path("docs/agents/issue-tracker.md")
BINDING = Path("docs/process/binding.md")
GLOSSARY = Path("GLOSSARY.md")
GLOSSARY_PLACEHOLDER = "<project>"
DESIGN_BRIEF = Path("docs/design-brief.md")
LOCAL_TICKETS = ".scratch/*/issues/*.md"
LOCAL_SPECS = ".scratch/*/spec.md"
PURPOSE_PLACEHOLDER = "<One paragraph"
PLUGIN_ROOT = Path(__file__).resolve().parents[1]
AGENTS_TEMPLATE = PLUGIN_ROOT / "skills/init/templates/AGENTS.md"
PLUGIN_LINE = re.compile(r"^- \*\*([^*]+)\*\*")
CHECK_FALLBACK = "the binding's `check` entry point"

INIT = "/ratchet:init"
TRACKER_SETUP = "/mattpocock-skills:setup-matt-pocock-skills"
ADOPT = "/ratchet:adopt"
GRILL = "/mattpocock-skills:grill-with-docs"
SPEC = "/mattpocock-skills:to-spec"
TICKETS = "/mattpocock-skills:to-tickets"
CONJECTURE = "/ratchet:conjecture"
FEATURE = "/ratchet:feature"
POTETO = "/pstack:poteto-mode"
PULL_REQUEST = (
    "open the pull request (the `pr` skill fills the body); its merge job merges it when the"
    " merge judge passes it, and comments why when the merge is yours"
)
WAIT = "nothing: the merge job merges it once `ci` is green, or comments why the merge is yours"
MERGE_FALLBACK = "the binding's `merge` entry point"
NEXT = "/ratchet:next"
SAFEGUARD = "/ratchet:safeguard"
ARCHITECTURE = "/ratchet:architecture"
EVENTS_LOG = "events.tsv"
EVENTS_HEADER = "date\tevent\tsign\tanswer"
DECLINED = "declined"
CHANGES = ".scratch/changes"
BLOCKED_RUN = ": blocked run"
SHOWN = 3


@dataclass(frozen=True)
class Step:
    """What the owner types next, why, and what follows."""

    command: str
    why: str
    then: str
    stage: str


@dataclass(frozen=True)
class Checkout:
    """What the script could see."""

    root: Path
    branch: str | None
    default_branch: str
    dirty: bool
    forge: bool = True

    @property
    def on_default(self) -> bool:
        """Whether HEAD is the default branch (a detached or unborn HEAD counts as it)."""
        return self.branch in (self.default_branch, None)


def observe(root: Path) -> Checkout | None:
    """The checkout at ``root``, or ``None`` outside a git repository."""
    status = gatelib.git(root, "status", "--porcelain", "--untracked-files=all")
    if status is None:
        return None
    branch = gatelib.git(root, "rev-parse", "--abbrev-ref", "HEAD")
    branch = None if branch == "HEAD" else branch
    default = gatelib.default_branches(root)[0].removeprefix("origin/")
    forge = gatelib.git(root, "remote", "get-url", "origin") is not None
    return Checkout(root, branch or None, default, bool(status.strip()), forge)


def review(checkout: Checkout) -> str:
    """How a finished branch reaches the default branch: a pull request, or the entry point."""
    if checkout.forge:
        return PULL_REQUEST
    return (
        f"{merge_command(checkout.root, '<branch>')} merges it when the merge judge and `check`"
        f" pass it, and says why when the merge is yours (no forge; {CHANGES}/<branch>.md)"
    )


def names_method(root: Path) -> bool:
    return any(
        STANZA in (root / name).read_text(encoding="utf-8", errors="replace")
        for name in INSTRUCTION_FILES
        if (root / name).is_file()
    )


def check_command(root: Path) -> str:
    """The command behind the binding's ``check`` entry point, as its table names it."""
    command = gatelib.entry_point(root, "check")
    return f"`{command}`" if command else CHECK_FALLBACK


def merge_command(root: Path, branch: str) -> str:
    """The binding's ``merge`` entry point for ``branch``, as its table names the command."""
    command = gatelib.entry_point(root, "merge")
    return f"`{command} {branch}`" if command else f"{MERGE_FALLBACK} with `{branch}`"


def records_missing(root: Path) -> str | None:
    """Which of the glossary and the design brief is still a template, if either."""
    glossary = root / GLOSSARY
    if not glossary.is_file():
        return "GLOSSARY.md is missing"
    if GLOSSARY_PLACEHOLDER in glossary.read_text(encoding="utf-8", errors="replace"):
        return f"GLOSSARY.md still holds the `{GLOSSARY_PLACEHOLDER}` placeholder"
    if not (root / DESIGN_BRIEF).is_file():
        return f"{DESIGN_BRIEF} is missing"
    return None


# --- tickets ---------------------------------------------------------------------------------


def tracker_tickets(root: Path, gh: preflight.Runner) -> list[preflight.Ticket] | None:
    """The open agent-ready issues on the tracker, or ``None`` when gh cannot answer."""
    label = preflight.ready_label(root)
    output = gh(
        ["issue", "list", "--state", "open", "--label", label, "--json", "number,title,body,labels"]
    )
    if output is None:
        return None
    try:
        data = json.loads(output)
    except ValueError:
        return None
    tickets = []
    for item in data if isinstance(data, list) else []:
        if not isinstance(item, dict) or not isinstance(item.get("number"), int):
            continue
        tickets.append(
            preflight.Ticket(
                ref=str(item["number"]),
                number=item["number"],
                title=str(item.get("title", "")),
                body=str(item.get("body", "")),
                labels=(label,),
                is_open=True,
            )
        )
    return sorted(tickets, key=lambda ticket: ticket.number or 0)


def local_tickets(root: Path, *, ready: bool = True) -> list[preflight.Ticket]:
    """The open tickets under ``.scratch``, where there is no tracker: agent-ready, or not."""
    label = preflight.ready_label(root)
    tickets = []
    for path in sorted(root.glob(LOCAL_TICKETS)):
        ref = path.relative_to(root).as_posix()
        ticket = preflight.local_ticket(ref, path.read_text(encoding="utf-8", errors="replace"))
        if ticket.is_open and (label in ticket.labels) == ready:
            tickets.append(ticket)
    return tickets


def triage_step(root: Path) -> Step | None:
    """Open local tickets, none agent-ready: the first unblocked one is the owner's to agree."""
    waiting = [
        t
        for t in local_tickets(root, ready=False)
        if not preflight.open_blockers(t, root, lambda _args: None)
    ]
    waiting = [t for t in waiting if not any("info" in label for label in t.labels)]
    for_agents = [t for t in waiting if not any("human" in label for label in t.labels)]
    if not waiting:
        return None
    pick = (for_agents or waiting)[0]
    return Step(
        f'agree ticket {pick.ref} "{pick.title}": set its `Status:` to'
        f" {preflight.ready_label(root)} if an agent may build it, or work it yourself",
        f"{len(waiting)} unblocked open ticket(s) wait for triage, and none is agent-ready",
        f"{CONJECTURE} {pick.ref}",
        "triage",
    )


def unsplit_spec(root: Path) -> Step | None:
    """A local spec with no tickets beside it: ``to-tickets`` splits it before a new spec.

    A spec that states no conjecture or operability goes to ``/ratchet:conjecture`` first.
    """
    for spec in sorted(root.glob(LOCAL_SPECS)):
        if not any((spec.parent / "issues").glob("*.md")):
            ref = spec.relative_to(root).as_posix()
            text = spec.read_text(encoding="utf-8", errors="replace")
            lacking = unstated(preflight.local_ticket(ref, text), root, lambda _args: None)
            if lacking:
                # Why: to-tickets writes no Conjecture or Operability, and the preflight accepts
                # both on the parent, so stating them on the spec first serves every child.
                return Step(
                    f"{CONJECTURE} {ref}",
                    f"{ref} has no tickets yet, and the spec {lacking}; its tickets inherit both",
                    f"{TICKETS} {ref}, then {FEATURE} <ticket>",
                    "specify",
                )
            return Step(
                f"{TICKETS} {ref}",
                f"{ref} has no tickets yet",
                f"{FEATURE} <ticket>",
                "specify",
            )
    return None


def unstated(ticket: preflight.Ticket, root: Path, gh: preflight.Runner) -> str | None:
    """What `/ratchet:conjecture` has still to write on the ticket; ``None`` when nothing."""
    if preflight.conjecture_failure(ticket, root, gh) is not None:
        return "states no conjecture"
    if preflight.operability_failure(ticket, root, gh) is not None:
        return "does not say how it is operated"
    return None


def blocking(tickets: list[preflight.Ticket], root: Path, gh: preflight.Runner) -> Step | None:
    """When every agent-ready ticket waits on an open blocker: the blocker to finish first."""
    for ticket in tickets:
        for ref in ticket.blockers:
            blocker = preflight.fetch(preflight.blocker_ref(ticket, ref, root), root, gh)
            if blocker is not None and blocker.is_open:
                return Step(
                    f'finish ticket {blocker.ref} "{blocker.title}", then mark it resolved',
                    f"every agent-ready ticket waits on an open blocker; {ticket.ref} waits on it",
                    NEXT,
                    "blocked",
                )
    return None


def merged_features(root: Path, default_branch: str) -> list[str]:
    """Feature branches merged into the default branch by a merge made on its own line.

    Each tip is the second parent of a merge on the default branch's first-parent line, as
    `git merge --no-ff` makes. A branch made and never built points at an old default commit,
    which no merge on that line brings in. A fast-forward or squash merge leaves no such merge,
    so the feature skill resolves the ticket in the branch itself.
    """
    merges = (
        gatelib.git(root, "rev-list", "--merges", "--first-parent", "--parents", default_branch)
        or ""
    )
    merged = {parent for line in merges.splitlines() for parent in line.split()[2:]}
    heads = gatelib.git(
        root, "for-each-ref", "--format=%(refname:short) %(objectname)", "refs/heads/feature/"
    )
    pairs = [line.split(" ", 1) for line in (heads or "").splitlines()]
    return [name for name, tip in pairs if tip in merged]


def finished(tickets: list[preflight.Ticket], merged: list[str]) -> Step | None:
    """An open ticket whose branch is already merged: it is done, so it is resolved, not rebuilt."""
    for ticket in tickets:
        branch = own_branch(ticket)
        if branch in merged:
            return Step(
                f"mark ticket {ticket.ref} resolved (`Status: resolved` on a local ticket;"
                " close the issue on a forge)",
                f"branch `{branch}` is merged, yet {ticket.ref} is still open",
                NEXT,
                "resolve",
            )
    return None


def blocked_runs(
    root: Path, default_branch: str, tickets: list[preflight.Ticket] | None = None
) -> dict[str, str]:
    """Branches whose last feature run blocked and whose block still stands, with its stash.

    The feature skill stashes a blocked run as ``<branch>: blocked run``. The block stands until
    what blocked it changes on the default branch (see ``answered``); any other move, as another
    ticket agreed or an unrelated binding edit, leaves a rerun to block the same way.
    """
    by_branch = {own_branch(ticket): ticket for ticket in tickets or []}
    listing = gatelib.git(root, "stash", "list", "--format=%gd%x09%gs") or ""
    found: dict[str, str] = {}
    for line in listing.splitlines():
        ref, _, subject = line.partition("\t")
        run = subject.split(": ", 1)[-1]
        branch = run.removesuffix(BLOCKED_RUN)
        if not run.endswith(BLOCKED_RUN):
            continue
        ticket, note = by_branch.get(branch), queue_notes.read_note(root, branch)
        recorded = isinstance(note.get("digest"), str)
        if ticket is not None and recorded and queue_notes.edited(note, ticket):
            continue
        asked = asked_paths(ticket, queue_notes.texts(note.get("causes")), recorded=recorded)
        if not answered(root, default_branch, ref, asked=asked):
            found.setdefault(branch, run)
    return found


def asked_paths(ticket: preflight.Ticket | None, causes: list[str], *, recorded: bool) -> list[str]:
    """The repo paths whose change answers a blocked run: its spec, the causes, and its ticket.

    The ticket's own file counts only when no note recorded its text: with one, ``edited`` has
    compared the text, so a forge issue is judged as a file is.
    """
    own = [] if ticket is None else [ticket.parent or ""] + ([] if recorded else [ticket.ref])
    return [path for path in [*own, *causes] if path and not path.lstrip("#").isdigit()]


def answered(root: Path, default_branch: str, stash: str, *, asked: list[str]) -> bool:
    """Whether the default branch changed, since the blocked run, what the run was blocked on.

    Why: a rerun is worth offering only when something the run asked was answered, never for any
    other move the owner merged (ADR 0031 lets records land alone, and the owner merges more).
    What it asked is read from durable facts: the ticket's own text and the spec it inherits
    (an answer, ``## Answers``), and the paths the drain's note recorded as the cause (a guard
    path it needed, a dispute it filed). A forge ticket has no file, so it is released by the
    owner dropping the stash.
    """
    if gatelib.git(root, "merge-base", "--is-ancestor", default_branch, stash) is not None:
        return False
    fork = gatelib.git(root, "merge-base", default_branch, stash) or default_branch
    if not asked:
        return False
    return bool(gatelib.git(root, "diff", "--name-only", fork, default_branch, "--", *asked))


def stalled(ticket: preflight.Ticket, runs: dict[str, str]) -> str | None:
    """The stash of the blocked run that still holds ``ticket``, if one does.

    Matched by the whole branch name: local ticket numbers repeat across features.
    """
    return runs.get(own_branch(ticket))


def own_branch(ticket: preflight.Ticket) -> str:
    """The branch the feature preflight names for ``ticket``."""
    return preflight.branch_of(ticket, preflight.slug_of(ticket.title, ticket.number))


def releases(hold: queue_notes.Hold, ticket: preflight.Ticket, default_branch: str) -> list[str]:
    """What the owner does to free a ticket the drain parked, one step for each kind of hold.

    Why: the drain keeps a parked ticket until its text changes, so a bare ``/ratchet:feature``
    would only be refused again; each kind names the step that changes what it was parked for.
    """
    ref = ticket.ref
    steps = []
    if queue_notes.SIZE in hold.kinds:
        # Why: ADR 0046. The agent splits a ticket by size itself; a hold of this kind is left
        # only where it could not (its plan was refused, or the ticket is already a part).
        steps.append(
            f"change the criteria of ticket {ref} so they divide into parts that fit: the"
            " agent's own split of it was refused (the drain's report says why)"
        )
    if queue_notes.QUESTIONS in hold.kinds:
        steps.append(f"answer the open questions the run of {ref} named, on the ticket")
    if queue_notes.GUARD in hold.kinds:
        steps.append(f"land the guard edits the run of {ref} named on {default_branch}")
    if hold.reviews:
        steps.append(f"run {ARCHITECTURE} for {', '.join(hold.reviews)}")
    if hold.owes:
        steps.append(f"run {SAFEGUARD} for {', '.join(hold.owes)}")
    return steps or [f"change ticket {ref} to meet what the drain parked it for"]


def drain_hold(root: Path, ticket: preflight.Ticket) -> queue_notes.Hold | None:
    """The hold the drain keeps on ``ticket`` from its note, until ``queue_notes.released``."""
    return queue_notes.held(root, own_branch(ticket), ticket)


def agent_ready(root: Path, gh: preflight.Runner | None) -> tuple[list[preflight.Ticket], bool]:
    """The open agent-ready tickets, and whether they are local files or the tracker's."""
    tickets = tracker_tickets(root, gh) if gh is not None else None
    return (local_tickets(root), True) if tickets is None else (tickets, False)


def build_step(ready: list[preflight.Ticket], queue: int, checkout: Checkout) -> Step | None:
    """The build among the tickets that state a conjecture: the drain for several, else the one."""
    if not ready:
        return None
    if queue > 1:
        return Step(
            FEATURE,
            f"{queue} agent-ready tickets state a conjecture: with no ticket named the command"
            " builds them at once, each in a work tree of its own, and merges what the merge judge"
            f" passes; {FEATURE} <ticket> builds one, and {POTETO} <ticket> when you stay at the"
            " keyboard",
            "the drain's one report: what merged, what waits for you and why, every assumption",
            "drain",
        )
    ticket = ready[0]
    return Step(
        f"{FEATURE} {ticket.ref}",
        f'ticket {ticket.ref} "{ticket.title}" is agent-ready with a conjecture;'
        f" {POTETO} {ticket.ref} instead when you stay at the keyboard",
        review(checkout),
        "build",
    )


def ticket_step(root: Path, gh: preflight.Runner | None, checkout: Checkout) -> Step:
    """The step among the tickets: write one, give one its conjecture, or build them."""
    tickets, local = agent_ready(root, gh)
    if not tickets:
        return (triage_step(root) or unsplit_spec(root) if local else None) or Step(
            SPEC,
            "no open ticket carries the agent-ready label",
            f"{TICKETS}, then {CONJECTURE} <ticket>",
            "specify",
        )
    done = finished(tickets, merged_features(root, checkout.default_branch))
    if done is not None:
        return done
    offline_gh = gh or (lambda _args: None)
    unblocked = [t for t in tickets if not preflight.open_blockers(t, root, offline_gh)]
    if not unblocked:
        return blocking(tickets, root, offline_gh) or Step(
            SPEC,
            "every agent-ready ticket names a blocker that cannot be found",
            f"{TICKETS}, then {CONJECTURE} <ticket>",
            "specify",
        )
    runs = blocked_runs(root, checkout.default_branch, unblocked)
    drained = {t.ref: hold for t in unblocked if (hold := drain_hold(root, t))}

    def free(t: preflight.Ticket) -> bool:
        return not stalled(t, runs) and t.ref not in drained

    stated = [t for t in tickets if free(t) and not unstated(t, root, offline_gh)]
    tickets = [t for t in unblocked if free(t)]
    if not tickets:
        held = unblocked[0]
        if held.ref in drained:
            hold = drained[held.ref]
            why = hold.reasons[0] if hold.reasons else "the drain parked it"
            return Step(
                "; ".join(releases(hold, held, checkout.default_branch)),
                f'the drain parked ticket {held.ref} "{held.title}" and holds it until its text'
                f" changes or the cause it names lands ({why});"
                f" {FEATURE} would be refused again",
                f"{FEATURE}, or {POTETO} {held.ref} now to make the change as the guard asks",
                "blocked-run",
            )
        return Step(
            f"answer the questions or land the guard edits the blocked run of {held.ref} named"
            f" (stash `{stalled(held, runs)}`) on {checkout.default_branch}; an answer given"
            " elsewhere, as an issue comment, releases it once you drop that stash",
            f"the last {FEATURE} run on every agent-ready ticket blocked, and"
            f" {checkout.default_branch} has not moved since; a rerun would block the same way",
            f"{FEATURE} {held.ref}, or {POTETO} {held.ref} now to make the edits as the guard asks",
            "blocked-run",
        )
    first = tickets[0]
    # Why: a ticket blocked only by another in the queue is built by the same drain (ADR 0023).
    ready = [ticket for ticket in tickets if ticket in stated]
    return build_step(ready, len(stated), checkout) or Step(
        f"{CONJECTURE} {first.ref}",
        f'ticket {first.ref} "{first.title}" is agent-ready but'
        f" {unstated(first, root, offline_gh)}",
        f"{FEATURE} {first.ref}",
        "conjecture",
    )


# --- the route -------------------------------------------------------------------------------


def pull_request_step(checkout: Checkout, gh: preflight.Runner | None) -> Step:
    """On a working branch with a clean tree: wait for the review, or open the pull request."""
    if not checkout.forge:
        return Step(
            merge_command(checkout.root, checkout.branch or ""),
            f"branch `{checkout.branch}` is clean and there is no forge: the entry point merges"
            " it when the merge judge and `check` pass it, and prints why when the merge is"
            f" yours (then: `git switch {checkout.default_branch} && git merge --no-ff"
            f" {checkout.branch}`); its change description"
            f" is {CHANGES}/{checkout.branch.replace('/', '-') if checkout.branch else ''}.md",
            NEXT,
            "merge",
        )
    output = None if gh is None else gh(["pr", "view", "--json", "state,url"])
    if output is not None:
        try:
            data = json.loads(output)
        except ValueError:
            data = None
        if isinstance(data, dict) and data.get("state") == "OPEN":
            return Step(
                WAIT, f"pull request {data.get('url', '')} is open", FEATURE + " <ticket>", "review"
            )
    return Step(
        PULL_REQUEST,
        f"branch `{checkout.branch}` is clean and has no open pull request",
        WAIT,
        "pull-request",
    )


def _plugin_lines(text: str) -> dict[str, str]:
    return {m.group(1): line for line in text.splitlines() if (m := PLUGIN_LINE.match(line))}


def stale_plugin_lines(agents: Path) -> list[str]:
    """The questions whose plugin line in ``agents`` differs from the init template's.

    Init writes the four-plugin stanza once; a later ratchet changes what it says about the
    other plugins, so an adopted repository keeps the old text until init runs again.
    """
    if not agents.is_file():
        return []
    template = _plugin_lines(AGENTS_TEMPLATE.read_text(encoding="utf-8"))
    present = _plugin_lines(agents.read_text(errors="replace"))
    return [q for q, line in present.items() if q in template and template[q] != line]


def adoption_step(root: Path) -> Step | None:
    """``/ratchet:adopt`` while the binding is missing or still the template's; else ``None``."""
    binding = root / BINDING
    if not binding.is_file():
        return Step(ADOPT, f"{BINDING} is missing, so no gate is bound", GRILL, "adopt")
    if plugin_pin.pinned(binding.read_text(errors="replace")) is None:
        # Why: adopt writes the pin last of what it binds (its step 10), so a binding without
        # one is the template's: an adoption that stopped, which hooks-install cannot finish.
        return Step(
            ADOPT,
            f"{BINDING} has no plugin pin, so an adoption stopped before it bound the gates;"
            " it resumes from the templates already copied, on its own branch if it has one",
            GRILL,
            "adopt",
        )
    return None


def setup_step(root: Path) -> Step | None:
    """The step before the work: adoption, then the commit hooks; ``None`` once both hold."""
    if not names_method(root):
        return Step(INIT, "AGENTS.md does not name the method", TRACKER_SETUP, "init")
    agents = root / "AGENTS.md"
    if agents.is_file() and PURPOSE_PLACEHOLDER in agents.read_text(errors="replace"):
        return Step(
            INIT,
            "AGENTS.md still holds the purpose placeholder, so init did not finish",
            TRACKER_SETUP,
            "init",
        )
    if stale := stale_plugin_lines(agents):
        return Step(
            INIT,
            f"AGENTS.md's plugin lines for {', '.join(stale)} differ from this ratchet's",
            NEXT,
            "init",
        )
    if not (root / TRACKER_RECORD).is_file():
        return Step(
            TRACKER_SETUP,
            f"{TRACKER_RECORD} is missing: no tracker, no labels (the setup is the owner's)",
            ADOPT,
            "tracker",
        )
    if adoption := adoption_step(root):
        return adoption
    hooks = hooks_installed.check(root)
    return None if hooks is None else Step(hooks.fix, hooks.detail, NEXT, "hooks")


def dirty_step(checkout: Checkout) -> Step:
    """Uncommitted changes: records land through the merge judge, anything else on a branch.

    Why: nothing is committed on the default branch itself (ADR 0031). Records the owner's
    conversation wrote there land through the ``merge`` entry point, which an agent runs.
    """
    root, default = checkout.root, checkout.default_branch
    if not checkout.on_default:
        return Step(
            f"run {check_command(root)}, then commit on branch `{checkout.branch}`",
            "the working tree has uncommitted changes",
            review(checkout),
            "change",
        )
    paths = merge_ready.uncommitted(root)
    named = checkout.branch == default
    if named and paths and not merge_ready.not_records(root, merge_ready.default_ref(root), paths):
        more = len(paths) - SHOWN
        listed = ", ".join(paths[:SHOWN]) + (f" and {more} more" if more > 0 else "")
        if checkout.forge:
            return Step(
                f"commit them on a branch of their own, then {PULL_REQUEST}",
                f"the work tree of `{default}` holds records alone ({listed}); the merge judge"
                " asks no verdict record of them",
                WAIT,
                "records",
            )
        return Step(
            merge_command(root, "--records"),
            f"the work tree of `{default}` holds records alone ({listed}): an agent runs this; it"
            " commits them on a branch of their own and merges it when the merge judge and"
            " `check` pass it",
            NEXT,
            "records",
        )
    return Step(
        f"run {check_command(root)}, then commit on a branch of its own, never on `{default}`",
        f"the work tree of `{default}` has uncommitted changes",
        review(checkout),
        "change",
    )


def records_stopped(checkout: Checkout) -> Step | None:
    """A ``merge --records`` run that stopped on its records branch, which the same command ends."""
    if checkout.forge or not (checkout.branch or "").startswith(merge.RECORDS):
        return None
    again = merge_command(checkout.root, "--records")
    return Step(
        again,
        f"a run of {again} stopped on branch `{checkout.branch}` before it switched back to"
        f" `{checkout.default_branch}`: an agent runs it again, and it picks up where that run"
        " stopped",
        NEXT,
        "records",
    )


def records_waiting(checkout: Checkout) -> Step | None:
    """A records branch ``merge --records`` left unmerged; the judge says whose merge it is.

    Why the judge is asked here: a run stopped while ``check`` ran never heard its answer, and the
    branch it leaves looks like one the judge left to the owner.
    """
    root, default = checkout.root, checkout.default_branch
    listed = gatelib.git(
        root,
        "branch",
        "--list",
        f"{merge.RECORDS}*",
        "--no-merged",
        default,
        "--format=%(refname:short)",
    )
    waiting = (listed or "").split()
    if not waiting:
        return None
    name = waiting[0]
    reasons = merge_ready.judge(root, f"{merge.HEADS}{default}", f"{merge.HEADS}{name}")
    if not reasons:
        return Step(
            merge_command(root, name),
            f"branch `{name}` holds records a run of {merge_command(root, '--records')} committed"
            " and did not merge; the merge judge passes them, so an agent runs this, and it merges"
            " them when `check` is green on the merge result",
            NEXT,
            "records",
        )
    return Step(
        f"`git switch {default} && git merge --no-ff {name}`, typed by the owner",
        f"the merge judge leaves branch `{name}` to the owner: {reasons[0].text};"
        f" {merge_command(root, name)} prints every reason",
        NEXT,
        "records",
    )


def decide(root: Path, gh: preflight.Runner | None) -> Step:
    """The owner's next command for the checkout at ``root``; ``gh=None`` stays offline."""
    checkout = observe(root)
    if checkout is None:
        return Step(INIT, f"{root} is not a git repository", TRACKER_SETUP, "init")
    first = setup_step(root) or records_stopped(checkout)
    if first is not None:
        return first
    if checkout.dirty:
        return dirty_step(checkout)
    if not checkout.on_default:
        return pull_request_step(checkout, gh)
    waiting = records_waiting(checkout)
    if waiting is not None:
        return waiting
    missing = records_missing(root)
    step = (
        Step(GRILL, missing, f"{SPEC}, then {TICKETS}", "records")
        if missing
        else ticket_step(root, gh, checkout)
    )
    return review_step(root, step) or safeguard_step(root, step) or step


# --- architecture reviews, safeguards and life events (ADR 0028, ADR 0052) -----------------


def review_step(root: Path, instead: Step) -> Step | None:
    """``/ratchet:architecture`` when the repository owes a review, before any safeguard.

    Why: ADR 0052. The review decides where the new kind of code lives, and so where its
    safeguards are bound; like a safeguard it never hides the build, since the change that
    brought the kind was free and only the next one that adds another waits for the review.
    """
    owing = triggers.reviews_owed(root)
    if not owing:
        return None
    reopening = application_types.load().architecture
    said = []
    for review in owing:
        if application_types.code_kinds(review.trigger):
            count = review.count
            said.append(
                f"`{review.name}` has arrived ({count} instance{'s' * (count != 1)}) and reopens"
                f" the architecture: {reopening[review.trigger]}"
            )
        else:
            said.append(
                f"the declared life event `{review.name}` reopens the architecture:"
                f" {reopening[review.trigger]}"
            )
    code = any(application_types.code_kinds(review.trigger) for review in owing)
    cost = (
        "the change that brought the kind was free, one that adds another waits for this"
        if code
        else "a declared event is never red"
    )
    then = SAFEGUARD if triggers.owed(root) else instead.command
    return Step(
        ARCHITECTURE,
        f"{'; '.join(said)}. Or carry on with {instead.command}: {cost}",
        then,
        "architecture-review",
    )


def safeguard_step(root: Path, instead: Step) -> Step | None:
    """``/ratchet:safeguard`` when the repository owes a row, offering ``instead`` beside it.

    Why: it comes before any build, since the next change that adds an instance of an owed kind
    is red; it never hides the build, since the owner may be mid-idea and the change that brings a
    kind is free.
    """
    owing = triggers.owed(root)
    if not owing:
        return None
    arrived: dict[str, list[triggers.Owed]] = {}
    for row in owing:
        arrived.setdefault(row.trigger, []).append(row)
    said = []
    for trigger, rows in arrived.items():
        questions_owed = ", ".join(row.question for row in rows)
        if application_types.code_kinds(trigger):
            count = rows[0].count
            said.append(
                f"`{rows[0].name}` has arrived ({count} instance{'s' * (count != 1)})"
                f" and owes {questions_owed}"
            )
        else:
            said.append(f"the declared life event `{rows[0].name}` owes {questions_owed}")
    code = any(application_types.code_kinds(row.trigger) for row in owing)
    cost = (
        "the change that brought the kind was free, one that adds another waits for this"
        if code
        else "a declared event is never red"
    )
    return Step(
        SAFEGUARD,
        f"{'; '.join(said)}. Or carry on with {instead.command}: {cost}",
        instead.command,
        "safeguard",
    )


def declined_signs(root: Path) -> set[tuple[str, str]]:
    """The (event, sign) pairs the owner said no to, from ``.ratchet/events.tsv``."""
    rows = (gatelib.read_text(root / gatelib.STATE / EVENTS_LOG) or "").splitlines()[1:]
    cells = [row.split("\t") for row in rows]
    return {(c[1], c[2]) for c in cells if len(c) == len(EVENTS_HEADER.split("\t"))}


def event_proposals(root: Path) -> list[str]:
    """One sentence per undeclared life event the tree shows a sign of that was not declined.

    Why: a sign is evidence of intent, not of users, so the owner declares the event; a sign
    already declined is not raised again, so an owner who said no is not asked at every turn.
    """
    declined = declined_signs(root)
    events = application_types.load().events
    lines = []
    for name, signs in application_types.signs_seen(root).items():
        fresh = [path for sign, path in signs.items() if (name, sign) not in declined]
        if fresh:
            lines.append(
                f"`{fresh[0]}` is a sign that the project is `{name}` ({events[name].meaning}):"
                f" if it is, type `{SAFEGUARD} {name}`; if not yet, say so and it is not proposed"
                " again until a new sign of it appears"
            )
    return lines


def decline(root: Path, event: str) -> int:
    """Record that the owner said no to ``event`` for every sign of it the tree now shows."""
    signs = application_types.signs_seen(root).get(event, {})
    if not signs:
        print(f"route: no sign of `{event}` is proposed, so there is nothing to decline")
        return 2
    log = gatelib.state_dir(root) / EVENTS_LOG
    if not log.is_file():
        log.write_text(EVENTS_HEADER + "\n", encoding="utf-8")
    with log.open("a", encoding="utf-8") as lines:
        for sign in signs:
            lines.write(f"{questions.today()}\t{event}\t{sign}\t{DECLINED}\n")
    print(f"route: `{event}` is not proposed again until a new sign of it appears")
    return 0


def pin_proposals(root: Path, plugin: Path = PLUGIN_ROOT) -> list[str]:
    """The upgrade, offered when the plugin in use is newer than the one the binding pins.

    Why: a newer plugin is green at every tier and says so in each tier's output, which an owner
    rarely reads; the route is what the owner reads (ADR 0027).
    """
    pin = plugin_pin.pinned(gatelib.read_text(root / BINDING))
    if pin is None or plugin_pin.relation(pin, plugin) is not plugin_pin.Relation.NEWER:
        return []
    return [
        (
            f"the Ratchet plugin in use is {plugin_pin.version_of(plugin)}, newer than the"
            f" {pin.version} the binding pins: run `{plugin_pin.upgrade_command(plugin)}`, which"
            " moves the pin, the workflow refs and the permission rules together"
        )
    ]


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", default=".")
    parser.add_argument("--offline", action="store_true", help="read no tracker")
    parser.add_argument("--json", action="store_true", dest="as_json")
    parser.add_argument("--decline", metavar="EVENT", help="the owner said no to this event")
    options = parser.parse_args(argv[1:])
    root = Path(options.root).resolve()
    if options.decline:
        return decline(root, options.decline)
    step = decide(root, None if options.offline else preflight.run_gh)
    also = pin_proposals(root) + questions.proposals(root) + event_proposals(root)
    if options.as_json:
        print(json.dumps({**asdict(step), "also": also}, indent=2))
    else:
        print(f"next: {step.command}")
        print(f"why: {step.why}")
        print(f"then: {step.then}")
        for line in also:
            print(f"also: {line}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
