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
and with a glob, a brace expansion, a variable or a substitution in any part standing for
whatever it could spell. One call that writes every ticket of a spec is one ask, which names
each of them. Out of reach, as for every shell rule (ADR 0024): a script run by path, a file
copied into place, a label put together when the command runs.

The ask is a courtesy on the honest path, not the control. A command can still give a ticket
the label without one: copy a file that holds it into place (``cp notes/ready.md
.scratch/feat/issues/01.md``), or hard-link a ticket, write the label through the second name
and remove it. Catching a label the owner did not give is the merge judge's job, not this ask's.
"""

import posixpath
import re
from collections.abc import Iterable, Mapping
from pathlib import Path

from ratchet_guards import splits
from ratchet_guards.context import Context, Guard
from ratchet_guards.gh import ready_label
from ratchet_guards.shell import PLACEHOLDER
from ratchet_guards.verdict import Verdict

TRACKER_DIRECTORY = ".scratch"
TICKETS_DIRECTORY = "issues"
TICKET_SUFFIX = ".md"
# Why: a glob, a brace expansion, a variable or a substitution may spell any name there.
_GLOB = re.compile(rf"[*?[{{$`]|{re.escape(PLACEHOLDER)}")
RULE = "agent-ready-label"
STATUS_LINE = re.compile(
    r"^\*{0,2}status:?\*{0,2}:?\s*`?([^`\n]+?)`?\s*$", re.IGNORECASE | re.MULTILINE
)


def _readings(path: Path, guard: Guard) -> tuple[Path, ...]:
    """``path`` as written with ``..`` folded, and as the filesystem resolves it."""
    return (Path(posixpath.normpath(path)), guard.locator.filesystem.canonical(path))


def _part(part: str, name: str) -> bool:
    """Whether a path part may name ``name``: in any case, or as a pattern or expansion."""
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
    text = after(tool, tool_input, before)
    if label in statuses(text) and label not in statuses(before):
        if inherits(path, text, label, guard=guard) is None:
            return None
        return Verdict(RULE, _detail([path.name], label))
    return None


def _tracker_root(path: Path) -> Path | None:
    """The directory that holds the ``.scratch`` ``path`` is under."""
    folded = [part.casefold() for part in path.parts]
    return (
        Path(*path.parts[: folded.index(TRACKER_DIRECTORY)])
        if TRACKER_DIRECTORY in folded
        else None
    )


def inherits(path: Path, text: str, label: str, *, guard: Guard) -> str | None:
    """Why ``path``, holding ``text``, may not carry the label as a part of a split ticket.

    ``None`` when it may (ADR 0046): the ticket it names with ``Split from:`` carries the label
    at the default branch's tip, is no part itself, and ``text`` adds no promise of it nor repeats
    one a sibling carries. The parent is read from git, never from the child's own words.
    """
    root = _tracker_root(path)
    ref = splits.split_from(text)
    if root is None or ref is None:
        return "names no ticket it was split from"
    target = posixpath.normpath(str((root / ref) if "/" in ref else (path.parent / ref)))
    relative = posixpath.relpath(target, str(root))
    parent = None if relative.startswith("..") else guard.repo.default_file(root, relative)
    filesystem = guard.locator.filesystem
    siblings = [
        found
        for found in filesystem.glob(path.parent / f"*{TICKET_SUFFIX}")
        if found != path and splits.split_from(filesystem.read_text(found) or "") == ref
    ]
    return splits.refusal(
        text, parent, label, siblings=[filesystem.read_text(found) or "" for found in siblings]
    )


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
