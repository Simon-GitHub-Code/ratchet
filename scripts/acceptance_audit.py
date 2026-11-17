from __future__ import annotations

import glob
import itertools
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
SEPARATORS = frozenset({"&&", "||", ";", "|", "&"})


@dataclass(frozen=True)
class Product:
    roots: tuple[Path, ...]
    store: Path

    def inside(self, path: Path) -> bool:
        if self.store in path.parents:
            parts = path.relative_to(self.store).parts
            if len(parts) >= TESTER_DEPTH and parts[TESTER_DEPTH - 1] == "tester":
                return False
        return any(root in path.parents for root in self.roots)

    def holds(self, path: Path) -> bool:
        return any(path == root or path in root.parents for root in self.roots)


class Links:
    def __init__(self) -> None:
        self.made: dict[Path, Path] = {}

    def real(self, text: str, cwd: Path) -> Path:
        done = Path("/")
        for part in (cwd / Path(text).expanduser()).parts[1:]:
            done = done.parent if part == ".." else done / part
            if done in self.made:
                done = self.made[done]
            elif os.path.islink(done):  # noqa: PTH114 # -- never raises, even on a name too long
                done = Path(os.path.realpath(done))
        return done

    def make(self, words: list[str], here: Path) -> list[tuple[Path, Path]]:
        made = []
        for start, word in enumerate(words):
            if word != "ln" and not word.endswith("/ln"):
                continue
            given = list(itertools.takewhile(lambda w: w not in SEPARATORS, words[start + 1 :]))
            options = [w for w in given if w.startswith("-")]
            named = [w for w in given if not w.startswith("-")]
            symbolic = any(o == "--symbolic" or (o[1:2] != "-" and "s" in o) for o in options)
            if not symbolic or not named:
                continue
            target, name = named[-2:] if len(named) > 1 else (named[0], Path(named[0]).name)
            folder = self.real(str(Path(name).parent), here)
            link = folder / Path(name).name
            self.made[link] = self.real(target, folder)
            made.append((link, self.made[link]))
        return made


def strings(value: object) -> Iterator[str]:
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for item in value.values():
            yield from strings(item)
    elif isinstance(value, list):
        for item in value:
            yield from strings(item)


def shell(command: str, cwd: Path, links: Links) -> tuple[list[str], Path]:
    moved = LEADING_CD.match(command)
    try:
        words = shlex.split(command)
    except ValueError:
        words = command.split()
    return words, links.real(moved.group(1), cwd) if moved else cwd


def relative_named(words: list[str], here: Path, *, product: Product, links: Links) -> list[Path]:
    found = []
    for word in words:
        if word.startswith(("-", "/", "~")) or "://" in word:
            continue
        for match in glob.glob(str(links.real(word, here))):  # noqa: PTH207 # -- a shell glob
            path = links.real(match, here)
            if product.inside(path):
                found.append(path)
    return found


def flagged(name: str, given: object, *, cwd: Path, product: Product, links: Links) -> list[str]:
    said = []
    found = [
        links.real(match, cwd)
        for text in strings(given)
        for match in ABSOLUTE.findall(text)
        if product.inside(links.real(match, cwd))
    ]
    command = given.get("command") if isinstance(given, dict) else None
    if isinstance(command, str):
        words, here = shell(command, cwd, links)
        said += [
            f"links {link} to {target}, which leads into the product"
            for link, target in links.make(words, here)
            if product.holds(target)
        ]
        found += relative_named(words, here, product=product, links=links)
    searched = name in SEARCHES and isinstance(given, dict) and not given.get("path")
    if searched and product.inside(links.real("x", cwd)):
        found.append(links.real(".", cwd))
    return said + [f"names {path}, inside the product" for path in dict.fromkeys(found)]


def calls(lines: Iterable[str]) -> list[tuple[int, str, object, Path]] | None:
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
    found = calls(lines)
    if found is None:
        return None
    count, problems, links = 0, [], Links()
    for number, name, given, cwd in found:
        count += 1
        problems += [
            f"line {number}, {name}, {clause}"
            for clause in flagged(name, given, cwd=cwd, product=product, links=links)
        ]
    return count, problems


def main(transcript: Path, root: Path, evidence: Path) -> int:
    common = evidence.parents[1]
    roots = (root, common.parent) if common.name == ".git" else (root,)
    real = Links().real
    product = Product(tuple(dict.fromkeys(real(str(r), r) for r in roots)), real(".", evidence))
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
