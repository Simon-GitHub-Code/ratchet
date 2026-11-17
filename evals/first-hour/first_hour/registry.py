from __future__ import annotations

import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Iterator

FORGES = (
    "github.com",
    "githubusercontent.com",
    "gitlab.com",
    "bitbucket.org",
    "codeberg.org",
    "sr.ht",
)
LOCAL_SUFFIXES = ("localhost", "local", "internal", "lan", "home", "corp", "arpa")
_URL = re.compile(r"https://([A-Za-z0-9][A-Za-z0-9.-]*)(?:/[\w./:%?=&+,~@${}-]*)?")
_SHORT_FLAGS = frozenset("sSfLg")
_SHORT_VALUES = {
    "m": "--max-time",
    "o": "--output",
    "w": "--write-out",
    "H": "--header",
    "A": "--user-agent",
    "X": "--request",
}
_LONG_FLAGS = frozenset(
    {"--silent", "--show-error", "--fail", "--location", "--compressed", "--globoff"}
)
_LONG_VALUES = frozenset(
    {
        "--max-time",
        "--connect-timeout",
        "--retry",
        "--output",
        "--write-out",
        "--header",
        "--user-agent",
        "--request",
    }
)
_HEADER = re.compile(r"(?:accept|accept-encoding|user-agent):", re.IGNORECASE)


def is_registry_read(args: tuple[str, ...]) -> bool:
    urls = 0
    words = iter(args)
    for arg in words:
        if arg.startswith("--"):
            name, equals, value = arg.partition("=")
            if name in _LONG_FLAGS and not equals:
                continue
            if name not in _LONG_VALUES:
                return False
            given = value if equals else next(words, None)
            if given is None or not _value_ok(name, given):
                return False
        elif arg.startswith("-") and len(arg) > 1:
            if not _short_ok(arg[1:], words):
                return False
        elif _public_url(arg):
            urls += 1
        else:
            return False
    return urls > 0


def _public_url(arg: str) -> bool:
    match = _URL.fullmatch(arg)
    if match is None:
        return False
    host = match.group(1).lower().rstrip(".")
    last = host.rsplit(".", 1)[-1]
    if "." not in host or last.isdigit() or last in LOCAL_SUFFIXES:
        return False
    return not any(host == forge or host.endswith("." + forge) for forge in FORGES)


def _short_ok(cluster: str, words: Iterator[str]) -> bool:
    for index, char in enumerate(cluster):
        if char in _SHORT_FLAGS:
            continue
        name = _SHORT_VALUES.get(char)
        if name is None or index != len(cluster) - 1:
            return False
        given = next(words, None)
        return given is not None and _value_ok(name, given)
    return True


def _value_ok(name: str, value: str) -> bool:
    if name == "--header":
        return _HEADER.match(value) is not None
    if name == "--request":
        return value == "GET"
    return True
