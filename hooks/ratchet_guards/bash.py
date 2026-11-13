"""Judge a Bash command line: every simple command, every nested script, every wrapper.

A command line is lexed (``ratchet_guards.shell``), and each simple command is checked in
order. Command substitutions, ``bash -c`` strings, ``eval`` arguments, ``find -exec`` commands
and heredocs fed to a shell are checked as scripts of their own. A wrapper (``sudo``, ``env``,
``xargs``, ``timeout`` ...) is checked from every later word that names a ruled command, so its
own options cannot hide one. An interpreter given an inline program (``python3 -c``, ``node
-e``, or a heredoc, here-string or pipe on its stdin) is judged by the program text
(``ratchet_guards.interpreters``).

The line is judged before any of it runs, so a ``cd`` or a ``git switch`` on it is a claim, not
a fact: the directory may not exist, the command may sit in a subshell, a pipeline or the far
side of ``||``. A later command is therefore judged in every state the line may have left it
in, and what any of them refuses is refused. One state is dropped only when the shell itself
guarantees it: the command is chained with ``&&`` to a plain ``cd <directory>`` or to a ``git``
command that must have moved ``HEAD`` (``ratchet_guards.git.switch``), and nothing on the line
redefines what those words do (ADR 0024).
"""

import re
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import replace
from itertools import pairwise, product
from pathlib import Path, PurePosixPath

from ratchet_guards import environment, gh, git, interpreters, tickets, writes
from ratchet_guards.context import Context, Guard
from ratchet_guards.options import Options, Spec, scan
from ratchet_guards.shell import PLACEHOLDER, SimpleCommand, parse
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
# Why: `claude plugin eval init --bare` writes a blank case template and starts no session.
# Only that exact leading form is exempt, so `claude -p plugin eval init --bare` stays denied.
_EVAL_TEMPLATE = frozenset({("plugin", "eval", "init"), ("plugins", "eval", "init")})
# `--eval-dir` writes the template into any directory, so a command with any `--eval...` option
# (spaced, `=` joined or shortened) is not exempt.
_EVAL_DIR = "--eval"
_PLUGIN_REMOVALS = frozenset({"disable", "uninstall", "remove"})
_REMOVALS: dict[str, tuple[frozenset[str], str]] = {
    "plugin": (_PLUGIN_REMOVALS, "claude plugin"),
    "plugins": (_PLUGIN_REMOVALS, "claude plugin"),
    "marketplace": (frozenset({"remove", "rm"}), "claude plugin marketplace"),
}
# Why: the same guard runs in Grok, Codex, Antigravity, pi and OpenCode; each has its own
# spelling of "remove the plugin that carries the guard".
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
_DIRECTORY_MOVERS = frozenset({"cd", "pushd", "popd"})
_MOVERS = _DIRECTORY_MOVERS | {"git"}
# Why: a `cd` to one of these goes where only the shell knows: the previous directory, a stack
# entry, another user's home, a variable, a substitution, a glob.
_UNREADABLE_STARTS = ("-", "+", "~")
_UNREADABLE_MARKS = ("$", "*", "?", "[", PLACEHOLDER)
_POPD = "popd"
_CDPATH = "CDPATH"
# Why: after one of these the words `cd` and `git` may name a function, an alias or a trap's
# command, so a move read from the line is no longer certain to be the one that happens.
_REDEFINERS = frozenset({"function", "alias", "enable", "shopt", "eval", "source", ".", "trap"})
_DEFINITION = "("
_AND = "&&"
# After `||` the command may have been skipped; in a pipeline it runs in a subshell.
_UNSURE_AFTER = frozenset({"||", "|", "|&"})
_VARIABLE = re.compile(r"\$(\w+)|\$\{(\w+)\}")
# Why: a value the guard cannot take as one literal word is not a value it knows.
_DYNAMIC = ("$", "`", PLACEHOLDER)
# Why: a block or a negation runs its commands as they stand; the other reserved words make the
# command after them conditional or repeated.
_BRANCHING = RESERVED - {"{", "}", "!"}
_FORGETTERS = frozenset({"unset", "for", "select"})
# Why: the shell runs these in the shell itself, so what they set is there for the next command.
_KEEPS = frozenset({";", "&&", "||", "\n", ""})
_CROWDED = Verdict("unreadable", "the command line moves between too many directories")
# Why: a `git switch` the guard does not read as a command of its own (in a shell's script, a
# substitution, `eval`, `xargs`, `find -exec`, an alias) still moves `HEAD`. The words are
# enough to say that it may have.
_HIDDEN_SWITCH = re.compile(
    r"(?<![\w.-])git(?![\w.-]).*(?<![\w-])(?:switch|checkout|symbolic-ref|bisect)(?![\w-])",
    re.DOTALL,
)
_XARGS = "xargs"
_PATH_REFUSALS = frozenset({"guard-path-write", "guard-path-default"})
_SHELL = Spec("oO", frozenset({"--rcfile", "--init-file"}))


def _name(word: str) -> str:
    return PurePosixPath(word).name


def _assignments(words: Sequence[str]) -> tuple[list[str], list[str]]:
    """Split leading assignments and reserved words from the command they prefix."""
    index = 0
    while index < len(words) and (
        words[index] in RESERVED or environment.is_assignment(words[index])
    ):
        index += 1
    return list(words[:index]), list(words[index:])


def _assigned(words: Iterable[str]) -> dict[str, str]:
    """The variables the assignments among ``words`` set, each with its assignment."""
    return {name: word for word in words if (name := environment.assigned(word)) is not None}


def _variables(names: Mapping[str, str]) -> list[Verdict]:
    """Verdicts for the variables a command sets: a hook skip, or git configuration."""
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
    """``contexts`` with what the line has set so far carried by every one of them.

    A variable set, a name unsettled and a switch unseen are facts about the line, not about
    where it stands.
    """
    exported = frozenset(name for context in contexts for name in context.exported)
    unsettled = any(context.unsettled for context in contexts)
    hidden = any(context.hidden_switch for context in contexts)
    return _distinct(
        replace(c, exported=exported, unsettled=unsettled, hidden_switch=hidden) for c in contexts
    )


def _bindings(words: Sequence[str]) -> dict[str, str | None]:
    """The variables a simple command sets, each with its literal value or ``None``.

    ``None`` means the command sets the variable to something the guard cannot read, or unsets
    it.
    """
    lead, rest = _assignments(words)
    if not rest:
        return {
            name: _literal(word)
            for word in lead
            if (name := environment.assigned(word)) is not None
        }
    if _name(rest[0]) in _FORGETTERS:
        # `for NAME in ...` names one variable; `unset` names every operand.
        names = [word for word in rest[1:] if not word.startswith("-")]
        return dict.fromkeys(names if rest[0] == "unset" else names[:1])
    return {name: _literal(word) for name, word in environment.declared(rest).items()}


def _literal(word: str) -> str | None:
    """The value a plain ``NAME=value`` word sets, when the guard can read it as one word."""
    name, _, value = word.partition("=")
    plain = environment.assigned(word) == name
    return value if plain and value and not _dynamic(value) else None


def _dynamic(value: str) -> bool:
    return any(mark in value for mark in _DYNAMIC) or len(value.split()) != 1


def _resolved(target: str, values: Mapping[str, frozenset[str | None]]) -> list[str]:
    """Each way ``target`` reads once the names the line has set are replaced by their values.

    A name with no known value, or one that may have none, stays in the word as ``$name``,
    which the caller takes for a target it cannot read.
    """
    names = sorted({m.group(1) or m.group(2) for m in _VARIABLE.finditer(target)} & values.keys())
    options = [sorted(values[name], key=lambda value: value or "") for name in names]
    return [
        _substituted(target, dict(zip(names, picks, strict=True))) for picks in product(*options)
    ]


def _substituted(target: str, chosen: Mapping[str, str | None]) -> str:
    def value(match: re.Match[str]) -> str:
        return chosen.get(match.group(1) or match.group(2)) or match.group(0)

    return _VARIABLE.sub(value, target)


def _redefines(command: SimpleCommand) -> bool:
    """Whether a command may change what ``cd`` or ``git`` means: a definition, ``eval``, a trap."""
    _, rest = _assignments(command.words)
    # Why: a wrapper before the name (`sudo cd() { :; }`) still defines it.
    defines = command.then == _DEFINITION and bool(rest)
    return defines or bool(rest and {rest[0], _name(rest[0])} & _REDEFINERS)


def _assigning(
    values: Mapping[str, frozenset[str | None]],
    command: SimpleCommand,
    *,
    position: int,
    previous: str,
) -> dict[str, frozenset[str | None]]:
    """``values`` after ``command``: every value each name may hold, ``None`` for one not known.

    A command that sets a name where the shell certainly keeps it replaces what the name held.
    In a subshell, a pipeline, a branch, a loop, a background job or the far side of ``||`` the
    new value is only one of the values the name may hold. A closing parenthesis forgets every
    name, since what ran inside it is gone.
    """
    if command.then == ")":
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
    """A shell's options; ``+o name`` is read like ``-o name``."""
    return scan([f"-{arg[1:]}" if arg.startswith("+") else arg for arg in args], _SHELL)


def _shell_script(words: Sequence[str]) -> str | None:
    """The ``-c`` script of a shell command line, if it has one."""
    options = _shell_options(words[1:])
    return options.operands[0] if "-c" in options.flags and options.operands else None


def _reads_script_from_stdin(words: Sequence[str]) -> bool:
    """Whether a shell, perhaps behind wrappers, runs what arrives on stdin.

    It does with ``-s`` or with neither ``-c`` nor a script file.
    """
    _, rest = _assignments(words)
    if not rest or _name(rest[0]) not in SHELLS | WRAPPERS:
        return False
    shell = next((index for index, word in enumerate(rest) if _name(word) in SHELLS), None)
    if shell is None:
        return False
    options = _shell_options(rest[shell + 1 :])
    return "-c" not in options.flags and ("-s" in options.flags or not options.operands)


def _interpreter_call(words: Sequence[str]) -> interpreters.Invocation | None:
    """The interpreter command in ``words``, perhaps behind wrappers."""
    _, rest = _assignments(words)
    if not rest:
        return None
    starts = range(len(rest)) if _name(rest[0]) in WRAPPERS else range(1)
    calls = (interpreters.invocation(_name(rest[index]), rest[index + 1 :]) for index in starts)
    return next((call for call in calls if call is not None), None)


def _stdin_texts(pipeline: Sequence[SimpleCommand], heredocs: Sequence[str]) -> list[str]:
    """What a pipeline's last command reads on stdin, as text.

    The heredoc bodies and here-strings of every command in the pipeline, and every run of
    trailing arguments of the commands upstream, so a wrapper or brace before ``echo`` cannot
    hide what it prints.
    """
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
    """A ``claude`` that loads other settings, runs without hooks or plugins, or removes one."""
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
    """``pi remove`` and ``pi uninstall`` drop a package, the guard's included."""
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
    """The ``cd``, ``pushd`` or ``git`` command in ``words``, seen through any wrappers."""
    _, rest = _assignments(words)
    if rest and _name(rest[0]) in WRAPPERS:
        later = (rest[index:] for index in range(len(rest)) if _name(rest[index]) in _MOVERS)
        return next(later, [])  # pragma: no mutate # -- None and [] both mean no mover
    return rest


def _hides_switch(words: Sequence[str], rest: Sequence[str], cwd: Path) -> bool:
    """Whether ``words`` may move ``HEAD`` in a way ``git.switch`` does not read.

    ``rest`` is the ``git`` command in ``words``, if any. Any other command hides a switch its
    words name; a ``git`` hides one in a script it runs (an alias, ``rebase -x``, ``bisect run``,
    ``submodule foreach``), or in its own words when ``xargs`` adds to them.
    """
    if not rest or _name(rest[0]) != "git":
        return bool(_HIDDEN_SWITCH.search(" ".join(words)))
    fed = _XARGS in map(_name, words[: len(words) - len(rest)])
    texts = [" ".join(rest)] if fed else git.scripts(rest, cwd)
    return any(map(_HIDDEN_SWITCH.search, texts))


def _inner_scripts(head: str, words: Sequence[str]) -> list[str]:
    """Scripts a command runs from its arguments: ``eval`` and ``bash -c``."""
    if head == "eval":
        return [" ".join(words[1:])]
    script = _shell_script(words) if head in SHELLS else None
    return [] if script is None else [script]


class _Checker:
    def __init__(self, guard: Guard, depth: int, line: str) -> None:
        self.guard = guard
        self.depth = depth
        self.line = line

    def _deeper(self) -> "_Checker":
        return _Checker(self.guard, self.depth + 1, self.line)

    def script(self, text: str, context: Context) -> list[Verdict]:
        if self.depth > MAX_DEPTH:
            return [Verdict("unreadable", "the command nests scripts too deeply")]
        inner = self._deeper()
        parsed = parse(text)
        if any(map(_HIDDEN_SWITCH.search, parsed.nested)):
            # Why: the lexer lifts a substitution out of its command, so where on the line it
            # runs is not kept; a switch in one may come before any write.
            context = context.switching_unseen()
        trusted = not any(map(_redefines, parsed.commands))
        now = chain = seen = [context]
        previous = ""
        values: dict[str, frozenset[str | None]] = {}
        crowded = False
        verdicts: list[Verdict] = []
        pipeline: list[SimpleCommand] = []
        for command in parsed.commands:
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
                for a in self._advance(command.words, place, seen, sure=sure, values=known)
            ]
            values = _assigning(values, command, position=position, previous=previous)
            chain = _distinct([*chain, *after])
            states = _levelled(after if chained else chain)
            crowded = crowded or len(states) > MAX_STATES
            now = states[:MAX_STATES]
            chain = chain if chained else now
            seen = _distinct([*seen, *now])
            previous = command.then
        verdicts += [_CROWDED] if crowded else []
        nested = [(script, place) for script in parsed.nested for place in seen[:MAX_STATES]]
        inside = [v for script, place in nested for v in inner.script(script, place)]
        return list(dict.fromkeys([*inside, *verdicts]))

    def _command(
        self, pipeline: Sequence[SimpleCommand], heredocs: Sequence[str], context: Context
    ) -> list[Verdict]:
        """Every verdict on the last command of ``pipeline``, run in ``context``."""
        command = pipeline[-1]
        verdicts = self._writes(command.writes, (), context, verb="redirects into")
        verdicts += self.words(command.words, context)
        if _reads_script_from_stdin(command.words):
            verdicts += self._fed(pipeline, heredocs, context)
        return verdicts + self._fed_interpreter(pipeline, heredocs, context)

    def _fed(
        self, pipeline: Sequence[SimpleCommand], heredocs: Sequence[str], context: Context
    ) -> list[Verdict]:
        """Check what a pipeline feeds into a shell as scripts of their own."""
        inner = self._deeper()
        scripts = _stdin_texts(pipeline, heredocs)
        return [v for script in scripts for v in inner.script(script, context)]

    def _fed_interpreter(
        self, pipeline: Sequence[SimpleCommand], heredocs: Sequence[str], context: Context
    ) -> list[Verdict]:
        """Check what a pipeline feeds into an interpreter that runs its stdin as a program."""
        call = _interpreter_call(pipeline[-1].words)
        if call is None or not call.reads_stdin:
            return []
        return self._programs(call, _stdin_texts(pipeline, heredocs), context)

    def _programs(
        self, call: interpreters.Invocation, programs: Sequence[str], context: Context
    ) -> list[Verdict]:
        """What inline programs do: write a guard path they name, or run a ``git`` command."""
        verdicts: list[Verdict] = []
        inner = self._deeper()
        for program in filter(call.writes, programs):
            for command in interpreters.git_commands(program):
                verdicts += inner.script(command, context)
            names = interpreters.named_paths(program)
            verdicts += tickets.named(names, self.line, context, guard=self.guard)
            hits = self.guard.locator.words(names, context.cwd, destructive=call.removes(program))
            verdicts += self.guard.writes(
                context, hits, lambda hit: interpreters.detail(call.name, hit.path, hit.group.name)
            )
            verdicts += self.guard.acting(self.guard.locator.named(names, context.cwd))
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
        if context.lost:
            # Why: the shell may be in any checkout, one on its default branch among them, so a
            # guard path there is refused whatever branch the line's own checkout has.
            locate = self.guard.locator.could_hit
            lost = [*locate(written, destructive=False), *locate(removed, destructive=True)]
            verdicts += [Verdict("unresolved-directory", f"{verb} {word}") for word in lost]
        return verdicts + tickets.named(written, self.line, context, guard=self.guard)

    def words(self, words: Sequence[str], context: Context) -> list[Verdict]:
        assignments, rest = _assignments(words)
        names = _assigned(assignments)
        verdicts = _variables(names) + self._scripts(environment.commands(names), context)
        if not rest:
            return verdicts
        context = context.exporting(names)
        head = _name(rest[0])
        if head in WRAPPERS:
            return verdicts + self._wrapped(rest, context)
        return verdicts + self._ruled(head, rest, context)

    def _scripts(self, scripts: Sequence[str], context: Context) -> list[Verdict]:
        inner = self._deeper()
        return [v for script in scripts for v in inner.script(script, context)]

    def _wrapped(self, words: Sequence[str], context: Context) -> list[Verdict]:
        names = _assigned(words)
        verdicts = _variables(names) + self._scripts(environment.commands(names), context)
        context = context.exporting(names)
        for index in range(len(words)):
            if _name(words[index]) not in WRAPPERS and not environment.is_assignment(words[index]):
                verdicts += self.words(words[index:], context)
        return verdicts

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
            return _variables(declared) + self._scripts(environment.commands(declared), context)
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
        words: Sequence[str],
        context: Context,
        seen: Sequence[Context],
        *,
        sure: bool,
        values: Mapping[str, frozenset[str | None]],
    ) -> list[Context]:
        """The states ``words`` may leave ``context`` in for the command after it.

        ``sure`` says that command runs only when this one succeeded. Then a plain ``cd`` or
        ``git`` leaves one state; in every other case the state before it stays possible.
        """
        context = context.exporting(environment.carried(words))
        rest = _mover(words)
        head = _name(rest[0]) if rest else ""
        bare = bool(rest) and list(words) == rest and rest[0] == head
        quiet = head in _DIRECTORY_MOVERS or (head == "git" and git.settled(rest, context.cwd))
        still = bare and quiet
        context = context if still else context.unsettling()
        plain = sure and bare
        if head in _DIRECTORY_MOVERS:
            return self._moved(rest, context, seen, plain=plain, values=values)
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
        """Where a ``cd``, ``pushd`` or ``popd`` may leave the commands after it.

        A name the line set to a literal is read as the shell will read it (ADR 0024). A target
        it cannot read leaves the directory unknown, which a later write must not take as a
        directory it has already seen.
        """
        readings = _resolved(words[-1], values) if len(words) > 1 else ["~"]
        places: list[Context] = []
        for word in readings:
            target = self.guard.locator.filesystem.expand(word)
            hidden = _name(words[0]) == _POPD or _unreadable(target) or _CDPATH in context.exported
            if hidden:
                earlier = (context.moved_to(place.cwd) for place in seen)
                places += [context, context.adrift(), *earlier]
                continue
            anchored = PurePosixPath(target).is_absolute()
            moved = context.moved_to(context.cwd / target, anchored=anchored)
            places += [moved] if plain else [context, moved]
        return _distinct(places)


def check(command: str, context: Context, guard: Guard) -> list[Verdict]:
    """Every verdict the rules give on a Bash command line run in ``context``."""
    verdicts = tickets.together(_Checker(guard, 0, command).script(command, context))
    # Why: a write that is refused outright needs no second refusal about where it ran.
    refused = any(verdict.rule in _PATH_REFUSALS for verdict in verdicts)
    return [v for v in verdicts if not (refused and v.rule == "unresolved-directory")]
