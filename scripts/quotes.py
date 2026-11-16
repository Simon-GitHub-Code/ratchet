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
of characters other than white space, and rejoined with one space. A quote is found when its text
is then a stretch of a file's text. Everything but white space is compared as written: case,
punctuation, markup and hyphenation, so a quote with ``ADR`` does not match ``adr`` and one that
drops a backtick does not match. Only white space is read loosely: any run of spaces, tabs and
line breaks equals any other run, so a quote may join a line the record wrapped. A quote with no
word is never found. A quote does not span two files.

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
from typing import TypedDict

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
WORDS = re.compile(r"\S+")

Records = dict[str, list[str] | None]


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


def words(text: str) -> str:
    return " ".join(WORDS.findall(text))


def holds(text: str, quote: str) -> bool:
    """Whether ``text`` holds ``quote`` word for word, white space aside."""
    wanted = words(quote)
    return bool(wanted) and wanted in words(text)


def files_of(root: Path, relative: str) -> list[str] | None:
    """The text of the file at ``relative``, or of each text file under it; ``None`` when absent."""
    path = root / relative
    if path.is_dir():
        texts = (gatelib.read_text(entry) for entry in sorted(path.rglob("*")) if entry.is_file())
        return [text for text in texts if text is not None]
    text = gatelib.read_text(path)
    if text is not None:
        return [text]
    return [] if path.is_file() else None


def place_of(name: str) -> str | None:
    """Where the record ``name`` is under the root, or ``None`` when it is not on the ADR's list."""
    if name in FIXED:
        return FIXED[name]
    inside = name.startswith(ADR_FILES) and ".." not in Path(name).parts
    return name if inside or SPEC_PATH.fullmatch(name) else None


def texts_of(name: str, root: Path, given: Records) -> list[str] | str | None:
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
    if not any(holds(text, entry["quote"]) for text in texts):
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
