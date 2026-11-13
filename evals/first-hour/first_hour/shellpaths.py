"""The paths a shell command line names, and not the text that only looks like one.

``policy.shell`` refuses a path outside the run. Reading every ``/word`` in the line as a path
refused work a normal session does: a slash command in a commit message, the ``/p`` of a ``sed``
script, the ``/**`` of a comment in a file a heredoc writes, the ``/items`` route in a test. This
module reads the line the way a shell does and yields only what is used as a path: a word that
starts like one, in a place a path can go.

What is left out is text another program reads, never a path the shell opens: the body of a
heredoc that only feeds a file, the script or pattern argument of ``sed``, ``awk`` and ``grep``,
and text inside a quoted string. A string literal in code a command runs (``python -c``, a
heredoc to ``node``) is read for a path it names, since that code may open it; only a literal whose
first directory the machine's root does not have is let through, as a route or a URL path.
Unbalanced quotes, which the shell would not run either, fall back to the earlier rule: every
word that starts like a path counts. The sandbox is the layer that makes a read outside the run
impossible; this one says no first and says why.
"""

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
    """A word used as a path; ``cd`` is set when the shell moves there, and later words follow."""

    raw: str
    cd: bool = False


# Why: a path starts here, in a word; `..` alone or `../x` but not `..x`.
_START = re.compile(r"~|\$\{?(?:HOME|TMPDIR)\}?|/|\.\.(?:/|$)")
# Why: the earlier rule, kept for a line the shell could not read: a path after a space, a quote,
# `=`, a bracket or a redirect.
_ANYWHERE = re.compile(
    r"(?:^|(?<=[\s=('\"<>`]))"
    r"(~[^\s'\";|&<>()`]*|\$\{?(?:HOME|TMPDIR)\}?[^\s'\";|&<>()`]*|/[^\s'\";|&<>()`]*"
    r"|\.\.(?:/[^\s'\";|&<>()`]*)?)"
)
# Why: `/ratchet:safeguard` is a slash command, and no directory at the root is named that.
_SLASH_COMMAND = re.compile(r"/[a-z][\w-]*:[\w-]+")
_ASSIGN = re.compile(r"[\w-]*=")
_SUBSTITUTION = re.compile(r"\$\(([^()]*)\)|`([^`]*)`")
_HEREDOC = re.compile(r"(?<!<)<<-?\s*(?:'([\w-]+)'|\"([\w-]+)\"|\\?([\w-]+))")
_CODE_FLAG = re.compile(r"-[A-Za-z]*[ce]")
_SHELL_FLAG = re.compile(r"-[A-Za-z]*c")
_LITERAL = re.compile(r"""["'](/[^\s"'\\]*|~[^\s"'\\]*|\.\.(?:/[^\s"'\\]*)?)["']""")
_INTERPRETER = re.compile(r"(?:python[\d.]*|node|deno|bun|perl|ruby)$")
_SHELL = re.compile(r"(?:ba|da|z)?sh$")
_SCRIPTED = frozenset({"sed", "awk", "gawk", "mawk", "grep", "egrep", "fgrep", "rg"})
_WRAPPERS = frozenset({"env", "command", "exec", "time", "nohup", "sudo", "xargs"})
# Why: the script of `sed -e` and the pattern of `grep -e` are the next word, not the first.
_SCRIPT_FLAGS = frozenset({"-e", "--expression", "--regexp"})
_PUNCTUATION = ";&|()<>\n"


def words(command: str) -> Iterator[Word]:
    """The words of ``command`` that are used as paths, in the order the shell reads them."""
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


def _tokens(text: str) -> list[str]:
    lexer = shlex.shlex(text, posix=True, punctuation_chars=_PUNCTUATION)
    lexer.whitespace_split = True
    lexer.commenters = ""
    lexer.whitespace = " \t\r"
    return list(lexer)


def _split_heredocs(command: str) -> tuple[str, list[tuple[str, str]]]:
    """The command line without heredoc bodies, and each body with what reads it.

    A body is ``shell`` for a shell, ``code`` for an interpreter and ``data`` for anything else,
    which only copies it to a file or a pipe.
    """
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
    # Why: a heredoc that never ends still ran; its text is checked as the code it feeds.
    bodies.extend((kind, "\n".join(body)) for _, kind, body in waiting)
    return "\n".join(kept), bodies


def _reader(before: str) -> str:
    """What reads a heredoc: the last command named before ``<<`` on its line."""
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


def _simple_commands(tokens: list[str]) -> Iterator[list[str]]:
    """The tokens split at each separator; a redirect operator is dropped, its target kept."""
    current: list[str] = []
    for token in tokens:
        if token and set(token) <= set(_PUNCTUATION):
            if "<" in token or ">" in token:
                continue
            if current:
                yield current
            current = []
        else:
            current.append(token)
    if current:
        yield current


def _basename(word: str) -> str:
    return word.rsplit("/", 1)[-1]


def _simple(simple: list[str]) -> Iterator[Word]:
    index = 0
    while index < len(simple) and (
        _ASSIGN.match(simple[index]) or _basename(simple[index]) in _WRAPPERS
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
        yield from _scripted(args)
    else:
        for arg in args:
            yield from _word(arg)


def _cd(args: list[str]) -> Iterator[Word]:
    operands = [arg for arg in args if not arg.startswith("-")]
    if not operands:
        return
    target = operands[0]
    # Why: a target that holds a variable or a substitution is not known here, so the words after
    # it are judged from where the line began, which is the stricter reading for a `..`.
    known = _START.match(target) or not re.search(r"[$`]", target)
    yield from _word(target, cd=bool(known))


def _program(name: str, args: list[str]) -> Iterator[Word]:
    """A shell or an interpreter: the word after `-c` or `-e` is code, not a path."""
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


def _scripted(args: list[str]) -> Iterator[Word]:
    """``sed``, ``awk`` and ``grep``: the script or pattern is text, not a path."""
    flagged = any(arg in _SCRIPT_FLAGS for arg in args)
    skip_next = False
    skip_first = not flagged
    for arg in args:
        if skip_next:
            skip_next = False
        elif arg in _SCRIPT_FLAGS:
            skip_next = True
        elif arg.startswith("-"):
            yield from _word(arg)
        elif skip_first:
            skip_first = False
        else:
            yield from _word(arg)


def _word(token: str, *, cd: bool = False) -> Iterator[Word]:
    """The path a word is, and the paths in a command substitution it holds."""
    for match in _SUBSTITUTION.finditer(token):
        yield from (Word(w.raw) for w in words(match.group(1) or match.group(2)))
    value = token.split("=", 1)[1] if _ASSIGN.match(token) else token
    value = value.rstrip("`")
    if cd and not _START.match(value):
        yield Word(value, cd=True)
    elif _START.match(value) and not _SLASH_COMMAND.fullmatch(value):
        yield Word(value, cd=cd)


def _literals(code: str) -> Iterator[Word]:
    """The paths a string literal in code names, except those no directory at the root holds."""
    roots = {entry.name for entry in Path("/").iterdir()}
    for match in _LITERAL.finditer(code):
        raw = match.group(1)
        first = raw.split("/")[1] if raw.startswith("/") else ""
        if not raw.startswith("/") or first in roots:
            yield Word(raw)
