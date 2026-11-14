"""The drain's note on a ticket: what it keeps between sessions, read by the drain and the route.

``drain.py close`` writes ``.ratchet/queue/tickets/<branch>.json``: the outcome, the reasons, the
digest of the ticket's text it was run on, and the kinds of hold. A parked ticket stays held until
its text changes (or, for an owed safeguard, until the safeguard is bound), and ``route.py`` reads
the same note, so the step it names is the one that releases the hold the drain keeps.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import TYPE_CHECKING

import gatelib
import triggers

if TYPE_CHECKING:
    from pathlib import Path

    import feature_preflight as preflight

QUEUE = "queue"
# Why: the kinds of hold a parked run leaves, read from its result; each has its own release.
SIZE, QUESTIONS, GUARD = "size", "questions", "guard"
# Why: the judge's reason codes the drain resolves itself: update, judge again, run the gates
# again. A ticket left to the owner for these alone is retried, not held; any other code is the
# owner's decision, and holds the ticket until its text changes.
MECHANICAL = frozenset({"behind", "moved", "conflict", "gates", "refs"})
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
    """Whether a ticket held for an owed safeguard is free: none of those kinds is owed now.

    Why: the ticket's text need not change for it to build; binding the safeguard is the change.
    """
    owes = set(texts(note.get("owes")))
    return bool(owes) and not owes & {row.name for row in triggers.owed(root)}


def holds_of(result: dict[str, object]) -> list[str]:
    """The kinds of hold a pipeline's result leaves: too large, open questions, guard edits."""
    found = [SIZE] if result.get("tooLarge") else []
    found += [QUESTIONS] if result.get("openQuestions") else []
    return found + ([GUARD] if result.get("guardEdits") else [])


@dataclass(frozen=True)
class Hold:
    """A parked ticket the drain still holds: why, and which kinds of release it needs."""

    reasons: list[str]
    kinds: list[str]
    owes: list[str]


def held(root: Path, branch: str, ticket: preflight.Ticket) -> Hold | None:
    """The drain's hold on ``ticket``; ``None`` once its text changed or its safeguard is bound."""
    note = read_note(root, branch)
    if note.get("hold") is not True or note.get("digest") != digest(ticket):
        return None
    if safeguarded(root, note):
        return None
    return Hold(texts(note.get("reasons")), texts(note.get("holds")), texts(note.get("owes")))
