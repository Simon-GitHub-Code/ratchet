"""Judge a Bash command line: every simple command, every nested script, every wrapper.

A command line is lexed (``ratchet_guards.shell``), and each simple command is checked in
order, tracking ``cd`` and branch switches. Command substitutions, ``bash -c`` strings,
``eval`` arguments, ``find -exec`` commands and heredocs fed to a shell are checked as
scripts of their own. A wrapper (``sudo``, ``env``, ``xargs``, ``timeout`` ...) is checked
from every later word that names a ruled command, so its own options cannot hide one.
"""

import re
from collections.abc import Callable, Sequence
from itertools import pairwise
from pathlib import PurePosixPath

from ratchet_guards import gh, git, writes
from ratchet_guards.context import Context, Guard
from ratchet_guards.options import Options, Spec, scan
from ratchet_guards.shell import SimpleCommand, parse
from ratchet_guards.verdict import Verdict

MAX_DEPTH = 8
SHELLS = frozenset({"bash", "sh", "zsh", "dash", "ksh"})
WRAPPERS = frozenset(
    {"sudo", "doas", "env", "nohup", "time", "command", "builtin", "exec", "nice", "ionice"}
    | {"timeout", "stdbuf", "xargs", "unbuffer", "setsid", "chronic", "watch"}
)
RESERVED = frozenset(
    {"{", "}", "!", "if", "then", "else", "elif", "fi", "do", "done"} | {"while", "until"}
)
_EXEC_FLAGS = frozenset({"-exec", "-execdir", "-ok", "-okdir"})
_EXEC_ENDS = frozenset({";", "+"})
_ASSIGNMENT = re.compile(r"^(?P<name>[A-Za-z_][A-Za-z0-9_]*)=")
_NESTED_SETTINGS = (
    "--settings",
    "--setting-sources",
    "--disable-plugin",
    "--no-plugins",
    "--disable-all-hooks",
    "--bare",
)
_DISABLE_ALL_HOOKS = "disableallhooks"
_PLUGIN_REMOVALS = frozenset({"disable", "uninstall", "remove"})
_REMOVALS: dict[str, tuple[frozenset[str], str]] = {
    "plugin": (_PLUGIN_REMOVALS, "claude plugin"),
    "plugins": (_PLUGIN_REMOVALS, "claude plugin"),
    "marketplace": (frozenset({"remove", "rm"}), "claude plugin marketplace"),
}
# Why: the same guard runs in Grok, Codex, pi and OpenCode; each has its own spelling of
# "remove the plugin that carries the guard".
_HARNESS_REMOVALS: dict[str, dict[str, tuple[frozenset[str], str]]] = {
    "grok": {
        "plugin": (_PLUGIN_REMOVALS | {"rm"}, "grok plugin"),
        "marketplace": (frozenset({"remove", "rm"}), "grok plugin marketplace"),
    },
    "codex": {
        "plugin": (frozenset({"remove", "rm"}), "codex plugin"),
        "marketplace": (frozenset({"remove", "rm"}), "codex plugin marketplace"),
    },
}
_PI_REMOVALS = frozenset({"remove", "uninstall"})
_MOVERS = frozenset({"cd", "pushd", "git"})
_SHELL = Spec("oO", frozenset({"--rcfile", "--init-file"}))


def _name(word: str) -> str:
    return PurePosixPath(word).name


def _assignments(words: Sequence[str]) -> tuple[list[str], list[str]]:
    """Split leading assignments and reserved words from the command they prefix."""
    index = 0
    while index < len(words) and (words[index] in RESERVED or _ASSIGNMENT.match(words[index])):
        index += 1
    return list(words[:index]), list(words[index:])


def _environment(words: Sequence[str]) -> list[Verdict]:
    verdicts: list[Verdict] = []
    for assignment in words:
        match = _ASSIGNMENT.match(assignment)
        if match is None:
            continue
        name = match.group("name")
        if name == "SKIP":
            verdicts.append(Verdict("skip-hooks", assignment))
        elif name.startswith("GIT_CONFIG"):
            verdicts.append(Verdict("hooks-path", assignment))
    return verdicts


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
    flags = [
        word
        for word in words[1:]
        if word.startswith(_NESTED_SETTINGS) or _DISABLE_ALL_HOOKS in word.lower()
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


def _export(words: Sequence[str]) -> list[Verdict]:
    return _environment(words[1:])


_DIRECT: dict[str, Callable[[Sequence[str]], list[Verdict]]] = {
    "pre-commit": _pre_commit,
    "claude": _nested_claude,
    "grok": _harness("grok"),
    "codex": _harness("codex"),
    "pi": _pi,
    "export": _export,
}


def _mover(words: Sequence[str]) -> list[str]:
    """The ``cd``, ``pushd`` or ``git`` command in ``words``, seen through any wrappers."""
    _, rest = _assignments(words)
    if rest and _name(rest[0]) in WRAPPERS:
        later = (rest[index:] for index in range(len(rest)) if _name(rest[index]) in _MOVERS)
        return next(later, [])  # pragma: no mutate # -- None and [] both mean no mover
    return rest


def _inner_scripts(head: str, words: Sequence[str]) -> list[str]:
    """Scripts a command runs from its arguments: ``eval`` and ``bash -c``."""
    if head == "eval":
        return [" ".join(words[1:])]
    script = _shell_script(words) if head in SHELLS else None
    return [] if script is None else [script]


class _Checker:
    def __init__(self, guard: Guard, depth: int) -> None:
        self.guard = guard
        self.depth = depth

    def script(self, text: str, context: Context) -> list[Verdict]:
        if self.depth > MAX_DEPTH:
            return [Verdict("unreadable", "the command nests scripts too deeply")]
        inner = _Checker(self.guard, self.depth + 1)
        parsed = parse(text)
        verdicts = [v for nested in parsed.nested for v in inner.script(nested, context)]
        pipeline: list[SimpleCommand] = []
        for command in parsed.commands:
            verdicts += self._writes(command.writes, (), context, verb="redirects into")
            verdicts += self.words(command.words, context)
            pipeline.append(command)
            if _reads_script_from_stdin(command.words):
                verdicts += self._fed(pipeline, parsed.heredocs, context)
            pipeline = pipeline if command.piped else []
            context = self._advance(command.words, context)
        return verdicts

    def _fed(
        self, pipeline: Sequence[SimpleCommand], heredocs: Sequence[str], context: Context
    ) -> list[Verdict]:
        """Check what a pipeline feeds into a shell: heredoc bodies and upstream arguments.

        Every run of trailing arguments counts, so a wrapper or brace before ``echo`` cannot
        hide what it prints.
        """
        scripts = [heredocs[index] for command in pipeline for index in command.heredocs]
        scripts += [
            " ".join(command.words[start:])
            for command in pipeline[:-1]
            for start in range(len(command.words))
        ]
        inner = _Checker(self.guard, self.depth + 1)
        return [v for script in scripts for v in inner.script(script, context)]

    def _writes(
        self, written: Sequence[str], removed: Sequence[str], context: Context, *, verb: str
    ) -> list[Verdict]:
        find, cwd = self.guard.locator.words, context.cwd
        hits = find(written, cwd, destructive=False)  # pragma: no mutate # -- None is False
        hits += find(removed, cwd, destructive=True)
        return [
            Verdict("guard-path-write", f"{verb} {hit.path} ({hit.group.name})") for hit in hits
        ]

    def words(self, words: Sequence[str], context: Context) -> list[Verdict]:
        assignments, rest = _assignments(words)
        verdicts = _environment(assignments)
        if not rest:
            return verdicts
        head = _name(rest[0])
        if head in WRAPPERS:
            return verdicts + self._wrapped(rest, context)
        return verdicts + self._ruled(head, rest, context)

    def _wrapped(self, words: Sequence[str], context: Context) -> list[Verdict]:
        verdicts = _environment([word for word in words if _ASSIGNMENT.match(word)])
        for index in range(len(words)):
            if _name(words[index]) not in WRAPPERS and not _ASSIGNMENT.match(words[index]):
                verdicts += self.words(words[index:], context)
        return verdicts

    def _ruled(self, head: str, words: Sequence[str], context: Context) -> list[Verdict]:
        if head == "git":
            return git.check(words, context, self.guard)
        if head == "gh":
            return gh.check(words, context, self.guard)
        direct = _DIRECT.get(head)
        if direct is not None:
            return direct(words)
        inner = _Checker(self.guard, self.depth + 1)
        verdicts = [
            v for script in _inner_scripts(head, words) for v in inner.script(script, context)
        ]
        commands = _exec_commands(words) if head == "find" else []
        verdicts += [v for command in commands for v in inner.words(command, context)]
        touched = writes.touched(head, words[1:])
        return verdicts + self._writes(touched.written, touched.removed, context, verb=head)

    def _advance(self, words: Sequence[str], context: Context) -> Context:
        rest = _mover(words)
        if not rest:
            return context
        head = _name(rest[0])
        if head in {"cd", "pushd"}:
            target = self.guard.locator.filesystem.expand(rest[-1] if len(rest) > 1 else "~")
            return context.moved_to(context.cwd / target)
        if head == "git":
            branch = git.switched_branch(rest, context.cwd)
            return context if branch is None else context.switched_to(branch)
        return context


def check(command: str, context: Context, guard: Guard) -> list[Verdict]:
    """Every verdict the rules give on a Bash command line run in ``context``."""
    return _Checker(guard, 0).script(command, context)
