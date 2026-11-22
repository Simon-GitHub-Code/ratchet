from __future__ import annotations

import functools
import re
from dataclasses import dataclass
from types import MappingProxyType
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping, Sequence

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
    return "\n".join(line for line in justfile.splitlines() if not line.lstrip().startswith("#"))


def uncommented(lines: Sequence[str]) -> list[str]:
    return [
        line
        for line in lines
        if not line.lstrip().startswith("#") or line.lstrip().startswith(SHEBANG)
    ]


def definition(text: str, name: str) -> tuple[str, ...]:
    lines = text.splitlines()
    for index, line in enumerate(lines):
        head = HEAD_NAME.match(line)
        if head is None or head.group(1) != name:
            continue
        start = index
        while start > 0 and lines[start - 1].startswith(("#", "[")):
            start -= 1
        end = index + 1
        while end < len(lines) and lines[end].startswith((" ", "\t")):
            end += 1
        return tuple(lines[start:end])
    return ()


def imports(text: str) -> list[str]:
    return [match.group(2) for match in IMPORT.finditer(text)]


def modules(text: str) -> set[str]:
    return set(MODULE.findall(text))


def runs(justfile: str, recipe: str, needle: str) -> bool:
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
    colon: int
    params: tuple[str, ...]
    before: tuple[str, ...]
    after: tuple[str, ...]


@dataclass(frozen=True)
class Block:
    name: str
    start: int
    head: int
    end: int
    parsed: Head | None


def mask(text: str) -> str:
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
    return (word.lstrip("(").split() or [word])[0]


def parse_head(line: str, name_end: int) -> Head | None:
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
    return parsed_blocks(tuple(lines))


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


def private_helper(template: str, name: str) -> bool:
    return name.startswith("_") and template_recipe(template, name) is not None


CARRIED_VARIABLE = "plugin_py"
BLOCK_FIRST = "plugin_choice"
BLOCK_LAST = "ratchet_plugin"


def plugin_block(template: str) -> list[str]:
    lines = template.split("\n")
    first = next((i for i, line in enumerate(lines) if line.startswith(f"{BLOCK_FIRST} :=")), None)
    last = next((i for i, line in enumerate(lines) if line.startswith(f"{BLOCK_LAST} :=")), None)
    if first is None or last is None or last < first:
        return []
    while first > 0 and lines[first - 1].startswith("#"):
        first -= 1
    return uncommented(lines[first:last])


def with_plugin_block(template: str) -> Want:
    block = plugin_block(template)

    def want(current: str | None) -> str:
        text = current or ""
        if (
            not block
            or defines_variable(text, CARRIED_VARIABLE)
            or defines_variable(text, BLOCK_FIRST)
        ):
            return text
        lines = text.split("\n")
        at = next((i for i, line in enumerate(lines) if line.startswith(f"{BLOCK_LAST} :=")), None)
        if at is None:
            at = next((b.start for b in blocks(lines) if b.parsed), len(lines))
        lines[at:at] = [*block, ""] if at < len(lines) else block
        return "\n".join(lines)

    return want


def unmet(template: str, project: str, name: str) -> tuple[str, ...]:
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
        if not (
            defines_recipe(project, word)
            or defines_variable(project, word)
            or private_helper(template, word)
            or word == CARRIED_VARIABLE
        )
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
    return tuple(
        block.name
        for block in blocks(template.split("\n"))
        if block.parsed is not None and block.parsed.before[:1] == (name,)
    )


def with_recipe(template: str, name: str) -> Want:
    found = template_recipe(template, name)
    if found is None:
        message = f"the template has no recipe {name!r}"
        raise ValueError(message)
    template_lines, wanted, head = found
    copy = uncommented(template_lines[wanted.start : wanted.end])
    rank = {block.name: place for place, block in enumerate(blocks(template_lines))}

    helpers = [
        with_recipe(template, word)
        for word in (*head.before, *head.after)
        if private_helper(template, word)
    ]

    carries = CARRIED_VARIABLE in INTERPOLATION.findall("\n".join(copy))

    def want(current: str | None) -> str:
        text = current or ""
        for helper in helpers:
            text = helper(text)
        if carries:
            text = with_plugin_block(template)(text)
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


def callers(template: str, name: str) -> tuple[str, ...]:
    lines = template.split("\n")
    return tuple(
        block.name
        for block in blocks(lines)
        if block.parsed is not None
        and name in JUST_CALL.findall(code_of("\n".join(lines[block.head + 1 : block.end])))
    )


def with_template_recipe(template: str, name: str) -> Want:
    found = template_recipe(template, name)
    if found is None:
        message = f"the template has no recipe {name!r}"
        raise ValueError(message)
    template_lines, wanted, _ = found
    copy = uncommented(template_lines[wanted.start : wanted.end])
    copied = with_recipe(template, name)

    def want(current: str | None) -> str:
        text = current or ""
        lines = text.split("\n")
        block = next((b for b in blocks(lines) if b.name == name), None)
        if block is None:
            return copied(text)
        lines[block.start : block.end] = copy
        return "\n".join(lines)

    return want


def with_first_dependency(recipe: str, dependency_name: str) -> Want:
    return with_dependency_after(recipe, dependency_name, None)


def with_dependency_after(recipe: str, dependency_name: str, after: str | None) -> Want:
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
        named = re.search(rf"(?<![\w-]){re.escape(after)}(?![\w-])", listed) if after else None
        if named is not None:
            at = colon + 1 + named.end()
            lines[block.head] = f"{head[:at]} {dependency_name}{head[at:]}"
            return "\n".join(lines)
        gap = len(listed) - len(listed.lstrip())
        if listed.strip():
            at = colon + 1 + gap
            lines[block.head] = f"{head[:at]}{'' if gap else ' '}{dependency_name} {head[at:]}"
        else:
            lines[block.head] = f"{head[: colon + 1]} {dependency_name}{head[colon + 1 :]}"
        return "\n".join(lines)

    return want


def with_first_line(recipe: str, line: str) -> Want:
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


VARIABLE = re.compile(r"^(?:export[ \t]+)?([A-Za-z_][\w-]*)[ \t]*:=[ \t]*(.*)$", re.MULTILINE)
STRING = re.compile(r"\"([^\"]*)\"|'([^']*)'")
QUOTED = re.compile(r'"([^"]*)"')
PART = re.compile(r"\"(?P<double>[^\"]*)\"|'(?P<single>[^']*)'|(?P<name>[A-Za-z_][\w-]*)")


@functools.lru_cache(maxsize=256)
def variables(justfile: str) -> Mapping[str, str]:
    found: dict[str, str] = {}
    for match in VARIABLE.finditer(justfile):
        found.setdefault(match.group(1), match.group(2))
    return MappingProxyType(found)


def listed(justfile: str, name: str) -> set[str]:
    return set(ordered(justfile, name))


def ordered(justfile: str, name: str, seen: frozenset[str] = frozenset()) -> tuple[str, ...]:
    value = variables(justfile).get(name)
    if value is None or name in seen:
        return ()
    found: list[str] = []
    for part in PART.finditer(value):
        if part["name"]:
            found += ordered(justfile, part["name"], seen | {name})
        else:
            found += (part["double"] or part["single"] or "").split()
    return tuple(dict.fromkeys(found))


def reaches(justfile: str, recipe: str) -> set[str]:
    return set(_reaches(justfile, recipe))


@functools.lru_cache(maxsize=1024)
def _reaches(justfile: str, recipe: str) -> frozenset[str]:
    lines = justfile.split("\n")
    defined = {block.name: (block, block.parsed) for block in blocks(lines) if block.parsed}
    waiting, found = [recipe], set()
    while waiting:
        name = waiting.pop()
        if name in found:
            continue
        found.add(name)
        if name not in defined:
            continue
        block, head = defined[name]
        body = code_of("\n".join(lines[block.head + 1 : block.end]))
        waiting += [*head.before, *head.after, *JUST_CALL.findall(body)]
        for variable in INTERPOLATION.findall(body):
            waiting += sorted(listed(justfile, variable))
    return frozenset(found - {recipe})


def undefined(justfile: str, recipe: str) -> list[str]:
    lines = justfile.split("\n")
    block = next((b for b in blocks(lines) if b.name == recipe and b.parsed), None)
    if block is None or block.parsed is None:
        return []
    body = code_of("\n".join(lines[block.head + 1 : block.end]))
    return [
        name
        for name in INTERPOLATION.findall(body)
        if BARE_NAME.fullmatch(name)
        and name not in block.parsed.params
        and not defines_variable(justfile, name)
    ]


def dependencies(justfile: str, recipe: str) -> tuple[str, ...] | None:
    lines = justfile.split("\n")
    block = next((b for b in blocks(lines) if b.name == recipe), None)
    if block is None or block.parsed is None or block.parsed.after:
        return None
    if code_of("\n".join(lines[block.head + 1 : block.end])).strip():
        return None
    return block.parsed.before


def with_dependency(recipe: str, name: str) -> Want:
    def want(current: str | None) -> str:
        text = current or ""
        before = dependencies(text, recipe)
        if before is None or name in before:
            return text
        lines = text.split("\n")
        block = next(b for b in blocks(lines) if b.name == recipe)
        head = lines[block.head]
        rest = head[block.parsed.colon + 1 :] if block.parsed else ""
        end = len(head) - len(rest) + len(rest[: comment_at(rest)].rstrip())
        lines[block.head] = f"{head[:end]} {name}{head[end:]}"
        return "\n".join(lines)

    return want


def with_listed(name: str, word: str) -> Want:
    return with_words(name, lambda words: words if word in words else [*words, word])


def with_words(name: str, change: Callable[[list[str]], list[str]]) -> Want:
    def want(current: str | None) -> str:
        text = current or ""
        match = next((m for m in VARIABLE.finditer(text) if m.group(1) == name), None)
        quoted = QUOTED.fullmatch(match.group(2).strip()) if match else None
        if match is None or quoted is None:
            return text
        words = quoted.group(1).split()
        changed = change(words)
        if changed == words:
            return text
        return f'{text[: match.start(2)]}"{" ".join(changed)}"{text[match.end(2) :]}'

    return want


def with_variable(name: str, value: Callable[[str], str]) -> Want:
    def want(current: str | None) -> str:
        text = current or ""
        if defines_variable(text, name):
            return text
        lines = text.split("\n")
        first_recipe = next((b.start for b in blocks(lines) if b.parsed), len(lines))
        at = max(
            (i + 1 for i in range(first_recipe) if VARIABLE.match(lines[i])),
            default=first_recipe,
        )
        lines[at:at] = [f"{name} := {value(text)}"]
        return "\n".join(lines)

    return want
