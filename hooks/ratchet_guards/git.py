"""Rules for ``git``: hook bypasses, history rewrites, pushes to main and guard-path writes.

The commands that write the default branch's ref are ``ratchet_guards.refmoves``'s.
"""

from collections.abc import Callable, Sequence
from dataclasses import replace
from pathlib import Path

from ratchet_guards import refmoves
from ratchet_guards.context import Context, Guard
from ratchet_guards.gitcall import Invocation, invocation
from ratchet_guards.options import NO_VALUES, Options, Spec, scan
from ratchet_guards.refs import MAIN_BRANCHES
from ratchet_guards.verdict import Verdict

_FORCE = frozenset({"-f", "--force", "--force-with-lease", "--force-if-includes", "--mirror"})
_ALL_BRANCHES = frozenset({"--all", "--branches"})
# Why: `git config <key>` alone reads and `git config <key> <value>` writes, so a flag is not
# what tells them apart. A call reads when every flag is a known read action or modifier and
# it names an action, the `get` verb of the newer syntax, or a lone key. Git takes
# any unambiguous prefix of a long option, so `--unset-a` is `--unset-all`: a flag outside this
# list, a prefix of a writing flag included, makes the call a write.
_READ_CONFIG = frozenset({"--get", "--get-all", "--get-regexp", "--get-urlmatch", "-l", "--list"})
_CONFIG_MODIFIERS = frozenset(
    {"--show-origin", "--show-scope", "--global", "--system", "--local", "--worktree", "-z"}
    | {"--null", "--name-only", "--includes", "--no-includes", "--all", "--regexp", "--value"}
    | {"--fixed-value", "--show-names", "--no-show-names", "--type", "--bool", "--int", "--path"}
    | {"--bool-or-int", "--expiry-date"}
)
_READ_VERB = "get"
_HOOKS_PATH = "core.hookspath"
_COMMIT = Spec(
    "mFcCt",
    frozenset(
        {"--message", "--file", "--reuse-message", "--reedit-message", "--template", "--author"}
        | {"--date", "--fixup", "--squash", "--trailer", "--cleanup"}
    ),
)
_PUSH = Spec("o", frozenset({"--push-option", "--repo", "--receive-pack", "--exec"}))
# Why: git takes any unambiguous prefix of a long option. Each push flag the guard rules on maps
# to the length of its shortest unambiguous spelling among push's options: `--al` (`--atomic`
# shares `--a`), `--m`, `--force-w`, `--force-i` (`--force` and `--follow-tags` share the rest)
# and `--b`. `--forc` is ambiguous, git refuses it, and it stays unrecognised here.
_PUSH_SHORTEST = {
    "--all": len("--al"),
    "--branches": len("--b"),
    "--mirror": len("--m"),
    "--force-with-lease": len("--force-w"),
    "--force-if-includes": len("--force-i"),
}
_PATHS = Spec("bBs", frozenset({"--source", "--orphan"}))
_SWITCH = Spec("bBcC", frozenset({"--create", "--force-create"}))
_SWITCH_FLAGS = ("-b", "-B", "-c", "-C", "--create", "--force-create")
_NO_VERIFY = "--no-verify"
# Why: git takes any unambiguous prefix of a long option; `--no-ver` is ambiguous with
# `--no-verbose` and git refuses it, so `--no-veri` is the shortest spelling that skips hooks.
_NO_VERIFY_SHORTEST = len("--no-veri")
# `git am` has no `--verbose`, so `--no-v` is already unambiguous there.
_AM_NO_VERIFY_SHORTEST = len("--no-v")
_AM = Spec("Cp", frozenset({"--directory", "--exclude", "--include", "--whitespace"}))


def _skips_verify(word: str, subcommand: str) -> bool:
    shortest = _AM_NO_VERIFY_SHORTEST if subcommand == "am" else _NO_VERIFY_SHORTEST
    return len(word) >= shortest and _NO_VERIFY.startswith(word)


def _hooks_redirected(configs: Sequence[str]) -> bool:
    return any(config.partition("=")[0].lower() == _HOOKS_PATH for config in configs)


def _commit(call: Invocation, context: Context, guard: Guard) -> list[Verdict]:
    del context
    options = scan(call.args, _COMMIT)
    verdicts = [Verdict("no-verify", "git commit -n")] if "-n" in options.flags else []
    if "--amend" in options.flags and guard.repo.head_is_pushed(call.cwd):
        verdicts.append(Verdict("amend-pushed", "git commit --amend on a pushed HEAD"))
    return verdicts


def _am(call: Invocation, context: Context, guard: Guard) -> list[Verdict]:
    del context, guard
    if "-n" in scan(call.args, _AM).flags:
        return [Verdict("no-verify", "git am -n")]
    return []


def _destination(refspec: str, current: str | None) -> str | None:
    target = refspec.lstrip("+").rpartition(":")[2]
    if target == "HEAD":
        return current
    return target.removeprefix("refs/heads/")


def _push_flag(flag: str) -> str:
    for full, shortest in _PUSH_SHORTEST.items():
        if len(flag) >= shortest and full.startswith(flag):
            return full
    return flag


def _push(call: Invocation, context: Context, guard: Guard) -> list[Verdict]:
    scanned = scan(call.args, _PUSH)
    options = replace(scanned, flags=frozenset(_push_flag(flag) for flag in scanned.flags))
    refspecs = options.operands[1:]
    verdicts: list[Verdict] = []
    if options.flags & _FORCE or any(refspec.startswith("+") for refspec in refspecs):
        verdicts.append(Verdict("force-push", "git push " + " ".join(call.args)))
    if _pushes_main(options, context, guard, cwd=call.cwd):
        verdicts.append(Verdict("push-main", "git push " + " ".join(call.args)))
    return verdicts + refmoves.pushes(call, refspecs)


def _pushes_main(options: Options, context: Context, guard: Guard, *, cwd: Path) -> bool:
    if options.flags & _ALL_BRANCHES:
        return True
    refspecs = options.operands[1:]
    current = guard.branch(context, cwd)
    destinations = [_destination(refspec, current) for refspec in refspecs] or [current]
    return any(destination in MAIN_BRANCHES for destination in destinations)


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
    if _reads_config(options):
        return []
    verdicts = [Verdict("hooks-path", "git config core.hooksPath")] if _HOOKS_PATH in keys else []
    if refmoves.edits_config(options.flags) and not verdicts:
        return [Verdict("default-ref", "git config --edit")]
    trusted = sorted(filter(refmoves.trusted_key, keys))
    return verdicts + ([Verdict("default-ref", f"git config {trusted[0]}")] if trusted else [])


def _reads_config(options: Options) -> bool:
    """Whether a ``git config`` call that names a key only reads."""
    if not options.flags <= _READ_CONFIG | _CONFIG_MODIFIERS:
        return False
    verb = options.operands[0] if options.operands else ""
    return bool(options.flags & _READ_CONFIG) or verb == _READ_VERB or len(options.operands) == 1


def _paths(call: Invocation, context: Context, guard: Guard) -> list[Verdict]:
    del context
    operands = scan(call.args, _PATHS).operands
    hits = guard.locator.words(operands, call.cwd, destructive=True)
    return [
        Verdict("guard-path-write", f"git {call.subcommand} {hit.path} ({hit.group.name})")
        for hit in hits
    ]


Rule = Callable[[Invocation, Context, Guard], list[Verdict]]


def _all(*rules: Rule) -> Rule:
    def check(call: Invocation, context: Context, guard: Guard) -> list[Verdict]:
        return [verdict for rule in rules for verdict in rule(call, context, guard)]

    return check


_SUBCOMMANDS: dict[str, Rule] = {
    "commit": _commit,
    "am": _am,
    "commit-tree": _commit_tree,
    "push": _push,
    "reset": refmoves.reset,
    "filter-branch": _rewrite,
    "filter-repo": _rewrite,
    "config": _config,
    "rm": _paths,
    "mv": _paths,
    "restore": _paths,
    "checkout": _all(_paths, refmoves.creates),
    "switch": refmoves.creates,
    "branch": refmoves.branch,
    "update-ref": refmoves.update_ref,
    "symbolic-ref": refmoves.symbolic_ref,
    "worktree": refmoves.worktree,
    "fetch": refmoves.fetch,
    "pull": refmoves.fetch,
    "rebase": refmoves.rebase,
    "remote": refmoves.remote,
    "replace": refmoves.refused,
    "fast-import": refmoves.refused,
    "send-pack": refmoves.refused,
    "receive-pack": refmoves.refused,
}


def check(words: Sequence[str], context: Context, guard: Guard) -> list[Verdict]:
    """Verdicts for one ``git`` command."""
    call = invocation(words, context.cwd)
    verdicts: list[Verdict] = []
    skipping = next((arg for arg in call.args if _skips_verify(arg, call.subcommand)), None)
    if skipping is not None:
        verdicts.append(Verdict("no-verify", f"git {call.subcommand} {skipping}"))
    if _hooks_redirected(call.configs):
        verdicts.append(Verdict("hooks-path", "git -c core.hooksPath"))
    verdicts += refmoves.configured(call)
    rule = _SUBCOMMANDS.get(call.subcommand)
    return verdicts + (rule(call, context, guard) if rule is not None else [])


def _pointed_head(call: Invocation) -> str | None:
    """The branch ``git symbolic-ref HEAD refs/heads/<branch>`` checks out without a switch."""
    head, *target = scan(call.args, Spec("m")).operands or ("",)
    return target[0].removeprefix("refs/heads/") if head == "HEAD" and len(target) == 1 else None


def switched_branch(words: Sequence[str], cwd: Path) -> str | None:
    """The branch ``git switch`` or ``git checkout`` moves to, if the command names one."""
    call = invocation(words, cwd)
    if call.subcommand == "symbolic-ref":
        return _pointed_head(call)
    if call.subcommand not in {"switch", "checkout"} or "--" in call.args:
        return None
    options = scan(call.args, _SWITCH)
    for flag in _SWITCH_FLAGS:
        if flag in options.values:
            return options.values[flag]
    return options.operands[0] if options.operands else None
