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
4. the binding ``docs/process/binding.md`` (``/ratchet:adopt``);
5. a clean tree (finish the change: ``check``, commit, pull request);
6. on a working branch: the pull request is open, else open it; with no forge (no ``origin``)
   the owner merges the branch once ``check`` is green;
7. on the default branch: the glossary and design brief (``grill-with-docs``), then an
   agent-ready ticket (``to-spec`` and ``to-tickets``) whose blockers are done, its conjecture
   (``/ratchet:conjecture``), and then the build (``/ratchet:feature`` when the owner steps
   away, ``/pstack:poteto-mode`` when they stay).

Usage: ``route.py [--root DIR] [--offline] [--json]``.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import asdict, dataclass
from pathlib import Path

import feature_preflight as preflight
import gatelib

INSTRUCTION_FILES = ("AGENTS.md", "CLAUDE.md")
STANZA = "ratchet:method"
TRACKER_RECORD = Path("docs/agents/issue-tracker.md")
BINDING = Path("docs/process/binding.md")
GLOSSARY = Path("GLOSSARY.md")
GLOSSARY_PLACEHOLDER = "<project>"
DESIGN_BRIEF = Path("docs/design-brief.md")
LOCAL_TICKETS = ".scratch/*/issues/*.md"
ENTRY_POINT_ROW = re.compile(r"^\|\s*check\s*\|\s*`([^`]+)`\s*\|", re.MULTILINE)
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
PULL_REQUEST = "open the pull request (the `pr` skill fills the body); a human merges"
WAIT = "nothing: wait for the review; a human merges"
NEXT = "/ratchet:next"
CHANGES = ".scratch/changes"
BLOCKED_RUN = ": blocked run"


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
    """How a finished branch reaches the default branch: a pull request, or the owner's merge."""
    if checkout.forge:
        return PULL_REQUEST
    return f"the owner merges it once `check` is green (no forge; {CHANGES}/<branch>.md)"


def names_method(root: Path) -> bool:
    return any(
        STANZA in (root / name).read_text(encoding="utf-8", errors="replace")
        for name in INSTRUCTION_FILES
        if (root / name).is_file()
    )


def check_command(root: Path) -> str:
    """The command behind the binding's ``check`` entry point, as its table names it."""
    text = (root / BINDING).read_text(encoding="utf-8", errors="replace")
    match = ENTRY_POINT_ROW.search(text)
    return f"`{match.group(1)}`" if match else CHECK_FALLBACK


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


def has_conjecture(ticket: preflight.Ticket, root: Path, gh: preflight.Runner) -> bool:
    return preflight.conjecture_failure(ticket, root, gh) is None


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


def blocked_runs(root: Path, default_branch: str) -> dict[str, str]:
    """Branches whose last feature run blocked and whose block still stands, with its stash.

    The feature skill stashes a blocked run as ``<branch>: blocked run``. The block stands while
    the default branch has not moved past the run: the owner answers the ticket or lands the
    guard edits there, so a newer default branch is the cue to build again.
    """
    listing = gatelib.git(root, "stash", "list", "--format=%gd%x09%gs") or ""
    found: dict[str, str] = {}
    for line in listing.splitlines():
        ref, _, subject = line.partition("\t")
        run = subject.split(": ", 1)[-1]
        if run.endswith(BLOCKED_RUN) and (
            gatelib.git(root, "merge-base", "--is-ancestor", default_branch, ref) is not None
        ):
            found.setdefault(run.removesuffix(BLOCKED_RUN), run)
    return found


def stalled(ticket: preflight.Ticket, runs: dict[str, str]) -> str | None:
    """The stash of the blocked run that still holds ``ticket``, if one does.

    Matched by the whole branch name: local ticket numbers repeat across features.
    """
    return runs.get(own_branch(ticket))


def own_branch(ticket: preflight.Ticket) -> str:
    """The branch the feature preflight names for ``ticket``."""
    return preflight.branch_of(ticket, preflight.slug_of(ticket.title, ticket.number))


def ticket_step(root: Path, gh: preflight.Runner | None, checkout: Checkout) -> Step:
    """The step among the tickets: write one, give one its conjecture, or build one."""
    tickets = tracker_tickets(root, gh) if gh is not None else None
    local = tickets is None
    if tickets is None:
        tickets = local_tickets(root)
    if not tickets:
        return (triage_step(root) if local else None) or Step(
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
    runs = blocked_runs(root, checkout.default_branch)
    tickets = [t for t in unblocked if not stalled(t, runs)]
    if not tickets:
        held = unblocked[0]
        return Step(
            f"answer the questions or land the guard edits the blocked run of {held.ref} named"
            f" (stash `{stalled(held, runs)}`) on {checkout.default_branch}; an answer given"
            " elsewhere, as an issue comment, releases it once you drop that stash",
            f"the last {FEATURE} run on every agent-ready ticket blocked, and"
            f" {checkout.default_branch} has not moved since; a rerun would block the same way",
            f"{FEATURE} {held.ref}, or {POTETO} {held.ref} now to make the edits as the guard asks",
            "blocked-run",
        )
    for ticket in tickets:
        if has_conjecture(ticket, root, offline_gh):
            return Step(
                f"{FEATURE} {ticket.ref}",
                f'ticket {ticket.ref} "{ticket.title}" is agent-ready with a conjecture;'
                f" {POTETO} {ticket.ref} instead when you stay at the keyboard",
                review(checkout),
                "build",
            )
    first = tickets[0]
    return Step(
        f"{CONJECTURE} {first.ref}",
        f'ticket {first.ref} "{first.title}" is agent-ready but states no conjecture',
        f"{FEATURE} {first.ref}",
        "conjecture",
    )


# --- the route -------------------------------------------------------------------------------


def pull_request_step(checkout: Checkout, gh: preflight.Runner | None) -> Step:
    """On a working branch with a clean tree: wait for the review, or open the pull request."""
    if not checkout.forge:
        return Step(
            f"merge `{checkout.branch}` once `check` is green:"
            f" `git switch {checkout.default_branch} && git merge --no-ff {checkout.branch}`",
            f"branch `{checkout.branch}` is clean and there is no forge; its change description"
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


def setup_step(root: Path) -> Step | None:
    """The step before adoption, or ``None`` once the repository is adopted."""
    if not names_method(root):
        return Step(INIT, "AGENTS.md does not name the method", TRACKER_SETUP, "init")
    if not (root / TRACKER_RECORD).is_file():
        return Step(
            TRACKER_SETUP,
            f"{TRACKER_RECORD} is missing: no tracker, no labels (the setup is the owner's)",
            ADOPT,
            "tracker",
        )
    if not (root / BINDING).is_file():
        return Step(ADOPT, f"{BINDING} is missing, so no gate is bound", GRILL, "adopt")
    return None


def decide(root: Path, gh: preflight.Runner | None) -> Step:
    """The owner's next command for the checkout at ``root``; ``gh=None`` stays offline."""
    checkout = observe(root)
    if checkout is None:
        return Step(INIT, f"{root} is not a git repository", TRACKER_SETUP, "init")
    setup = setup_step(root)
    if setup is not None:
        return setup
    if checkout.dirty:
        return Step(
            f"run {check_command(root)}, then commit on branch `{checkout.branch}`",
            "the working tree has uncommitted changes",
            NEXT if checkout.on_default else review(checkout),
            "change",
        )
    if not checkout.on_default:
        return pull_request_step(checkout, gh)
    missing = records_missing(root)
    if missing:
        return Step(GRILL, missing, f"{SPEC}, then {TICKETS}", "records")
    return ticket_step(root, gh, checkout)


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", default=".")
    parser.add_argument("--offline", action="store_true", help="read no tracker")
    parser.add_argument("--json", action="store_true", dest="as_json")
    options = parser.parse_args(argv[1:])
    step = decide(Path(options.root).resolve(), None if options.offline else preflight.run_gh)
    if options.as_json:
        print(json.dumps(asdict(step), indent=2))
    else:
        print(f"next: {step.command}")
        print(f"why: {step.why}")
        print(f"then: {step.then}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
