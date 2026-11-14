"""Rules for the commands that merge into the default branch: the judge's and the owner's.

A change reaches the default branch in two ways (ADR 0021). On a forge the pull request's merge
job merges it, and ``ratchet_guards.gh`` asks on an agent's own ``gh pr merge``. With no forge the
binding's ``merge`` entry point runs ``scripts/merge.py``, which judges the branch and runs
``check`` on the merge result before the branch moves. Every other way of bringing another
branch's commits onto ``main`` or ``master`` asks the owner: ``git merge``, ``git cherry-pick``,
``git am``, ``git read-tree`` and a ``git pull`` that names another repository or branch, while
on a default branch.

What still passes there is the sync with the remote's own default branch: ``git pull``, ``git
pull origin``, ``git pull origin main``, and ``git merge --ff-only`` of ``origin/main`` or the
upstream. So does finishing or abandoning a merge already begun. The entry point passes because
no rule here names it: the script's own git calls are not tool calls.

A sync is what git says it is, not what it is called. ``@{u}`` is whatever ``git branch -u``
last set, ``origin/main`` is a local branch or a tag of that name before it is the tracking ref,
and a bare ``git pull`` merges what ``branch.main.merge`` names. So the rule asks git for the
full name each spelling resolves to and passes only ``refs/remotes/origin/main`` or
``refs/remotes/origin/master``. The answer describes the repository before the command line
runs, so the sync passes only on a line whose earlier commands cannot have changed it
(``ratchet_guards.git.settled``), and never in a repository the guard does not read (ADR 0024).
"""

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
# Why: git takes any unambiguous prefix of a long option, so the options that share a prefix
# with the ones this rule reads are listed too, and a prefix of two of them stays as written.
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


def _asks(call: Invocation) -> list[Verdict]:
    return [Verdict("merge-default", " ".join(("git", call.subcommand, *call.args)))]


def _tracks(names: Sequence[str], call: Invocation, context: Context, *, guard: Guard) -> bool:
    """Whether git reads every one of ``names`` as the remote's default branch, here and now.

    The upstream is asked for each default branch the command may run on.
    """
    if context.unread or context.unsettled:
        return False
    defaults = guard.defaults(context, call.cwd)
    mains = sorted(guard.branches(context, call.cwd) & defaults)
    spelled = [
        f"{branch}{_UPSTREAM}" if name in _UPSTREAMS else name for name in names for branch in mains
    ]
    return all(guard.repo.full_name(call.cwd, name) in tracking(defaults) for name in spelled)


def merge(call: Invocation, context: Context, guard: Guard) -> list[Verdict]:
    """``git merge`` on a default branch, other than a fast-forward to the remote's own."""
    if not guard.on_main(context, call.cwd):
        return []
    options = scan(expand_long(call.args, _MERGE_LONG), _MERGE)
    if options.flags & _RESUMES:
        return []
    names = options.operands or (_UPSTREAM,)
    synced = "--ff-only" in options.flags and _tracks(names, call, context, guard=guard)
    return [] if synced else _asks(call)


def pull(call: Invocation, context: Context, guard: Guard) -> list[Verdict]:
    """``git pull`` on a default branch that names another repository or another branch.

    With no branch named it merges the upstream, which must be the remote's default branch.
    """
    if not guard.on_main(context, call.cwd):
        return []
    repository, *refspecs = scan(call.args, _PULL).operands or (REMOTE,)
    sources = [spec.removeprefix("+").partition(":")[0] for spec in refspecs]
    named = {source.removeprefix("refs/heads/") for source in sources}
    remote = repository == REMOTE and named <= _REMOTE_DEFAULTS
    return (
        []
        if remote and (named or _tracks([_UPSTREAM], call, context, guard=guard))
        else _asks(call)
    )


def cherry_pick(call: Invocation, context: Context, guard: Guard) -> list[Verdict]:
    """``git cherry-pick`` or ``git am`` on a default branch: commits made elsewhere, one by one."""
    resumes = set(expand_long(call.args, _RESUMES)) & _RESUMES
    return [] if resumes or not guard.on_main(context, call.cwd) else _asks(call)


def read_tree(call: Invocation, context: Context, guard: Guard) -> list[Verdict]:
    """``git read-tree`` on a default branch: another commit's files, ready to commit there.

    Why: on a branch a guard path changes with no prompt (ADR 0033), so a tree read from that
    branch onto the default one would take the change past the merge judge.
    """
    return _asks(call) if guard.on_main(context, call.cwd) else []
