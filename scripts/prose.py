#!/usr/bin/env python3
"""The prose gate: the glossary's ``_Avoid_`` words stay out of the documents and comments.

Reads every ``_Avoid_:`` line of ``GLOSSARY.md`` (the format is domain-modeling's: a bold term,
a definition, then the synonyms to avoid) and scans the tracked documents (``.md``, ``.txt``,
``.rst``, ``.adoc``) and the comment lines of source files for any of those words as a whole
word, case-insensitively. The check cannot see meaning, so the whole word is banned; a line that
must use one carries ``glossary: allow`` and the reason, which is a record a review can read.
The glossary files themselves are not scanned.

Like the markers gate, it judges the change: a word fails a file only when its uses there rose
against the merge base, read with today's glossary. So putting a word on an Avoid list never
turns the records written before it red, and every new use of it is caught. Without a base
commit every use counts.

Usage: ``prose.py [--root DIR]``.
"""

from __future__ import annotations

import argparse
import re
import sys
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

import gatelib

GLOSSARY = "GLOSSARY.md"
TERM = re.compile(r"^\*\*(.+?)\*\*:\s*$")
AVOID = re.compile(r"^_Avoid_:\s*(.+?)\s*$")
ALLOW = "glossary: allow"
DOCUMENTS = (".md", ".txt", ".rst", ".adoc")
COMMENT = re.compile(r"(?:^|\s)(?:#|//|/\*|\*|--|<!--)\s*(.*)$")
SOURCE = (
    ".py", ".pyi", ".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs", ".go", ".rs", ".java", ".kt",
    ".swift", ".c", ".h", ".cc", ".cpp", ".hpp", ".cs", ".rb", ".php", ".sh", ".bash", ".sql",
    ".toml", ".yml", ".yaml", ".just", "justfile", "Dockerfile",
)  # fmt: skip


def avoided(glossary: str) -> dict[str, str]:
    """Each word to avoid (lower-cased) mapped to the term it stands in for."""
    words: dict[str, str] = {}
    term = "?"
    for line in glossary.splitlines():
        if match := TERM.match(line.strip()):
            term = match.group(1)
        elif match := AVOID.match(line.strip()):
            for word in match.group(1).split(","):
                cleaned = word.strip().strip("`*_").lower()
                if cleaned and not cleaned.startswith("<"):
                    words.setdefault(cleaned, term)
    return words


def pattern(words: dict[str, str]) -> re.Pattern[str] | None:
    if not words:
        return None
    alternatives = "|".join(re.escape(word) for word in sorted(words, key=len, reverse=True))
    return re.compile(rf"(?<![\w-])({alternatives})(?![\w-])", re.IGNORECASE)


def is_document(path: str) -> bool:
    return path.endswith(DOCUMENTS) and not path.rsplit("/", 1)[-1].startswith("GLOSSARY")


def is_source(path: str) -> bool:
    name = path.rsplit("/", 1)[-1]
    return name.endswith(SOURCE) and not name.startswith("GLOSSARY")


@dataclass(frozen=True, slots=True)
class Hit:
    """One use of an avoided word: where, as written, and the term it stands in for."""

    path: str
    line: int
    word: str
    term: str

    def __str__(self) -> str:
        """``path:line: 'word' is on the Avoid list of 'Term'``."""
        return f"{self.path}:{self.line}: '{self.word}' is on the Avoid list of '{self.term}'"


def scan(path: str, text: str, *, found: re.Pattern[str], words: dict[str, str]) -> list[Hit]:
    """Each use of an avoided word in the file."""
    document = is_document(path)
    hits = []
    for number, line in enumerate(text.splitlines(), 1):
        if ALLOW in line:
            continue
        comment = COMMENT.search(line)
        subject = line if document else (comment.group(1) if comment else "")
        hits += [
            Hit(path, number, match.group(1), words[match.group(1).lower()])
            for match in found.finditer(subject)
        ]
    return hits


def renames(root: Path, base: str) -> dict[str, str]:
    """Each path renamed since ``base`` mapped to the path it had there."""
    out = gatelib.git(root, "diff", "-M", "--name-status", "-z", base) or ""
    fields = out.split("\0")
    moved: dict[str, str] = {}
    index = 0
    while index < len(fields):
        status = fields[index]
        if status.startswith("R") and index + 2 < len(fields):
            moved[fields[index + 2]] = fields[index + 1]
            index += 3
        else:
            index += 2
    return moved


def problems(root: Path, base: str | None = None) -> list[str]:
    glossary = gatelib.read_text(root / GLOSSARY)
    if glossary is None:
        return [f"{GLOSSARY}: missing"]
    words = avoided(glossary)
    found = pattern(words)
    if found is None:
        return []
    moved = renames(root, base) if base is not None else {}
    hits: list[Hit] = []
    for path in gatelib.tracked_paths(root):
        if not (is_document(path) or is_source(path)):
            continue
        text = gatelib.read_text(root / path)
        if text is None:
            continue
        now = scan(path, text, found=found, words=words)
        old_path = moved.get(path, path)
        before = gatelib.show(root, base, old_path) if base is not None and now else None
        earlier = Counter(
            hit.word.lower() for hit in scan(old_path, before or "", found=found, words=words)
        )
        current = Counter(hit.word.lower() for hit in now)
        hits += [hit for hit in now if current[hit.word.lower()] > earlier[hit.word.lower()]]
    return [str(hit) for hit in hits]


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", default=".")
    options = parser.parse_args(argv[1:])
    root = Path(options.root).resolve()
    return gatelib.report("prose", problems(root, gatelib.base_commit(root)))


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
