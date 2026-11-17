from __future__ import annotations

import re
import shlex
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Iterator


@dataclass(frozen=True)
class Word:
    raw: str
    cd: bool = False


_START = re.compile(r"~|\$\{?(?:HOME|TMPDIR)\}?|/|\.\.(?:/|$)")
_ANYWHERE = re.compile(
    r"(?:^|(?<=[\s=('\"<>`]))"
    r"(~[^\s'\";|&<>()`]*|\$\{?(?:HOME|TMPDIR)\}?[^\s'\";|&<>()`]*|/[^\s'\";|&<>()`]*"
    r"|\.\.(?:/[^\s'\";|&<>()`]*)?)"
)
_SLASH_COMMAND = re.compile(r"/[a-z][\w-]*:[\w-]+")
_ASSIGN = re.compile(r"[\w-]*=")
_SUBSTITUTION = re.compile(r"\$\(([^()]*)\)|`([^`]*)`")
_HEREDOC = re.compile(r"(?<!<)<<-?\s*(?:'([\w-]+)'|\"([\w-]+)\"|\\?([\w-]+))")
_CODE_FLAG = re.compile(r"-[A-Za-z]*[ce]")
_SHELL_FLAG = re.compile(r"-[A-Za-z]*c")
_LITERAL = re.compile(r"""["'](/[^\s"'\\]*|~[^\s"'\\]*|\.\.(?:/[^\s"'\\]*)?)["']""")
_INTERPRETER = re.compile(r"(?:python[\d.]*|node|deno|bun|perl|ruby)$")
_SHELL = re.compile(r"(?:ba|da|z)?sh$")
_SCRIPTED = frozenset({"sed", "awk", "gawk", "mawk", "grep", "egrep", "fgrep", "rg", "printf"})
_AWKS = frozenset({"awk", "gawk", "mawk"})
_AWK_VALUE_FLAGS = frozenset({"-v", "-F"})
_WRAPPERS = frozenset({"env", "command", "exec", "time", "nohup", "sudo", "xargs"})
_SCRIPT_FLAGS = frozenset({"-e", "--expression", "--regexp"})
_PUNCTUATION = ";&|()<>\n"
_KEYWORDS = frozenset({"do", "then", "else", "elif", "if", "while", "until", "!", "{", "}"})


def words(command: str) -> Iterator[Word]:
    shell_text, bodies = _split_heredocs(command)
    try:
        tokens = _tokens(shell_text)
    except ValueError:
        yield from (Word(raw) for raw in _ANYWHERE.findall(command))
        return
    for simple in _simple_commands(tokens):
        yield from _simple(simple)
    for kind, body in bodies:
        if kind == "shell":
            yield from (Word(w.raw) for w in words(body))
        elif kind == "code":
            yield from _literals(body)


@dataclass(frozen=True)
class Call:
    name: str
    args: tuple[str, ...]


@dataclass(frozen=True)
class Scan:
    calls: tuple[Call, ...]
    code: tuple[str, ...]


def scan(command: str) -> Scan | None:
    try:
        calls, code = _scan(command)
    except ValueError:
        return None
    return Scan(tuple(calls), tuple(code))


def _scan(command: str) -> tuple[list[Call], list[str]]:
    shell_text, bodies = _split_heredocs(command)
    calls: list[Call] = []
    code: list[str] = []
    for simple in _simple_commands(_tokens(shell_text), targets=False):
        for token in simple:
            for match in _SUBSTITUTION.finditer(token):
                _extend(calls, code, _scan(match.group(1) or match.group(2)))
        _extend(calls, code, _scan_simple(_after_prefix(simple)))
    for kind, body in bodies:
        if kind == "shell":
            _extend(calls, code, _scan(body))
        elif kind == "code":
            code.append(body)
    return calls, code


def _scan_simple(words: list[str]) -> tuple[list[Call], list[str]]:
    calls: list[Call] = []
    code: list[str] = []
    if not words:
        return calls, code
    name = _basename(words[0]).lstrip("`")
    calls.append(Call(name, tuple(words[1:])))
    previous = ""
    for arg in words[1:]:
        if _SHELL.match(name) and _SHELL_FLAG.fullmatch(previous):
            _extend(calls, code, _scan(arg))
        elif _INTERPRETER.match(name) and _CODE_FLAG.fullmatch(previous):
            code.append(arg)
        previous = arg
    return calls, code


def _extend(calls: list[Call], code: list[str], found: tuple[list[Call], list[str]]) -> None:
    calls.extend(found[0])
    code.extend(found[1])


def _after_prefix(simple: list[str]) -> list[str]:
    index = 0
    while index < len(simple):
        word = simple[index]
        if _ASSIGN.match(word) or word in _KEYWORDS:
            index += 1
        elif _basename(word) in _WRAPPERS:
            index += 1
            while index < len(simple) and simple[index].startswith("-"):
                index += 1
        else:
            break
    return simple[index:]


def _tokens(text: str) -> list[str]:
    lexer = shlex.shlex(text, posix=True, punctuation_chars=_PUNCTUATION)
    lexer.whitespace_split = True
    lexer.commenters = ""
    lexer.whitespace = " \t\r"
    return list(lexer)


def _split_heredocs(command: str) -> tuple[str, list[tuple[str, str]]]:
    kept: list[str] = []
    bodies: list[tuple[str, str]] = []
    waiting: list[tuple[str, str, list[str]]] = []
    for line in command.split("\n"):
        if waiting:
            delimiter, kind, body = waiting[0]
            if line.strip() == delimiter:
                bodies.append((kind, "\n".join(body)))
                waiting.pop(0)
            else:
                body.append(line)
            continue
        kept.append(line)
        for match in _HEREDOC.finditer(line):
            delimiter = next(group for group in match.groups() if group)
            waiting.append((delimiter, _reader(line[: match.start()]), []))
    bodies.extend((kind, "\n".join(body)) for _, kind, body in waiting)
    return "\n".join(kept), bodies


def _reader(before: str) -> str:
    plain = (w for w in _last_simple(before) if not _ASSIGN.match(w))
    name = next((_basename(w) for w in plain if _basename(w) not in _WRAPPERS), "")
    if _SHELL.match(name):
        return "shell"
    return "code" if _INTERPRETER.match(name) else "data"


def _last_simple(text: str) -> list[str]:
    try:
        simples = list(_simple_commands(_tokens(text)))
    except ValueError:
        return []
    return simples[-1] if simples else []


def _simple_commands(tokens: list[str], *, targets: bool = True) -> Iterator[list[str]]:
    current: list[str] = []
    redirected = False
    for token in tokens:
        if token and set(token) <= set(_PUNCTUATION):
            if "<" in token or ">" in token:
                if not targets:
                    redirected = True
                    if current and current[-1].isdigit():
                        current.pop()
                continue
            if current:
                yield current
            current = []
        elif redirected:
            redirected = False
        else:
            current.append(token)
    if current:
        yield current


def _basename(word: str) -> str:
    return word.rsplit("/", 1)[-1]


def _simple(simple: list[str]) -> Iterator[Word]:
    index = 0
    while index < len(simple) and (
        _ASSIGN.match(simple[index])
        or simple[index] in _KEYWORDS
        or _basename(simple[index]) in _WRAPPERS
    ):
        yield from _word(simple[index])
        index += 1
    if index == len(simple):
        return
    name, args = _basename(simple[index]), simple[index + 1 :]
    yield from _word(simple[index])
    if name == "cd":
        yield from _cd(args)
    elif _SHELL.match(name) or _INTERPRETER.match(name):
        yield from _program(name, args)
    elif name in _SCRIPTED:
        yield from _scripted(args, awk=name in _AWKS)
    else:
        for arg in args:
            yield from _word(arg)


def _cd(args: list[str]) -> Iterator[Word]:
    operands = [arg for arg in args if not arg.startswith("-")]
    if not operands:
        return
    target = operands[0]
    known = _START.match(target) or not re.search(r"[$`]", target)
    yield from _word(target, cd=bool(known))


def _program(name: str, args: list[str]) -> Iterator[Word]:
    previous = ""
    flag = _SHELL_FLAG if _SHELL.match(name) else _CODE_FLAG
    for arg in args:
        if flag.fullmatch(previous):
            if _SHELL.match(name):
                yield from (Word(w.raw) for w in words(arg))
            else:
                yield from _literals(arg)
        else:
            yield from _word(arg)
        previous = arg


def _scripted(args: list[str], *, awk: bool) -> Iterator[Word]:
    flagged = any(arg in _SCRIPT_FLAGS for arg in args)
    skip_next = False
    skip_first = not flagged
    for arg in args:
        if skip_next:
            skip_next = False
        elif arg in _SCRIPT_FLAGS or (awk and arg in _AWK_VALUE_FLAGS):
            skip_next = True
        elif arg.startswith("-"):
            yield from _word(arg)
        elif skip_first:
            skip_first = False
        else:
            yield from _word(arg)


def _word(token: str, *, cd: bool = False) -> Iterator[Word]:
    for match in _SUBSTITUTION.finditer(token):
        yield from (Word(w.raw) for w in words(match.group(1) or match.group(2)))
    value = token.split("=", 1)[1] if _ASSIGN.match(token) else token
    value = value.rstrip("`")
    if cd and not _START.match(value):
        yield Word(value, cd=True)
    elif _START.match(value) and not _SLASH_COMMAND.fullmatch(value):
        yield Word(value, cd=cd)


def _literals(code: str) -> Iterator[Word]:
    roots = {entry.name for entry in Path("/").iterdir()}
    for match in _LITERAL.finditer(code):
        raw = match.group(1)
        first = raw.split("/")[1] if raw.startswith("/") else ""
        if not raw.startswith("/") or first in roots:
            yield Word(raw)
