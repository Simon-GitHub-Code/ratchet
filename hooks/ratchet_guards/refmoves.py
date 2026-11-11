"""Rules for the ``git`` commands that write the refs the guard reads the default branch through.

Each rule reads one subcommand's arguments the way git does: a long option is any unambiguous
prefix of its name, flags bundle, ``-C`` and ``git -c`` come before the subcommand, and a
refspec's destination is what follows its colon. ``ratchet_guards.refs`` says which names are
protected. What these rules cannot see is a ref name that a variable, a file or a pipe supplies
(ADR 0016).
"""

from collections.abc import Sequence

from ratchet_guards.context import Context, Guard
from ratchet_guards.gitcall import Invocation
from ratchet_guards.options import Spec, expand_long, scan
from ratchet_guards.refs import REMOTE, protects
from ratchet_guards.verdict import Verdict

# Why: a config key that decides what a fetch writes into a remote-tracking ref (`remote.*`,
# `url.*.insteadOf`), what a command line means (`alias.*`), or which file git reads more config
# from (`include.*`) re-points the default branch as surely as a ref write does.
TRUSTED_KEYS = ("alias.", "remote.", "url.", "include.", "includeif.", "extensions.")
TRUSTED_CORE_KEYS = frozenset(
    {"core.usereplacerefs", "core.alternaterefscommand", "core.alternaterefsprefixes"}
    | {"core.worktree", "core.bare"}
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
# Why: with any of these a `git branch` call reads or sets configuration and creates nothing.
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
# Why: `--force` is a real option of `git switch` and `git checkout`, and an exact name beats a
# longer one it prefixes, so it must be among the names or it would read as `--force-create`.
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


def _refused(call: Invocation, because: str = "") -> list[Verdict]:
    return [Verdict("default-ref", because or " ".join(("git", call.subcommand, *call.args)))]


def _writes(call: Invocation, names: Sequence[str | None], *, heads: bool = True) -> list[Verdict]:
    """A verdict when any of ``names`` is a ref the call writes and the guard reads."""
    return _refused(call) if any(protects(n, heads=heads) for n in names if n) else []


def trusted_key(key: str) -> bool:
    """Whether a git config key, in any case, sets something the default branch is read through."""
    lowered = key.lower()
    return lowered.startswith(TRUSTED_KEYS) or lowered in TRUSTED_CORE_KEYS


def configured(call: Invocation) -> list[Verdict]:
    """A ``git -c`` setting of a trusted key, which re-points what the command then does."""
    keys = [config.partition("=")[0] for config in call.configs]
    trusted = next(filter(trusted_key, keys), None)
    return _refused(call, f"git -c {trusted}") if trusted else []


def edits_config(flags: frozenset[str]) -> bool:
    """Whether ``git config`` opens its file in an editor: ``-e`` or a prefix of ``--edit``."""
    return "-e" in flags or any(f.startswith("--ed") and "--edit".startswith(f) for f in flags)


def _branch_names(call: Invocation, context: Context, guard: Guard) -> list[str]:
    """The refs a ``git branch`` call creates, deletes, renames away or copies onto."""
    options = scan(expand_long(call.args, _BRANCH_LONG), _BRANCH)
    flags, operands = options.flags, list(options.operands)
    if flags & {"-d", "-D", "--delete"}:
        prefix = "refs/remotes/" if flags & {"-r", "--remotes"} else ""
        return [prefix + name for name in operands]
    if flags & {"-m", "-M", "--move"}:
        renamed = [guard.branch(context, call.cwd)] if len(operands) == 1 else []
        return [*operands, *filter(None, renamed)]
    if flags & {"-c", "-C", "--copy"}:
        return operands[-1:]
    return [] if flags & _BRANCH_READS else operands[:1]


def branch(call: Invocation, context: Context, guard: Guard) -> list[Verdict]:
    """``git branch`` creating, forcing, deleting, renaming or copying onto a default branch."""
    return _writes(call, _branch_names(call, context, guard))


def update_ref(call: Invocation, context: Context, guard: Guard) -> list[Verdict]:
    """``git update-ref`` on a protected ref, on ``HEAD`` while on main, or with ``--stdin``."""
    options = scan(expand_long(call.args, _UPDATE_REF_LONG), _UPDATE_REF)
    ref = options.operands[0] if options.operands else ""
    if "--stdin" in options.flags:
        return _refused(call)
    if ref == "HEAD" and "--no-deref" not in options.flags:
        return _refused(call) if guard.on_main(context, call.cwd) else []
    return _writes(call, [ref])


def symbolic_ref(call: Invocation, context: Context, guard: Guard) -> list[Verdict]:
    """``git symbolic-ref`` setting or deleting a protected ref; reading one is fine."""
    del context, guard
    options = scan(expand_long(call.args, _SYMBOLIC_REF_LONG), _SYMBOLIC_REF)
    writes = bool(options.flags & {"-d", "--delete"}) or len(options.operands) > 1
    return _writes(call, options.operands[:1]) if writes else []


def creates(call: Invocation, context: Context, guard: Guard) -> list[Verdict]:
    """``git switch`` and ``git checkout`` creating or resetting a branch named like a default."""
    del context, guard
    options = scan(expand_long(call.args, _CREATE_LONG), _CREATE)
    return _writes(call, [options.values.get(flag) for flag in _CREATE_FLAGS])


def worktree(call: Invocation, context: Context, guard: Guard) -> list[Verdict]:
    """``git worktree add -b`` or ``-B`` naming a default branch."""
    del context, guard
    if call.args[:1] != ("add",):
        return []
    options = scan(call.args[1:], _WORKTREE_ADD)
    return _writes(call, [options.values.get("-b"), options.values.get("-B")])


def fetch(call: Invocation, context: Context, guard: Guard) -> list[Verdict]:
    """``git fetch`` and ``git pull`` with a refspec whose destination is a protected ref."""
    del context, guard
    options = scan(expand_long(call.args, _FETCH_LONG), _FETCH)
    refspecs = [*options.operands[1:], options.values.get("--refmap", "")]
    return _writes(call, [spec.removeprefix("+").partition(":")[2] for spec in refspecs])


def pushes(call: Invocation, refspecs: Sequence[str]) -> list[Verdict]:
    """A push whose destination is a remote-tracking or replace ref, which ``git push .`` writes.

    Pushes to the default branch's own name are ``push-main``'s.
    """
    targets = [spec.removeprefix("+").rpartition(":")[2] for spec in refspecs]
    return _writes(call, targets, heads=False)


def rebase(call: Invocation, context: Context, guard: Guard) -> list[Verdict]:
    """``git rebase`` run on a default branch, or naming one as the branch to rebase."""
    options = scan(expand_long(call.args, _REBASE_LONG), _REBASE)
    operands = options.operands
    named = operands[:1] if "--root" in options.flags else operands[1:2]
    return _refused(call) if _writes(call, named) or guard.on_main(context, call.cwd) else []


def reset(call: Invocation, context: Context, guard: Guard) -> list[Verdict]:
    """``git reset`` that moves the branch it runs on, refused while that is a default branch."""
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
    """``git remote`` changes to ``origin``'s URL, name or head, or a new remote that mirrors."""
    del context, guard
    options = scan(expand_long(call.args, _REMOTE_LONG), _REMOTE)
    verb, *rest = options.operands or ("",)
    adds_origin = verb == "add" and rest[:1] == [REMOTE]
    redirects = verb == "add" and bool(options.flags & _REMOTE_ADD_REDIRECTS)
    return _refused(call) if verb in _REMOTE_REWRITES or adds_origin or redirects else []


def refused(call: Invocation, context: Context, guard: Guard) -> list[Verdict]:
    """A command that writes refs or objects from input the guard cannot read."""
    del context, guard
    return _refused(call)
