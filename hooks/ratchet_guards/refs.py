"""The refs the guard reads the default branch through, and the spellings git accepts for them.

The path rules come from ``guards/manifest.toml`` at the default branch's tip and the additive
records check compares with the merge base of ``HEAD`` and that branch (ADR 0005, ADR 0014).
``ratchet_guards.committed`` finds the branch through ``refs/remotes/origin/HEAD`` and what it
names, else through ``refs/remotes/origin/main``, ``refs/remotes/origin/master``,
``refs/heads/main`` and ``refs/heads/master``, and git shows it with object replacement
(``refs/replace/*``) applied. A command that writes any of them, or a setting that decides what a
fetch writes into them, switches the path guards off or turns merged records into the branch's own
additions (ADR 0016). This module says which names those are, in every spelling a command line
can give them.
"""

from fnmatch import fnmatchcase

REMOTE = "origin"
MAIN_BRANCHES = frozenset({"main", "master"})
REMOTE_HEAD = f"refs/remotes/{REMOTE}/HEAD"
TRACKING = tuple(f"refs/remotes/{REMOTE}/{name}" for name in sorted(MAIN_BRANCHES))
LOCAL = tuple(f"refs/heads/{name}" for name in sorted(MAIN_BRANCHES))
# Why: git reads a short name from `refs/tags` before `refs/heads`, and a forge lists a run on
# the tag `main` beside the runs on the branch, so a tag named as a default branch stands in for it.
TAGS = tuple(f"refs/tags/{name}" for name in sorted(MAIN_BRANCHES))
# Why: whichever branch ``origin/HEAD`` names is read, and the guard cannot know which without
# asking git, so every remote-tracking ref and every replace ref is treated as one of them.
_NAMESPACES = frozenset({"remotes", "replace"})
_DEFAULTS = frozenset({"heads", "tags"})
# Why: `git fetch` writes a destination that starts with one of these under `refs/`, so
# `remotes/origin/main` is the tracking ref and not a branch of that name.
_QUALIFIED = ("heads/", "tags/", "remotes/")
# A tag pattern is left out: `git fetch` follows tags whatever the hook says, so refusing
# `refs/tags/*` would cost every tag fetch and stop nothing.
_SAMPLES = (*LOCAL, REMOTE_HEAD, *TRACKING, "refs/replace/x")


def protected(ref: str) -> bool:
    """Whether the full name ``ref``, or a pattern with ``*``, is or covers a ref the guard reads.

    Those are the local default branches, a tag named as one, and everything under
    ``refs/remotes`` and ``refs/replace``.
    """
    if "*" in ref:
        return any(fnmatchcase(sample, ref) for sample in _SAMPLES)
    space, _, name = ref.removeprefix("refs/").partition("/")
    return ref.startswith("refs/") and (
        space in _NAMESPACES or (space in _DEFAULTS and name in MAIN_BRANCHES)
    )


def protects(ref: str) -> bool:
    """Whether writing ``ref``, as a command line spells it, can move what the guard reads.

    ``ref`` may carry ``+``, may be fully qualified (``refs/heads/main``), may start from a
    namespace as ``git fetch`` reads a destination (``heads/main``, ``tags/main``,
    ``remotes/origin/main``), or be bare (``main``), which names a branch. It may be a pattern
    with ``*``. A bare ``replace/x`` is a branch of that name.
    """
    name = ref.removeprefix("+")
    if name.startswith("refs/"):
        return protected(name)
    return protected(f"refs/{name}" if name.startswith(_QUALIFIED) else f"refs/heads/{name}")


def pushed(ref: str) -> tuple[str, ...]:
    """The full names ``git push`` may write for the destination ``ref``, or read for a source.

    A name that starts with ``refs/`` is taken as written. Any other is matched against the refs
    the receiving repository holds, by the rules git resolves a revision with: ``refs/<name>``,
    ``refs/tags/<name>``, ``refs/heads/<name>``, ``refs/remotes/<name>`` and
    ``refs/remotes/<name>/HEAD``. So ``heads/main`` is the branch, ``origin/main`` the tracking
    ref and ``origin`` the remote's ``HEAD``. The last two rules are followed for ``origin``
    alone: the guard reads no other remote's refs, and ``feature/x`` is not a tracking ref.
    """
    name = ref.removeprefix("+")
    if name.startswith("refs/"):
        return (name,)
    tracks = name.partition("/")[0] == REMOTE
    tracking = (f"refs/remotes/{name}", f"refs/remotes/{name}/HEAD") if tracks else ()
    return (f"refs/{name}", f"refs/tags/{name}", f"refs/heads/{name}", *tracking)


def default_branch(ref: str) -> bool:
    """Whether the full name ``ref``, or a pattern with ``*``, is or covers a default branch."""
    return any(fnmatchcase(local, ref) for local in LOCAL)
