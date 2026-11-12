"""A small, conservative bash lexer for the guard: simple commands, redirects and nested scripts.

It is not a shell. It finds what a guard needs and errs towards seeing more commands, never
fewer:

- quotes, backslash escapes and line continuations are honoured, and each word is unquoted
  with ``shlex.split``;
- ``;``, ``&&``, ``||``, ``|``, ``&``, newlines and subshell parentheses separate commands;
- ``>``, ``>>``, ``>|``, ``&>`` and friends record their target word as a write; a numeric
  file descriptor written against the operator (``2>``) is dropped;
- ``$(...)`` and backticks, quoted or not, are lifted out as nested scripts and leave a
  placeholder word behind; a ``case`` pattern's ``)`` does not end a ``$(...)``;
- the word after ``<<<`` is kept as a here-string, to be read as the stdin of its command;
- heredoc bodies are lifted out too, so prose in a commit message is never read as commands;
  the operator leaves a numbered marker so a shell fed by the heredoc can be checked;
- ``#`` at the start of a word begins a comment.
"""

import re
import shlex
from dataclasses import dataclass, field

PLACEHOLDER = "_substitution_"
HEREDOC_MARK = "_heredoc_"
_OPERATOR_CHARS = frozenset(";&|<>\n")
_PARENS = frozenset("()")
_BLANKS = frozenset(" \t\r")
_REDIRECT_CHARS = frozenset("<>")
_DEPTH = {"(": 1, ")": -1}
_CASE_NESTING = {"case": 1, "esac": -1}
_OPERATOR_RUN = re.compile(r"[;&|<>]+")
_HEREDOC = re.compile(r"<<(?!<)(?P<strip>-?)[ \t\r]*(?P<delimiter>[^ \t\r;&|<>\n()]*)")
_QUOTES = str.maketrans("", "", "'\"")
_DELIMITER_QUOTING = str.maketrans("", "", "'\"\\")


@dataclass(frozen=True, slots=True)
class Token:
    """A word (still quoted as written) or an operator."""

    text: str
    operator: bool = False


@dataclass(frozen=True, slots=True)
class SimpleCommand:
    """One command: its unquoted words, the paths it redirects into, the heredocs it reads.

    ``piped`` is true when its output feeds the next command through ``|`` or ``|&``;
    ``herestrings`` are the words it reads through ``<<<``; ``then`` is the operator that ends
    it (``&&``, ``;``, ``|``, a parenthesis, a newline), which says whether the next command
    runs only when this one succeeded.
    """

    words: tuple[str, ...]
    writes: tuple[str, ...] = ()
    heredocs: tuple[int, ...] = ()
    piped: bool = False
    herestrings: tuple[str, ...] = ()
    then: str = ""


@dataclass(frozen=True, slots=True)
class Script:
    """A lexed command line: its simple commands and the scripts nested inside it."""

    commands: tuple[SimpleCommand, ...]
    nested: tuple[str, ...]
    heredocs: tuple[str, ...]


@dataclass(slots=True)
class _Shared:
    nested: list[str] = field(default_factory=list)
    heredocs: dict[int, str] = field(default_factory=dict)
    allocated: int = 0


class _Lexer:
    def __init__(
        self, text: str, start: int, shared: _Shared, *, substitution: bool = False
    ) -> None:
        self.text = text
        self.at = start
        self.shared = shared
        self.substitution = substitution
        self.tokens: list[Token] = []
        self.word: list[str] = []
        self.pending: list[tuple[int, str, bool]] = []
        self.depth = 0
        self.cases = 0
        self.closed = False

    def run(self) -> None:
        while self.at < len(self.text):
            char = self.text[self.at]
            if char == ")":
                self._end_word()
                if self.substitution and self.depth == 0 and self.cases == 0:
                    self.at += 1
                    self.closed = True
                    return
            self._step(char)
        self._end_word()

    def _step(self, char: str) -> None:
        if char in "'\"\\`" or self.text.startswith("$(", self.at):
            self._quoted(char)
        elif char == "#" and not self.word:
            self._skip_comment()
        elif char in _BLANKS:
            self._end_word()
            self.at += 1
        elif char in _PARENS:
            self._paren(char)
        elif char in _OPERATOR_CHARS:
            self._operator()
        else:
            self.word.append(char)
            self.at += 1

    def _quoted(self, char: str) -> None:
        if char == "'":
            self._single()
        elif char == '"':
            self._double()
        elif char == "\\":
            self._escape()
        else:
            self._substitute()

    def _end_word(self) -> None:
        if self.word:
            word = "".join(self.word)
            self.cases += _CASE_NESTING.get(word, 0)  # pragma: no mutate # -- only zero matters
            self.tokens.append(Token(word))
            self.word = []

    def _find(self, needle: str, start: int) -> int:
        """Where ``needle`` next occurs from ``start``, or the end of the text."""
        try:
            return self.text.index(needle, start)
        except ValueError:
            return len(self.text)

    def _single(self) -> None:
        end = self._find("'", self.at + 1) + 1
        self.word.append(self.text[self.at : end])
        self.at = end

    def _double(self) -> None:
        self.word.append('"')
        self.at += 1
        while self.at < len(self.text) and self.text[self.at] != '"':
            char = self.text[self.at]
            if char == "\\":
                self.word.append(self.text[self.at : self.at + 2])
                self.at += 2
            elif char == "`" or self.text.startswith("$(", self.at):
                self._substitute()
            else:
                self.word.append(char)
                self.at += 1
        self.word.append('"')
        self.at += 1

    def _escape(self) -> None:
        pair = self.text[self.at : self.at + 2]
        if pair != "\\\n":
            self.word.append(pair)
        self.at += 2

    def _substitute(self) -> None:
        if self.text[self.at] == "`":
            end = self._find("`", self.at + 1)
            self.shared.nested.append(self.text[self.at + 1 : end])
            self.at = end + 1
        else:
            child = _Lexer(self.text, self.at + 2, self.shared, substitution=True)
            child.run()
            self.shared.nested.append(self.text[self.at + 2 : child.at - child.closed])
            self.at = child.at
        self.word.append(PLACEHOLDER)

    def _skip_comment(self) -> None:
        self.at = self._find("\n", self.at)

    def _paren(self, char: str) -> None:
        self._end_word()
        # Why: a ")" with nothing open ends a case pattern; it must not unbalance the depth.
        if char == "(" or self.depth > 0:
            self.depth += _DEPTH[char]  # pragma: no mutate # -- negated, still returns to 0
        self.tokens.append(Token(char, operator=True))
        self.at += 1

    def _operator(self) -> None:
        heredoc = _HEREDOC.match(self.text, self.at)
        if heredoc is not None:
            self._heredoc(heredoc)
            return
        run = _OPERATOR_RUN.match(self.text, self.at)
        end = self.at + 1 if run is None else run.end()
        operator = self.text[self.at : end]
        self.at = end
        if operator[0] in _REDIRECT_CHARS and "".join(self.word).isdigit():
            self.word = []
        self._end_word()
        self.tokens.append(Token(operator, operator=True))
        if operator == "\n":
            self._read_heredoc_bodies()

    def _heredoc(self, heredoc: re.Match[str]) -> None:
        self._end_word()
        self.at = heredoc.end()
        strip_tabs = bool(heredoc.group("strip"))
        delimiter = heredoc.group("delimiter").translate(_DELIMITER_QUOTING)
        index = self.shared.allocated
        self.shared.allocated += 1
        self.pending.append((index, delimiter, strip_tabs))
        self.tokens.append(Token("<<", operator=True))
        self.tokens.append(Token(f"{HEREDOC_MARK}{index}"))

    def _read_heredoc_bodies(self) -> None:
        for index, delimiter, strip_tabs in self.pending:
            body: list[str] = []
            while self.at < len(self.text):
                end = self._find("\n", self.at)
                line = self.text[self.at : end]
                self.at = end + 1
                if (line.lstrip("\t") if strip_tabs else line) == delimiter:
                    break
                body.append(line)
            self.shared.heredocs[index] = "\n".join(body)
        self.pending = []


def lex(text: str) -> tuple[list[Token], tuple[str, ...], tuple[str, ...]]:
    """Tokens of ``text`` plus the nested scripts and heredoc bodies lifted out of it."""
    shared = _Shared()
    lexer = _Lexer(text + "\n", 0, shared)
    lexer.run()
    bodies = tuple(shared.heredocs.get(index, "") for index in range(shared.allocated))
    return lexer.tokens, tuple(shared.nested), bodies


def unquote(word: str) -> str:
    """``word`` with shell quoting removed; quote characters are dropped if it will not parse."""
    try:
        return "".join(shlex.split(word))  # pragma: no mutate # -- one word is one shlex part
    except ValueError:
        return word.translate(_QUOTES)


def _is_fd_target(operator: str, word: str) -> bool:
    return operator.endswith("&") and (word.isdigit() or word == "-")


@dataclass(slots=True)
class _Splitter:
    commands: list[SimpleCommand] = field(default_factory=list)
    words: list[str] = field(default_factory=list)
    writes: list[str] = field(default_factory=list)
    heredocs: list[int] = field(default_factory=list)
    herestrings: list[str] = field(default_factory=list)
    redirect: str | None = None

    def word(self, word: str) -> None:
        redirect, self.redirect = self.redirect, None
        if redirect is None:
            self.words.append(word)
        elif redirect == "<<<":
            self.herestrings.append(word)
        elif redirect.startswith("<<") and word.startswith(HEREDOC_MARK):
            self.heredocs.append(int(word.removeprefix(HEREDOC_MARK)))
        elif ">" in redirect and not _is_fd_target(redirect, word):
            self.writes.append(word)

    def operator(self, operator: str) -> None:
        if "<" in operator or ">" in operator:
            self.redirect = operator
            return
        if self.words or self.writes:
            piped = operator in {"|", "|&"}
            command = SimpleCommand(
                tuple(self.words),
                tuple(self.writes),
                tuple(self.heredocs),
                piped,
                tuple(self.herestrings),
                operator,
            )
            self.commands.append(command)
        self.words, self.writes, self.heredocs, self.redirect = [], [], [], None
        self.herestrings = []


def split(tokens: list[Token]) -> list[SimpleCommand]:
    """Group tokens into simple commands, recording redirect targets and heredoc markers."""
    splitter = _Splitter()
    for token in tokens:
        if token.operator:
            splitter.operator(token.text)
        else:
            splitter.word(unquote(token.text))
    return splitter.commands


def parse(text: str) -> Script:
    """Lex and split ``text``."""
    tokens, nested, heredocs = lex(text)
    return Script(tuple(split(tokens)), nested, heredocs)
