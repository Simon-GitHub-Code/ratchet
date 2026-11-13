"""The agent-ready label on a local ticket: an agent that writes it asks the owner.

On a local markdown tracker (mattpocock-skills' ``.scratch/<feature>/``) a ticket's label is its
``Status:`` line, so writing the agent-ready label there is the act ``gh issue edit
--add-label`` is on a forge: the owner's agreement that the ticket is ready to build, which the
feature preflight reads as such. A ticket is what the route and the preflight read as one, a
markdown file at ``.scratch/<feature>/issues/<name>.md``; the spec beside it and a change
description under ``.scratch/changes/`` are not. An Edit, Write or MultiEdit of a ticket that
leaves it with that status where it had none asks.

A command line or a patch cannot be run ahead to see what a file will hold. There the label is
read as ``gh`` reads it: a call whose text names the label and which writes a ticket that does
not hold the status gives it, and asks. So does one that writes a directory tickets live in
(``.scratch``, a feature's directory or its ``issues``), which may bring tickets with it. A path
is read the way the shell and the filesystem may read it: in any case, through ``..`` and links,
and with a glob in any part standing for whatever it could match. One call that writes every
ticket of a spec is one ask, which names each of them. Out of reach, as for every shell rule
(ADR 0024): a script run by path, a file copied into place, a label put together when the
command runs.
"""

import posixpath
import re
from collections.abc import Iterable, Mapping
from pathlib import Path

from ratchet_guards.context import Context, Guard
from ratchet_guards.gh import ready_label
from ratchet_guards.verdict import Verdict

TRACKER_DIRECTORY = ".scratch"
TICKETS_DIRECTORY = "issues"
TICKET_SUFFIX = ".md"
_GLOB = re.compile(r"[*?[]")
RULE = "agent-ready-label"
STATUS_LINE = re.compile(
    r"^\*{0,2}status:?\*{0,2}:?\s*`?([^`\n]+?)`?\s*$", re.IGNORECASE | re.MULTILINE
)


def _readings(path: Path, guard: Guard) -> tuple[Path, ...]:
    """``path`` as written with ``..`` folded, and as the filesystem resolves it."""
    return (Path(posixpath.normpath(path)), guard.locator.filesystem.canonical(path))


def _part(part: str, name: str) -> bool:
    """Whether a path part may name ``name``: in any case, or as a glob that could match it."""
    return part.casefold() == name or _GLOB.search(part) is not None


def _is_ticket(path: Path) -> bool:
    """Whether ``path`` is a ticket: ``.scratch/<feature>/issues/<name>.md``."""
    *_, tracker, _, issues, name = ("", "", "", *path.parts)
    return (
        _part(tracker, TRACKER_DIRECTORY)
        and _part(issues, TICKETS_DIRECTORY)
        and (name.casefold().endswith(TICKET_SUFFIX) or _GLOB.search(name) is not None)
    )


def _holds_tickets(path: Path) -> bool:
    """Whether ``path`` is ``.scratch``, a feature's directory in it, or that feature's issues."""
    *_, tracker, feature, last = ("", "", *path.parts)
    return (
        _part(last, TRACKER_DIRECTORY)
        or _part(feature, TRACKER_DIRECTORY)
        or (_part(tracker, TRACKER_DIRECTORY) and _part(last, TICKETS_DIRECTORY))
    )


def statuses(text: str) -> set[str]:
    """Every value a ``Status:`` line in ``text`` gives."""
    return {value.strip() for value in STATUS_LINE.findall(text)}


def _replace(text: str, edit: Mapping[str, object]) -> str:
    old, new = str(edit.get("old_string", "")), str(edit.get("new_string", ""))
    return text.replace(old, new) if edit.get("replace_all") is True else text.replace(old, new, 1)


def after(tool: str, tool_input: Mapping[str, object], before: str) -> str:
    """The file's text once the tool call has run."""
    if tool == "Write":
        return str(tool_input.get("content", ""))
    if tool == "Edit":
        return _replace(before, tool_input)
    edits = tool_input.get("edits")
    for edit in edits if isinstance(edits, list) else []:
        if isinstance(edit, Mapping):
            before = _replace(before, edit)
    return before


def check(
    tool: str, tool_input: Mapping[str, object], path: Path, *, guard: Guard
) -> Verdict | None:
    """``agent-ready-label`` when the call gives a local ticket the agent-ready status.

    The label's spelling is the record of the checkout holding the ticket, not the session's.
    """
    if not any(map(_is_ticket, _readings(path, guard))):
        return None
    label = ready_label(Context(path.parent), guard)
    before = guard.locator.filesystem.read_text(path) or ""
    if label in statuses(after(tool, tool_input, before)) and label not in statuses(before):
        return Verdict(RULE, _detail([path.name], label))
    return None


def _detail(names: list[str], label: str) -> str:
    return f"{', '.join(names)} {'gets' if len(names) == 1 else 'get'} the status `{label}`"


def named(words: Iterable[str], text: str, context: Context, *, guard: Guard) -> list[Verdict]:
    """``agent-ready-label`` for the local tickets ``words`` name, when ``text`` names the label.

    ``words`` are the paths a command or a patch writes, patterns included, and ``text`` is the
    whole call. A ticket that holds the status already is left out, so the ask names the tickets
    that would get it and a call that takes the status away asks nothing.
    """
    filesystem = guard.locator.filesystem
    given: dict[str, list[str]] = {}
    for word in filter(None, words):
        path = context.cwd / filesystem.expand(word)
        for found in filesystem.glob(path) or [path]:
            readings = _readings(found, guard)
            ticket = any(map(_is_ticket, readings))
            if not ticket and not any(map(_holds_tickets, readings)):
                continue
            label = ready_label(Context(found.parent), guard)
            held = statuses(filesystem.read_text(found) or "") if ticket else set[str]()
            if label in text and label not in held:
                given.setdefault(label, []).append(found.name if ticket else f"{found.name}/")
    return [
        Verdict(RULE, _detail(list(dict.fromkeys(names)), label)) for label, names in given.items()
    ]


def together(verdicts: list[Verdict]) -> list[Verdict]:
    """``verdicts`` with every agent-ready ask folded into one, which names all the call gives."""
    asks = [verdict.detail for verdict in verdicts if verdict.rule == RULE]
    if not asks:
        return verdicts
    rest = [verdict for verdict in verdicts if verdict.rule != RULE]
    return [*rest, Verdict(RULE, "; ".join(dict.fromkeys(asks)))]
