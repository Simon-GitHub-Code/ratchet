import re
from collections.abc import Callable, Collection, Iterable, Iterator, Mapping, Sequence
from dataclasses import dataclass, field, replace
from itertools import dropwhile, islice, pairwise, product
from pathlib import Path, PurePosixPath

from ratchet_guards import environment, gh, git, interpreters, tickets, writes
from ratchet_guards.context import Context, Guard
from ratchet_guards.options import Options, Spec, scan
from ratchet_guards.shell import PLACEHOLDER, Script, SimpleCommand, parse
from ratchet_guards.verdict import Verdict

MAX_DEPTH = 8
SHELLS = frozenset({"bash", "sh", "zsh", "dash", "ksh"})
WRAPPERS = frozenset(
    {"sudo", "doas", "env", "nohup", "time", "command", "builtin", "exec", "nice", "ionice"}
    | {"timeout", "stdbuf", "xargs", "unbuffer", "setsid", "chronic", "watch"}
    | {"uv", "uvx", "poetry"}
)
RESERVED = frozenset(
    {"{", "}", "!", "if", "then", "else", "elif", "fi", "do", "done"} | {"while", "until"}
)
_CHANGES_DIRECTORY = {
    "env": ("C", "--chdir"),
    "sudo": ("D", "--chdir"),
    "uv": ("", "--directory"),
    "uvx": ("", "--directory"),
    "poetry": ("C", "--directory"),
}
_EXEC_FLAGS = frozenset({"-exec", "-execdir", "-ok", "-okdir"})
_EXEC_ENDS = frozenset({";", "+"})
_NESTED_SETTINGS = (
    "--settings",
    "--setting-sources",
    "--disable-plugin",
    "--no-plugins",
    "--disable-all-hooks",
    "--bare",
)
_DISABLE_ALL_HOOKS = "disableallhooks"
_EVAL_TEMPLATE = frozenset({("plugin", "eval", "init"), ("plugins", "eval", "init")})
_EVAL_DIR = "--eval"
_PLUGIN_REMOVALS = frozenset({"disable", "uninstall", "remove"})
_REMOVALS: dict[str, tuple[frozenset[str], str]] = {
    "plugin": (_PLUGIN_REMOVALS, "claude plugin"),
    "plugins": (_PLUGIN_REMOVALS, "claude plugin"),
    "marketplace": (frozenset({"remove", "rm"}), "claude plugin marketplace"),
}
_HARNESS_REMOVALS: dict[str, dict[str, tuple[frozenset[str], str]]] = {
    "grok": {
        "plugin": (_PLUGIN_REMOVALS | {"rm"}, "grok plugin"),
        "marketplace": (frozenset({"remove", "rm"}), "grok plugin marketplace"),
    },
    "codex": {
        "plugin": (frozenset({"remove", "rm"}), "codex plugin"),
        "marketplace": (frozenset({"remove", "rm"}), "codex plugin marketplace"),
    },
    "agy": {"plugin": (_PLUGIN_REMOVALS, "agy plugin")},
}
_PI_REMOVALS = frozenset({"remove", "uninstall"})
MAX_STATES = 64
_PICKS = 4 * MAX_STATES
_DIRECTORY_MOVERS = frozenset({"cd", "pushd", "popd"})
_PUSHD = "pushd"
_MOVERS = _DIRECTORY_MOVERS | {"git"}
_UNREADABLE_STARTS = ("-", "+", "~")
_UNREADABLE_MARKS = ("$", "*", "?", "[", PLACEHOLDER)
_REDIRECT = "redirects into"
_POPD = "popd"
_CDPATH = "CDPATH"
_HOME = "HOME"
_FUNCTION = "function"
_REDEFINERS = frozenset({"function", "alias", "enable", "shopt", "eval", "source", ".", "trap"})
_DEFINITION = "("
_AND = "&&"
_UNSURE_AFTER = frozenset({"||", "|", "|&"})
_APART = frozenset({"|", "|&", "&"})
_VARIABLE = re.compile(r"\$(\w+)|\$\{(\w+)\}")
_DYNAMIC = ("$", "`", PLACEHOLDER)
_BRANCHING = RESERVED - {"{", "}", "!"}
_FORGETTERS = frozenset({"unset", "for", "select"})
_KEEPS = frozenset({";", "&&", "||", "\n", ""})
_CROWDED = Verdict("unreadable", "the command line moves between too many directories")
_GIT_WORD = re.compile(r"(?<![\w.-])git(?![\w.-])")
_SWITCH_WORD = re.compile(r"(?<![\w-])(?:switch|checkout|symbolic-ref|bisect)(?![\w-])")
_XARGS = "xargs"
_PATH_REFUSALS = frozenset({"guard-path-write", "guard-path-default", "guard-path-unseen-switch"})

_UNCLOSED = Verdict(
    "unreadable", "the guard cannot tell where a quote or a substitution on the command line ends"
)
_SHELL = Spec("oO", frozenset({"--rcfile", "--init-file"}))


def _name(word: str) -> str:
    return PurePosixPath(word).name


def _assignments(words: Sequence[str]) -> tuple[list[str], list[str]]:
    index = 0
    while index < len(words):
        if words[index] == _FUNCTION:
            index += 2
        elif words[index] in RESERVED or environment.is_assignment(words[index]):
            index += 1
        else:
            break
    return list(words[:index]), list(words[index:])


def _assigned(words: Iterable[str]) -> dict[str, str]:
    return {name: word for word in words if (name := environment.assigned(word)) is not None}


def _variables(names: Mapping[str, str]) -> list[Verdict]:
    verdicts: list[Verdict] = []
    for name, word in names.items():
        if name in environment.HOOK_SKIPS:
            verdicts.append(Verdict("skip-hooks", word))
        elif name.startswith(environment.CONFIG_PREFIX):
            verdicts.append(Verdict("hooks-path", word))
    return verdicts


def _distinct(contexts: Iterable[Context]) -> list[Context]:
    return list(dict.fromkeys(contexts))


def _levelled(contexts: Sequence[Context]) -> list[Context]:
    exported = frozenset(name for context in contexts for name in context.exported)
    unsettled = any(context.unsettled for context in contexts)
    hidden = any(context.hidden_switch for context in contexts)
    return _distinct(
        replace(c, exported=exported, unsettled=unsettled, hidden_switch=hidden) for c in contexts
    )


def _bindings(words: Sequence[str]) -> dict[str, str | None]:
    lead, rest = _assignments(words)
    if not rest:
        return {
            name: _literal(word)
            for word in lead
            if (name := environment.assigned(word)) is not None
        }
    if _name(rest[0]) in _FORGETTERS:
        names = [word for word in rest[1:] if not word.startswith("-")]
        return dict.fromkeys(names if rest[0] == "unset" else names[:1])
    return {name: _literal(word) for name, word in environment.declared(rest).items()}


def _literal(word: str) -> str | None:
    name, _, value = word.partition("=")
    plain = environment.assigned(word) == name
    return value if plain and value and not _dynamic(value) else None


def _dynamic(value: str) -> bool:
    return any(mark in value for mark in _DYNAMIC) or len(value.split()) != 1


def _resolved(target: str, values: Mapping[str, frozenset[str | None]]) -> Iterator[str | None]:
    names = sorted({m.group(1) or m.group(2) for m in _VARIABLE.finditer(target)} & values.keys())
    options = [sorted(values[name], key=lambda value: value or "") for name in names]
    combinations = product(*options)
    for picks in islice(combinations, _PICKS):
        yield _substituted(target, dict(zip(names, picks, strict=True)))
    if next(combinations, None) is not None:
        yield None


def _homed(readings: Iterable[str | None], homes: Collection[str | None]) -> Iterator[str | None]:
    tilded: list[str] = []
    for word in readings:
        tilded += [word] if word and (word == "~" or word.startswith("~/")) else []
        yield word
    yield from (str(PurePosixPath(home, word[2:])) for word in tilded for home in homes if home)


def _substituted(target: str, chosen: Mapping[str, str | None]) -> str:
    def value(match: re.Match[str]) -> str:
        return chosen.get(match.group(1) or match.group(2)) or match.group(0)

    return _VARIABLE.sub(value, target)


def _left(entered: Sequence[Context], inside: Sequence[Context]) -> list[Context]:
    claims = frozenset(b for c in inside for b in (*c.maybe, c.branch) if b is not None)
    unsettled = any(context.unsettled for context in inside)
    return _distinct(
        replace(c, maybe=c.maybe | claims, unsettled=c.unsettled or unsettled) for c in entered
    )


def _redefines(command: SimpleCommand) -> bool:
    lead, rest = _assignments(command.words)
    defines = command.then == _DEFINITION and bool(rest)
    named = [*rest[:1], *(rest[1:] if rest and _name(rest[0]) in WRAPPERS else [])]
    return defines or _FUNCTION in lead or any({w, _name(w)} & _REDEFINERS for w in named)


_Read = tuple[Script, bool, list[tuple[str, str]]]


def _read(text: str) -> _Read:
    parsed = _followed(text)
    switching = any(map(_switches, parsed.nested))
    found = enumerate(parsed.nested)
    nested = list(dict.fromkeys((script, parsed.expanded.get(i, "")) for i, script in found))
    return parsed, switching, nested


def _followed(text: str) -> Script:
    parsed = parse(text)
    if parsed.modelled and any(map(_redefines, parsed.commands)):
        return replace(parse(text, subshells=False), modelled=False)
    return parsed


def _anywhere(context: Context, lost: Context, seen: Sequence[Context]) -> list[Context]:
    return [context, lost, *(context.moved_to(place.cwd) for place in seen)]


def _assigning(
    values: Mapping[str, frozenset[str | None]],
    command: SimpleCommand,
    *,
    position: int,
    previous: str,
) -> dict[str, frozenset[str | None]]:
    if command.then == ")" or command.closes:
        return {}
    lead, _ = _assignments(command.words)
    kept = (
        position == 1
        and command.then in _KEEPS
        and previous not in _UNSURE_AFTER
        and not any(word in _BRANCHING for word in lead)
    )
    after = dict(values)
    for name, value in _bindings(command.words).items():
        new = frozenset({value})
        after[name] = new if kept else after.get(name, frozenset({None})) | new
    return after


def _unreadable(target: str) -> bool:
    return target.startswith(_UNREADABLE_STARTS) or any(m in target for m in _UNREADABLE_MARKS)


def _shell_options(args: Sequence[str]) -> Options:
    return scan([f"-{arg[1:]}" if arg.startswith("+") else arg for arg in args], _SHELL)


def _shell_script(words: Sequence[str]) -> str | None:
    options = _shell_options(words[1:])
    return options.operands[0] if "-c" in options.flags and options.operands else None


def _reads_script_from_stdin(words: Sequence[str]) -> bool:
    _, rest = _assignments(words)
    if not rest or _name(rest[0]) not in SHELLS | WRAPPERS:
        return False
    shell = next((index for index, word in enumerate(rest) if _name(word) in SHELLS), None)
    if shell is None:
        return False
    options = _shell_options(rest[shell + 1 :])
    return "-c" not in options.flags and ("-s" in options.flags or not options.operands)


def _interpreter_call(words: Sequence[str]) -> interpreters.Invocation | None:
    _, rest = _assignments(words)
    if not rest:
        return None
    starts = range(len(rest)) if _name(rest[0]) in WRAPPERS else range(1)
    calls = (interpreters.invocation(_name(rest[index]), rest[index + 1 :]) for index in starts)
    return next((call for call in calls if call is not None), None)


def _stdin_texts(pipeline: Sequence[SimpleCommand], heredocs: Sequence[str]) -> list[str]:
    texts = [heredocs[index] for command in pipeline for index in command.heredocs]
    texts += [text for command in pipeline for text in command.herestrings]
    texts += [
        " ".join(command.words[start:])
        for command in pipeline[:-1]
        for start in range(len(command.words))
    ]
    return texts


def _exec_commands(words: Sequence[str]) -> list[list[str]]:
    commands: list[list[str]] = []
    current: list[str] | None = None
    for word in words:
        if word in _EXEC_FLAGS:
            current = []
            commands.append(current)
        elif current is not None and word in _EXEC_ENDS:
            current = None
        elif current is not None:
            current.append(word)
    return commands


def _nested_claude(words: Sequence[str]) -> list[Verdict]:
    template = tuple(words[1:4]) in _EVAL_TEMPLATE and not any(
        word.lower().startswith(_EVAL_DIR) for word in words[4:]
    )
    flags = [
        word
        for word in words[1:]
        if (word.startswith(_NESTED_SETTINGS) and not (template and word == "--bare"))
        or _DISABLE_ALL_HOOKS in word.lower()
    ]
    verdicts = [Verdict("nested-settings", flag) for flag in flags]
    for noun, verb in pairwise(words):
        verbs, prefix = _REMOVALS.get(noun, (frozenset[str](), ""))
        if verb in verbs:
            verdicts.append(Verdict("plugin-removal", f"{prefix} {verb}"))
    return verdicts


def _harness(name: str) -> Callable[[Sequence[str]], list[Verdict]]:
    removals = _HARNESS_REMOVALS[name]

    def check(words: Sequence[str]) -> list[Verdict]:
        verdicts = []
        for noun, verb in pairwise(words):
            verbs, prefix = removals.get(noun, (frozenset[str](), ""))
            if verb in verbs:
                verdicts.append(Verdict("plugin-removal", f"{prefix} {verb}"))
        return verdicts

    return check


def _pi(words: Sequence[str]) -> list[Verdict]:
    verb = words[1] if len(words) > 1 else ""
    return [Verdict("plugin-removal", f"pi {verb}")] if verb in _PI_REMOVALS else []


def _pre_commit(words: Sequence[str]) -> list[Verdict]:
    return [Verdict("skip-hooks", "pre-commit uninstall")] if "uninstall" in words else []


_DIRECT: dict[str, Callable[[Sequence[str]], list[Verdict]]] = {
    "pre-commit": _pre_commit,
    "claude": _nested_claude,
    "grok": _harness("grok"),
    "codex": _harness("codex"),
    "agy": _harness("agy"),
    "pi": _pi,
}


def _mover(words: Sequence[str]) -> list[str]:
    _, rest = _assignments(words)
    if rest and _name(rest[0]) in WRAPPERS:
        later = (rest[index:] for index in range(len(rest)) if _name(rest[index]) in _MOVERS)
        return next(later, [])  # pragma: no mutate # -- None and [] both mean no mover
    return rest


def _hides_switch(words: Sequence[str], rest: Sequence[str], cwd: Path) -> bool:
    if not rest or _name(rest[0]) != "git":
        return _switches(" ".join(words))
    fed = _XARGS in map(_name, words[: len(words) - len(rest)])
    texts = [" ".join(rest)] if fed else git.scripts(rest, cwd)
    return any(map(_switches, texts))


def _switches(text: str) -> bool:
    found = _GIT_WORD.search(text)
    return found is not None and _SWITCH_WORD.search(text, found.end()) is not None


def _directories(words: Sequence[str]) -> list[str]:
    found: list[str] = []
    short: set[str] = set()
    long: set[str] = set()
    for word, following in zip(words, [*words[1:], ""], strict=True):
        name, equals, value = word.partition("=")
        letters = [i for i, letter in enumerate(word) if i and letter in short]
        if name.startswith("--") and name != "--" and any(o.startswith(name) for o in long):
            found.append(value if equals else following)
        elif word.startswith("-") and not word.startswith("--") and letters:
            found.append(word[letters[0] + 1 :] or following)
        letter, option = _CHANGES_DIRECTORY.get(_name(word), ("", ""))
        short |= set(letter)
        long |= {option} - {""}
    return found


def _inner_scripts(head: str, words: Sequence[str]) -> list[str]:
    if head == "eval":
        return [" ".join(words[1:])]
    script = _shell_script(words) if head in SHELLS else None
    return [] if script is None else [script]


@dataclass(frozen=True, slots=True)
class _Memo:
    judged: dict[tuple[str, Context, int, str], list[Verdict]] = field(default_factory=dict)
    parsed: dict[str, _Read] = field(default_factory=dict)


class _Checker:
    def __init__(self, guard: Guard, depth: int, line: str, *, memo: _Memo) -> None:
        self.guard = guard
        self.depth = depth
        self.line = line
        self.memo = memo
        self.modelled = True

    def _deeper(self) -> "_Checker":
        return _Checker(self.guard, self.depth + 1, self.line, memo=self.memo)

    def script(self, text: str, context: Context, heredoc: str = "") -> list[Verdict]:
        key = (text, context, self.depth, heredoc)
        judged, parsed = self.memo.judged, self.memo.parsed
        if key in judged:
            return judged[key]
        if heredoc:
            verdicts = [replace(v, heredoc=heredoc) for v in self.script(text, context)]
        elif self.depth > MAX_DEPTH:
            verdicts = [Verdict("unreadable", "the command nests scripts too deeply")]
        else:
            if text not in parsed:
                parsed[text] = _read(text)
            verdicts = self._judged(parsed[text], context)
        judged[key] = verdicts
        return verdicts

    def _judged(self, read: _Read, context: Context) -> list[Verdict]:
        parsed, switching, nested = read
        self.modelled = parsed.modelled
        if switching:
            context = context.switching_unseen()
        trusted = not any(map(_redefines, parsed.commands))
        now = chain = seen = [context]
        previous = ""
        values: dict[str, frozenset[str | None]] = {}
        crowded = False
        verdicts: list[Verdict] = []
        pipeline: list[SimpleCommand] = []
        entered: list[tuple[list[Context], list[Context]]] = []
        for command in parsed.commands:
            if _redefines(command):
                now = _distinct(p for place in now for p in _anywhere(place, place.adrift(), seen))
                crowded = crowded or len(now) > MAX_STATES
                now = now[:MAX_STATES]
            entered += [(now, chain)] * command.opens
            pipeline.append(command)
            position = len(pipeline)
            for place in now:
                verdicts += self._command(pipeline, parsed.heredocs, place)
            pipeline = pipeline if command.piped else []
            chained = command.then == _AND
            sure = trusted and chained and previous not in _UNSURE_AFTER
            known = values if trusted else {}
            after = [
                a
                for place in now
                for a in self._advance(command, place, seen, sure=sure, values=known)
            ]
            values = _assigning(values, command, position=position, previous=previous)
            for _ in range(command.closes):
                ran = [*chain, *after]
                now, chain = entered.pop()
                after = _left(now, ran)
            chain = _distinct([*chain, *after])
            states = _levelled(after if chained else chain)
            unspelled = any(state.unspelled for state in states)
            crowded = crowded or len(states) > MAX_STATES or unspelled
            now = states[:MAX_STATES]
            chain = chain if chained else now
            seen = _distinct([*seen, *now])
            previous = command.then
        verdicts += [_CROWDED] if crowded else []
        verdicts += [] if parsed.closed else [_UNCLOSED]
        inside = self._nested(nested, seen[:MAX_STATES])
        return list(dict.fromkeys([*inside, *verdicts]))

    def _nested(
        self, nested: Sequence[tuple[str, str]], places: Sequence[Context]
    ) -> list[Verdict]:
        inner = self._deeper()
        return [
            v
            for script, heredoc in nested
            for place in places
            for v in inner.script(script, place, heredoc)
        ]

    def _command(
        self, pipeline: Sequence[SimpleCommand], heredocs: Sequence[str], context: Context
    ) -> list[Verdict]:
        command = pipeline[-1]
        verdicts = self._writes(command.writes, (), context, verb=_REDIRECT)
        verdicts += self.words(command.words, context)
        if _reads_script_from_stdin(command.words):
            verdicts += self._fed(pipeline, heredocs, context)
        verdicts += self._fed_interpreter(pipeline, heredocs, context)
        return self.guard.noted(
            [replace(v, heredoc=command.heredoc) for v in verdicts] if command.heredoc else verdicts
        )

    def _fed(
        self, pipeline: Sequence[SimpleCommand], heredocs: Sequence[str], context: Context
    ) -> list[Verdict]:
        inner = self._deeper()
        scripts = _stdin_texts(pipeline, heredocs)
        return [v for script in scripts for v in inner.script(script, context)]

    def _fed_interpreter(
        self, pipeline: Sequence[SimpleCommand], heredocs: Sequence[str], context: Context
    ) -> list[Verdict]:
        call = _interpreter_call(pipeline[-1].words)
        if call is None or not call.reads_stdin:
            return []
        return self._programs(call, _stdin_texts(pipeline, heredocs), context)

    def _programs(
        self, call: interpreters.Invocation, programs: Sequence[str], context: Context
    ) -> list[Verdict]:
        verdicts: list[Verdict] = []
        inner = self._deeper()
        for program in filter(call.writes, programs):
            for command in interpreters.git_commands(program):
                verdicts += inner.script(command, context)
            names = interpreters.named_paths(program)
            verdicts += tickets.named(names, self.line, context, guard=self.guard)
            removes = call.removes(program)
            hits = self.guard.locator.words(names, context.cwd, destructive=removes)
            verdicts += self.guard.writes(
                context, hits, lambda hit: interpreters.detail(call.name, hit.path, hit.group.name)
            )
            verdicts += self.guard.unfollowed(context, names, destructive=removes, by=call.name)
            written = interpreters.written_names(program)
            verdicts += self.guard.acting(self.guard.locator.named(written, context.cwd))
        return verdicts

    def _writes(
        self, written: Sequence[str], removed: Sequence[str], context: Context, *, verb: str
    ) -> list[Verdict]:
        find, cwd = self.guard.locator.words, context.cwd
        hits = find(written, cwd, destructive=False)  # pragma: no mutate # -- None is False
        hits += find(removed, cwd, destructive=True)
        verdicts = self.guard.writes(
            context, hits, lambda hit: f"{verb} {hit.path} ({hit.group.name})"
        )
        verdicts += self.guard.acting(self.guard.locator.paths([*written, *removed], cwd))
        by = "a redirect" if verb == _REDIRECT else verb
        verdicts += self.guard.unfollowed(context, written, destructive=False, by=by)
        verdicts += self.guard.unfollowed(context, removed, destructive=True, by=by)
        return verdicts + tickets.named(written, self.line, context, guard=self.guard)

    def words(self, words: Sequence[str], context: Context) -> list[Verdict]:
        assignments, rest = _assignments(words)
        names = _assigned(assignments)
        verdicts = self._environment(names, context)
        if not rest:
            return verdicts
        context = context.exporting(names)
        head = _name(rest[0])
        if head in WRAPPERS:
            return verdicts + self._wrapped(rest, context)
        return self.guard.noted(verdicts + self._ruled(head, rest, context))

    def _environment(self, names: Mapping[str, str], context: Context) -> list[Verdict]:
        verdicts = self.guard.noted(_variables(names))
        return verdicts + self._scripts(environment.commands(names), context)

    def _scripts(self, scripts: Sequence[str], context: Context) -> list[Verdict]:
        inner = self._deeper()
        return [v for script in scripts for v in inner.script(script, context)]

    def _wrapped(self, words: Sequence[str], context: Context) -> list[Verdict]:
        names = _assigned(words)
        verdicts = self._environment(names, context)
        context = context.exporting(names)
        places = _distinct([context, *(self._entered(w, context) for w in _directories(words))])
        for index in range(len(words)):
            if _name(words[index]) not in WRAPPERS and not environment.is_assignment(words[index]):
                verdicts += [v for place in places for v in self.words(words[index:], place)]
        return verdicts

    def _entered(self, word: str, context: Context) -> Context:
        target = self.guard.locator.filesystem.expand(word)
        if _unreadable(target):
            return context.adrift()
        anchored = PurePosixPath(target).is_absolute()
        return context.moved_to(context.cwd / target, anchored=anchored)

    def _ruled(self, head: str, words: Sequence[str], context: Context) -> list[Verdict]:
        if head == "git":
            ran = self._scripts(git.scripts(words, context.cwd), context)
            return ran + git.check(words, context, self.guard)
        if head == "gh":
            return gh.check(words, context, self.guard)
        direct = _DIRECT.get(head)
        if direct is not None:
            return direct(words)
        declared = environment.declared(words)
        if declared:
            return self._environment(declared, context)
        inner = self._deeper()
        verdicts = [
            v for script in _inner_scripts(head, words) for v in inner.script(script, context)
        ]
        call = interpreters.invocation(head, words[1:])
        if call is not None:
            verdicts += self._programs(call, call.programs, context)
        commands = _exec_commands(words) if head == "find" else []
        verdicts += [v for command in commands for v in inner.words(command, context)]
        touched = writes.touched(head, words[1:])
        return verdicts + self._writes(touched.written, touched.removed, context, verb=head)

    def _advance(
        self,
        command: SimpleCommand,
        context: Context,
        seen: Sequence[Context],
        *,
        sure: bool,
        values: Mapping[str, frozenset[str | None]],
    ) -> list[Context]:
        words = command.words
        unreserved = list(dropwhile(RESERVED.__contains__, words))
        context = context.exporting(environment.carried(unreserved))
        rest = _mover(words)
        head = _name(rest[0]) if rest else ""
        bare = bool(rest) and list(words) == rest and rest[0] == head
        quiet = head in _DIRECTORY_MOVERS or (head == "git" and git.settled(rest, context.cwd))
        still = bare and quiet
        context = context if still else context.unsettling()
        plain = sure and bare
        if head in _DIRECTORY_MOVERS:
            if self.modelled and command.then in _APART:
                return [context]
            return self._moved(words, context, seen, plain=plain, values=values)
        if _hides_switch(words, rest, context.cwd):
            return [context.switching_unseen()]
        move = git.switch(rest, context, self.guard) if head == "git" else None
        if move is None:
            return [context]
        if plain and move.certain:
            return [context.switched_to(*move.branches)]
        return [context.claiming(move.branches)]

    def _moved(
        self,
        words: Sequence[str],
        context: Context,
        seen: Sequence[Context],
        *,
        plain: bool,
        values: Mapping[str, frozenset[str | None]],
    ) -> list[Context]:
        assigned = _assigned(_assignments(words)[0])
        lookups = context.exported | assigned.keys()
        words = _mover(words)
        spelled = _resolved(words[-1], values) if len(words) > 1 else iter(["~"])
        bare = len(words) == 1 and _HOME in assigned
        homes = {_literal(assigned[_HOME])} if bare else values.get(_HOME, frozenset())
        stack = _name(words[0]) == _POPD or (_name(words[0]) == _PUSHD and len(words) == 1)
        places: list[Context] = []
        reached: set[Path] = set()
        for word in _homed(spelled, homes):
            if len(reached) > MAX_STATES:
                break
            if word is None:
                places += _anywhere(context, context.unspelling(), seen)
                continue
            target = self.guard.locator.filesystem.expand(word)
            unknown = stack or _unreadable(target)
            if unknown or _CDPATH in lookups or (_HOME in lookups and word.startswith("~")):
                places += _anywhere(context, context.adrift(), seen)
            if unknown:
                continue
            anchored = PurePosixPath(target).is_absolute()
            moved = context.moved_to(context.cwd / target, anchored=anchored)
            reached.add(moved.cwd)
            places += [moved] if plain else [context, moved]
        return _distinct(places)


def check(command: str, context: Context, guard: Guard) -> list[Verdict]:
    verdicts = _Checker(guard, 0, command, memo=_Memo()).script(command, context)
    elsewhere = {verdict for verdict in verdicts if not verdict.heredoc}
    verdicts = [v for v in verdicts if not v.heredoc or replace(v, heredoc="") not in elsewhere]
    verdicts = tickets.together(verdicts)
    refused = any(verdict.rule in _PATH_REFUSALS for verdict in verdicts)
    return [v for v in verdicts if not (refused and v.rule == "unresolved-directory")]
