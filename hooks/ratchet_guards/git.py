import shlex
from collections.abc import Callable, Sequence
from dataclasses import dataclass, replace
from itertools import dropwhile
from pathlib import Path

from ratchet_guards import environment, merges, refmoves
from ratchet_guards.context import Context, Guard
from ratchet_guards.gitcall import Invocation, invocation
from ratchet_guards.options import NO_VALUES, Options, Spec, expand_long, scan
from ratchet_guards.refs import MAIN_BRANCHES, tracking
from ratchet_guards.shell import PLACEHOLDER
from ratchet_guards.verdict import Verdict

_FORCE = frozenset({"-f", "--force", "--force-with-lease", "--force-if-includes", "--mirror"})
_ALL_BRANCHES = frozenset({"--all", "--branches"})
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
_PUSH_SHORTEST = {
    "--all": len("--al"),
    "--branches": len("--b"),
    "--mirror": len("--m"),
    "--force-with-lease": len("--force-w"),
    "--force-if-includes": len("--force-i"),
}
_PATHS = Spec("bBs", frozenset({"--source", "--orphan"}))
_SWITCH = Spec("bBcC", frozenset({"--create", "--force-create", "--orphan"}))
_SWITCH_FLAGS = ("-b", "-B", "-c", "-C", "--create", "--force-create", "--orphan")
_TRACKS = frozenset({"-t", "--track"})
_DETACHES = frozenset({"-d", "--detach"})
_SWITCH_LONG = _SWITCH.long_values | _TRACKS | _DETACHES | {"--force"}
_UNKNOWN_MARKS = ("$", "`", PLACEHOLDER)
_PUSHED = "@{push}"
_SETTLED_FETCH = frozenset({"origin", "--prune", "-p", "--all", "--tags", "-q", "--quiet"})
_SETTLED_STASH = frozenset({"push", "-u", "--include-untracked", "-q", "--quiet"})
_QUIET = frozenset({"-q", "--quiet"})
_KEEPS_REMOTE = frozenset({"branch", "tag", "switch", "checkout", "status", "stash"})
_BRANCH_KEY = "branch."
_UPSTREAM_KEYS = (".merge", ".remote")
_OUTPUT = "--output"
_OUTPUT_INDICATOR = "--output-indicator"
_OUTPUT_SHORT = {"format-patch": "-o", "archive": "-o", "config": "-f"}
_CONFIG_FILE = "--file"
_CONFIG_FILE_SHORTEST = len("--fil")
_EXEC_VALUES = {"rebase": ("--exec", "-x"), "difftool": ("--extcmd", "-x")}
_EXEC_SHORTEST = len("--ex")
_RUNNERS = {"bisect": "run", "submodule": "foreach"}
_SHELL_ALIAS = "!"
_NO_VERIFY = "--no-verify"
_NO_VERIFY_SHORTEST = len("--no-veri")
_AM_NO_VERIFY_SHORTEST = len("--no-v")
_AM = Spec("Cp", frozenset({"--directory", "--exclude", "--include", "--whitespace"}))


def _skips_verify(word: str, subcommand: str) -> bool:
    shortest = _AM_NO_VERIFY_SHORTEST if subcommand == "am" else _NO_VERIFY_SHORTEST
    return len(word) >= shortest and _NO_VERIFY.startswith(word)


def _hooks_redirected(configs: Sequence[str]) -> bool:
    return any(config.partition("=")[0].lower() == _HOOKS_PATH for config in configs)


def _final(call: Invocation, context: Context, guard: Guard) -> list[Verdict]:
    del context
    branch = guard.repo.awaiting_final(call.cwd)
    if branch is None:
        return []
    return [Verdict("queue-final-check", f"git {call.subcommand} on `{branch}`")]


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


def _push_flag(flag: str) -> str:
    for full, shortest in _PUSH_SHORTEST.items():
        if len(flag) >= shortest and full.startswith(flag):
            return full
    return flag


def _push(call: Invocation, context: Context, guard: Guard) -> list[Verdict]:
    scanned = scan(call.args, _PUSH)
    options = replace(scanned, flags=frozenset(_push_flag(flag) for flag in scanned.flags))
    refspecs = options.operands[1:]
    targets = list(refmoves.push_targets(refspecs)) or [None]
    named = [target for target in targets if target is not None]
    verdicts: list[Verdict] = []
    if options.flags & _FORCE or any(refspec.startswith("+") for refspec in refspecs):
        verdicts.append(Verdict("force-push", "git push " + " ".join(call.args)))
    current = guard.branches(context, call.cwd) if None in targets else frozenset()
    bare = [spec.removeprefix("+") for spec in refspecs if ":" not in spec]
    pushed = (
        guard.repo.full_name(call.cwd, f"{name}{_PUSHED}") for name in (*sorted(current), *bare)
    )
    names = guard.defaults(context, call.cwd)
    default = current & names or refmoves.pushes_default(named, names)
    if options.flags & _ALL_BRANCHES or default or any(name in tracking(names) for name in pushed):
        verdicts.append(Verdict("push-main", "git push " + " ".join(call.args)))
    return verdicts + refmoves.pushes(call, named, names)


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
    if not options.flags <= _READ_CONFIG | _CONFIG_MODIFIERS:
        return False
    verb = options.operands[0] if options.operands else ""
    return bool(options.flags & _READ_CONFIG) or verb == _READ_VERB or len(options.operands) == 1


def _paths(call: Invocation, context: Context, guard: Guard) -> list[Verdict]:
    operands = scan(call.args, _PATHS).operands
    hits = guard.locator.words(operands, call.cwd, destructive=True)
    by = f"git {call.subcommand}"
    return (
        guard.writes(context, hits, lambda hit: f"{by} {hit.path} ({hit.group.name})")
        + guard.acting(guard.locator.paths(operands, call.cwd))
        + guard.unfollowed(context, operands, destructive=True, by=by)
    )


def _names_output(option: str, subcommand: str) -> bool:
    if subcommand == "config":
        return len(option) >= _CONFIG_FILE_SHORTEST and _CONFIG_FILE.startswith(option)
    return option.startswith(_OUTPUT) and not option.startswith(_OUTPUT_INDICATOR)


def _outputs(call: Invocation) -> list[str]:
    short = _OUTPUT_SHORT.get(call.subcommand)
    paths: list[str] = []
    args = iter(call.args)
    for arg in args:
        option, equals, value = arg.partition("=")
        if arg.startswith("--") and _names_output(option, call.subcommand):
            paths.append(value if equals else next(args, ""))
        elif short is not None and arg.startswith(short) and not arg.startswith("--"):
            paths.append(arg.removeprefix(short) or next(args, ""))
    bundle = call.subcommand == "bundle" and call.args[:1] == ("create",)
    return paths + [arg for arg in call.args[1:] if bundle and not arg.startswith("-")][:1]


def _written(call: Invocation, context: Context, guard: Guard) -> list[Verdict]:
    outputs = _outputs(call)
    hits = guard.locator.words(outputs, call.cwd, destructive=False)
    by = f"git {call.subcommand}"
    return (
        guard.writes(context, hits, lambda hit: f"{by} writes {hit.path} ({hit.group.name})")
        + guard.acting(guard.locator.paths(outputs, call.cwd))
        + guard.unfollowed(context, outputs, destructive=False, by=by)
    )


def _is_option(word: str) -> bool:
    return word.startswith("-")


def scripts(words: Sequence[str], cwd: Path) -> list[str]:
    call = invocation(words, cwd)
    found = [config.partition("=")[2] for config in call.configs]
    long, short = _EXEC_VALUES.get(call.subcommand, ("", ""))
    for index, arg in enumerate(call.args):
        option, equals, value = arg.partition("=")
        following = " ".join(call.args[index + 1 : index + 2])
        if len(option) >= _EXEC_SHORTEST and long.startswith(option):
            found.append(value if equals else following)
        elif short and arg.startswith(short) and not arg.startswith("--"):
            found.append(arg.removeprefix(short) or following)
        elif arg == _RUNNERS.get(call.subcommand):
            command = list(dropwhile(_is_option, call.args[index + 1 :]))
            found += [shlex.join(command), " ".join(command)]
    stored = [arg for arg in call.args if call.subcommand == "config" and not _is_option(arg)]
    return [script.removeprefix(_SHELL_ALIAS) for script in (*found, *stored) if script]


Rule = Callable[[Invocation, Context, Guard], list[Verdict]]


def _all(*rules: Rule) -> Rule:
    def check(call: Invocation, context: Context, guard: Guard) -> list[Verdict]:
        return [verdict for rule in rules for verdict in rule(call, context, guard)]

    return check


_SUBCOMMANDS: dict[str, Rule] = {
    "commit": _all(_commit, _final),
    "am": _all(_am, _final, merges.cherry_pick),
    "read-tree": merges.read_tree,
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
    "tag": refmoves.tag,
    "update-ref": refmoves.update_ref,
    "symbolic-ref": refmoves.symbolic_ref,
    "worktree": refmoves.worktree,
    "fetch": refmoves.fetch,
    "pull": _all(refmoves.fetch, merges.pull),
    "merge": merges.merge,
    "cherry-pick": _all(_final, merges.cherry_pick),
    "revert": _final,
    "rebase": refmoves.rebase,
    "remote": refmoves.remote,
    "reflog": refmoves.reflog,
    "replace": refmoves.refused,
    "fast-import": refmoves.refused,
    "send-pack": refmoves.refused,
    "receive-pack": refmoves.refused,
}


def check(words: Sequence[str], context: Context, guard: Guard) -> list[Verdict]:
    call = invocation(words, context.cwd)
    if call.redirected or context.exported & environment.REPOSITORIES:
        context = context.elsewhere()
    home = sorted(context.exported & environment.HOMES)
    verdicts = [Verdict("hooks-path", f"git with {name} set") for name in home[:1]]
    skipping = next((arg for arg in call.args if _skips_verify(arg, call.subcommand)), None)
    if skipping is not None:
        verdicts.append(Verdict("no-verify", f"git {call.subcommand} {skipping}"))
    if _hooks_redirected(call.configs):
        verdicts.append(Verdict("hooks-path", "git -c core.hooksPath"))
    verdicts += refmoves.configured(call)
    verdicts += _written(call, context, guard)
    rule = _SUBCOMMANDS.get(call.subcommand)
    return verdicts + (rule(call, context, guard) if rule is not None else [])


def _pointed_head(call: Invocation) -> str | None:
    head, *target = scan(call.args, Spec("m")).operands or ("",)
    return target[0].removeprefix("refs/heads/") if head == "HEAD" and len(target) == 1 else None


def settled(words: Sequence[str], cwd: Path) -> bool:
    call = invocation(words, cwd)
    args = frozenset(call.args)
    plain = not call.configs and not call.redirected
    if call.subcommand == "fetch":
        return plain and args <= _SETTLED_FETCH | MAIN_BRANCHES
    if call.subcommand == "stash":
        return plain and args <= _SETTLED_STASH
    if call.subcommand in {"switch", "checkout"}:
        names = sorted(args - _QUIET)
        return plain and len(names) == 1 and not names[0].startswith("-")
    return plain and call.subcommand == "status"


def keeps_remote(words: Sequence[str], cwd: Path) -> bool:
    call = invocation(words, cwd)
    if call.configs or call.redirected:
        return False
    if call.subcommand in _KEEPS_REMOTE:
        return True
    args = call.args
    if any("$" in arg for arg in args):
        return False
    if call.subcommand == "fetch":
        return all(arg in _SETTLED_FETCH or not arg.startswith("-") for arg in args)
    if call.subcommand == "remote":
        return args[:1] == ("add",)
    key = args[0].lower() if args else ""
    upstream = key.startswith(_BRANCH_KEY) and key.endswith(_UPSTREAM_KEYS)
    return call.subcommand == "config" and upstream and len(args[1:]) == 1


@dataclass(frozen=True, slots=True)
class Switch:
    branches: frozenset[str]
    certain: bool = False


def _anywhere(guard: Guard, cwd: Path) -> Switch:
    return Switch(MAIN_BRANCHES | guard.repo.default_branches(cwd))


def _unknown(name: str) -> bool:
    return name == "-" or name.startswith("@") or any(mark in name for mark in _UNKNOWN_MARKS)


def _checked_out(call: Invocation, guard: Guard, *, here: bool) -> Switch | None:
    args = list(call.args)
    paths = args[args.index("--") + 1 :] if "--" in args and call.subcommand == "checkout" else []
    options = scan(expand_long(args, _SWITCH_LONG), _SWITCH)
    created = next((options.values[flag] for flag in _SWITCH_FLAGS if flag in options.values), None)
    name = options.operands[0] if options.operands else None
    if created is not None:
        return (
            _anywhere(guard, call.cwd) if _unknown(created) else Switch(frozenset({created}), here)
        )
    if name is None or paths or options.flags & _DETACHES:
        return None
    if options.flags & _TRACKS or _unknown(name):
        return _anywhere(guard, call.cwd)
    branch = call.subcommand == "switch" or (
        guard.repo.full_name(call.cwd, name) == f"refs/heads/{name}"
    )
    return Switch(frozenset({name}), here and branch)


def switch(words: Sequence[str], context: Context, guard: Guard) -> Switch | None:
    call = invocation(words, context.cwd)
    here = call.cwd == context.cwd and not call.redirected
    here = here and not context.exported & (environment.REPOSITORIES | environment.LOOKUPS)
    if call.subcommand == "bisect":
        return _anywhere(guard, call.cwd)
    if call.subcommand == "symbolic-ref":
        pointed = _pointed_head(call)
        return None if pointed is None else Switch(frozenset({pointed}), here)
    if call.subcommand in {"switch", "checkout"}:
        return _checked_out(call, guard, here=here)
    return None
