"""The one network read a session may make: public package-registry metadata, with ``curl``.

A normal owner's session picks tools by looking up what is current: ``curl -s
<registry>/<package>``. The harness refuses ``WebFetch`` and ``WebSearch`` (they run
in the Claude Code process, outside the sandbox, which holds the model's token), so this is how a
session reads a registry. A ``curl`` is allowed when every flag is on a short list that cannot
send or sign anything, and every URL is ``https`` on a named public host: any ecosystem's
registry, none favoured. The sandbox has no credential to send (README, "Safety"); the rule keeps
a read a read.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Iterator

# Why: forge hosts are refused here as the policy refuses them everywhere. A name ending in one of
# these suffixes is not a public host: a session must not reach a machine on the local network.
FORGES = (
    "github.com",
    "githubusercontent.com",
    "gitlab.com",
    "bitbucket.org",
    "codeberg.org",
    "sr.ht",
)
LOCAL_SUFFIXES = ("localhost", "local", "internal", "lan", "home", "corp", "arpa")
# Why: a host is letters, digits, dots and hyphens, then a path or nothing: no userinfo, no port,
# no `$VAR`, no bracketed address.
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
# Why: a header that names the content wanted; any other (`Authorization`, `Cookie`, a token in
# a custom one) is how a request signs itself.
_HEADER = re.compile(r"(?:accept|accept-encoding|user-agent):", re.IGNORECASE)


def is_registry_read(args: tuple[str, ...]) -> bool:
    """Whether the words after ``curl`` only read from a registry host, without signing in."""
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
    """An ``https`` URL on a named public host: not a forge, an address or a local name."""
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
