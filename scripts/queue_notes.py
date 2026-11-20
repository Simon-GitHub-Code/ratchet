from __future__ import annotations

import hashlib
import json
import os
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

import gatelib
import triggers

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "hooks"))
from ratchet_guards.events import FEATURE_STARTS

if TYPE_CHECKING:
    import feature_preflight as preflight

QUEUE = "queue"
SIZE, QUESTIONS, GUARD = "size", "questions", "guard"
SPLIT = "split"
ERRORED = "errored"
REASON_ROW = re.compile(r"\s*(?:[-*]\s+)?`?([a-z][a-z-]*)`?\s*:")
MECHANICAL = frozenset({"behind", "moved", "conflict", "gates", "refs", ERRORED})
RETRIES = 3


def code_of(line: str) -> str:
    found = REASON_ROW.match(line)
    return found.group(1) if found else line.split(":", 1)[0]


def mechanical(reasons: list[str]) -> bool:
    return bool(reasons) and all(code_of(reason) in MECHANICAL for reason in reasons)


def digest(ticket: preflight.Ticket) -> str:
    return hashlib.sha256(ticket.body.encode()).hexdigest()


def edited(note: dict[str, object], ticket: preflight.Ticket) -> bool:
    return note.get("digest") != digest(ticket)


def tip(root: Path) -> str | None:
    for ref in gatelib.default_branches(root):
        found = gatelib.git(root, "rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}")
        if found:
            return found
    return None


def caused(root: Path, note: dict[str, object]) -> bool:
    paths, base, now = texts(note.get("causes")), note.get("base"), tip(root)
    if not paths or not isinstance(base, str) or now is None:
        return False
    return bool(gatelib.git(root, "diff", "--name-only", base, now, "--", *paths))


def loaded(text: str | None) -> dict[str, object]:
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


def feature_start_path(root: Path, branch: str) -> Path | None:
    common = gatelib.git(root, "rev-parse", "--path-format=absolute", "--git-common-dir")
    if not common:
        return None
    return Path(common) / FEATURE_STARTS / f"{branch.replace('/', '-')}.json"


def record_feature_start(root: Path, branch: str, start: dict[str, object]) -> None:
    path = feature_start_path(root, branch)
    if path is not None:
        write_note(path, {"branch": branch, "worktree": str(root), "start": start})


def pass_feature_start(root: Path, branch: str, *, tree: Path | None = None) -> None:
    path = feature_start_path(root, branch)
    if path is None or not path.is_file():
        return
    note = loaded(gatelib.read_text(path))
    if tree is None or Path(str(note.get("worktree"))).resolve() == tree.resolve():
        write_note(path, {**note, "passed": True})


def read_note(root: Path, branch: str) -> dict[str, object]:
    return loaded(gatelib.read_text(note_path(root, branch)))


def write_note(path: Path, note: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    staged = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    staged.write_text(json.dumps(note, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    staged.replace(path)


def safeguarded(root: Path, note: dict[str, object]) -> bool:
    owes, reviews = set(texts(note.get("owes"))), set(texts(note.get("reviews")))
    if not owes and not reviews:
        return False
    if owes & {row.name for row in triggers.owed(root)}:
        return False
    return not reviews & {review.name for review in triggers.reviews_owed(root)}


def holds_of(result: dict[str, object]) -> list[str]:
    found = [SIZE] if result.get("tooLarge") else []
    found += [QUESTIONS] if result.get("openQuestions") else []
    guarded = result.get("guardEdits") or result.get("disputes")
    return found + ([GUARD] if guarded else [])


def causes_of(result: dict[str, object]) -> list[str]:
    edits = [edit.get("path") for edit in tables(result.get("guardEdits"))]
    disputes = tables(result.get("disputes"))
    filed = [dispute.get("path") for dispute in disputes]
    covered = [path for dispute in disputes for path in texts(dispute.get("guardPathsCovered"))]
    paths = [*edits, *filed, *covered]
    return list(dict.fromkeys(path for path in paths if isinstance(path, str) and path))


@dataclass(frozen=True)
class Hold:
    reasons: list[str]
    kinds: list[str]
    owes: list[str]
    reviews: list[str] = field(default_factory=list)
    based: bool = True


def split_into(root: Path, branch: str, ticket: preflight.Ticket) -> list[str] | None:
    note = read_note(root, branch)
    if note.get("outcome") != SPLIT or (ticket.is_open and note.get("digest") != digest(ticket)):
        return None
    return texts(note.get("children"))


def met(root: Path, note: dict[str, object], ticket: preflight.Ticket, *, kind: str) -> bool:
    if kind == GUARD and isinstance(note.get("base"), str):
        return caused(root, note)
    return edited(note, ticket)


def held(root: Path, branch: str, ticket: preflight.Ticket) -> Hold | None:
    note = read_note(root, branch)
    if note.get("hold") is not True:
        return None
    kinds = texts(note.get("holds"))
    owes, reviews = texts(note.get("owes")), texts(note.get("reviews"))
    unmet = [kind for kind in kinds if not met(root, note, ticket, kind=kind)]
    owing = bool(owes or reviews) and not (edited(note, ticket) or safeguarded(root, note))
    other = not (kinds or owes or reviews) and not (edited(note, ticket) or caused(root, note))
    if not (unmet or owing or other):
        return None
    return Hold(
        texts(note.get("reasons")),
        unmet,
        owes if owing else [],
        reviews if owing else [],
        based=isinstance(note.get("base"), str),
    )
