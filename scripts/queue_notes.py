"""The drain's note on a ticket: what it keeps between sessions, read by the drain and the route.

``drain.py close`` writes ``.ratchet/queue/tickets/<branch>.json``: the outcome, the reasons, the
digest of the ticket's text it was run on, and the kinds of hold. A parked ticket stays held until
its text changes, a path its run named as the cause changes on the default branch, or (for an owed
safeguard) the safeguard is bound; ``released`` is that one rule, and ``route.py`` reads the same
note through it, so the step it names is the one that releases the hold the drain keeps.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

import gatelib
import triggers

if TYPE_CHECKING:
    from pathlib import Path

    import feature_preflight as preflight

QUEUE = "queue"
# Why: the kinds of hold a parked run leaves, read from its result; each has its own release.
SIZE, QUESTIONS, GUARD = "size", "questions", "guard"
# Why: ADR 0046. A ticket the agent split by size is closed with this outcome, its text untouched.
SPLIT = "split"
# Why: the judge's reason codes the drain resolves itself: update, judge again, run the gates
# again. A ticket left to the owner for these alone is retried, not held; any other code is the
# owner's decision, and holds the ticket until its text changes. ``errored`` (ADR 0055) is a
# pipeline run that threw twice, an agent having died each time: the run is the thing to retry.
ERRORED = "errored"
MECHANICAL = frozenset({"behind", "moved", "conflict", "gates", "refs", ERRORED})
# Why: how many times in a row those may leave a ticket waiting before it is the owner's.
RETRIES = 3


def code_of(line: str) -> str:
    """The code a judge's reason line starts with: ``code: text``, as a ``Reason`` prints."""
    return line.split(":", 1)[0]


def mechanical(reasons: list[str]) -> bool:
    """Whether every reason is one the drain resolves itself."""
    return bool(reasons) and all(code_of(reason) in MECHANICAL for reason in reasons)


def digest(ticket: preflight.Ticket) -> str:
    return hashlib.sha256(ticket.body.encode()).hexdigest()


def edited(note: dict[str, object], ticket: preflight.Ticket) -> bool:
    """Whether ``ticket``'s text differs from the text the note was written on.

    Why: the one test of "the ticket changed", for a local file and a forge issue alike. The drain
    holds a parked ticket until it is true, and the route releases a blocked run when it is.
    """
    return note.get("digest") != digest(ticket)


def tip(root: Path) -> str | None:
    """The commit the default branch is at now, or ``None`` when no candidate names one."""
    for ref in gatelib.default_branches(root):
        found = gatelib.git(root, "rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}")
        if found:
            return found
    return None


def caused(root: Path, note: dict[str, object]) -> bool:
    """Whether a path the run named as its cause changed on the default branch since the hold.

    Why: landing the guard edits a parked run asked for is the answer it waits on; the note keeps
    the default branch's commit at that time (``base``) so the change can be read from git.
    """
    paths, base, now = texts(note.get("causes")), note.get("base"), tip(root)
    if not paths or not isinstance(base, str) or now is None:
        return False
    return bool(gatelib.git(root, "diff", "--name-only", base, now, "--", *paths))


def loaded(text: str | None) -> dict[str, object]:
    """``text`` as a JSON object, or an empty one when it is not."""
    try:
        data: object = json.loads(text or "")
    except ValueError:
        return {}
    return data if isinstance(data, dict) else {}


def texts(value: object) -> list[str]:
    return [item for item in value if isinstance(item, str)] if isinstance(value, list) else []


def tables(value: object) -> list[dict[str, object]]:
    return [item for item in value if isinstance(item, dict)] if isinstance(value, list) else []


def note_path(root: Path, branch: str) -> Path:
    return gatelib.state_dir(root) / QUEUE / "tickets" / f"{branch.replace('/', '-')}.json"


def read_note(root: Path, branch: str) -> dict[str, object]:
    return loaded(gatelib.read_text(note_path(root, branch)))


def write_note(path: Path, note: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(note, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def safeguarded(root: Path, note: dict[str, object]) -> bool:
    """Whether a ticket held for an owed safeguard or review is free: none of those is owed now.

    Why: the ticket's text need not change for it to build; binding the safeguard, or landing the
    architecture review (ADR 0052), is the change.
    """
    owes, reviews = set(texts(note.get("owes"))), set(texts(note.get("reviews")))
    if not owes and not reviews:
        return False
    if owes & {row.name for row in triggers.owed(root)}:
        return False
    return not reviews & {review.name for review in triggers.reviews_owed(root)}


def holds_of(result: dict[str, object]) -> list[str]:
    """The kinds of hold a pipeline's result leaves: too large, open questions, guard edits."""
    found = [SIZE] if result.get("tooLarge") else []
    found += [QUESTIONS] if result.get("openQuestions") else []
    return found + ([GUARD] if result.get("guardEdits") else [])


def causes_of(result: dict[str, object]) -> list[str]:
    """The repo paths a blocked run named as its cause: guard paths it needed, disputes it filed.

    Why: the route releases a blocked run only when one of these changes on the default branch.
    """
    edits = [edit.get("path") for edit in tables(result.get("guardEdits"))]
    filed = [dispute.get("path") for dispute in tables(result.get("disputes"))]
    return list(dict.fromkeys(path for path in [*edits, *filed] if isinstance(path, str) and path))


@dataclass(frozen=True)
class Hold:
    """A parked ticket the drain still holds: why, and which kinds of release it needs."""

    reasons: list[str]
    kinds: list[str]
    owes: list[str]
    reviews: list[str] = field(default_factory=list)
    # Why: a note written before the drain kept the default branch's commit has no base, so
    # landing the guard edits cannot release it; only a change to the ticket does.
    based: bool = True


def split_into(root: Path, branch: str, ticket: preflight.Ticket) -> list[str] | None:
    """The parts the agent split ``ticket`` into, or ``None`` if it was not, as it reads now."""
    note = read_note(root, branch)
    if note.get("outcome") != SPLIT or note.get("digest") != digest(ticket):
        return None
    return texts(note.get("children"))


def released(root: Path, note: dict[str, object], ticket: preflight.Ticket) -> bool:
    """The one rule that frees a held ticket: its text changed, a cause landed, or it is bound.

    Why: the drain's plan and the route both ask this, so neither keeps a hold the other lifted.
    """
    return edited(note, ticket) or caused(root, note) or safeguarded(root, note)


def held(root: Path, branch: str, ticket: preflight.Ticket) -> Hold | None:
    """The drain's hold on ``ticket``; ``None`` once ``released`` says it is free."""
    note = read_note(root, branch)
    if note.get("hold") is not True or released(root, note, ticket):
        return None
    return Hold(
        texts(note.get("reasons")),
        texts(note.get("holds")),
        texts(note.get("owes")),
        texts(note.get("reviews")),
        based=isinstance(note.get("base"), str),
    )
