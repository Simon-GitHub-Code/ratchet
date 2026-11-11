"""What a change adds to a record, when adding is all it does (ADR 0014).

A guard group marked ``edit = "append"`` holds records an agent may add to and only the owner
changes: the glossary and the decision records. ``additions`` is the one judge of "is this
change an addition". The hook calls it on an Edit, Write or MultiEdit, with the file the call
would leave; ``guard-diff`` calls it on the work tree. Both compare against the merge base with
the default branch, read by ``before``, so they cannot disagree.

A glossary (``GLOSSARY.md``, in domain-modeling's format) changes additively when everything the
merge base holds is still there, in the same order and under the same heading: every term with
the lines that define it, every item of its ``_Avoid_`` list, every heading and every line
outside the entries. What may come in is a term, an ``_Avoid_`` item, and a heading of level
three or deeper. A decision record (``docs/adr/NNNN-<slug>.md``) is an addition when the file is
new, its number is above every number the merge base holds, it has a title, it carries no
``Relaxes:`` line (``scripts/lowered.py`` would read one as leave to loosen a ratchet) and it
does not speak of superseding.

Anything else raises ``NotAdditiveError`` with the reason, and so does anything this module
cannot read: a path that is neither kind of record, a line break other than a newline, an
indented term, ``_Avoid_`` line or heading, a term defined twice. The check reads shape, never
meaning: a new term can restate an old concept and a new record can contradict an old one. The
listing shows both to the owner; no gate catches either.
"""

import re
import unicodedata
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from enum import Enum
from pathlib import PurePosixPath

GLOSSARY = "GLOSSARY.md"
ADR_DIRECTORY = ("docs", "adr")
SIMULATED_TOOLS = frozenset({"Edit", "Write", "MultiEdit"})
ADDITIONS = "additions:"
NOT_ADDITIVE = "not additive:"
_ADR_NAME = re.compile(r"^([0-9]{4})-[a-z0-9]+(?:-[a-z0-9]+)*\.md$")
_LEADING_NUMBER = re.compile(r"^\d+")
_TITLE = re.compile(r"^# (\S.*)$", re.MULTILINE)
_HEADING = re.compile(r"^(#{1,6})\s+\S")
_TERM = re.compile(r"^\*\*([^*]+?)(?::\*\*|\*\*\s*:)\s*(.*)$")
_AVOID = re.compile(r"^_Avoid_:(.*)$")
_CLUSTER_LEVEL = 3
# Why: a fence or a comment opened in one new entry and closed in another hides the entries
# between them from a reader of the rendered page, while every line parser still reads them.
_HIDES = ("```", "~~~", "<!--", "-->")
# Why: `scripts/lowered.py` matches `Relaxes:` at the start of a line; refusing it anywhere,
# in any case, is a superset that no line-splitting rule can get around.
_RELAXES = "relaxes:"
_SUPERSEDES = ("supersed", "superced")

Git = Callable[[Sequence[str]], str | None]


class NotAdditiveError(ValueError):
    """The change does more than add to a record, or cannot be read; the message says what."""


@dataclass(frozen=True, slots=True)
class Before:
    """A record as the merge base has it: its text, and the file names in its directory."""

    text: str | None
    siblings: tuple[str, ...] = ()


class Kind(Enum):
    """What one outline item of a glossary is."""

    HEADING = "heading"
    LOOSE = "text outside the entries"
    ENTRY = "term"


@dataclass(frozen=True, slots=True)
class Item:
    """One heading, loose line or entry, with the heading it sits under."""

    kind: Kind
    text: str
    under: str


@dataclass(frozen=True, slots=True)
class Entry:
    """A term: the lines that define it (its own line first) and its ``_Avoid_`` items."""

    term: str
    lines: tuple[str, ...]
    avoid: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class Glossary:
    """A glossary as an outline in file order, and its entries by term."""

    outline: tuple[Item, ...]
    entries: Mapping[str, Entry]


@dataclass(slots=True)
class _Reader:
    """Builds a ``Glossary`` one line at a time."""

    outline: list[Item] = field(default_factory=list)
    entries: dict[str, Entry] = field(default_factory=dict)
    folded: set[str] = field(default_factory=set)
    under: str = ""
    term: str | None = None
    lines: list[str] = field(default_factory=list)
    avoid: list[str] = field(default_factory=list)

    def close(self) -> None:
        """End the open entry, if there is one."""
        if self.term is None:
            return
        while self.lines[-1] == "":
            self.lines.pop()
        self.entries[self.term] = Entry(self.term, tuple(self.lines), tuple(self.avoid))
        self.outline.append(Item(Kind.ENTRY, self.term, self.under))
        self.term = None

    def heading(self, line: str) -> None:
        """A heading ends the open entry and is what later items sit under."""
        self.close()
        self.outline.append(Item(Kind.HEADING, line, ""))
        self.under = line

    def open(self, term: str, line: str) -> None:
        """A term line ends the open entry and starts the next."""
        self.close()
        key = fold(term)
        if key in self.folded:
            msg = f"it defines {term} twice"
            raise NotAdditiveError(msg)
        self.folded.add(key)
        self.term, self.lines, self.avoid = term, [line], []

    def body(self, line: str) -> None:
        """A line that is neither heading nor term: part of the open entry, or loose."""
        avoid = _AVOID.match(line)
        if self.term is None:
            if line:
                self.outline.append(Item(Kind.LOOSE, line, self.under))
        elif avoid:
            self.avoid.extend(item for part in avoid.group(1).split(",") if (item := part.strip()))
        else:
            self.lines.append(line)


def fold(name: str) -> str:
    """A term or ``_Avoid_`` item as a reader takes it: case, spacing and format marks aside."""
    visible = "".join(c for c in name if unicodedata.category(c) != "Cf")
    return " ".join(unicodedata.normalize("NFKC", visible).casefold().strip("`*_ ").split())


def glossary(text: str) -> Glossary:
    """Parse a glossary; raise ``NotAdditiveError`` on what cannot be read without guessing."""
    reader = _Reader()
    for number, raw in enumerate(text.split("\n"), start=1):
        line = raw.rstrip()
        if len(line.splitlines()) > 1:
            msg = f"line {number} holds a line break that is not a newline"
            raise NotAdditiveError(msg)
        flush = line.lstrip()
        heading, term = _HEADING.match(flush), _TERM.match(flush)
        if flush != line and (heading or term or _AVOID.match(flush)):
            msg = f"line {number} is an indented heading, term or _Avoid_ line"
            raise NotAdditiveError(msg)
        if term:
            reader.open(term.group(1).strip(), line)
        elif heading:
            reader.heading(line)
        else:
            reader.body(line)
    reader.close()
    return Glossary(tuple(reader.outline), reader.entries)


def _added(before: Sequence[Item], after: Sequence[Item]) -> list[Item]:
    """What ``after`` holds beyond ``before``; raise unless ``before`` runs through it in order."""
    added: list[Item] = []
    kept = 0
    for item in after:
        if kept < len(before) and item == before[kept]:
            kept += 1
        else:
            added.append(item)
    if kept < len(before):
        gone = before[kept]
        msg = f"the {gone.kind.value} {gone.text!r} is gone, changed or moved"
        raise NotAdditiveError(msg)
    return added


def _hidden(lines: Iterable[str]) -> bool:
    return any(mark in line for line in lines for mark in _HIDES)


def _new_item(item: Item, new: Glossary) -> str | None:
    """What an added item adds, ``None`` for a cluster heading; raise if it may not come in."""
    if item.kind is Kind.LOOSE:
        msg = f"it adds text outside any term's entry: {item.text!r}"
        raise NotAdditiveError(msg)
    if item.kind is Kind.HEADING:
        match = _HEADING.match(item.text)
        if match is None or len(match.group(1)) < _CLUSTER_LEVEL or _hidden([item.text]):
            msg = (
                f"it adds the heading {item.text!r}, which is not a cluster heading (### or deeper)"
            )
            raise NotAdditiveError(msg)
        return None
    entry = new.entries[item.text]
    if _hidden((*entry.lines, *entry.avoid)):
        msg = f"the new term {entry.term} holds a code fence or an HTML comment"
        raise NotAdditiveError(msg)
    for other in new.entries.values():
        if fold(entry.term) in map(fold, other.avoid):
            msg = f"it adds the term {entry.term}, which {other.term} lists under _Avoid_"
            raise NotAdditiveError(msg)
    _no_term_avoided(entry.term, entry.avoid, new)
    avoid = f" (avoid: {', '.join(entry.avoid)})" if entry.avoid else ""
    return f"term {entry.term}{avoid}"


def _no_term_avoided(term: str, items: Iterable[str], new: Glossary) -> None:
    """Raise when a new ``_Avoid_`` item bans a term the glossary defines."""
    terms = {fold(name): name for name in new.entries}
    for item in items:
        if fold(item) in terms:
            msg = f"it puts {item} under _Avoid_ for {term}, and {terms[fold(item)]} is a term"
            raise NotAdditiveError(msg)


def _kept_entry(was: Entry, new: Glossary) -> str | None:
    """The ``_Avoid_`` items an existing term gained; raise if its lines or its list changed."""
    now = new.entries[was.term]
    if now.lines != was.lines:
        msg = f"it changes the definition of {was.term}"
        raise NotAdditiveError(msg)
    if dropped := [item for item in was.avoid if item not in now.avoid]:
        msg = f"it drops {', '.join(dropped)} from the _Avoid_ list of {was.term}"
        raise NotAdditiveError(msg)
    gained = [item for item in now.avoid if item not in was.avoid]
    if _hidden(gained):
        msg = f"a new _Avoid_ item of {was.term} holds a code fence or an HTML comment"
        raise NotAdditiveError(msg)
    _no_term_avoided(was.term, gained, new)
    return f"avoid {', '.join(gained)} (under {was.term})" if gained else None


def _glossary_additions(before: str | None, after: str) -> list[str]:
    if before is None:
        msg = "it creates a glossary, and a new vocabulary is the owner's"
        raise NotAdditiveError(msg)
    try:
        old = glossary(before)
    except NotAdditiveError as error:
        msg = f"the merge base's copy cannot be read: {error}"
        raise NotAdditiveError(msg) from error
    new = glossary(after)
    found = [_new_item(item, new) for item in _added(old.outline, new.outline)]
    found += [_kept_entry(was, new) for was in old.entries.values()]
    return [addition for addition in found if addition is not None]


def _adr_additions(name: str, before: Before, after: str) -> list[str]:
    if before.text is not None:
        msg = "it changes a decision record the default branch already holds"
        raise NotAdditiveError(msg)
    match = _ADR_NAME.match(name)
    if match is None:
        msg = f"{name} is not named NNNN-<slug>.md"
        raise NotAdditiveError(msg)
    number = int(match.group(1))
    numbers = (int(m.group()) for s in before.siblings if (m := _LEADING_NUMBER.match(s)))
    highest = max(numbers, default=0)
    if number <= highest:
        msg = f"its number {number:04d} is not above {highest:04d}, the highest at the merge base"
        raise NotAdditiveError(msg)
    text = after.casefold()
    if _RELAXES in text:
        msg = "it carries a Relaxes: line, and relaxing a ratchet is the owner's"
        raise NotAdditiveError(msg)
    if any(word in text for word in _SUPERSEDES):
        msg = "it speaks of superseding, and superseding a decision record is the owner's"
        raise NotAdditiveError(msg)
    title = _TITLE.search(after)
    if title is None:
        msg = "it has no '# ' title"
        raise NotAdditiveError(msg)
    return [f"ADR {number:04d} {title.group(1).strip()}"]


def additions(path: str, before: Before, after: str | None) -> list[str]:
    """What the change of ``path`` adds, when adding is all it does.

    ``before`` is the merge base's copy and ``after`` the text the change leaves (``None`` for a
    deleted file). Raises ``NotAdditiveError`` with the reason otherwise.
    """
    record = PurePosixPath(path)
    if after is None:
        msg = "it is deleted"
        raise NotAdditiveError(msg)
    if record.name == GLOSSARY:
        return _glossary_additions(before.text, after)
    if record.parent.parts[-2:] == ADR_DIRECTORY:
        return _adr_additions(record.name, before, after)
    msg = f"{record.name} is neither a GLOSSARY.md nor a record under docs/adr/"
    raise NotAdditiveError(msg)


def note(path: str, before: Before | None, after: str | None) -> str:
    """The line ``guard-diff`` prints under a record: ``additions:`` or ``not additive:``."""
    if before is None:
        return f"{NOT_ADDITIVE} the merge base cannot be read"
    try:
        found = additions(path, before, after)
    except NotAdditiveError as reason:
        return f"{NOT_ADDITIVE} {reason}"
    return f"{ADDITIONS} " + ("; ".join(found) or "none")


def before(git: Git, merge_base: str, path: str) -> Before | None:
    """``path`` as ``merge_base`` has it, or ``None`` when git cannot read that commit.

    ``git`` runs one git command in the checkout and returns its stdout unstripped.
    """
    directory = PurePosixPath(path).parent.as_posix()
    where = [] if directory == "." else [f"{directory}/"]
    listing = git(["ls-tree", "--full-tree", "-z", "--name-only", merge_base, *where])
    if listing is None:
        return None
    names = tuple(PurePosixPath(name).name for name in listing.split("\0") if name)
    if PurePosixPath(path).name not in names:
        return Before(None, names)
    text = git(["show", f"{merge_base}:{path}"])
    return None if text is None else Before(text, names)


def _replaced(text: str, edit: object) -> str | None:
    """``text`` after one Edit as Claude Code applies it, or ``None`` when it would not apply."""
    if not isinstance(edit, Mapping):
        return None
    old, new, every = edit.get("old_string"), edit.get("new_string"), edit.get("replace_all", False)
    if not isinstance(old, str) or not isinstance(new, str) or not isinstance(every, bool):
        return None
    if not old:
        return None if text else new
    if old not in text or (not every and text.count(old) > 1):
        return None
    return text.replace(old, new)


def written(tool: str, tool_input: Mapping[str, object], existing: str | None) -> str | None:
    """The text the file holds once the call has run, or ``None`` when that cannot be told.

    Only Claude Code's Edit, Write and MultiEdit are simulated, and only from an input of the
    documented shape: any other tool, key or type is unknown, and unknown asks.
    """
    if tool not in SIMULATED_TOOLS:
        return None
    if tool == "Write":
        content = tool_input.get("content")
        return content if isinstance(content, str) else None
    edits = [tool_input] if tool == "Edit" else tool_input.get("edits")
    if not isinstance(edits, list) or not edits:
        return None
    text: str | None = existing or ""
    for edit in edits:
        text = None if text is None else _replaced(text, edit)
    return text
