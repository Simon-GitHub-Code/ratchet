"""Rules for ``git``: hook bypasses, history rewrites, pushes to main and guard-path writes."""

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path

from ratchet_guards.context import MAIN_BRANCHES, Context, Guard
from ratchet_guards.options import NO_VALUES, Options, Spec, scan
from ratchet_guards.verdict import Verdict

_GLOBAL_VALUES = frozenset({"-C", "-c", "--git-dir", "--work-tree", "--namespace", "--config-env"})
_FORCE = frozenset({"-f", "--force", "--force-with-lease", "--force-if-includes", "--mirror"})
_ALL_BRANCHES = frozenset({"--all", "--branches"})
_READ_CONFIG = frozenset({"--get", "--get-all", "--get-regexp", "-l", "--list"})
_HOOKS_PATH = "core.hookspath"
_CONFIG_VALUES = frozenset({"-c", "--config-env"})
_COMMIT = Spec(
    "mFcCt",
    frozenset(
        {"--message", "--file", "--reuse-message", "--reedit-message", "--template", "--author"}
        | {"--date", "--fixup", "--squash", "--trailer", "--cleanup"}
    ),
)
_PUSH = Spec("o", frozenset({"--push-option", "--repo", "--receive-pack", "--exec"}))
_PATHS = Spec("bBs", frozenset({"--source"}))
_SWITCH = Spec("bBcC", frozenset({"--create", "--force-create"}))
_SWITCH_FLAGS = ("-b", "-B", "-c", "-C", "--create", "--force-create")
_NO_VERIFY = "--no-verify"
# Why: git takes any unambiguous prefix of a long option; `--no-ver` is ambiguous with
# `--no-verbose` and git refuses it, so `--no-veri` is the shortest spelling that skips hooks.
_NO_VERIFY_SHORTEST = len("--no-veri")


def _skips_verify(word: str) -> bool:
    return len(word) >= _NO_VERIFY_SHORTEST and _NO_VERIFY.startswith(word)


@dataclass(frozen=True, slots=True)
class Invocation:
    """A git command line split around its subcommand."""

    cwd: Path
    configs: tuple[str, ...]
    subcommand: str
    args: tuple[str, ...]


def invocation(words: Sequence[str], cwd: Path) -> Invocation:
    """Parse ``git [global options] <subcommand> args``; ``-C`` moves the working directory."""
    configs: list[str] = []
    rest = list(words[1:])
    while rest and rest[0].startswith("-"):
        option = rest.pop(0)
        if option in _GLOBAL_VALUES and rest:
            value = rest.pop(0)
            cwd = cwd / value if option == "-C" else cwd
            configs.extend([value] if option in _CONFIG_VALUES else [])
    subcommand = rest.pop(0) if rest else ""
    return Invocation(cwd, tuple(configs), subcommand, tuple(rest))


def _hooks_redirected(configs: Sequence[str]) -> bool:
    return any(config.partition("=")[0].lower() == _HOOKS_PATH for config in configs)


def _commit(call: Invocation, context: Context, guard: Guard) -> list[Verdict]:
    del context
    options = scan(call.args, _COMMIT)
    verdicts = [Verdict("no-verify", "git commit -n")] if "-n" in options.flags else []
    if "--amend" in options.flags and guard.repo.head_is_pushed(call.cwd):
        verdicts.append(Verdict("amend-pushed", "git commit --amend on a pushed HEAD"))
    return verdicts


def _destination(refspec: str, current: str | None) -> str | None:
    target = refspec.lstrip("+").rpartition(":")[2]
    if target == "HEAD":
        return current
    return target.removeprefix("refs/heads/")


def _push(call: Invocation, context: Context, guard: Guard) -> list[Verdict]:
    options = scan(call.args, _PUSH)
    refspecs = options.operands[1:]
    verdicts: list[Verdict] = []
    if options.flags & _FORCE or any(refspec.startswith("+") for refspec in refspecs):
        verdicts.append(Verdict("force-push", "git push " + " ".join(call.args)))
    if _pushes_main(options, context, guard, cwd=call.cwd):
        verdicts.append(Verdict("push-main", "git push " + " ".join(call.args)))
    return verdicts


def _pushes_main(options: Options, context: Context, guard: Guard, *, cwd: Path) -> bool:
    if options.flags & _ALL_BRANCHES:
        return True
    refspecs = options.operands[1:]
    current = guard.branch(context, cwd)
    destinations = [_destination(refspec, current) for refspec in refspecs] or [current]
    return any(destination in MAIN_BRANCHES for destination in destinations)


def _reset(call: Invocation, context: Context, guard: Guard) -> list[Verdict]:
    if "--hard" in scan(call.args, NO_VALUES).flags and guard.on_main(context, call.cwd):
        return [Verdict("reset-main", "git reset --hard on main")]
    return []


def _commit_tree(call: Invocation, context: Context, guard: Guard) -> list[Verdict]:
    del call, context, guard
    return [Verdict("no-verify", "git commit-tree writes a commit no hook sees")]


def _rewrite(call: Invocation, context: Context, guard: Guard) -> list[Verdict]:
    del context, guard
    return [Verdict("rewrite-history", f"git {call.subcommand}")]


def _config(call: Invocation, context: Context, guard: Guard) -> list[Verdict]:
    del context, guard
    options = scan(call.args, NO_VALUES)
    keys = {operand.lower() for operand in options.operands}
    if _HOOKS_PATH in keys and not options.flags & _READ_CONFIG:
        return [Verdict("hooks-path", "git config core.hooksPath")]
    return []


def _paths(call: Invocation, context: Context, guard: Guard) -> list[Verdict]:
    del context
    operands = scan(call.args, _PATHS).operands
    hits = guard.locator.words(operands, call.cwd, destructive=True)
    return [
        Verdict("guard-path-write", f"git {call.subcommand} {hit.path} ({hit.group.name})")
        for hit in hits
    ]


_SUBCOMMANDS: dict[str, Callable[[Invocation, Context, Guard], list[Verdict]]] = {
    "commit": _commit,
    "commit-tree": _commit_tree,
    "push": _push,
    "reset": _reset,
    "filter-branch": _rewrite,
    "filter-repo": _rewrite,
    "config": _config,
    "rm": _paths,
    "mv": _paths,
    "restore": _paths,
    "checkout": _paths,
}


def check(words: Sequence[str], context: Context, guard: Guard) -> list[Verdict]:
    """Verdicts for one ``git`` command."""
    call = invocation(words, context.cwd)
    verdicts: list[Verdict] = []
    skipping = next((arg for arg in call.args if _skips_verify(arg)), None)
    if skipping is not None:
        verdicts.append(Verdict("no-verify", f"git {call.subcommand} {skipping}"))
    if _hooks_redirected(call.configs):
        verdicts.append(Verdict("hooks-path", "git -c core.hooksPath"))
    rule = _SUBCOMMANDS.get(call.subcommand)
    return verdicts + (rule(call, context, guard) if rule is not None else [])


def switched_branch(words: Sequence[str], cwd: Path) -> str | None:
    """The branch ``git switch`` or ``git checkout`` moves to, if the command names one."""
    call = invocation(words, cwd)
    if call.subcommand not in {"switch", "checkout"} or "--" in call.args:
        return None
    options = scan(call.args, _SWITCH)
    for flag in _SWITCH_FLAGS:
        if flag in options.values:
            return options.values[flag]
    return options.operands[0] if options.operands else None
