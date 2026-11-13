"""The transcript audit: did the acceptance tester look inside the product it was trying?

The tester is meant to judge by use alone, so a verdict is worth nothing if it rests on reading
the code. This reads the tester's transcript and names every tool call that touched a file under
the product's directory, other than a session's ``tester/`` directory, where the tester writes
its record. The request log, the stored answers and the seal beside it are the harness's, and a
tester that names them is flagged as surely as one that reads the code.

The input is a Claude Code session transcript: JSON Lines, one object per line, as the Workflow
runtime and the Agent tool keep them for each agent. A tool call is a ``tool_use`` block in an
assistant message's ``content``; each line carries the ``cwd`` the call ran in. A call is
flagged when any text in its input holds an absolute path, or a ``file://`` URL, under the
product; when a Bash command names a relative path that exists under the product, resolved from
its working directory (a leading ``cd`` included); or when a Grep or Glob with no path searches a
working directory inside the product.

What it cannot see: a program that builds a path at run time (``python3 -c`` joining parts, a
shell variable, an encoded string) and then reads it; a script the tester wrote earlier that
reads files when run; anything another agent or process read for it; a copy of the code fetched
from elsewhere (a forge, a package registry); and code the product itself serves to a browser,
which is part of using it. It audits the transcript it is given, and a transcript that was
trimmed or compacted shows only what is left.
"""

from __future__ import annotations

import glob
import json
import os
import re
import shlex
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Iterable, Iterator

ABSOLUTE = re.compile(r"(?:file://)?(/[^\s'\"`;|&<>(){}*?\[\]$,]+)")
LEADING_CD = re.compile(r"^\s*cd\s+(\S+)\s*(?:&&|;)")
SEARCHES = ("Grep", "Glob")
TESTER_DEPTH = 4


@dataclass(frozen=True)
class Product:
    """Where the product lives, and the evidence store whose ``tester`` directories it may touch."""

    roots: tuple[Path, ...]
    store: Path

    def inside(self, path: Path) -> bool:
        """Whether ``path`` is strictly under the product and outside every tester directory.

        A session is ``<store>/<slug>/<head>/<session>/``; its ``tester/`` is the tester's, and
        the log, the probes and the seal beside it are the harness's.
        """
        if self.store in path.parents:
            parts = path.relative_to(self.store).parts
            if len(parts) >= TESTER_DEPTH and parts[TESTER_DEPTH - 1] == "tester":
                return False
        return any(root in path.parents for root in self.roots)


def strings(value: object) -> Iterator[str]:
    """Every text in a tool call's input, however deeply nested."""
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for item in value.values():
            yield from strings(item)
    elif isinstance(value, list):
        for item in value:
            yield from strings(item)


def normal(path: str, cwd: Path) -> Path:
    return Path(os.path.normpath(cwd / Path(path).expanduser()))


def relative_named(command: str, cwd: Path, product: Product) -> list[Path]:
    """Relative paths a shell command names that exist under the product."""
    moved = LEADING_CD.match(command)
    here = normal(moved.group(1), cwd) if moved else cwd
    try:
        words = shlex.split(command)
    except ValueError:
        words = command.split()
    found = []
    for word in words:
        if word.startswith(("-", "/", "~")) or "://" in word:
            continue
        for match in glob.glob(str(here / word)):  # noqa: PTH207 # -- the word is a shell glob
            path = normal(match, here)
            if product.inside(path):
                found.append(path)
    return found


def flagged(name: str, given: object, *, cwd: Path, product: Product) -> list[Path]:
    """The paths under the product one tool call named."""
    found = [
        normal(match, cwd)
        for text in strings(given)
        for match in ABSOLUTE.findall(text)
        if product.inside(normal(match, cwd))
    ]
    command = given.get("command") if isinstance(given, dict) else None
    if isinstance(command, str):
        found += relative_named(command, cwd, product)
    searched = name in SEARCHES and isinstance(given, dict) and not given.get("path")
    if searched and product.inside(cwd / "x"):
        found.append(cwd)
    return list(dict.fromkeys(found))


def calls(lines: Iterable[str]) -> list[tuple[int, str, object, Path]] | None:
    """Each tool call: its line, tool, input and working directory; ``None`` if no transcript."""
    found: list[tuple[int, str, object, Path]] = []
    entries = 0
    for number, line in enumerate(lines, 1):
        try:
            entry = json.loads(line)
        except ValueError:
            continue
        message = entry.get("message") if isinstance(entry, dict) else None
        if not isinstance(message, dict):
            continue
        entries += 1
        content = message.get("content")
        cwd = Path(str(entry.get("cwd") or "/"))
        found += [
            (number, str(block.get("name")), block.get("input"), cwd)
            for block in (content if isinstance(content, list) else [])
            if isinstance(block, dict) and block.get("type") == "tool_use"
        ]
    return found if entries else None


def audit(lines: Iterable[str], product: Product) -> tuple[int, list[str]] | None:
    """How many tool calls there were and a sentence per call that looked inside the product.

    ``None`` when ``lines`` hold no transcript at all, so a wrong file is never a clean audit.
    """
    found = calls(lines)
    if found is None:
        return None
    count, problems = 0, []
    for number, name, given, cwd in found:
        count += 1
        problems += [
            f"line {number}, {name}, names {path}, inside the product"
            for path in flagged(name, given, cwd=cwd, product=product)
        ]
    return count, problems


def main(transcript: Path, root: Path, evidence: Path) -> int:
    """Print the audit of ``transcript`` against the product at ``root``; exit 0, 1 or 2."""
    common = evidence.parents[1]
    roots = (root.resolve(), common.parent) if common.name == ".git" else (root.resolve(),)
    product = Product(tuple(dict.fromkeys(roots)), evidence)
    try:
        lines = transcript.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError as error:
        print(f"acceptance audit: cannot read {transcript}: {error}")
        return 2
    result = audit(lines, product)
    if result is None:
        print(f"acceptance audit: {transcript} is not a session transcript")
        return 2
    count, problems = result
    for line in problems:
        print(line)
    if problems:
        print(f"acceptance audit: red, the tester looked inside the product ({count} tool calls)")
        return 1
    print(f"acceptance audit: green, {count} tool calls and none inside the product")
    return 0
