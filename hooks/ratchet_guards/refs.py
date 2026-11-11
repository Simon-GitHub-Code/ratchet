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
# Why: whichever branch ``origin/HEAD`` names is read, and the guard cannot know which without
# asking git, so every remote-tracking ref and every replace ref is treated as one of them.
_NAMESPACES = frozenset({"remotes", "replace"})
_SAMPLES = (*(ref.removeprefix("refs/") for ref in (*LOCAL, REMOTE_HEAD, *TRACKING)), "replace/x")


def protects(ref: str, *, heads: bool = True) -> bool:
    """Whether writing ``ref``, as a command line spells it, can move what the guard reads.

    ``ref`` may carry ``+``, may be fully qualified (``refs/heads/main``), bare (``main``) or
    ``heads/main``, and may be a pattern with ``*``. It is protected when it is a default branch,
    or fully qualified under ``refs/remotes`` or ``refs/replace``; a bare ``replace/x`` is a
    branch of that name. ``heads=False`` leaves the default branch names out, for a push, where
    ``push-main`` already judges them.
    """
    name = ref.removeprefix("+")
    path = name.removeprefix("refs/")
    if "*" in path:
        return any(
            fnmatchcase(sample, path)
            for sample in _SAMPLES
            if heads or not sample.startswith("heads/")
        )
    if name.startswith("refs/") and path.partition("/")[0] in _NAMESPACES:
        return True
    return heads and path.removeprefix("heads/") in MAIN_BRANCHES
