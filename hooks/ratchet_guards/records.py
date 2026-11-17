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
_HIDES = ("```", "~~~", "<!--", "-->")
_TAG = re.compile(r"<[A-Za-z/!?]")
_UNDERLINE = re.compile(r"^\s*(?:=+|-+)\s*$")
_TERMLIKE = re.compile(r"^\s*[*_]{1,3}[^*_]+?(?:[*_]{1,3}\s*:|:\s*[*_]{1,3})")
_SCRIPTS = ("LATIN", "CYRILLIC", "GREEK")
_LOOKALIKES = str.maketrans(
    "АВЕЅІЈКМНОРСТУХԚԜаеѕіјкорсухԁԛԝһӏΑΒΕΖΗΙΚΜΝΟΡΤΥΧαικνορυϲϳ",
    "ABESIJKMHOPCTYXQWaesijkopcyxdqwhlABEZHIKMNOPTYXaikvopucj",
)
_RELAXES = "relaxes:"
_SUPERSEDES = ("supersed", "superced")

Git = Callable[[Sequence[str]], str | None]


class NotAdditiveError(ValueError): ...


@dataclass(frozen=True, slots=True)
class Before:
    text: str | None
    siblings: tuple[str, ...] = ()


class Kind(Enum):
    HEADING = "heading"
    LOOSE = "text outside the entries"
    ENTRY = "term"


@dataclass(frozen=True, slots=True)
class Item:
    kind: Kind
    text: str
    under: str


@dataclass(frozen=True, slots=True)
class Entry:
    term: str
    lines: tuple[str, ...]
    avoid: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class Glossary:
    outline: tuple[Item, ...]
    entries: Mapping[str, Entry]


@dataclass(slots=True)
class _Reader:
    outline: list[Item] = field(default_factory=list)
    entries: dict[str, Entry] = field(default_factory=dict)
    folded: set[str] = field(default_factory=set)
    under: str = ""
    term: str | None = None
    lines: list[str] = field(default_factory=list)
    avoid: list[str] = field(default_factory=list)

    def close(self) -> None:
        if self.term is None:
            return
        while self.lines[-1] == "":
            self.lines.pop()
        self.entries[self.term] = Entry(self.term, tuple(self.lines), tuple(self.avoid))
        self.outline.append(Item(Kind.ENTRY, self.term, self.under))
        self.term = None

    def heading(self, line: str) -> None:
        self.close()
        self.outline.append(Item(Kind.HEADING, line, ""))
        self.under = line

    def open(self, term: str, line: str) -> None:
        self.close()
        key = fold(term)
        if key in self.folded:
            msg = f"it defines {term} twice"
            raise NotAdditiveError(msg)
        self.folded.add(key)
        self.term, self.lines, self.avoid = term, [line], []

    def body(self, line: str) -> None:
        avoid = _AVOID.match(line)
        if self.term is None:
            if line:
                self.outline.append(Item(Kind.LOOSE, line, self.under))
        elif avoid:
            self.avoid.extend(item for part in avoid.group(1).split(",") if (item := part.strip()))
        else:
            self.lines.append(line)


def fold(name: str) -> str:
    visible = "".join(c for c in name if unicodedata.category(c) != "Cf")
    return " ".join(unicodedata.normalize("NFKC", visible).casefold().strip("`*_ ").split())


def skeleton(name: str) -> str:
    apart = unicodedata.normalize("NFKD", name)
    bare = "".join(c for c in apart if not unicodedata.combining(c))
    return fold(bare.translate(_LOOKALIKES))


def _scripts(name: str) -> list[str]:
    letters = [unicodedata.name(c, "") for c in unicodedata.normalize("NFKC", name) if c.isalpha()]
    return [script for script in _SCRIPTS if any(script in letter.split() for letter in letters)]


def glossary(text: str) -> Glossary:
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


def _hidden(lines: Iterable[str]) -> str | None:
    for line in lines:
        if any(mark in line for mark in _HIDES):
            return "a code fence or an HTML comment"
        if _TAG.search(line):
            return "raw HTML"
    return None


def _misread(lines: Iterable[str]) -> str | None:
    for line in lines:
        if _UNDERLINE.match(line):
            return f"the underline of a heading: {line!r}"
        if _TERMLIKE.match(line):
            return f"a line that reads as a term of its own: {line!r}"
    return None


def _new_term(entry: Entry, new: Glossary) -> None:
    scripts = _scripts(entry.term)
    if len(scripts) > 1:
        mixed = " and ".join(script.title() for script in scripts)
        msg = f"the new term {entry.term} mixes {mixed} letters"
        raise NotAdditiveError(msg)
    what = _hidden((*entry.lines, *entry.avoid)) or _misread(entry.lines[1:])
    if what is not None:
        msg = f"the new term {entry.term} holds {what}"
        raise NotAdditiveError(msg)
    for other in new.entries.values():
        if other.term != entry.term and skeleton(other.term) == skeleton(entry.term):
            msg = f"it adds the term {entry.term}, which reads as the term {other.term}"
            raise NotAdditiveError(msg)
        if skeleton(entry.term) in map(skeleton, other.avoid):
            msg = f"it adds the term {entry.term}, which {other.term} lists under _Avoid_"
            raise NotAdditiveError(msg)


def _new_item(item: Item, new: Glossary) -> str | None:
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
    _new_term(entry, new)
    _no_term_avoided(entry.term, entry.avoid, new)
    avoid = f" (avoid: {', '.join(entry.avoid)})" if entry.avoid else ""
    return f"term {entry.term}{avoid}"


def _no_term_avoided(term: str, items: Iterable[str], new: Glossary) -> None:
    terms = {skeleton(name): name for name in new.entries}
    for item in items:
        if skeleton(item) in terms:
            msg = f"it puts {item} under _Avoid_ for {term}, and {terms[skeleton(item)]} is a term"
            raise NotAdditiveError(msg)


def _kept_entry(was: Entry, new: Glossary) -> str | None:
    now = new.entries[was.term]
    if now.lines != was.lines:
        msg = f"it changes the definition of {was.term}"
        raise NotAdditiveError(msg)
    if dropped := [item for item in was.avoid if item not in now.avoid]:
        msg = f"it drops {', '.join(dropped)} from the _Avoid_ list of {was.term}"
        raise NotAdditiveError(msg)
    gained = [item for item in now.avoid if item not in was.avoid]
    what = _hidden(gained)
    if what is not None:
        msg = f"a new _Avoid_ item of {was.term} holds {what}"
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
    if before is None:
        return f"{NOT_ADDITIVE} the merge base cannot be read"
    try:
        found = additions(path, before, after)
    except NotAdditiveError as reason:
        return f"{NOT_ADDITIVE} {reason}"
    return f"{ADDITIONS} " + ("; ".join(found) or "none")


def before(git: Git, merge_base: str, path: str) -> Before | None:
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
