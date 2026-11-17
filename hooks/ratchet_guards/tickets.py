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
_GLOB = re.compile(rf"[*?[{{$`]|{re.escape(PLACEHOLDER)}")
RULE = "agent-ready-label"
STATUS_LINE = re.compile(
    r"^\*{0,2}status:?\*{0,2}:?\s*`?([^`\n]+?)`?\s*$", re.IGNORECASE | re.MULTILINE
)


def _readings(path: Path, guard: Guard) -> tuple[Path, ...]:
    return (Path(posixpath.normpath(path)), guard.locator.filesystem.canonical(path))


def _part(part: str, name: str) -> bool:
    return part.casefold() == name or _GLOB.search(part) is not None


def _is_ticket(path: Path) -> bool:
    *_, tracker, _, issues, name = ("", "", "", *path.parts)
    return (
        _part(tracker, TRACKER_DIRECTORY)
        and _part(issues, TICKETS_DIRECTORY)
        and (name.casefold().endswith(TICKET_SUFFIX) or _GLOB.search(name) is not None)
    )


def _holds_tickets(path: Path) -> bool:
    *_, tracker, feature, last = ("", "", *path.parts)
    return (
        _part(last, TRACKER_DIRECTORY)
        or _part(feature, TRACKER_DIRECTORY)
        or (_part(tracker, TRACKER_DIRECTORY) and _part(last, TICKETS_DIRECTORY))
    )


def statuses(text: str) -> set[str]:
    return {value.strip() for value in STATUS_LINE.findall(text)}


def _replace(text: str, edit: Mapping[str, object]) -> str:
    old, new = str(edit.get("old_string", "")), str(edit.get("new_string", ""))
    return text.replace(old, new) if edit.get("replace_all") is True else text.replace(old, new, 1)


def after(tool: str, tool_input: Mapping[str, object], before: str) -> str:
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
    folded = [part.casefold() for part in path.parts]
    return (
        Path(*path.parts[: folded.index(TRACKER_DIRECTORY)])
        if TRACKER_DIRECTORY in folded
        else None
    )


def inherits(path: Path, text: str, label: str, *, guard: Guard) -> str | None:
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
    asks = [verdict.detail for verdict in verdicts if verdict.rule == RULE]
    if not asks:
        return verdicts
    rest = [verdict for verdict in verdicts if verdict.rule != RULE]
    return [*rest, Verdict(RULE, "; ".join(dict.fromkeys(asks)))]
