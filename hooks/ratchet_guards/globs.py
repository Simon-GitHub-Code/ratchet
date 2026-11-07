"""Glob matching over POSIX paths with the ``PurePosixPath.full_match`` rules, on Python 3.11.

``*`` and ``?`` stay within one segment, ``[...]`` is a character class (``!`` negates), a
whole-segment ``**`` spans any number of segments including none (a trailing ``**`` still needs
its slash: ``a/**`` does not match ``a``), and the pattern must match the whole path. Path and
pattern are normalised as ``PurePosixPath`` normalises them (``./a`` is ``a``, ``a//b`` is
``a/b``, a trailing slash is dropped). ``full_match`` itself
arrived in Python 3.13; the plugin runs on the system ``python3``, which is 3.12 on today's
GitHub runners, so this is the one implementation every hook and gate uses.

``scripts/gatelib.py`` carries the same function for the gates, which cannot import this
package; ``hooks/tests/test_globs.py`` holds the two to the same answers.
"""

import fnmatch
import re
from functools import lru_cache
from pathlib import PurePosixPath

ANY_SEGMENTS = "(?:[^/]+/)*"


def matches(path: str, pattern: str) -> bool:
    """Whether ``pattern`` matches all of ``path``."""
    # Why: the empty path (``.``) matches exactly the patterns whose regex matches the empty
    # string, as pathlib has it: ``**`` and ``***`` do, ``*`` and ``?`` do not.
    return regex(pattern).fullmatch(normalised(path)) is not None


def normalised(text: str) -> str:
    """The path as ``PurePosixPath`` prints it, with ``.`` as the empty string."""
    posix = PurePosixPath(text).as_posix()
    return "" if posix == "." else posix


def segments(pattern: str) -> list[str]:
    """The pattern's segments; none for the empty pattern."""
    normal = normalised(pattern)
    return normal.split("/") if normal else []


@lru_cache(maxsize=4096)
def regex(pattern: str) -> re.Pattern[str]:
    """The compiled regular expression for a glob pattern."""
    parts = segments(pattern)
    expression = ""
    separator_due = False
    for index, segment in enumerate(parts):
        last = index == len(parts) - 1
        if segment == "**":
            if last:
                # Why: as pathlib has it, `a/**` needs the slash, so `a` itself is not matched.
                expression += "/.*" if separator_due else ".*"
            else:
                expression += ("/" if separator_due else "") + ANY_SEGMENTS
            separator_due = False
            continue
        expression += ("/" if separator_due else "") + _segment(segment)
        separator_due = True
    return re.compile(expression)


def _segment(segment: str) -> str:
    """One segment translated: ``*`` and ``?`` never cross a slash."""
    if segment == "*":
        # Why: pathlib gives a lone `*` one non-empty segment, so `/*` does not match `/`.
        return "[^/]+"
    out = ""
    index = 0
    while index < len(segment):
        char = segment[index]
        if char == "*":
            while index + 1 < len(segment) and segment[index + 1] == "*":
                index += 1
            out += "[^/]*"
        elif char == "?":
            out += "[^/]"
        elif char == "[":
            body, index = _character_class(segment, index)
            out += body
        else:
            out += re.escape(char)
        index += 1
    return out


def _character_class(segment: str, start: int) -> tuple[str, int]:
    """The class opening at ``start`` as a regex and the index of its ``]``, or a literal ``[``."""
    end = start + 1
    if end < len(segment) and segment[end] == "!":
        end += 1
    if end < len(segment) and segment[end] == "]":
        end += 1
    while end < len(segment) and segment[end] != "]":
        end += 1
    if end >= len(segment):
        return re.escape("["), start
    # Why: ranges, negation and escaping inside a class follow fnmatch, as pathlib's do.
    translated = fnmatch.translate(segment[start : end + 1])
    return translated[len("(?s:") : -len(")\\Z")], end
