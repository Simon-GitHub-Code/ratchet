from fnmatch import fnmatchcase

REMOTE = "origin"
MAIN_BRANCHES = frozenset({"main", "master"})
REMOTE_HEAD = f"refs/remotes/{REMOTE}/HEAD"
TRACKING = tuple(f"refs/remotes/{REMOTE}/{name}" for name in sorted(MAIN_BRANCHES))
LOCAL = tuple(f"refs/heads/{name}" for name in sorted(MAIN_BRANCHES))
TAGS = tuple(f"refs/tags/{name}" for name in sorted(MAIN_BRANCHES))
_NAMESPACES = frozenset({"remotes", "replace"})
_DEFAULTS = frozenset({"heads", "tags"})
_QUALIFIED = ("heads/", "tags/", "remotes/")


def local(names: frozenset[str]) -> tuple[str, ...]:
    return tuple(f"refs/heads/{name}" for name in sorted(names))


def tracking(names: frozenset[str]) -> tuple[str, ...]:
    return tuple(f"refs/remotes/{REMOTE}/{name}" for name in sorted(names))


def protected(ref: str, names: frozenset[str] = MAIN_BRANCHES) -> bool:
    if "*" in ref:
        samples = (*local(names), REMOTE_HEAD, *tracking(names), "refs/replace/x")
        return any(fnmatchcase(sample, ref) for sample in samples)
    space, _, name = ref.removeprefix("refs/").partition("/")
    return ref.startswith("refs/") and (
        space in _NAMESPACES or (space in _DEFAULTS and name in names)
    )


def protects(ref: str, names: frozenset[str] = MAIN_BRANCHES) -> bool:
    name = ref.removeprefix("+")
    if name.startswith("refs/"):
        return protected(name, names)
    return protected(f"refs/{name}" if name.startswith(_QUALIFIED) else f"refs/heads/{name}", names)


def pushed(ref: str) -> tuple[str, ...]:
    name = ref.removeprefix("+")
    if name.startswith("refs/"):
        return (name,)
    tracks = name.partition("/")[0] == REMOTE
    tracking = (f"refs/remotes/{name}", f"refs/remotes/{name}/HEAD") if tracks else ()
    return (f"refs/{name}", f"refs/tags/{name}", f"refs/heads/{name}", *tracking)


def default_branch(ref: str, names: frozenset[str] = MAIN_BRANCHES) -> bool:
    return any(fnmatchcase(name, ref) for name in local(names))
