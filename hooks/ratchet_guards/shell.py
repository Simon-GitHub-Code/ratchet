"""A small, conservative bash lexer for the guard: simple commands, redirects and nested scripts.

It is not a shell. It finds what a guard needs and errs towards seeing more commands, never
fewer:

- quotes, backslash escapes and line continuations are honoured, and each word is unquoted
  with ``shlex.split``;
- ``;``, ``&&``, ``||``, ``|``, ``&``, newlines and subshell parentheses separate commands; a
  run of operator characters is read as the operators bash reads in it, so ``|<<E`` is a pipe and
  a heredoc;
- ``>``, ``>>``, ``>|``, ``&>`` and friends record their target word as a write; a numeric
  file descriptor written against the operator (``2>``) is dropped;
- ``$(...)`` and backticks, quoted or not, are lifted out as nested scripts and leave a
  placeholder word behind; a ``case`` pattern's ``)`` does not end a ``$(...)``;
- ``${ cmd; }`` and ``${| cmd; }``, quoted or not, run ``cmd`` in the shell itself (bash 5.3), so
  its commands are read as commands of the line, just before the command that holds them;
- any other ``${...}`` expansion, quoted or not, is read to its closing brace;
- the word after ``<<<`` is kept as a here-string, to be read as the stdin of its command;
- heredoc bodies are lifted out too, so prose in a commit message is never read as commands;
  the operator leaves a numbered marker so a shell fed by the heredoc can be checked. A body
  whose end bash may place elsewhere (a delimiter the lexer may read apart from bash, or a ``<<``
  on a line with ``((``, which may be a shift) is read as commands as well;
- ``#`` at the start of a word begins a comment.

``Script.modelled`` is false on a line that holds a construct the lexer does not model as bash
does: a heredoc, a ``case``, a ``${ cmd; }``, a quote or a substitution left open. Such a line is
split with no subshell (``ratchet_guards.bash`` states the rule). ``Script.closed`` is false when a
quote or a substitution runs to the end of the text, where the lexer may have read commands as
quoted text.
"""

import re
import shlex
from dataclasses import dataclass, field, replace

PLACEHOLDER = "_substitution_"
HEREDOC_MARK = "_heredoc_"
_OPERATOR_CHARS = frozenset(";&|<>\n")
# Why: bash splits a run of operator characters into its operators, longest first.
_OPERATORS = (
    *(";;&", "&>>", "<<<"),
    *(";;", ";&", "&&", "||", "|&", "&>", ">>", ">&", "<&", ">|", "<>"),
    *";&|<>\n",
)
_PARENS = frozenset("()")
_BLANKS = frozenset(" \t\r")
_REDIRECT_CHARS = frozenset("<>")
_DEPTH = {"(": 1, ")": -1}
_CASE_NESTING = {"case": 1, "esac": -1}
# Why: the delimiter is one shell word, so a quoted or escaped blank (`<<'E F'`, `<<E\ F`) is in it.
_HEREDOC = re.compile(
    r"<<(?!<)(?P<strip>-?)[ \t\r]*"
    r"""(?P<delimiter>(?:'[^']*'|"(?:[^"\\]|\\.)*"|\\.|[^ \t\r;&|<>\n()'"\\])*)"""
)
# Why: a delimiter written as one plain word, bare, escaped or wholly quoted, is the one bash reads.
_PLAIN_DELIMITER = re.compile(r"\\?[\w.+-]+|'[\w.+-]*'|\"[\w.+-]*\"")
# Why: inside `((` or `$((`, `<<` is a shift, not a heredoc.
_ARITHMETIC = "(("
_FUNCTION_SUBSTITUTION = re.compile(r"\$\{[ \t\n|]")
_QUOTES = str.maketrans("", "", "'\"")
_DELIMITER_QUOTING = str.maketrans("", "", "'\"\\")
_PIPES = frozenset({"|", "|&"})
_SUBSHELL = "subshell"
_GROUP = "group"
# Why: a `case` pattern ends in a `)` that no `(` opened, and a heredoc the lexer reads apart from
# bash may show it a `)` that bash does not see; either would close a subshell early.
_UNSURE_WORDS = frozenset({"case", "esac"})
# Why: after these reserved words a command starts, so a `(` there opens a subshell.
_OPENERS = frozenset({"{", "!", "if", "then", "else", "elif", "do", "while", "until"})
# Why: around the commands of a `${ cmd; }`, which the splitter reads ahead of the command that
# holds it. The lexer makes no other operator token of these texts.
_HOIST = "${"
_HOISTED = "}"
_BRACES = {"{": 1, "}": -1}


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
    runs only when this one succeeded. ``opens`` counts the subshells that start just before it
    and ``closes`` those that end just after it; when it closes one, ``then`` is the operator
    after the closing parenthesis, which joins the subshell to what follows.
    """

    words: tuple[str, ...]
    writes: tuple[str, ...] = ()
    heredocs: tuple[int, ...] = ()
    piped: bool = False
    herestrings: tuple[str, ...] = ()
    then: str = ""
    opens: int = 0
    closes: int = 0


@dataclass(frozen=True, slots=True)
class Script:
    """A lexed command line: its simple commands and the scripts nested inside it.

    ``modelled`` and ``closed`` say how far the lexer could read it as bash does (see the module).
    """

    commands: tuple[SimpleCommand, ...]
    nested: tuple[str, ...]
    heredocs: tuple[str, ...]
    modelled: bool = True
    closed: bool = True


@dataclass(slots=True)
class _Shared:
    nested: list[str] = field(default_factory=list)
    heredocs: dict[int, str] = field(default_factory=dict)
    allocated: int = 0
    modelled: bool = True
    closed: bool = True


class _Lexer:
    """Reads ``text`` from ``start``; ``closer`` is the ``)`` or ``}`` that ends a substitution."""

    def __init__(self, text: str, start: int, shared: _Shared, *, closer: str = "") -> None:
        self.text = text
        self.at = start
        self.shared = shared
        self.closer = closer
        self.tokens: list[Token] = []
        self.word: list[str] = []
        self.pending: list[tuple[int, str, bool, bool]] = []
        self.depth = 0
        self.cases = 0
        self.braces = 0
        self.closed = False

    def run(self) -> None:
        while self.at < len(self.text):
            char = self.text[self.at]
            if char == ")":
                self._end_word()
            if self._closes(char):
                self.at += 1
                self.closed = True
                return
            self._step(char)
        self._end_word()

    def _closes(self, char: str) -> bool:
        if self.closer == ")":
            return char == ")" and self.depth == 0 and self.cases == 0
        # Why: `}` ends a `${ cmd; }` where a command could start, as it ends a `{ ...; }` group.
        brace = char == "}" and not self.word and self._command_starts()
        return self.closer == "}" and brace and self.braces == 0

    def _command_starts(self) -> bool:
        return not self.tokens or self.tokens[-1].operator

    def _step(self, char: str) -> None:
        if self.text.startswith("$$", self.at):
            # Why: `$$` is the shell's process id, so a quote after it opens no `$'...'` word.
            self.word.append("$$")
            self.at += 2
        elif _FUNCTION_SUBSTITUTION.match(self.text, self.at):
            self._function_substitution()
        elif self.text.startswith("${", self.at):
            self._parameter()
        elif self.text.startswith("$'", self.at):
            self._ansi()
        elif char in "'\"\\`" or self.text.startswith("$(", self.at):
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
            if self._command_starts():
                self.braces = max(self.braces + _BRACES.get(word, 0), 0)
            self.tokens.append(Token(word))
            self.word = []

    def _find(self, needle: str, start: int) -> int:
        """Where ``needle`` next occurs from ``start``, or the end of the text."""
        try:
            return self.text.index(needle, start)
        except ValueError:
            return len(self.text)

    def _single(self) -> None:
        end = self._find("'", self.at + 1)
        self.shared.closed &= end < len(self.text)
        self.word.append(self.text[self.at : end + 1])
        self.at = end + 1

    def _ansi(self) -> None:
        """A ``$'...'`` word, where a backslash escapes the next character, the quote included."""
        end = self.at + 2
        while end < len(self.text) and self.text[end] != "'":
            end += 2 if self.text[end] == "\\" else 1
        self.shared.closed &= end < len(self.text)
        end = min(end + 1, len(self.text))
        self.word.append(self.text[self.at : end])
        self.at = end

    def _parameter(self) -> None:
        """A ``${...}`` expansion read to its closing brace, so a ``)`` or blank in it ends nothing.

        A substitution inside it is still lifted out, so the command it runs is checked.
        """
        self.word.append("${")
        self.at += 2
        while self.at < len(self.text) and self.text[self.at] != "}":
            char = self.text[self.at]
            if self.text.startswith(("$$", "${", "$'"), self.at):
                self._step(char)
            elif char in "'\"\\`" or self.text.startswith("$(", self.at):
                self._quoted(char)
            else:
                self.word.append(char)
                self.at += 1
        self.shared.closed &= self.at < len(self.text)
        self.word.append("}")
        self.at += 1

    def _function_substitution(self) -> None:
        """A ``${ cmd; }`` or ``${| cmd; }``, whose ``cmd`` bash runs in the shell itself.

        It runs before the command that holds it, so its commands go into the line there.
        """
        self.shared.modelled = False
        child = _Lexer(self.text, self.at + 3, self.shared, closer=_HOISTED)
        child.run()
        self.shared.closed &= child.closed
        self.tokens += [Token(_HOIST, operator=True), *child.tokens, Token(_HOISTED, operator=True)]
        self.at = child.at
        self.word.append(PLACEHOLDER)

    def _double(self) -> None:
        self.word.append('"')
        self.at += 1
        while self.at < len(self.text) and self.text[self.at] != '"':
            char = self.text[self.at]
            if char == "\\":
                self.word.append(self.text[self.at : self.at + 2])
                self.at += 2
            elif _FUNCTION_SUBSTITUTION.match(self.text, self.at):
                self._function_substitution()
            elif self.text.startswith("${", self.at):
                self._parameter()
            elif char == "`" or self.text.startswith("$(", self.at):
                self._substitute()
            else:
                self.word.append(char)
                self.at += 1
        self.shared.closed &= self.at < len(self.text)
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
            self.shared.closed &= end < len(self.text)
            self.shared.nested.append(self.text[self.at + 1 : end])
            self.at = end + 1
        else:
            child = _Lexer(self.text, self.at + 2, self.shared, closer=")")
            child.run()
            self.shared.closed &= child.closed
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
        operator = next(op for op in _OPERATORS if self.text.startswith(op, self.at))
        self.at += len(operator)
        if operator[0] in _REDIRECT_CHARS and "".join(self.word).isdigit():
            self.word = []
        self._end_word()
        self.tokens.append(Token(operator, operator=True))
        if operator == "\n":
            self._read_heredoc_bodies()

    def _heredoc(self, heredoc: re.Match[str]) -> None:
        self._end_word()
        self.shared.modelled = False
        line = self.text[self.text.rfind("\n", 0, self.at) + 1 : self.at]
        written = heredoc.group("delimiter")
        sure = _PLAIN_DELIMITER.fullmatch(written) is not None and _ARITHMETIC not in line
        self.at = heredoc.end()
        strip_tabs = bool(heredoc.group("strip"))
        delimiter = written.translate(_DELIMITER_QUOTING)
        index = self.shared.allocated
        self.shared.allocated += 1
        self.pending.append((index, delimiter, strip_tabs, sure))
        self.tokens.append(Token("<<", operator=True))
        self.tokens.append(Token(f"{HEREDOC_MARK}{index}"))

    def _read_heredoc_bodies(self) -> None:
        for index, delimiter, strip_tabs, sure in self.pending:
            body: list[str] = []
            while self.at < len(self.text):
                end = self._find("\n", self.at)
                line = self.text[self.at : end]
                self.at = end + 1
                if (line.lstrip("\t") if strip_tabs else line) == delimiter:
                    break
                body.append(line)
            self.shared.heredocs[index] = "\n".join(body)
            if not sure:
                # Why: bash may end this body elsewhere, so its lines may be commands too.
                reader = _Lexer(self.shared.heredocs[index] + "\n", 0, self.shared)
                reader.run()
                self.tokens += reader.tokens
        self.pending = []


def lex(text: str) -> tuple[list[Token], tuple[str, ...], tuple[str, ...]]:
    """Tokens of ``text`` plus the nested scripts and heredoc bodies lifted out of it."""
    tokens, shared = _lex(text)
    bodies = tuple(shared.heredocs.get(index, "") for index in range(shared.allocated))
    return tokens, tuple(shared.nested), bodies


def _lex(text: str) -> tuple[list[Token], _Shared]:
    shared = _Shared()
    lexer = _Lexer(text + "\n", 0, shared)
    lexer.run()
    return lexer.tokens, shared


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
    """Groups tokens into simple commands and tells a subshell's parentheses from the others.

    A ``(`` where a command starts, alone or after a reserved word such as ``{`` or ``then``,
    opens a subshell when ``subshells`` is set. One after another word (a definition, an array,
    a process substitution) does not, nor does the ``)`` that closes
    it. Without ``subshells`` no parenthesis is read as a subshell, which keeps a ``cd`` inside
    one in force for what follows: the reading that protects. The commands of a ``${ cmd; }``
    are set down ahead of the command whose words hold it, which waits in ``held``.
    """

    commands: list[SimpleCommand] = field(default_factory=list)
    words: list[str] = field(default_factory=list)
    writes: list[str] = field(default_factory=list)
    heredocs: list[int] = field(default_factory=list)
    herestrings: list[str] = field(default_factory=list)
    redirect: str | None = None
    subshells: bool = True
    nesting: list[str] = field(default_factory=list)
    opens: int = 0
    closed: bool = False
    held: list[tuple[list[str], list[str], list[int], list[str], str | None]] = field(
        default_factory=list
    )

    def word(self, word: str) -> None:
        redirect, self.redirect = self.redirect, None
        if redirect is None:
            self.closed = False
            self.words.append(word)
        elif redirect == "<<<":
            self.herestrings.append(word)
        elif redirect.startswith("<<") and word.startswith(HEREDOC_MARK):
            self.heredocs.append(int(word.removeprefix(HEREDOC_MARK)))
        elif ">" in redirect and not _is_fd_target(redirect, word):
            self.writes.append(word)

    def operator(self, operator: str) -> None:
        if operator == _HOIST:
            self.held.append(
                (self.words, self.writes, self.heredocs, self.herestrings, self.redirect)
            )
            self.words, self.writes, self.heredocs, self.herestrings = [], [], [], []
            self.redirect = None
            return
        if operator == _HOISTED:
            self._end(";")
            self.words, self.writes, self.heredocs, self.herestrings, self.redirect = (
                self.held.pop()
            )
            return
        if "<" in operator or ">" in operator:
            self.redirect = operator
            return
        starts = not (self.writes or self.redirect) and _OPENERS.issuperset(self.words)
        if operator == "(" and self.subshells and starts:
            self.nesting.append(_SUBSHELL)
            self.opens += 1
            return
        if operator == ")" and self.nesting[-1:] == [_SUBSHELL]:
            self.nesting.pop()
            self._close()
            return
        if operator == "(":
            self.nesting.append(_GROUP)
        elif operator == ")" and self.nesting[-1:] == [_GROUP]:
            self.nesting.pop()
        if self.closed:
            last = self.commands[-1]
            self.commands[-1] = replace(last, then=operator, piped=operator in _PIPES)
            self.closed = False
        self._end(operator)

    def _close(self) -> None:
        if self.words or self.writes:
            self._end(")", closes=1)
        elif self.opens:
            self.opens -= 1
            return
        else:
            last = self.commands[-1]
            self.commands[-1] = replace(last, closes=last.closes + 1)
        self.closed = True

    def _end(self, operator: str, closes: int = 0) -> None:
        if self.words or self.writes:
            command = SimpleCommand(
                tuple(self.words),
                tuple(self.writes),
                tuple(self.heredocs),
                operator in _PIPES,
                tuple(self.herestrings),
                operator,
                self.opens,
                closes,
            )
            self.commands.append(command)
            self.opens = 0
        self.words, self.writes, self.heredocs, self.redirect = [], [], [], None
        self.herestrings = []


def split(tokens: list[Token], *, subshells: bool = True) -> list[SimpleCommand]:
    """Group tokens into simple commands, recording redirect targets and heredoc markers."""
    splitter = _Splitter(subshells=subshells)
    for token in tokens:
        if token.operator:
            splitter.operator(token.text)
        else:
            splitter.word(unquote(token.text))
    return splitter.commands


def parse(text: str, *, subshells: bool = True) -> Script:
    """Lex and split ``text``; a subshell is read only on a line the lexer models."""
    tokens, shared = _lex(text)
    words = {unquote(token.text) for token in tokens if not token.operator}
    modelled = shared.modelled and shared.closed and not words & _UNSURE_WORDS
    commands = split(tokens, subshells=subshells and modelled)
    bodies = tuple(shared.heredocs.get(index, "") for index in range(shared.allocated))
    return Script(tuple(commands), tuple(shared.nested), bodies, modelled, shared.closed)
