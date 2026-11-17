from collections.abc import Iterator, Sequence
from itertools import pairwise

from ratchet_guards.committed import anchored_on_reflog
from ratchet_guards.context import Context, Guard
from ratchet_guards.gitcall import Invocation
from ratchet_guards.options import Spec, expand_long, scan
from ratchet_guards.refs import MAIN_BRANCHES, REMOTE, default_branch, protected, protects, pushed
from ratchet_guards.verdict import Verdict

TRUSTED_KEYS = ("alias.", "remote.", "url.", "include.", "includeif.", "extensions.")
TRUSTED_CORE_KEYS = frozenset(
    {"core.usereplacerefs", "core.alternaterefscommand", "core.alternaterefsprefixes"}
    | {"core.worktree", "core.bare", "push.default"}
)
_BRANCH_VALUES = frozenset(
    {"--set-upstream-to", "--contains", "--no-contains", "--merged", "--no-merged"}
    | {"--points-at", "--sort", "--format"}
)
_BRANCH_FLAGS = frozenset(
    {"--list", "--show-current", "--unset-upstream", "--edit-description", "--force"}
    | {"--delete", "--move", "--copy", "--remotes"}
)
_BRANCH_LONG = _BRANCH_VALUES | _BRANCH_FLAGS
_BRANCH_READS = _BRANCH_VALUES - {"--sort", "--format"} | {
    "-l",
    "-u",
    "--list",
    "--show-current",
    "--unset-upstream",
    "--edit-description",
}
_BRANCH = Spec("u", _BRANCH_VALUES)
_CREATE_FLAGS = ("-b", "-B", "-c", "-C", "--create", "--force-create", "--orphan")
_CREATE_VALUES = frozenset({"--create", "--force-create", "--orphan"})
_CREATE_LONG = _CREATE_VALUES | {"--force", "--detach", "--merge", "--track", "--no-track"}
_CREATE = Spec("bBcC", _CREATE_VALUES)
_WORKTREE_ADD = Spec("bB", frozenset({"--reason"}))
_UPDATE_REF_LONG = frozenset({"--stdin", "--no-deref", "--create-reflog", "--batch-updates"})
_UPDATE_REF = Spec("m")
_SYMBOLIC_REF_LONG = frozenset({"--delete", "--quiet", "--short", "--recurse", "--no-recurse"})
_SYMBOLIC_REF = Spec("m")
_FETCH_VALUES = frozenset(
    {"--refmap", "--depth", "--deepen", "--shallow-since", "--shallow-exclude", "--jobs"}
    | {"--server-option", "--upload-pack", "--negotiation-tip", "--filter", "--submodule-prefix"}
)
_FETCH = Spec("jo", _FETCH_VALUES)
_FETCH_LONG = _FETCH_VALUES | {"--force", "--prune", "--all", "--tags", "--update-head-ok"}
_REBASE_VALUES = frozenset(
    {"--onto", "--strategy", "--strategy-option", "--exec", "--empty", "--whitespace"}
)
_REBASE = Spec("sXxC", _REBASE_VALUES)
_REBASE_LONG = _REBASE_VALUES | {"--root", "--abort", "--continue", "--skip", "--quit"}
_RESET_MODES = frozenset({"--soft", "--mixed", "--merge", "--keep"})
_RESET_VALUES = frozenset({"--pathspec-from-file"})
_RESET_LONG = _RESET_MODES | _RESET_VALUES | {"--hard", "--patch", "--pathspec-file-nul"}
_RESET = Spec("", _RESET_VALUES)
_REMOTE_REWRITES = frozenset({"rename", "remove", "rm", "set-url", "set-head", "set-branches"})
_REMOTE_LONG = frozenset({"--mirror", "--master", "--track", "--tags", "--no-tags", "--fetch"})
_REMOTE_ADD_REDIRECTS = frozenset({"-m", "--master", "--mirror"})
_REMOTE = Spec("tm", frozenset({"--track", "--master"}))
_HEAD = frozenset({"HEAD", "@"})
_TAG_VALUES = frozenset(
    {"--message", "--file", "--local-user", "--cleanup", "--sort", "--format", "--contains"}
    | {"--no-contains", "--merged", "--no-merged", "--points-at"}
)
_TAG = Spec("mFu", _TAG_VALUES)
_TAG_LONG = _TAG_VALUES | {"--list", "--delete", "--verify", "--force", "--column", "--annotate"}
_TAG_READS = _TAG_VALUES - {"--message", "--file", "--local-user", "--cleanup"} | {
    "-l",
    "-n",
    "-d",
    "-v",
    "--list",
    "--delete",
    "--verify",
    "--column",
}
_MATCHING = ":"
_EVERY_BRANCH = "refs/heads/*"


def _refused(call: Invocation, because: str = "") -> list[Verdict]:
    return [Verdict("default-ref", because or " ".join(("git", call.subcommand, *call.args)))]


def _writes(
    call: Invocation, written: Sequence[str | None], guard: Guard, *, context: Context
) -> list[Verdict]:
    names = guard.defaults(context, call.cwd)
    return _refused(call) if any(protects(n, names) for n in written if n) else []


def _unrecorded_move(context: Context, call: Invocation, guard: Guard) -> bool:
    return guard.on_main(context, call.cwd) and anchored_on_reflog(call.cwd)


def trusted_key(key: str) -> bool:
    lowered = key.lower()
    return lowered.startswith(TRUSTED_KEYS) or lowered in TRUSTED_CORE_KEYS


def configured(call: Invocation) -> list[Verdict]:
    keys = [config.partition("=")[0] for config in call.configs]
    trusted = next(filter(trusted_key, keys), None)
    return _refused(call, f"git -c {trusted}") if trusted else []


def edits_config(flags: frozenset[str]) -> bool:
    return "-e" in flags or any(f.startswith("--ed") and "--edit".startswith(f) for f in flags)


def _branch_names(call: Invocation, context: Context, guard: Guard) -> list[str]:
    options = scan(expand_long(call.args, _BRANCH_LONG), _BRANCH)
    flags, operands = options.flags, list(options.operands)
    if flags & {"-d", "-D", "--delete"}:
        prefix = "refs/remotes/" if flags & {"-r", "--remotes"} else ""
        return [prefix + name for name in operands]
    if flags & {"-m", "-M", "--move"}:
        renamed = sorted(guard.branches(context, call.cwd)) if len(operands) == 1 else []
        return [*operands, *renamed]
    if flags & {"-c", "-C", "--copy"}:
        return operands[-1:]
    return [] if flags & _BRANCH_READS else operands[:1]


def branch(call: Invocation, context: Context, guard: Guard) -> list[Verdict]:
    return _writes(call, _branch_names(call, context, guard), guard, context=context)


def update_ref(call: Invocation, context: Context, guard: Guard) -> list[Verdict]:
    options = scan(expand_long(call.args, _UPDATE_REF_LONG), _UPDATE_REF)
    ref = options.operands[0] if options.operands else ""
    if "--stdin" in options.flags:
        return _refused(call)
    if ref == "HEAD" and "--no-deref" not in options.flags:
        return _refused(call) if guard.on_main(context, call.cwd) else []
    return _writes(call, [ref], guard, context=context)


def symbolic_ref(call: Invocation, context: Context, guard: Guard) -> list[Verdict]:
    options = scan(expand_long(call.args, _SYMBOLIC_REF_LONG), _SYMBOLIC_REF)
    writes = bool(options.flags & {"-d", "--delete"}) or len(options.operands) > 1
    if writes and options.operands[:1] == ("HEAD",) and _unrecorded_move(context, call, guard):
        return _refused(call)
    return _writes(call, options.operands[:1], guard, context=context) if writes else []


def creates(call: Invocation, context: Context, guard: Guard) -> list[Verdict]:
    options = scan(expand_long(call.args, _CREATE_LONG), _CREATE)
    if "--orphan" in options.values and _unrecorded_move(context, call, guard):
        return _refused(call)
    return _writes(
        call, [options.values.get(flag) for flag in _CREATE_FLAGS], guard, context=context
    )


def worktree(call: Invocation, context: Context, guard: Guard) -> list[Verdict]:
    if call.args[:1] != ("add",):
        return []
    options = scan(call.args[1:], _WORKTREE_ADD)
    return _writes(
        call, [options.values.get("-b"), options.values.get("-B")], guard, context=context
    )


def fetch(call: Invocation, context: Context, guard: Guard) -> list[Verdict]:
    options = scan(expand_long(call.args, _FETCH_LONG), _FETCH)
    refspecs = [*options.operands[1:], options.values.get("--refmap", "")]
    tags = [f"refs/tags/{name}" for word, name in pairwise(refspecs) if word == "tag"]
    destinations = (spec.removeprefix("+").partition(":")[2] for spec in refspecs)
    return _writes(call, [*tags, *destinations], guard, context=context)


def tag(call: Invocation, context: Context, guard: Guard) -> list[Verdict]:
    options = scan(expand_long(call.args, _TAG_LONG), _TAG)
    reads = bool(options.flags & _TAG_READS)
    created = [f"refs/tags/{name}" for name in options.operands[:1]]
    return [] if reads else _writes(call, created, guard, context=context)


def push_targets(refspecs: Sequence[str]) -> Iterator[str | None]:
    words = iter(refspecs)
    for word in words:
        spec = word.removeprefix("+")
        source, _, destination = spec.partition(":")
        if spec == "tag":
            yield f"refs/tags/{next(words, '')}"
        elif spec == _MATCHING:
            yield _EVERY_BRANCH
        else:
            target = destination or source
            yield None if target in _HEAD else target


def pushes(
    call: Invocation, targets: Sequence[str], names: frozenset[str] = MAIN_BRANCHES
) -> list[Verdict]:
    spelled = [pushed(target) for target in targets]
    strays = [full for full in spelled if not any(default_branch(name, names) for name in full)]
    return _refused(call) if any(protected(n, names) for full in strays for n in full) else []


def pushes_default(targets: Sequence[str], names: frozenset[str] = MAIN_BRANCHES) -> bool:
    return any(default_branch(name, names) for target in targets for name in pushed(target))


def rebase(call: Invocation, context: Context, guard: Guard) -> list[Verdict]:
    options = scan(expand_long(call.args, _REBASE_LONG), _REBASE)
    operands = options.operands
    named = operands[:1] if "--root" in options.flags else operands[1:2]
    return (
        _refused(call)
        if _writes(call, named, guard, context=context) or guard.on_main(context, call.cwd)
        else []
    )


def reset(call: Invocation, context: Context, guard: Guard) -> list[Verdict]:
    args = expand_long(call.args, _RESET_LONG)
    cut = args.index("--") if "--" in args else len(args)
    options = scan(args[:cut], _RESET)
    operands = options.operands
    pathspec = bool(args[cut + 1 :]) or bool(
        options.flags & {"-p", "--patch", "--pathspec-from-file"}
    )
    names_revision = any(operand not in _HEAD for operand in operands)
    named = names_revision and (len(operands) == 1 or bool(options.flags & _RESET_MODES))
    moves = "--hard" in options.flags or (named and not pathspec)
    if moves and guard.on_main(context, call.cwd):
        return [Verdict("reset-main", f"git reset {' '.join(call.args)} on main")]
    return []


def remote(call: Invocation, context: Context, guard: Guard) -> list[Verdict]:
    del context, guard
    options = scan(expand_long(call.args, _REMOTE_LONG), _REMOTE)
    verb, *rest = options.operands or ("",)
    adds_origin = verb == "add" and rest[:1] == [REMOTE]
    redirects = verb == "add" and bool(options.flags & _REMOTE_ADD_REDIRECTS)
    return _refused(call) if verb in _REMOTE_REWRITES or adds_origin or redirects else []


def reflog(call: Invocation, context: Context, guard: Guard) -> list[Verdict]:
    del context, guard
    return _refused(call) if call.args[:1] in (("expire",), ("delete",)) else []


def refused(call: Invocation, context: Context, guard: Guard) -> list[Verdict]:
    del context, guard
    return _refused(call)
