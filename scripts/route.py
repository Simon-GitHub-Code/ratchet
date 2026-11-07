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
6. on a working branch: the pull request is open, else open it;
7. on the default branch: the glossary and design brief (``grill-with-docs``), then an
   agent-ready ticket (``to-spec`` and ``to-tickets``), its conjecture
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
    default = gatelib.default_branches(root)[0].removeprefix("origin/")
    return Checkout(root, branch or None, default, bool(status.strip()))


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


def local_tickets(root: Path) -> list[preflight.Ticket]:
    """The agent-ready tickets under ``.scratch``, where there is no tracker."""
    label = preflight.ready_label(root)
    tickets = []
    for path in sorted(root.glob(LOCAL_TICKETS)):
        ref = path.relative_to(root).as_posix()
        ticket = preflight.local_ticket(ref, path.read_text(encoding="utf-8", errors="replace"))
        if ticket.is_open and label in ticket.labels:
            tickets.append(ticket)
    return tickets


def has_conjecture(ticket: preflight.Ticket, root: Path, gh: preflight.Runner) -> bool:
    return preflight.conjecture_failure(ticket, root, gh) is None


def ticket_step(root: Path, gh: preflight.Runner | None) -> Step:
    """The step among the tickets: write one, give one its conjecture, or build one."""
    tickets = tracker_tickets(root, gh) if gh is not None else None
    if tickets is None:
        tickets = local_tickets(root)
    if not tickets:
        return Step(
            SPEC,
            "no open ticket carries the agent-ready label",
            f"{TICKETS}, then {CONJECTURE} <ticket>",
            "specify",
        )
    offline_gh = gh or (lambda _args: None)
    for ticket in tickets:
        if has_conjecture(ticket, root, offline_gh):
            return Step(
                f"{FEATURE} {ticket.ref}",
                f'ticket {ticket.ref} "{ticket.title}" is agent-ready with a conjecture;'
                f" {POTETO} {ticket.ref} instead when you stay at the keyboard",
                PULL_REQUEST,
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


def decide(root: Path, gh: preflight.Runner | None = preflight.run_gh) -> Step:
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
            PULL_REQUEST,
            "change",
        )
    if not checkout.on_default:
        return pull_request_step(checkout, gh)
    missing = records_missing(root)
    if missing:
        return Step(GRILL, missing, f"{SPEC}, then {TICKETS}", "records")
    return ticket_step(root, gh)


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
