#!/usr/bin/env python3
from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
from dataclasses import asdict, dataclass
from pathlib import Path

import application_types
import documents
import feature_preflight as preflight
import flags
import formal_model
import gatelib
import hooks_installed
import merge
import merge_ready
import owed
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
EXPIRE = (
    "python3 -E -s -B -X pycache_prefix=/nonexistent/ratchet-no-bytecode"
    f' "{PLUGIN_ROOT / "scripts/documents.py"}" expire'
)
CHECK_FALLBACK = "the binding's `check` entry point"

INIT = "/ratchet:init"
TRACKER_SETUP = "/mattpocock-skills:setup-matt-pocock-skills"
ADOPT = "/ratchet:adopt"
GRILL = "/mattpocock-skills:grill-with-docs"
SPEC = "/mattpocock-skills:to-spec"
TICKETS = "/mattpocock-skills:to-tickets"
CONJECTURE = "/ratchet:conjecture"
FEATURE = "/ratchet:feature"
PULL_REQUEST = (
    "open the pull request (`mattpocock-skills:pr` fills the body); its merge job merges it when"
    " the merge judge passes it, and comments why when the merge is yours"
)
WAIT = "nothing: the merge job merges it once `ci` is green, or comments why the merge is yours"
MERGE_FALLBACK = "the binding's `merge` entry point"
NEXT = "/ratchet:next"
SAFEGUARD = "/ratchet:safeguard"
FILL = "/ratchet:fill"
ARCHITECTURE = "/ratchet:architecture"
ARCHITECTURE_RULE = "architecture"
EVENTS_LOG = "events.tsv"
EVENTS_HEADER = "date\tevent\tsign\tanswer"
DECLINED = "declined"
CHANGES = ".scratch/changes"
BLOCKED_RUN = ": blocked run"
SHOWN = 3


@dataclass(frozen=True)
class Step:
    command: str
    why: str
    then: str
    stage: str


@dataclass(frozen=True)
class Checkout:
    root: Path
    branch: str | None
    default_branch: str
    dirty: bool
    forge: bool = True

    @property
    def on_default(self) -> bool:
        return self.branch in (self.default_branch, None)


def observe(root: Path) -> Checkout | None:
    status = gatelib.git(root, "status", "--porcelain", "--untracked-files=all")
    if status is None:
        return None
    branch = gatelib.git(root, "rev-parse", "--abbrev-ref", "HEAD")
    branch = None if branch == "HEAD" else branch
    default = gatelib.default_branches(root)[0].removeprefix("origin/")
    forge = gatelib.git(root, "remote", "get-url", "origin") is not None
    return Checkout(root, branch or None, default, bool(status.strip()), forge)


def review(checkout: Checkout) -> str:
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
    command = gatelib.entry_point(root, "check")
    return f"`{command}`" if command else CHECK_FALLBACK


def merge_command(root: Path, branch: str) -> str:
    command = gatelib.entry_point(root, "merge")
    return f"`{command} {branch}`" if command else f"{MERGE_FALLBACK} with `{branch}`"


def records_missing(root: Path) -> str | None:
    glossary = root / GLOSSARY
    if not glossary.is_file():
        return "GLOSSARY.md is missing"
    if GLOSSARY_PLACEHOLDER in glossary.read_text(encoding="utf-8", errors="replace"):
        return f"GLOSSARY.md still holds the `{GLOSSARY_PLACEHOLDER}` placeholder"
    if not (root / DESIGN_BRIEF).is_file():
        return f"{DESIGN_BRIEF} is missing"
    return None


def tracker_tickets(root: Path, gh: preflight.Runner) -> list[preflight.Ticket] | None:
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
    label = preflight.ready_label(root)
    tickets = []
    for path in sorted(root.glob(LOCAL_TICKETS)):
        ref = path.relative_to(root).as_posix()
        ticket = preflight.local_ticket(ref, path.read_text(encoding="utf-8", errors="replace"))
        if ticket.is_open and (label in ticket.labels) == ready:
            tickets.append(ticket)
    return tickets


def triage_step(root: Path) -> Step | None:
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
    for spec in sorted(root.glob(LOCAL_SPECS)):
        if not any((spec.parent / "issues").glob("*.md")):
            ref = spec.relative_to(root).as_posix()
            text = spec.read_text(encoding="utf-8", errors="replace")
            lacking = unstated(preflight.local_ticket(ref, text), root, lambda _args: None)
            if lacking:
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
    if preflight.conjecture_failure(ticket, root, gh) is not None:
        return "states no conjecture"
    if preflight.operability_failure(ticket, root, gh) is not None:
        return "does not say how it is operated"
    return None


def then_expire(root: Path, ref: str, line: str) -> str:
    last = documents.closes(documents.work_tree(root), ref)
    return f"{line}, then run `{EXPIRE}`" if last else line


def expire_step(root: Path) -> Step | None:
    left = sorted(documents.listed(documents.work_tree(root)))
    if not left:
        return None
    paths = f"{', '.join(left[:SHOWN])}{' ...' if len(left) > SHOWN else ''}"
    one = len(left) == 1
    return Step(
        EXPIRE,
        f"{paths} {'has' if one else 'have'} expired and {'is' if one else 'are'} still in the"
        f" tree: this deletes {'it' if one else 'them'} and carries {'its' if one else 'their'}"
        " `## Answers` lines to the design brief's `## In the owner's words`",
        NEXT,
        "expire",
    )


def blocking(tickets: list[preflight.Ticket], root: Path, gh: preflight.Runner) -> Step | None:
    for ticket in tickets:
        for ref in ticket.blockers:
            blocker = preflight.fetch(preflight.blocker_ref(ticket, ref, root), root, gh)
            if blocker is not None and blocker.is_open:
                finish = f'finish ticket {blocker.ref} "{blocker.title}", then mark it resolved'
                return Step(
                    finish,
                    f"every agent-ready ticket waits on an open blocker; {ticket.ref} waits on it",
                    NEXT,
                    "blocked",
                )
    return None


def merged_features(root: Path, default_branch: str) -> list[str]:
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


def finished(root: Path, tickets: list[preflight.Ticket], merged: list[str]) -> Step | None:
    for ticket in tickets:
        branch = own_branch(ticket)
        if branch in merged:
            mark = (
                f"mark ticket {ticket.ref} resolved (`Status: resolved` on a local ticket;"
                " close the issue on a forge)"
            )
            return Step(
                then_expire(root, ticket.ref, mark),
                f"branch `{branch}` is merged, yet {ticket.ref} is still open",
                NEXT,
                "resolve",
            )
    return None


def blocked_runs(
    root: Path, default_branch: str, tickets: list[preflight.Ticket] | None = None
) -> dict[str, str]:
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
        if not of_this_run(root, default_branch, ref, note=note):
            note = {}
        recorded = isinstance(note.get("digest"), str)
        if ticket is not None and recorded and queue_notes.edited(note, ticket):
            continue
        asked = asked_paths(ticket, queue_notes.texts(note.get("causes")), recorded=recorded)
        if not answered(root, default_branch, ref, asked=asked):
            found.setdefault(branch, run)
    return found


def of_this_run(root: Path, default_branch: str, stash: str, *, note: dict[str, object]) -> bool:
    base = note.get("base")
    if not isinstance(base, str):
        return False
    fork = gatelib.git(root, "merge-base", default_branch, stash) or default_branch
    return gatelib.git(root, "merge-base", "--is-ancestor", fork, base) is not None


def asked_paths(ticket: preflight.Ticket | None, causes: list[str], *, recorded: bool) -> list[str]:
    own = [] if ticket is None else [ticket.parent or ""] + ([] if recorded else [ticket.ref])
    return [path for path in [*own, *causes] if path and not path.lstrip("#").isdigit()]


def answered(root: Path, default_branch: str, stash: str, *, asked: list[str]) -> bool:
    if gatelib.git(root, "merge-base", "--is-ancestor", default_branch, stash) is not None:
        return False
    fork = gatelib.git(root, "merge-base", default_branch, stash) or default_branch
    if not asked:
        return False
    return bool(gatelib.git(root, "diff", "--name-only", fork, default_branch, "--", *asked))


def stalled(ticket: preflight.Ticket, runs: dict[str, str]) -> str | None:
    return runs.get(own_branch(ticket))


def own_branch(ticket: preflight.Ticket) -> str:
    return preflight.branch_of(ticket, preflight.slug_of(ticket.title, ticket.number))


def releases(hold: queue_notes.Hold, ticket: preflight.Ticket, default_branch: str) -> list[str]:
    ref = ticket.ref
    steps = []
    if queue_notes.SIZE in hold.kinds:
        steps.append(
            f"change the criteria of ticket {ref} so they divide into parts that fit: the"
            " agent's own split of it was refused (the drain's report says why)"
        )
    if queue_notes.QUESTIONS in hold.kinds:
        steps.append(f"answer the open questions the run of {ref} named, on the ticket")
    if queue_notes.GUARD in hold.kinds and hold.based:
        steps.append(f"land the guard edits the run of {ref} named on {default_branch}")
    elif queue_notes.GUARD in hold.kinds:
        steps.append(
            f"change ticket {ref}: its hold was written before the drain kept the commit that"
            f" landing the guard edits on {default_branch} is read from, so landing frees nothing"
        )
    if hold.reviews:
        steps.append(f"run {ARCHITECTURE} for {', '.join(hold.reviews)}")
    meet = meet_step(ref)
    defective = defect_kind(ticket) if hold.owes else None
    if defective in hold.owes:
        others = [kind for kind in hold.owes if kind != defective]
        if others:
            steps.append(f"run {SAFEGUARD} for {', '.join(others)}")
        steps.append(meet)
    elif hold.owes:
        steps.append(f"run {SAFEGUARD} for {', '.join(hold.owes)}")
    return steps or [meet]


def meet_step(ref: str) -> str:
    return f"change ticket {ref} to meet what the drain parked it for"


def owner_merge(default: str, name: str) -> str:
    return f"`git switch {default} && git merge --no-ff {name}`, typed by the owner"


def defect_kind(ticket: preflight.Ticket) -> str | None:
    line = ticket.formal_model
    if line is None or line.defect is None:
        return None
    kinds = application_types.code_kinds(application_types.load().trigger_of(line.defect))
    return kinds[0] if kinds else None


def drain_hold(root: Path, ticket: preflight.Ticket) -> queue_notes.Hold | None:
    return queue_notes.held(root, own_branch(ticket), ticket)


def agent_ready(root: Path, gh: preflight.Runner | None) -> tuple[list[preflight.Ticket], bool]:
    tickets = tracker_tickets(root, gh) if gh is not None else None
    return (local_tickets(root), True) if tickets is None else (tickets, False)


def build_step(ready: list[preflight.Ticket], queue: int, checkout: Checkout) -> Step | None:
    if not ready:
        return None
    if queue > 1:
        return Step(
            FEATURE,
            f"{queue} agent-ready tickets state a conjecture: with no ticket named the command"
            " builds them at once, each in a work tree of its own, and merges what the merge judge"
            f" passes; {FEATURE} <ticket> builds one, or ask this session to build one of them"
            " with you here",
            "the drain's one report: what merged, what waits for you and why, every assumption",
            "drain",
        )
    ticket = ready[0]
    return Step(
        f"{FEATURE} {ticket.ref}",
        f'ticket {ticket.ref} "{ticket.title}" is agent-ready with a conjecture;'
        f" or ask this session to build ticket {ticket.ref} with you here",
        review(checkout),
        "build",
    )


def guard_words(kinds: list[str]) -> str:
    return (
        ", or ask this session to make the guard edits with you here; their merge is yours"
        if queue_notes.GUARD in kinds
        else ""
    )


def blocked_kinds(root: Path, ticket: preflight.Ticket) -> list[str]:
    return queue_notes.texts(queue_notes.read_note(root, own_branch(ticket)).get("holds"))


def ticket_step(root: Path, gh: preflight.Runner | None, checkout: Checkout) -> Step:
    tickets, local = agent_ready(root, gh)
    if not tickets:
        return (triage_step(root) or unsplit_spec(root) if local else None) or Step(
            SPEC,
            "no open ticket carries the agent-ready label",
            f"{TICKETS}, then {CONJECTURE} <ticket>",
            "specify",
        )
    done = finished(root, tickets, merged_features(root, checkout.default_branch))
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
                FEATURE + guard_words(hold.kinds),
                "blocked-run",
            )
        return Step(
            f"answer the questions or land the guard edits the blocked run of {held.ref} named"
            f" (stash `{stalled(held, runs)}`) on {checkout.default_branch}; an answer given"
            " elsewhere, as an issue comment, releases it once you drop that stash",
            f"the last {FEATURE} run on every agent-ready ticket blocked, and"
            f" {checkout.default_branch} has not moved since; a rerun would block the same way",
            f"{FEATURE} {held.ref}" + guard_words(blocked_kinds(root, held)),
            "blocked-run",
        )
    first = tickets[0]
    ready = [ticket for ticket in tickets if ticket in stated]
    return build_step(ready, len(stated), checkout) or Step(
        f"{CONJECTURE} {first.ref}",
        f'ticket {first.ref} "{first.title}" is agent-ready but'
        f" {unstated(first, root, offline_gh)}",
        f"{FEATURE} {first.ref}",
        "conjecture",
    )


def switch_step(checkout: Checkout) -> Step | None:
    default, root = checkout.default_branch, checkout.root
    if gatelib.git(root, "merge-base", "--is-ancestor", "HEAD", default) is None:
        return None
    if (
        checkout.forge
        and gatelib.git(root, "merge-base", "--is-ancestor", default, "HEAD") is not None
    ):
        return None
    return Step(
        f"git switch {default}",
        f"`{default}` holds every commit of branch `{checkout.branch}`, so it has nothing to merge",
        NEXT,
        "switch",
    )


def pull_request_step(checkout: Checkout, gh: preflight.Runner | None) -> Step:
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


def adoption_step(root: Path) -> Step | None:
    binding = root / BINDING
    if not binding.is_file():
        return Step(ADOPT, f"{BINDING} is missing, so no gate is bound", GRILL, "adopt")
    if plugin_pin.pinned(binding.read_text(errors="replace")) is None:
        return Step(
            ADOPT,
            f"{BINDING} has no plugin pin, so an adoption stopped before it bound the gates;"
            " it resumes from the templates already copied, on its own branch if it has one",
            GRILL,
            "adopt",
        )
    return None


def setup_step(root: Path) -> Step | None:
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


def owned_by_a_run(root: Path, name: str) -> bool:
    return name.startswith(preflight.FEATURE_BRANCHES) or bool(queue_notes.read_note(root, name))


def could_be_records(root: Path, default: str, name: str) -> bool:
    listed = gatelib.git(root, "diff", "--name-only", "--no-renames", f"{default}...{name}")
    paths = (listed or "").split("\n")
    paths = [path for path in paths if path]
    shapes = [kind.shape for kind in merge_ready.RECORDS]
    return bool(paths) and all(any(shape.fullmatch(path) for shape in shapes) for path in paths)


def first_commit_time(root: Path, default: str, name: str) -> int:
    said = gatelib.git(root, "log", "--reverse", "--format=%at", f"{default}..{name}")
    log = (said or "").split()
    return int(log[0]) if log else 0


def checked_out(root: Path) -> set[str]:
    listed = gatelib.git(root, "worktree", "list", "--porcelain") or ""
    return {
        line.removeprefix("branch refs/heads/")
        for line in listed.splitlines()
        if line.startswith("branch refs/heads/")
    }


def waiting_records(checkout: Checkout) -> list[str]:
    root, default = checkout.root, checkout.default_branch
    listed = gatelib.git(root, "branch", "--no-merged", default, "--format=%(refname:short)")
    held = checked_out(root)
    names = [
        name
        for name in (listed or "").split()
        if name not in (default, checkout.branch)
        and name not in held
        and could_be_records(root, default, name)
        and not owned_by_a_run(root, name)
        and merge.holds_only_records(root, default, name)
    ]
    return sorted(names, key=lambda name: first_commit_time(root, default, name))


def records_waiting(checkout: Checkout) -> Step | None:
    root, default = checkout.root, checkout.default_branch
    waiting = waiting_records(checkout)
    if not waiting:
        return None
    name = waiting[0]
    count = len(waiting)
    more = f"; {count} such branches wait, and this is the oldest" if count > 1 else ""
    reasons = merge_ready.judge(root, f"{merge.HEADS}{default}", f"{merge.HEADS}{name}")
    if not reasons:
        return Step(
            merge_command(root, name),
            f"branch `{name}` holds records and was not merged; its change holds only records,"
            " and the merge judge passes them, so an agent runs this, and it merges them when"
            f" `check` is green on the merge result{more}",
            NEXT,
            "records",
        )
    return Step(
        owner_merge(default, name),
        f"the merge judge leaves branch `{name}` to the owner, and its change holds only"
        f" records, so the owner merges or deletes it: {reasons[0].text};"
        f" {merge_command(root, name)} prints every reason{more}",
        NEXT,
        "records",
    )


def decide(root: Path, gh: preflight.Runner | None) -> Step:
    checkout = observe(root)
    if checkout is None:
        return Step(INIT, f"{root} is not a git repository", TRACKER_SETUP, "init")
    first = setup_step(root) or records_stopped(checkout)
    if first is not None:
        return first
    if checkout.dirty:
        return dirty_step(checkout)
    if not checkout.on_default:
        return switch_step(checkout) or pull_request_step(checkout, gh)
    waiting = records_waiting(checkout) or fill_step(root)
    if waiting is not None:
        return waiting
    missing = records_missing(root)
    step = expire_step(root) or (
        Step(GRILL, missing, f"{SPEC}, then {TICKETS}", "records")
        if missing
        else ticket_step(root, gh, checkout)
    )
    formal = upkeep(root, gh, checkout)
    return (
        review_step(root, step, formal.defects)
        or safeguard_step(root, step, formal.defects)
        or architecture_step(root, step)
        or formal_step(formal, checkout, step)
        or step
    )


def fill_step(root: Path) -> Step | None:
    owing = owed.fill_first(root)
    if not owing:
        return None
    rules = ", ".join(sorted({entry.rule for entry in owing}))
    return Step(
        FILL,
        f"the upgrade to {owing[0].since} left {len(owing)} rows owed ({rules}) that an agent"
        " writes from the repository alone: it reads the tree and its tools, proposes each row"
        " and writes it, and nothing asks you. No feature or drain starts until they are written",
        NEXT,
        "fill",
    )


def review_step(root: Path, instead: Step, defects: frozenset[str]) -> Step | None:
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
    then = (
        safeguard_step(root, instead, defects) or architecture_step(root, instead) or instead
    ).command
    return Step(
        ARCHITECTURE,
        f"{'; '.join(said)}. Or carry on with {instead.command}: {cost}",
        then,
        "architecture-review",
    )


def safeguard_step(root: Path, instead: Step, defects: frozenset[str]) -> Step | None:
    owing = [row for row in triggers.owed(root) if row.question not in defects]
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
    then = (architecture_step(root, instead) or instead).command
    return Step(
        SAFEGUARD,
        f"{'; '.join(said)}. Or carry on with {instead.command}: {cost}",
        then,
        "safeguard",
    )


@dataclass(frozen=True)
class Upkeep:
    defects: frozenset[str] = frozenset()
    ticket: preflight.Ticket | None = None
    stated: bool = True
    waiting: str | None = None
    behind: formal_model.Behind | None = None


def formal_tickets(root: Path, gh: preflight.Runner | None) -> list[preflight.Ticket]:
    ready, local = agent_ready(root, gh)
    unready = local_tickets(root, ready=False) if local else []
    return [ticket for ticket in [*ready, *unready] if ticket.formal_model is not None]


def upkeep(root: Path, gh: preflight.Runner | None, checkout: Checkout) -> Upkeep:
    formal = formal_model.read(gatelib.read_text(root / BINDING) or "")
    if formal is None and not formal_model.started(root):
        return Upkeep()
    lines = [(ticket, ticket.formal_model) for ticket in formal_tickets(root, gh)]
    defects = frozenset(line.defect for _, line in lines if line and line.defect)
    for ticket, line in lines:
        if line and line.round:
            stated = unstated(ticket, root, gh or (lambda _args: None)) is None
            return Upkeep(defects, ticket=ticket, stated=stated)
    branch = formal_model.waiting(root, checkout.default_branch)
    if branch is not None or formal is None:
        return Upkeep(defects, waiting=branch)
    return Upkeep(defects, behind=formal_model.behind(root, checkout.default_branch, formal))


def formal_step(upkeeping: Upkeep, checkout: Checkout, instead: Step) -> Step | None:
    carry = f"Or carry on with {instead.command}: nothing is red for it"
    if upkeeping.ticket is not None:
        ticket = upkeeping.ticket
        if not upkeeping.stated:
            return None
        return Step(
            f"{FEATURE} {ticket.ref}",
            f'ticket {ticket.ref} "{ticket.title}" carries what the formal model\'s last round'
            f" found, and fixing it is the model's next step. {carry}",
            instead.command,
            "formal-ticket",
        )
    if upkeeping.waiting is not None:
        return Step(
            owner_merge(checkout.default_branch, upkeeping.waiting),
            f"branch `{upkeeping.waiting}` holds a round of the formal model's upkeep, and it"
            f" changes guard paths, so it waits for your merge. {carry}",
            instead.command,
            "formal-round",
        )
    late = upkeeping.behind
    if late is None:
        return None
    if not late.synced:
        why = (
            "the formal model has never been checked against the code: the binding's"
            " `- Synced:` line names no commit"
        )
    elif not late.commit:
        why = (
            f"the formal model may be behind: this clone does not hold the `- Synced:` commit"
            f" {late.synced}, so it cannot tell; fetch the default branch's full history, then"
            " run this again"
        )
    else:
        why = (
            f"the formal model is behind the code it mirrors: commit {late.commit} on"
            f" `{checkout.default_branch}` changed mirrored code after the `- Synced:` commit"
            f" {late.synced}, so a round checks the model against it"
        )
    return Step(SAFEGUARD, f"{why}. {carry}", instead.command, "formal-upkeep")


def architecture_step(root: Path, instead: Step) -> Step | None:
    owing = [entry for entry in owed.read(root).entries if entry.rule == ARCHITECTURE_RULE]
    if not owing:
        return None
    return Step(
        ARCHITECTURE,
        f"the architecture is owed since the upgrade to {owing[0].since}: no decision record"
        " says how the system is built, what was weighed against it and what changing it would"
        " cost, or the binding declares no parts; an agent drafts the options from the code and"
        f" asks you once. Or carry on with {instead.command}: nothing is red for it",
        instead.command,
        "architecture",
    )


def declined_signs(root: Path) -> set[tuple[str, str]]:
    rows = (gatelib.read_text(root / gatelib.STATE / EVENTS_LOG) or "").splitlines()[1:]
    cells = [row.split("\t") for row in rows]
    return {(c[1], c[2]) for c in cells if len(c) == len(EVENTS_HEADER.split("\t"))}


def event_proposals(root: Path) -> list[str]:
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


def expired_switches(root: Path) -> list[str]:
    text = gatelib.read_text(root / flags.REGISTRY)
    if text is None:
        return []
    return flags.expired(flags.parse(text)[0], dt.datetime.now(tz=dt.UTC).date())


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
    also = (
        pin_proposals(root)
        + questions.proposals(root)
        + event_proposals(root)
        + expired_switches(root)
    )
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
