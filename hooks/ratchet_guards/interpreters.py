"""Interpreters run on an inline program, and the write calls such a program can hold.

``python3 -c`` or ``python3 - <<'EOF'`` changes a file with no ``cp`` or ``sed`` in the
command line for a check to see. The program text is in the command, though, so a program
that holds a quoted string naming a guard path and also a write call is treated as a write to
that path. Each interpreter is a row of ``INTERPRETERS``: the name it runs under, the
argument that introduces a program, and the table of write calls of its language. A program
read from stdin (a heredoc, a here-string, a pipe) is the caller's to find; this module says
only that the interpreter reads one.

A program that runs ``git`` is read for the command it runs (``git_commands``), so that a
ref-moving or hook-skipping command is refused the same way through ``subprocess`` or
``child_process`` as at the prompt.

Out of reach, left to diff detection: a script file run by path, a path built at run time,
a program fetched or decoded before it runs.
"""

import re
import shlex
from collections.abc import Sequence
from dataclasses import dataclass, field

ADVICE = (
    "Write the guard path with the Edit or Write tool so the owner is asked (one Write for "
    "many changes in one file, since each Edit asks), and split a read of a guard path from "
    "an unrelated write."
)
_NEVER = re.compile(r"(?!)")
# One call argument: quoted strings and balanced parentheses (two deep) hide their commas.
_ARG = r"""(?:'[^'\n]*'|"[^"\n]*"|[^,()'"]|\((?:[^()]|\([^()]*\))*\))*"""
_PYTHON_MODE = r"""(?:'[rbtU]*[wax+][rbtU]*'|"[rbtU]*[wax+][rbtU]*")"""
_NODE_MODE = r"""['"][rwaxs+]*[wax+][rwaxs+]*['"]"""
_RUBY_MODE = r"""['"][rbt]*[wax+][rbt]*['"]"""
_PERL_SIMPLE = "acilnpsStTuUwWX0-9"
_RUBY_SIMPLE = "acdlnpsSvwWx0-9"
_LITERAL = re.compile(
    r"""'''(?P<a>.*?)'''|\"\"\"(?P<b>.*?)\"\"\"|'(?P<c>(?:\\.|[^'\\\n])*)'"""
    r"""|"(?P<d>(?:\\.|[^"\\\n])*)"|`(?P<e>(?:\\.|[^`\\])*)`""",
    re.DOTALL,
)
_WORD = re.compile(r"""[^\s'"`<>|;&(),=]+""")
# `'git', 'branch', '-f', name`: the quoted program name and the arguments that follow, in an
# argument list, a call's argument list or a bare run of arguments, as every language here
# spells an argv. A bare name or call stands for an argument the program computes.
_ARGS_OPTION = re.compile(r"\{\s*args\s*:")
_ARGV = re.compile(
    r"""(['"])(?:[^'"\n]*/)?git\1(?P<rest>(?:\s*,\s*(?:\{\s*args\s*:\s*)?\[?\s*"""
    r"""(?:'[^'\n]*'|"[^"\n]*"|[\w.]+(?:\([^()]*\))?))*)"""
)
_ARGV_ITEM = re.compile(r"""'(?P<a>[^'\n]*)'|"(?P<b>[^"\n]*)"|(?P<c>[\w.]+(?:\([^()]*\))?)""")
_COMMAND_STRING = re.compile(r"(?:^|[\s;&|(/])git\s")
UNKNOWN = "$ratchet_unknown"


@dataclass(frozen=True, slots=True)
class Call:
    """A write call as it appears in program text; ``removes`` if it deletes or moves away."""

    pattern: re.Pattern[str]
    removes: bool = False


def _calls(*patterns: str, removes: bool = False) -> tuple[Call, ...]:
    return tuple(Call(re.compile(pattern), removes) for pattern in patterns)


PYTHON_CALLS = _calls(
    rf"\bopen\s*\(\s*{_ARG},\s*(?:mode\s*=\s*)?{_PYTHON_MODE}",
    rf"\bopen\s*\((?:\s*{_ARG},)*\s*mode\s*=\s*{_PYTHON_MODE}",
    rf"\.open\s*\(\s*(?:mode\s*=\s*)?{_PYTHON_MODE}",
    r"\.(?:write_text|write_bytes|touch|chmod|truncate)\s*\(",
    r"\bos\.(?:chmod|chown|truncate)\b",
    r"\bO_(?:WRONLY|RDWR|CREAT|APPEND|TRUNC)\b",
    r"\binplace\s*=\s*(?:True|1)\b",
    r"\bshutil\.",
    r"\bsubprocess\b",
    r"\bos\.(?:system|popen|exec\w*|spawn\w*)\b",
) + _calls(
    r"\.(?:unlink|rename|rmdir)\s*\(",
    rf"\.replace\s*\(\s*{_ARG}\)",
    r"\bos\.(?:remove|unlink|rename|replace|rmdir|removedirs|renames)\b",
    r"\bshutil\.(?:rmtree|move)\b",
    removes=True,
)
NODE_CALLS = _calls(
    r"\b(?:writeFile|appendFile|createWriteStream|copyFile|chmod|truncate)\w*",
    rf"\bopen(?:Sync)?\s*\(\s*{_ARG},\s*{_NODE_MODE}",
    r"\bchild_process\b",
    r"\bDeno\.(?:write|copy|chmod|truncate|Command|run)\w*",
    r"\bBun\.(?:write|spawn)\w*",
) + _calls(
    r"\b(?:rm|rmdir|unlink|rename)(?:Sync)?\s*\(",
    r"\bDeno\.(?:remove|rename)\w*",
    removes=True,
)
PERL_CALLS = _calls(
    r"""\bopen\b[^;\n]*['"]\s*(?:\+[<>]|>)""",
    r"\b(?:system|exec|qx|sysopen|chmod|chown|truncate|symlink|link)\b|`",
    r"\b(?:File::Copy|File::Path|File::Slurp|Path::Tiny)\b",
) + _calls(r"\b(?:unlink|rename|rmdir|rmtree)\b", removes=True)
RUBY_CALLS = _calls(
    rf"\b(?:open|File\.open|File\.new|IO\.new)\s*\(?\s*{_ARG},\s*{_RUBY_MODE}",
    r"\b(?:File|IO)\.(?:write|binwrite|chmod|chown|truncate|symlink|link|utime)\b",
    r"\bFile::(?:WRONLY|RDWR|CREAT|APPEND|TRUNC)\b",
    r"\b(?:system|exec|spawn|fork|Open3|FileUtils)\b|`|%x",
) + _calls(
    r"\b(?:File|Dir|FileUtils|Pathname)\.(?:delete|unlink|rename|rmdir|rm\w*|mv|remove\w*)\b",
    r"\.(?:unlink|rename)\b",
    removes=True,
)


@dataclass(frozen=True, slots=True)
class Interpreter:
    """One interpreter: its names, how it is told to run an inline program, what that can do.

    ``program`` matches an argument that introduces a program; its ``code`` group is a
    program attached to it, and when empty the next operand is the program. ``value_flags``
    are options that take the next argument as a value. ``in_place`` matches a flag that
    rewrites the files named on the command line. ``stdin`` is whether the interpreter runs
    a program it is fed when it is given none.
    """

    name: re.Pattern[str]
    program: re.Pattern[str]
    calls: tuple[Call, ...]
    value_flags: frozenset[str] = frozenset()
    in_place: re.Pattern[str] = _NEVER
    stdin: bool = True


INTERPRETERS = (
    Interpreter(
        name=re.compile(r"python(?:\d+(?:\.\d+)*)?"),
        program=re.compile(r"-[BbdEiIOPqRsSuvx]*c(?P<code>.*)", re.DOTALL),
        calls=PYTHON_CALLS,
        value_flags=frozenset({"-W", "-X"}),
    ),
    Interpreter(
        name=re.compile(r"node(?:js)?"),
        program=re.compile(r"(?:-[a-z]*[ep]|--eval|--print)(?:=(?P<code>.*))?", re.DOTALL),
        calls=NODE_CALLS,
        value_flags=frozenset(
            {"-r", "--require", "--import", "--loader", "-C", "--conditions", "--input-type"}
        ),
    ),
    Interpreter(
        name=re.compile(r"bun"),
        program=re.compile(r"(?:-[ep]|--eval|--print)(?:=(?P<code>.*))?", re.DOTALL),
        calls=NODE_CALLS,
        stdin=False,
    ),
    Interpreter(
        name=re.compile(r"deno"),
        program=re.compile(r"(?P<code>)eval"),
        calls=NODE_CALLS,
        value_flags=frozenset({"--ext", "--config", "-c", "--import-map"}),
        stdin=False,
    ),
    Interpreter(
        name=re.compile(r"perl"),
        program=re.compile(rf"-[{_PERL_SIMPLE}]*[eE](?P<code>.*)", re.DOTALL),
        calls=PERL_CALLS,
        value_flags=frozenset({"-I"}),
        in_place=re.compile(rf"-[{_PERL_SIMPLE}]*i"),
    ),
    Interpreter(
        name=re.compile(r"ruby"),
        program=re.compile(rf"-[{_RUBY_SIMPLE}i]*e(?P<code>.*)", re.DOTALL),
        calls=RUBY_CALLS,
        value_flags=frozenset({"-I", "-r"}),
        in_place=re.compile(rf"-[{_RUBY_SIMPLE}]*i"),
    ),
)


@dataclass(frozen=True, slots=True)
class Invocation:
    """An interpreter command line: the inline programs it carries, or that it reads stdin."""

    name: str
    interpreter: Interpreter
    programs: tuple[str, ...]
    reads_stdin: bool
    in_place: bool

    def writes(self, program: str) -> bool:
        """Whether ``program`` holds a write call, or the command rewrites files in place."""
        return self.in_place or any(call.pattern.search(program) for call in self.interpreter.calls)

    def removes(self, program: str) -> bool:
        """Whether ``program`` holds a call that deletes or moves a path away."""
        return any(call.removes and call.pattern.search(program) for call in self.interpreter.calls)


@dataclass(slots=True)
class _Walk:
    """The arguments of one interpreter command, taken in order."""

    interpreter: Interpreter
    programs: list[str] = field(default_factory=list)
    operands: list[str] = field(default_factory=list)
    in_place: bool = False
    awaiting: bool = False
    skipping: bool = False

    def take(self, arg: str) -> None:
        introduces = self.interpreter.program.fullmatch(arg)
        if self.skipping:
            self.skipping = False
        elif arg in self.interpreter.value_flags:
            self.skipping = True
        elif introduces:
            code = introduces.group("code")
            self.programs += [code] if code else []
            self.awaiting = self.awaiting or not code
        elif arg.startswith("-") and arg != "-":
            self.in_place = self.in_place or self.interpreter.in_place.match(arg) is not None
        elif self.awaiting:
            self.programs.append(arg)
            self.awaiting = False
        else:
            self.operands.append(arg)

    def reads_stdin(self) -> bool:
        """Whether no program was given and the interpreter runs the one on its stdin."""
        bare = not self.operands or self.operands[0] == "-"
        return self.interpreter.stdin and not self.programs and not self.awaiting and bare


def invocation(head: str, args: Sequence[str]) -> Invocation | None:
    """What ``head args`` runs, when ``head`` names an interpreter; otherwise ``None``."""
    interpreter = next((row for row in INTERPRETERS if row.name.fullmatch(head)), None)
    if interpreter is None:
        return None
    walk = _Walk(interpreter)
    for arg in args:
        walk.take(arg)
    return Invocation(head, interpreter, tuple(walk.programs), walk.reads_stdin(), walk.in_place)


def named_paths(program: str) -> list[str]:
    """The words a program's string literals could name a path with, each once.

    A literal is a candidate whole, and for a command string (``"echo x > DESIGN.md"``) so is
    each word of it, with redirection and quoting characters cut away. A word of slashes alone
    (``a / b``, ``root // x``) is an operator in the text around it, not the root directory,
    which a literal that is exactly ``/`` still names.
    """
    found: list[str] = []
    for literal in _LITERAL.finditer(program):
        text = next(group for group in literal.groups() if group is not None)
        found += [text, *(word for word in _WORD.findall(text) if word.strip("/"))]
    return list(dict.fromkeys(word for word in found if word))


def git_commands(program: str) -> list[str]:
    """Shell command lines in ``program`` that run ``git``, for the shell rules to judge.

    A string literal holding ``git`` and a space is a command line (``os.system("git ...")``),
    and an argv that starts with the literal ``git`` is joined into one, with ``UNKNOWN`` where
    an argument is not a literal. Nothing is run or evaluated.
    """
    commands = [
        text
        for literal in _LITERAL.finditer(program)
        for text in [next(group for group in literal.groups() if group is not None)]
        if _COMMAND_STRING.search(text)
    ]
    for argv in _ARGV.finditer(program):
        words = [
            next((group for group in (item["a"], item["b"]) if group is not None), UNKNOWN)
            for item in _ARGV_ITEM.finditer(_ARGS_OPTION.sub("", argv["rest"]))
        ]
        if words:
            commands.append(shlex.join(["git", *words]).replace(f"'{UNKNOWN}'", UNKNOWN))
    return commands


def detail(name: str, path: str, group: str) -> str:
    """The sentence a deny carries: what matched, then how to do it the owner's way."""
    return f"an inline {name} program names {path} ({group}) and writes files. {ADVICE}"
