import re
from collections.abc import Mapping, Sequence
from itertools import pairwise
from pathlib import PurePosixPath

_ASSIGNMENT = re.compile(r"^(?P<name>[A-Za-z_][A-Za-z0-9_]*)(\[[^]]*\])?\+?=")
_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_DECLARERS = frozenset(
    {"export", "declare", "typeset", "readonly", "local", "read", "mapfile", "readarray"}
    | {"getopts"}
)
_PRINTF = "printf"
_PRINTF_INTO = "-v"
HOOK_SKIPS = frozenset({"SKIP", "RATCHET_HOOK_PROBE"})
CONFIG_PREFIX = "GIT_CONFIG"
REPOSITORIES = frozenset({"GIT_DIR", "GIT_WORK_TREE", "GIT_COMMON_DIR", "GIT_NAMESPACE"})
HOMES = frozenset({"HOME", "XDG_CONFIG_HOME"})
LOOKUPS = frozenset({"PATH", "CDPATH"})
GIT_PREFIX = "GIT_"
COMMANDS = frozenset({"EDITOR", "VISUAL", "PAGER", "SSH_ASKPASS"})


def assigned(word: str) -> str | None:
    match = _ASSIGNMENT.match(word)
    return None if match is None else match.group("name")


def is_assignment(word: str) -> bool:
    return assigned(word) is not None


def _named(word: str) -> str | None:
    return word if _NAME.match(word) else assigned(word)


def declared(words: Sequence[str]) -> dict[str, str]:
    head = PurePosixPath(words[0]).name if words else ""
    if head == _PRINTF:
        apart = [value for flag, value in pairwise(words) if flag == _PRINTF_INTO]
        joined = [
            word.removeprefix(_PRINTF_INTO) for word in words if word.startswith(_PRINTF_INTO)
        ]
        return {name: name for name in (*apart, *joined) if _NAME.match(name)}
    if head not in _DECLARERS:
        return {}
    return {name: word for word in words[1:] if (name := _named(word)) is not None}


def commands(names: Mapping[str, str]) -> list[str]:
    return [
        word.partition("=")[2]
        for name, word in names.items()
        if name.startswith(GIT_PREFIX) or name in COMMANDS
    ]


def carried(words: Sequence[str]) -> frozenset[str]:
    names = [assigned(word) for word in words]
    if words and all(names):
        return frozenset(filter(None, names))
    return frozenset(declared(words))
