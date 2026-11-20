import re
import shlex
from dataclasses import dataclass, field, replace

PLACEHOLDER = "_substitution_"
HEREDOC_MARK = "_heredoc_"
_OPERATOR_CHARS = frozenset(";&|<>\n")
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
_HEREDOC = re.compile(
    r"<<(?!<)(?P<strip>-?)[ \t\r]*"
    r"""(?P<delimiter>(?:'[^']*'|"(?:[^"\\]|\\.)*"|\\.|[^ \t\r;&|<>\n()'"\\])*)"""
)
_PLAIN_DELIMITER = re.compile(r"\\?[\w.+-]+|'[\w.+-]*'|\"[\w.+-]*\"")
_ARITHMETIC = "(("
_FUNCTION_SUBSTITUTION = re.compile(r"\$\{[ \t\n|]")
_BACKTICK_ESCAPE = re.compile(r"\\([$`\\])")
_CONTINUED = re.compile(r"(?<!\\)(?:\\\\)*\\$")
_EXPANDING = {"": re.compile(r"[\\`]|\$[({]"), '"': re.compile(r"[\\`\"]|\$[({]")}
_SAME_EITHER_WAY = re.compile(r"'[^'\]\[{}\"\\`$]*'")
_BRACKETS = {"[": 1, "]": -1}
_EXPANSIONS = {"${": "}", "$[": "]"}
_QUOTES = str.maketrans("", "", "'\"")
_DELIMITER_QUOTING = str.maketrans("", "", "'\"\\")
_PIPES = frozenset({"|", "|&"})
_SUBSHELL = "subshell"
_GROUP = "group"
_UNSURE_WORDS = frozenset({"case", "esac"})
_OPENERS = frozenset({"{", "!", "if", "then", "else", "elif", "do", "while", "until"})
_HOIST = "${"
_HOISTED = "}"
_BRACES = {"{": 1, "}": -1}


@dataclass(frozen=True, slots=True)
class Token:
    text: str
    operator: bool = False


@dataclass(frozen=True, slots=True)
class SimpleCommand:
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
    commands: tuple[SimpleCommand, ...]
    nested: tuple[str, ...]
    heredocs: tuple[str, ...]
    modelled: bool = True
    closed: bool = True
    expanded: frozenset[int] = frozenset()


@dataclass(slots=True)
class _Shared:
    nested: list[str] = field(default_factory=list)
    expanded: set[int] = field(default_factory=set)
    heredocs: dict[int, str] = field(default_factory=dict)
    allocated: int = 0
    modelled: bool = True
    closed: bool = True


class _Lexer:
    def __init__(self, text: str, start: int, shared: _Shared, *, closer: str = "") -> None:
        self.text = text
        self.at = start
        self.shared = shared
        self.closer = closer
        self.tokens: list[Token] = []
        self.word: list[str] = []
        self.pending: list[tuple[int, str, bool, bool, int, bool]] = []
        self.depth = 0
        self.cases = 0
        self.braces = 0
        self.doubled = 0
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
        if self.closer != "}" or char != "}" or self.word or self.braces:
            return False
        self.shared.closed &= self._command_starts()
        return True

    def _command_starts(self) -> bool:
        return not self.tokens or self.tokens[-1].operator

    def _step(self, char: str) -> None:
        if self.text.startswith("$$", self.at):
            self.word.append("$$")
            self.at += 2
        elif _FUNCTION_SUBSTITUTION.match(self.text, self.at):
            self._function_substitution()
        elif (opener := self.text[self.at : self.at + 2]) in _EXPANSIONS:
            self._expansion(opener, _EXPANSIONS[opener])
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
        end = self.at + 2
        while end < len(self.text) and self.text[end] != "'":
            end += 2 if self.text[end] == "\\" else 1
        if "\\'" in self.text[self.at + 2 : end]:
            self.shared.closed = False
            self.word.append("$")
            self.at += 1
            self._single()
            return
        self.shared.closed &= end < len(self.text)
        end = min(end + 1, len(self.text))
        self.word.append(self.text[self.at : end])
        self.at = end

    def _expansion(self, opener: str, closer: str) -> None:
        self.word.append(opener)
        self.at += len(opener)
        depth = 0
        while self.at < len(self.text) and (self.text[self.at] != closer or depth):
            char = self.text[self.at]
            if self.doubled and char == "'":
                self._single_in_double()
            elif self.text.startswith(("$$", "${", "$["), self.at) or (
                not self.doubled and self.text.startswith("$'", self.at)
            ):
                self._step(char)
            elif char in "'\"\\`" or self.text.startswith("$(", self.at):
                self._quoted(char)
            else:
                depth += _BRACKETS.get(char, 0) if closer == "]" else 0
                self.word.append(char)
                self.at += 1
        self.shared.closed &= self.at < len(self.text)
        self.word.append(closer)
        self.at += 1

    def _single_in_double(self) -> None:
        same = _SAME_EITHER_WAY.match(self.text, self.at)
        self.shared.closed &= same is not None
        end = self.at + 1 if same is None else same.end()
        self.word.append(self.text[self.at : end])
        self.at = end

    def _function_substitution(self) -> None:
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
        self.expand('"')
        self.shared.closed &= self.at < len(self.text)
        self.word.append('"')
        self.at += 1

    def expand(self, until: str = "") -> None:
        self.doubled += 1
        stop = _EXPANDING[until]
        while self.at < len(self.text) and self.text[self.at] != until:
            char = self.text[self.at]
            if char == "\\":
                self.word.append(self.text[self.at : self.at + 2])
                self.at += 2
            elif _FUNCTION_SUBSTITUTION.match(self.text, self.at):
                self._function_substitution()
            elif self.text.startswith("${", self.at):
                self._expansion("${", "}")
            elif char == "`" or self.text.startswith("$(", self.at):
                self._substitute()
            else:
                found = stop.search(self.text, self.at + 1)
                end = len(self.text) if found is None else found.start()
                self.word.append(self.text[self.at : end])
                self.at = end
        self.doubled -= 1

    def _escape(self) -> None:
        pair = self.text[self.at : self.at + 2]
        if pair != "\\\n":
            self.word.append(pair)
        self.at += 2

    def _substitute(self) -> None:
        if self.text[self.at] == "`":
            end = self.at + 1
            while end < len(self.text) and self.text[end] != "`":
                end += 2 if self.text[end] == "\\" else 1
            end = min(end, len(self.text))
            self.shared.closed &= end < len(self.text)
            self.shared.nested.append(_BACKTICK_ESCAPE.sub(r"\1", self.text[self.at + 1 : end]))
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
        self.tokens.append(Token("<<", operator=True))
        self.tokens.append(Token(f"{HEREDOC_MARK}{index}"))
        self.pending.append(
            (index, delimiter, strip_tabs, sure, len(self.tokens), delimiter == written)
        )

    def _read_heredoc_bodies(self) -> None:
        hoisted: list[tuple[int, list[Token]]] = []
        for index, delimiter, strip_tabs, sure, after, expands in self.pending:
            body: list[str] = []
            while self.at < len(self.text):
                lines = [self._line()]
                while expands and _CONTINUED.search(lines[-1]) and self.at < len(self.text):
                    lines[-1] = lines[-1][:-1]
                    lines.append(self._line())
                line = "".join(lines)
                if (line.lstrip("\t") if strip_tabs else line) == delimiter:
                    break
                body.append(line)
            text = "\n".join(body)
            self.shared.heredocs[index] = _BACKTICK_ESCAPE.sub(r"\1", text) if expands else text
            if expands:
                first = len(self.shared.nested)
                reader = _Lexer(text, 0, self.shared)
                reader.expand()
                self.shared.expanded.update(range(first, len(self.shared.nested)))
                hoisted += [(after, reader.tokens)] if reader.tokens else []
            if not sure:
                reader = _Lexer(text + "\n", 0, self.shared)
                reader.run()
                self.tokens += reader.tokens
        if hoisted:
            start = hoisted[0][0]
            self.tokens[start:] = _spliced(self.tokens, hoisted, start)
        self.pending = []

    def _line(self) -> str:
        end = self._find("\n", self.at)
        line = self.text[self.at : end]
        self.at = end + 1
        return line


def _spliced(tokens: list[Token], hoisted: list[tuple[int, list[Token]]], at: int) -> list[Token]:
    spliced: list[Token] = []
    for after, inserted in hoisted:
        spliced += tokens[at:after]
        spliced += inserted
        at = after
    return spliced + tokens[at:]


def lex(text: str) -> tuple[list[Token], tuple[str, ...], tuple[str, ...]]:
    tokens, shared = _lex(text)
    bodies = tuple(shared.heredocs.get(index, "") for index in range(shared.allocated))
    return tokens, tuple(shared.nested), bodies


def _lex(text: str) -> tuple[list[Token], _Shared]:
    shared = _Shared()
    lexer = _Lexer(text + "\n", 0, shared)
    lexer.run()
    return lexer.tokens, shared


def unquote(word: str) -> str:
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
    splitter = _Splitter(subshells=subshells)
    for token in tokens:
        if token.operator:
            splitter.operator(token.text)
        else:
            splitter.word(unquote(token.text))
    return splitter.commands


def parse(text: str, *, subshells: bool = True) -> Script:
    tokens, shared = _lex(text)
    words = {unquote(token.text) for token in tokens if not token.operator}
    modelled = shared.modelled and shared.closed and not words & _UNSURE_WORDS
    commands = split(tokens, subshells=subshells and modelled)
    bodies = tuple(shared.heredocs.get(index, "") for index in range(shared.allocated))
    nested = tuple(shared.nested)
    expanded = frozenset(shared.expanded)
    return Script(tuple(commands), nested, bodies, modelled, shared.closed, expanded)
