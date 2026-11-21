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


def _asks(call: Invocation, guard: Guard) -> list[Verdict]:
    detail = " ".join(("git", call.subcommand, *call.args))
    return [guard.merging("merge-default", detail, call.cwd)]


def _tracks(names: Sequence[str], call: Invocation, context: Context, *, guard: Guard) -> bool:
    if context.unread or context.unsettled or call.configs:
        return False
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
    return [] if _tracks(names, call, context, guard=guard) else _asks(call, guard)


def pull(call: Invocation, context: Context, guard: Guard) -> list[Verdict]:
    if not guard.on_main(context, call.cwd):
        return []
    repository, *refspecs = scan(call.args, _PULL).operands or (REMOTE,)
    sources = [spec.removeprefix("+").partition(":")[0] for spec in refspecs]
    named = {source.removeprefix("refs/heads/") for source in sources}
    remote = repository == REMOTE and named <= _REMOTE_DEFAULTS
    return (
        []
        if remote and (named or _tracks([_UPSTREAM], call, context, guard=guard))
        else _asks(call, guard)
    )


def cherry_pick(call: Invocation, context: Context, guard: Guard) -> list[Verdict]:
    resumes = set(expand_long(call.args, _RESUMES)) & _RESUMES
    return [] if resumes or not guard.on_main(context, call.cwd) else _asks(call, guard)


def read_tree(call: Invocation, context: Context, guard: Guard) -> list[Verdict]:
    return _asks(call, guard) if guard.on_main(context, call.cwd) else []
