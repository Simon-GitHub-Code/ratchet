from collections.abc import Sequence

from ratchet_guards.context import Context, Guard
from ratchet_guards.gitcall import Invocation
from ratchet_guards.options import Spec, expand_long, scan
from ratchet_guards.refs import MAIN_BRANCHES, REMOTE, tracking
from ratchet_guards.verdict import Verdict

_RESUMES = frozenset({"--abort", "--continue", "--quit", "--skip"})
_MERGE_VALUES = frozenset(
    {"--message", "--file", "--strategy", "--strategy-option", "--into-name", "--cleanup"}
)
_MERGE = Spec("mFsX", _MERGE_VALUES)
_MERGE_LONG = (
    _MERGE_VALUES
    | _RESUMES
    | {"--ff", "--ff-only", "--no-ff", "--allow-unrelated-histories", "--autostash"}
    | {"--commit", "--quiet", "--squash", "--stat", "--signoff", "--summary"}
)
_PULL_VALUES = _MERGE_VALUES | frozenset(
    {"--refmap", "--depth", "--deepen", "--shallow-since", "--shallow-exclude", "--jobs"}
    | {"--server-option", "--upload-pack", "--negotiation-tip", "--recurse-submodules"}
)
_PULL = Spec("josX", _PULL_VALUES)
_UPSTREAMS = frozenset({"@{u}", "@{upstream}"})
_UPSTREAM = "@{upstream}"
_REMOTE_DEFAULTS = MAIN_BRANCHES | {"HEAD"}
_PRINTING = ("color.", "advice.")


def _asks(call: Invocation, guard: Guard, *, sync: bool = False) -> list[Verdict]:
    detail = " ".join(("git", call.subcommand, *call.args))
    return [guard.merging("sync-default" if sync else "merge-default", detail, call.cwd)]


def _inert(call: Invocation) -> bool:
    keys = (config.partition("=")[0].lower() for config in call.configs)
    return all(key.startswith(_PRINTING) for key in keys)


def _readable(call: Invocation, context: Context) -> bool:
    unsure = context.unread or context.unsettled or context.repointed
    return _inert(call) and not unsure


def _tracks(names: Sequence[str], call: Invocation, context: Context, *, guard: Guard) -> bool:
    defaults = guard.defaults(context, call.cwd)
    mains = sorted(guard.branches(context, call.cwd) & defaults)
    spelled = [
        f"{branch}{_UPSTREAM}" if name in _UPSTREAMS else name for name in names for branch in mains
    ]
    return all(guard.repo.full_name(call.cwd, name) in tracking(defaults) for name in spelled)


def merge(call: Invocation, context: Context, guard: Guard) -> list[Verdict]:
    if not guard.on_main(context, call.cwd):
        return []
    options = scan(expand_long(call.args, _MERGE_LONG), _MERGE)
    if options.flags & _RESUMES:
        return []
    names = options.operands or (_UPSTREAM,)
    if not _readable(call, context):
        spelled = {f"{REMOTE}/{name}" for name in guard.defaults(context, call.cwd)}
        bare = (name.removeprefix("refs/").removeprefix("remotes/") for name in names)
        return _asks(call, guard, sync=all(n in _UPSTREAMS or n in spelled for n in bare))
    return [] if _tracks(names, call, context, guard=guard) else _asks(call, guard)


def pull(call: Invocation, context: Context, guard: Guard) -> list[Verdict]:
    if not guard.on_main(context, call.cwd):
        return []
    repository, *refspecs = scan(call.args, _PULL).operands or (REMOTE,)
    sources = [spec.removeprefix("+").partition(":")[0] for spec in refspecs]
    named = {source.removeprefix("refs/heads/") for source in sources}
    if repository != REMOTE or not named <= _REMOTE_DEFAULTS:
        return _asks(call, guard)
    if named:
        unsure = not _inert(call) or context.unread or context.repointed
    else:
        unsure = not _readable(call, context)
    if unsure:
        return _asks(call, guard, sync=True)
    return [] if named or _tracks([_UPSTREAM], call, context, guard=guard) else _asks(call, guard)


def cherry_pick(call: Invocation, context: Context, guard: Guard) -> list[Verdict]:
    resumes = set(expand_long(call.args, _RESUMES)) & _RESUMES
    return [] if resumes or not guard.on_main(context, call.cwd) else _asks(call, guard)


def read_tree(call: Invocation, context: Context, guard: Guard) -> list[Verdict]:
    return _asks(call, guard) if guard.on_main(context, call.cwd) else []
