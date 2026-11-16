#!/usr/bin/env python3
"""The quote check: does a finding's quote appear, word for word, in the record it names? ADR 0072.

A blocker or a major finding carries ``breaks``: the record it breaks and the promise, criterion,
non-negotiable or rule it quotes. The finding is owed a fix only when the record holds the quote.
A role runs this over its own findings and returns what it printed; ``publish_check.py`` runs the
same check over the run's result.

The findings are a JSON list, or an object whose ``findings`` member is that list. Each finding is
an object; ``severity`` is ``blocker``, ``major`` or ``minor``; ``title`` is optional; ``breaks``
is ``{"record": "<name>", "quote": "<text>"}``. A blocker or major with no ``breaks`` has no
quote and is not found. A minor is checked only when it carries ``breaks``.

The records, by name:

- ``AGENTS.md``, ``GLOSSARY.md``, ``DESIGN.md``, ``guards/manifest.toml``: the file of that name
  at the root;
- ``binding``, or ``docs/process/binding.md``: the binding;
- ``docs/adr/``: a decision record, any file under that directory; a path to one file under it
  names that file alone;
- ``docs/features/<slug>/spec.md``, or ``spec.md`` given with ``--record``;
- ``ticket``, given with ``--record ticket=PATH``: the ticket has no fixed place.

``--record NAME=PATH`` gives the file of a name, relative to the root, and replaces the default. A
name that is not one of these is not a record of the ADR's list and nothing in it is found. A
record whose file is absent, or whose directory holds no text file, holds no quote.

How a quote matches: the quote and each file of the record are cut into words, a word being a run
of characters other than white space. Words are compared whole and as written: case, punctuation,
markup and hyphenation, so ``ADR`` does not match ``adr`` and a quote that drops a backtick does
not match. Only white space is read loosely: any run of spaces, tabs and line breaks equals any
other run, so a quote may join the lines of a wrapped block. A quote is found in a file when its
words equal one of these, and nothing shorter:

- a block of a markdown file: a heading, with its ``#`` marker set aside; a list item with its
  continuation lines joined, its marker and checkbox set aside; or a paragraph, all its lines
  joined. A paragraph is the lines up to a blank line, a heading, a list item's start or a table
  row. A ``>`` that starts a line is set aside. A line inside a wrapped block is not a block;
- a table cell of a markdown file: a line that starts with ``|`` is split on each ``|`` not
  escaped by a backslash, and each cell is a unit;
- a line of a file that is not markdown (a ``.toml`` file has no paragraphs);
- whole words inside one paragraph or list item, from a sentence start to a sentence end. A
  sentence starts at the paragraph's first word, or at the word after one that ends in ``.``,
  ``!`` or ``?``, with an optional closing quote or bracket after it. A sentence ends at the
  paragraph's last word, or at a word that ends so. An abbreviation such as ``e.g.`` only adds a
  place a quote may start or end.

A quote with no letter or digit is never found, so no rule, table separator or marker is a quote.
A quote does not span two files. It cannot tell that the quoted sentence is the one the finding
breaks: that judgement is the review's and the owner's, with the quote in front of them on the
pull request.

Prints JSON: ``found``, the checked findings whose quote the record holds, and ``not_found``, the
others with a ``reason``. Each entry names the finding by its ``index`` in the list and its
``title``, with its ``record`` and ``quote``.

Exit status: 0 when every checked finding was found, 1 when one was not, 2 when the findings or a
``--record`` cannot be read. Neither 1 nor 2 is a pass.

Usage: ``quotes.py FINDINGS [--root DIR] [--record NAME=PATH]...``.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import NamedTuple, TypedDict

import gatelib

FIXED = {
    "AGENTS.md": "AGENTS.md",
    "GLOSSARY.md": "GLOSSARY.md",
    "DESIGN.md": "DESIGN.md",
    "guards/manifest.toml": "guards/manifest.toml",
    "binding": "docs/process/binding.md",
    "docs/process/binding.md": "docs/process/binding.md",
    "docs/adr": "docs/adr",
    "docs/adr/": "docs/adr",
}
ADR_FILES = "docs/adr/"
SPEC_PATH = re.compile(r"docs/features/[^/]+/spec\.md")
BLOCKING = frozenset({"blocker", "major"})
QUOTER = re.compile(r"^(?:>\s*)+")
HEADING = re.compile(r"^#{1,6}(?:\s+|$)")
LIST_ITEM = re.compile(r"^(?:[-*+]|\d+[.)])(?:\s+|$)")
CHECKBOX = re.compile(r"^\[[ xX]\]\s+")
CELL_BREAK = re.compile(r"(?<!\\)\|")
SENTENCE_END = re.compile(r"""[.!?]["'\u2019\u201d)\]]*$""")
NOT_MARKDOWN = ".toml"


class Document(NamedTuple):
    """A file of a record: its text, and whether it is markdown."""

    text: str
    markdown: bool


Records = dict[str, list[Document] | None]


class Entry(TypedDict):
    """A finding named by its place in the list, with the record and quote it carries."""

    index: int
    title: str
    record: str
    quote: str


class Missed(Entry):
    """A finding whose quote was not found, and why."""

    reason: str


class Verdict(TypedDict):
    """The findings whose quotes were found and those whose quotes were not."""

    found: list[Entry]
    not_found: list[Missed]


class UnreadableError(ValueError):
    """The findings, or a given record, cannot be read."""


def blocks(document: Document) -> tuple[list[list[str]], list[list[str]]]:
    """The whole units of a document, and its paragraphs, each as its words.

    A markdown document's units are its headings, its list items and paragraphs with their
    lines joined, and its table cells. Its paragraphs are the list items and paragraphs. A
    document that is not markdown has each line as a unit and no paragraphs.
    """
    if not document.markdown:
        lines = (line.split() for line in document.text.splitlines())
        return [words for words in lines if words], []
    units: list[list[str]] = []
    paragraphs: list[list[str]] = []
    current: list[str] = []

    def flush() -> None:
        if current:
            units.append(current.copy())
            paragraphs.append(current.copy())
            current.clear()

    for line in document.text.splitlines():
        text = QUOTER.sub("", line.strip()).strip()
        item = LIST_ITEM.match(text)
        heading = HEADING.match(text)
        if not text or text.startswith("|") or heading or item:
            flush()
        if text.startswith("|"):
            units.extend(words for cell in CELL_BREAK.split(text) if (words := cell.split()))
        elif heading:
            units.extend(rest for rest in [text[heading.end() :].split()] if rest)
        elif item:
            current.extend(CHECKBOX.sub("", text[item.end() :]).split())
        else:
            current.extend(text.split())
    flush()
    return units, paragraphs


def sentence(paragraph: list[str], wanted: list[str]) -> bool:
    """Whether ``wanted`` is whole words of ``paragraph``, from a sentence start to an end."""
    size = len(wanted)
    return any(
        paragraph[start : start + size] == wanted
        and (start == 0 or SENTENCE_END.search(paragraph[start - 1]) is not None)
        and (start + size == len(paragraph) or SENTENCE_END.search(wanted[-1]) is not None)
        for start in range(len(paragraph) - size + 1)
    )


def holds(document: Document, quote: str) -> bool:
    """Whether ``document`` holds ``quote`` as a whole unit or whole sentences of a paragraph."""
    wanted = quote.split()
    if not any(character.isalnum() for character in quote):
        return False
    units, paragraphs = blocks(document)
    return wanted in units or any(sentence(paragraph, wanted) for paragraph in paragraphs)


def document_of(path: Path) -> Document | None:
    text = gatelib.read_text(path)
    return None if text is None else Document(text, path.suffix != NOT_MARKDOWN)


def files_of(root: Path, relative: str) -> list[Document] | None:
    """The documents of the file at ``relative``, or under it; ``None`` when it is absent."""
    path = root / relative
    if path.is_dir():
        entries = (document_of(entry) for entry in sorted(path.rglob("*")) if entry.is_file())
        return [entry for entry in entries if entry is not None]
    document = document_of(path)
    if document is not None:
        return [document]
    return [] if path.is_file() else None


def place_of(name: str) -> str | None:
    """Where the record ``name`` is under the root, or ``None`` when it is not on the ADR's list."""
    if name in FIXED:
        return FIXED[name]
    inside = name.startswith(ADR_FILES) and ".." not in Path(name).parts
    return name if inside or SPEC_PATH.fullmatch(name) else None


def texts_of(name: str, root: Path, given: Records) -> list[Document] | str | None:
    """The texts of the record ``name``; ``None`` when absent; a reason when it is no record."""
    if name in given:
        return given[name]
    place = place_of(name)
    if place is None:
        return f"`{name}` is not a record the ADR lists"
    return files_of(root, place)


def named(index: int, finding: dict[str, object]) -> Entry:
    breaks = finding.get("breaks")
    record = breaks.get("record") if isinstance(breaks, dict) else None
    quote = breaks.get("quote") if isinstance(breaks, dict) else None
    return Entry(
        index=index,
        title=str(finding.get("title", "")),
        record=record if isinstance(record, str) else "",
        quote=quote if isinstance(quote, str) else "",
    )


def reason_missed(
    entry: Entry, finding: dict[str, object], *, root: Path, given: Records
) -> str | None:
    """Why the record does not hold the finding's quote; ``None`` when it does."""
    breaks = finding.get("breaks")
    if not (
        isinstance(breaks, dict)
        and isinstance(breaks.get("record"), str)
        and isinstance(breaks.get("quote"), str)
    ):
        return "it carries no `breaks` with a `record` and a `quote`, both text"
    texts = texts_of(entry["record"], root, given)
    if isinstance(texts, str):
        return texts
    if texts is None:
        return f"the record `{entry['record']}` is absent"
    if not any(holds(document, entry["quote"]) for document in texts):
        return f"the record `{entry['record']}` does not hold the quote"
    return None


def check(findings: list[object], root: Path, given: Records | None = None) -> Verdict:
    """Which checked findings the records they name hold word for word, and which they do not.

    ``given`` supplies the texts of records that have no fixed place, by name, ``None`` for one
    that is absent.
    """
    found: list[Entry] = []
    not_found: list[Missed] = []
    for index, finding in enumerate(findings):
        if not isinstance(finding, dict):
            not_found.append(Missed(**named(index, {}), reason="it is not an object"))
        elif finding.get("severity") in BLOCKING or "breaks" in finding:
            entry = named(index, finding)
            reason = reason_missed(entry, finding, root=root, given=given or {})
            if reason is None:
                found.append(entry)
            else:
                not_found.append(Missed(**entry, reason=reason))
    return Verdict(found=found, not_found=not_found)


def findings_in(path: Path) -> list[object]:
    """The findings in the file at ``path``; raises ``UnreadableError`` when it holds none."""
    text = gatelib.read_text(path)
    if text is None:
        message = "there is no such file, or it is not text"
        raise UnreadableError(message)
    try:
        data: object = json.loads(text)
    except ValueError as error:
        message = f"it is not JSON ({error})"
        raise UnreadableError(message) from None
    if isinstance(data, dict):
        data = data.get("findings")
    if not isinstance(data, list):
        message = "it is neither a list of findings nor an object with a `findings` list"
        raise UnreadableError(message)
    return data


def given_records(root: Path, pairs: list[str]) -> Records:
    """The texts of ``NAME=PATH`` pairs; a path with no file gives ``None``."""
    given: Records = {}
    for pair in pairs:
        name, separator, relative = pair.partition("=")
        if not name or not separator or not relative:
            message = f"`--record {pair}` is not NAME=PATH"
            raise UnreadableError(message)
        given[name] = files_of(root, relative)
    return given


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="quotes.py", description=__doc__.splitlines()[0])
    parser.add_argument("findings", type=Path, help="a JSON list of findings, each with `breaks`")
    parser.add_argument("--root", default=".")
    parser.add_argument("--record", action="append", default=[], metavar="NAME=PATH")
    options = parser.parse_args(argv[1:])
    root = Path(options.root).resolve()
    try:
        verdict = check(findings_in(options.findings), root, given_records(root, options.record))
    except UnreadableError as error:
        print(f"quote check: {options.findings} cannot be read: {error}", file=sys.stderr)
        return 2
    print(json.dumps(verdict, indent=2))
    return 1 if verdict["not_found"] else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
