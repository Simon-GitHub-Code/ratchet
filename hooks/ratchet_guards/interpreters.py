import re
import shlex
from collections.abc import Sequence
from dataclasses import dataclass, field

from ratchet_guards.writes import WRITERS

ADVICE = "A read of a guard path goes in a tool call of its own, apart from an unrelated write."
_NEVER = re.compile(r"(?!)")
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
_ARGS_OPTION = re.compile(r"\{\s*args\s*:")
_ARGV = re.compile(
    r"""(['"])(?:[^'"\n]*/)?git\1(?P<rest>(?:\s*,\s*(?:\{\s*args\s*:\s*)?\[?\s*"""
    r"""(?:'[^'\n]*'|"[^"\n]*"|[\w.]+(?:\([^()]*\))?))*)"""
)
_ARGV_ITEM = re.compile(r"""'(?P<a>[^'\n]*)'|"(?P<b>[^"\n]*)"|(?P<c>[\w.]+(?:\([^()]*\))?)""")
_COMMAND_STRING = re.compile(r"(?:^|[\s;&|(/])git\s")
_PLACEHOLDER = re.compile(r"<[\w./-]*>")
UNKNOWN = "$ratchet_unknown"


@dataclass(frozen=True, slots=True)
class Call:
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
    name: str
    interpreter: Interpreter
    programs: tuple[str, ...]
    reads_stdin: bool
    in_place: bool

    def writes(self, program: str) -> bool:
        return self.in_place or any(call.pattern.search(program) for call in self.interpreter.calls)

    def removes(self, program: str) -> bool:
        return any(call.removes and call.pattern.search(program) for call in self.interpreter.calls)


@dataclass(slots=True)
class _Walk:
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
        bare = not self.operands or self.operands[0] == "-"
        return self.interpreter.stdin and not self.programs and not self.awaiting and bare


def invocation(head: str, args: Sequence[str]) -> Invocation | None:
    interpreter = next((row for row in INTERPRETERS if row.name.fullmatch(head)), None)
    if interpreter is None:
        return None
    walk = _Walk(interpreter)
    for arg in args:
        walk.take(arg)
    return Invocation(head, interpreter, tuple(walk.programs), walk.reads_stdin(), walk.in_place)


def named_paths(program: str) -> list[str]:
    found: list[str] = []
    for literal in _LITERAL.finditer(program):
        text = next(group for group in literal.groups() if group is not None)
        found += [text, *(word for word in _WORD.findall(text) if word.strip("/"))]
    return list(dict.fromkeys(word for word in found if word))


def written_names(program: str) -> list[str]:
    found: list[str] = []
    for literal in _LITERAL.finditer(program):
        text = next(group for group in literal.groups() if group is not None)
        words = [word for word in _WORD.findall(text) if word.strip("/")]
        command = ">" in _PLACEHOLDER.sub("", text) or bool(words and words[0] in WRITERS)
        found += [text, *(words if command else [])]
    return list(dict.fromkeys(word for word in found if word))


def git_commands(program: str) -> list[str]:
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
    return f"an inline {name} program names {path} ({group}) and writes files. {ADVICE}"
