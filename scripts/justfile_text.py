"""A justfile, read and edited as text: which of the template's recipes a project may be given.

``just`` runs a recipe as one script only when its body starts with ``#!``, so a line added
before a shebang line breaks the script. ``just`` needs one indentation style within a recipe,
so a line added follows its recipe's body and a copied recipe keeps the template's own.
"""

from __future__ import annotations

import functools
import re
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Sequence

    from gatelib import Want

HEAD_NAME = re.compile(r"@?([A-Za-z_][\w-]*)")
INTERPOLATION = re.compile(r"\{\{\s*([^}]*?)\s*\}\}")
JUST_CALL = re.compile(r"\bjust[ \t]+(?:-\S+[ \t]+)*@?([A-Za-z_][\w-]*)")
BARE_NAME = re.compile(r"[A-Za-z_][\w-]*")
IMPORT = re.compile(r"""^import\??[ \t]+(['"])(.+?)\1""", re.MULTILINE)
MODULE = re.compile(r"^mod\??[ \t]+([A-Za-z_][\w-]*)", re.MULTILINE)
BINDS = "bind:"
CHAIN = "&&"
SHEBANG = "#!"
QUOTES = "'\"`"


def code_of(justfile: str) -> str:
    """A justfile less its whole-line comments, where the template names what it explains."""
    return "\n".join(line for line in justfile.splitlines() if not line.lstrip().startswith("#"))


def definition(text: str, name: str) -> tuple[str, ...]:
    """The recipe or variable ``name`` of a justfile: its comment, its head and its body."""
    lines = text.splitlines()
    for index, line in enumerate(lines):
        head = HEAD_NAME.match(line)
        if head is None or head.group(1) != name:
            continue
        start = index
        while start > 0 and lines[start - 1].startswith("#"):
            start -= 1
        end = index + 1
        while end < len(lines) and lines[end].startswith((" ", "\t")):
            end += 1
        return tuple(lines[start:end])
    return ()


def imports(text: str) -> list[str]:
    """The paths a justfile's ``import`` lines name, as written: relative to the file itself."""
    return [match.group(2) for match in IMPORT.finditer(text)]


def modules(text: str) -> set[str]:
    """The modules a justfile declares with ``mod``, whose recipes ``just`` runs by their name."""
    return set(MODULE.findall(text))


def runs(justfile: str, recipe: str, needle: str) -> bool:
    """Whether running ``recipe`` runs a line holding ``needle``.

    It does when the recipe's own lines hold it, or a recipe it depends on or calls with ``just``
    runs it.
    """
    lines = justfile.split("\n")
    defined = {block.name: (block, block.parsed) for block in blocks(lines) if block.parsed}
    waiting, visited = [recipe], set()
    while waiting:
        name = waiting.pop()
        if name in visited or name not in defined:
            continue
        visited.add(name)
        block, head = defined[name]
        body = code_of("\n".join(lines[block.head + 1 : block.end]))
        if needle in body:
            return True
        waiting += [*head.before, *head.after, *JUST_CALL.findall(body)]
    return False


def defines_recipe(justfile: str, name: str) -> bool:
    pattern = rf"^@?{re.escape(name)}(?:[ \t][^\n]*)?:(?!=)"
    return re.search(pattern, justfile, re.MULTILINE) is not None


def defines_variable(justfile: str, name: str) -> bool:
    return (
        re.search(rf"^(?:export[ \t]+)?{re.escape(name)}[ \t]*:=", justfile, re.MULTILINE)
        is not None
    )


@dataclass(frozen=True)
class Head:
    """What a recipe's head line says: where its colon is and the names it takes and needs."""

    colon: int
    params: tuple[str, ...]
    before: tuple[str, ...]
    after: tuple[str, ...]


@dataclass(frozen=True)
class Block:
    """A top-level definition: the comment and attribute lines above it, its head, its body."""

    name: str
    start: int
    head: int
    end: int
    parsed: Head | None


def mask(text: str) -> str:
    """The text with what a quote holds replaced by ``_``: a colon in a string is not syntax."""
    out = []
    quote = ""
    escaped = False
    for char in text:
        if not quote:
            quote = char if char in QUOTES else ""
            out.append(char)
            continue
        closing = char == quote and not escaped
        escaped = quote == '"' and char == "\\" and not escaped
        out.append(char if closing else "_")
        quote = "" if closing else quote
    return "".join(out)


def words(text: str) -> list[str]:
    """The words of the text, a quoted string or a parenthesised group kept whole."""
    masked = mask(text)
    found = []
    depth = 0
    begin = None
    for index, char in enumerate(masked + " "):
        if begin is None and not char.isspace():
            begin = index
        depth += (char == "(") - (char == ")")
        if begin is not None and char.isspace() and depth <= 0:
            found.append(text[begin:index])
            begin = None
    return found


def comment_at(text: str) -> int:
    at = mask(text).find("#")
    return len(text) if at < 0 else at


def dependency(word: str) -> str:
    """The recipe a dependency word names: ``(build debug)`` is ``build``."""
    return (word.lstrip("(").split() or [word])[0]


def parse_head(line: str, name_end: int) -> Head | None:
    """The head of a recipe, or ``None`` when the line assigns (``:=``) or has no colon."""
    masked = mask(line)
    colon = masked.find(":", name_end)
    if colon < 0 or masked[colon + 1 : colon + 2] == "=":
        return None
    params = tuple(w.lstrip("*+$").split("=")[0] for w in words(line[name_end:colon]))
    rest = line[colon + 1 :]
    listed = words(rest[: comment_at(rest)])
    chain = listed.index(CHAIN) if CHAIN in listed else len(listed)
    before = tuple(dependency(w) for w in listed[:chain])
    return Head(colon, params, before, tuple(dependency(w) for w in listed[chain + 1 :]))


def blocks(lines: Sequence[str]) -> tuple[Block, ...]:
    """The top-level definitions of a justfile, in order."""
    return parsed_blocks(tuple(lines))


# Why: one conformance run asks the same few justfiles about each recipe it names, and parsing
# them again for every question was most of the run's time.
@functools.lru_cache(maxsize=64)
def parsed_blocks(lines: tuple[str, ...]) -> tuple[Block, ...]:
    found = []
    pending = None
    index = 0
    while index < len(lines):
        line = lines[index]
        if line.startswith(("#", "[")):
            pending = index if pending is None else pending
            index += 1
            continue
        name = HEAD_NAME.match(line)
        if name is None:
            pending = None
            index += 1
            continue
        end = index + 1
        for follow in range(index + 1, len(lines)):
            if lines[follow].startswith((" ", "\t")):
                end = follow + 1
            elif lines[follow].strip():
                break
        start = index if pending is None else pending
        found.append(Block(name.group(1), start, index, end, parse_head(line, name.end())))
        pending = None
        index = end
    return tuple(found)


def template_recipe(template: str, name: str) -> tuple[list[str], Block, Head] | None:
    lines = template.split("\n")
    block = next((b for b in blocks(lines) if b.name == name), None)
    if block is None or block.parsed is None:
        return None
    return lines, block, block.parsed


def unmet(template: str, project: str, name: str) -> tuple[str, ...]:
    """Why the template's recipe ``name`` cannot be copied as it stands; empty when it can."""
    found = template_recipe(template, name)
    if found is None:
        return (f"the template has no recipe `{name}`",)
    lines, block, parsed = found
    code = code_of("\n".join(lines[block.start : block.end]))
    body = code_of("\n".join(lines[block.head + 1 : block.end]))
    interpolated = INTERPOLATION.findall(code)
    named = {*parsed.before, *parsed.after, *JUST_CALL.findall(body)}
    named |= {e for e in interpolated if BARE_NAME.fullmatch(e)}
    reasons = [f"a `{BINDS}` placeholder is in it"] if BINDS in code else []
    lacking = [
        f"`{word}`"
        for word in sorted(named - set(parsed.params))
        if not (defines_recipe(project, word) or defines_variable(project, word))
    ]
    if lacking:
        *head, last = lacking
        listed = f"{', '.join(head)} and {last}" if head else last
        reasons.append(f"it needs {listed}, which the justfile does not define")
    reasons += [
        f"it {'calls' if '(' in e else 'computes'} `{e}`"
        for e in sorted(set(interpolated))
        if not BARE_NAME.fullmatch(e)
    ]
    return tuple(reasons)


def first_dependents(template: str, name: str) -> tuple[str, ...]:
    """The recipes of the template that run ``name`` before their other dependencies."""
    return tuple(
        block.name
        for block in blocks(template.split("\n"))
        if block.parsed is not None and block.parsed.before[:1] == (name,)
    )


def with_recipe(template: str, name: str) -> Want:
    """The want of a justfile that has the template's recipe ``name``, its comment above it."""
    found = template_recipe(template, name)
    if found is None:
        message = f"the template has no recipe {name!r}"
        raise ValueError(message)
    template_lines, wanted, _ = found
    copy = template_lines[wanted.start : wanted.end]
    rank = {block.name: place for place, block in enumerate(blocks(template_lines))}

    def want(current: str | None) -> str:
        text = current or ""
        lines = text.split("\n")
        present = blocks(lines)
        if any(block.name == name for block in present):
            return text
        tail = len(present)
        while tail and rank.get(present[tail - 1].name, -1) > rank[name]:
            tail -= 1
        if tail < len(present):
            at = present[tail].start
            lines[at:at] = [*([""] if at and lines[at - 1].strip() else []), *copy, ""]
            return "\n".join(lines)
        end = len(lines)
        while end and not lines[end - 1].strip():
            end -= 1
        lines[end:end] = [*([""] if end else []), *copy]
        return "\n".join(lines)

    return want


def with_first_dependency(recipe: str, dependency_name: str) -> Want:
    """The want of a justfile whose recipe runs ``dependency_name`` first among its dependencies."""

    def want(current: str | None) -> str:
        text = current or ""
        lines = text.split("\n")
        block = next((b for b in blocks(lines) if b.name == recipe), None)
        if block is None or block.parsed is None or dependency_name in block.parsed.before:
            return text
        head = lines[block.head]
        colon = block.parsed.colon
        rest = head[colon + 1 :]
        listed = rest[: comment_at(rest)]
        gap = len(listed) - len(listed.lstrip())
        if listed.strip():
            at = colon + 1 + gap
            lines[block.head] = f"{head[:at]}{'' if gap else ' '}{dependency_name} {head[at:]}"
        else:
            lines[block.head] = f"{head[: colon + 1]} {dependency_name}{head[colon + 1 :]}"
        return "\n".join(lines)

    return want


def with_first_line(recipe: str, line: str) -> Want:
    """The want of a justfile whose recipe starts its body with ``line``, indented as the body."""

    def want(current: str | None) -> str:
        text = current or ""
        lines = text.split("\n")
        block = next((b for b in blocks(lines) if b.name == recipe), None)
        if block is None or block.parsed is None:
            return text
        first = next((i for i in range(block.head + 1, block.end) if lines[i].strip()), None)
        if first is None:
            return text
        shown = lines[first].strip()
        if shown.startswith(SHEBANG) or shown.removeprefix("@") == line.removeprefix("@"):
            return text
        indent = lines[first][: len(lines[first]) - len(lines[first].lstrip())]
        lines.insert(first, indent + line)
        return "\n".join(lines)

    return want
