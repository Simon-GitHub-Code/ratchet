"""The variables a command line sets for the commands it runs.

A variable reaches a later command in four ways: an assignment in front of it (``NAME=x git
commit``), a wrapper that takes assignments (``env NAME=x``), a builtin that declares or exports
one (``export``, ``declare -x``, ``typeset -x``, ``readonly``, ``local``), and a builtin that
fills one for a later ``export`` or ``set -a`` to pass on (``read``, ``mapfile``, ``getopts``,
``printf -v``). Every rule about a variable reads its name here, so a spelling closed for one
variable is closed for all of them (ADR 0024). A name that a variable, a file or a sourced script
supplies is not seen.
"""

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
# Why: with one of these git works on a repository other than the one in the working directory,
# so the branch a command would move is not the one the guard can ask about.
REPOSITORIES = frozenset({"GIT_DIR", "GIT_WORK_TREE", "GIT_COMMON_DIR", "GIT_NAMESPACE"})
# Why: git reads its global configuration from under these, so a `git` run with either set reads
# the aliases, the hooks path and the URL rewrites of whatever file is there.
HOMES = frozenset({"HOME", "XDG_CONFIG_HOME"})
# Why: these decide what the words `cd` and `git` do, so a move or a switch read from the command
# line is no longer certain to be the one that happens.
LOOKUPS = frozenset({"PATH", "CDPATH"})
# Why: git runs the value of these as a command (an editor, a pager, a password prompt), as it
# does the value of most variables named `GIT_*`, and no shell on the command line shows it.
GIT_PREFIX = "GIT_"
COMMANDS = frozenset({"EDITOR", "VISUAL", "PAGER", "SSH_ASKPASS"})


def assigned(word: str) -> str | None:
    """The variable ``word`` assigns (``NAME=value``, ``NAME+=value``), else ``None``."""
    match = _ASSIGNMENT.match(word)
    return None if match is None else match.group("name")


def is_assignment(word: str) -> bool:
    """Whether ``word`` is an assignment."""
    return assigned(word) is not None


def _named(word: str) -> str | None:
    return word if _NAME.match(word) else assigned(word)


def declared(words: Sequence[str]) -> dict[str, str]:
    """The variables a declaring builtin sets or exports, each with the word that names it.

    ``words`` is one simple command. ``printf`` names a variable only as the value of ``-v``.
    """
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
    """The values, among the assignments in ``names``, that git may run as a command."""
    return [
        word.partition("=")[2]
        for name, word in names.items()
        if name.startswith(GIT_PREFIX) or name in COMMANDS
    ]


def carried(words: Sequence[str]) -> frozenset[str]:
    """The variables a simple command leaves set for the commands after it.

    A command made only of assignments, or a declaring builtin. An assignment in front of a
    command is that command's alone.
    """
    names = [assigned(word) for word in words]
    if words and all(names):
        return frozenset(filter(None, names))
    return frozenset(declared(words))
