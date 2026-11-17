import fnmatch
import re
from functools import lru_cache
from pathlib import PurePosixPath

ANY_SEGMENTS = "(?:[^/]+/)*"


def matches(path: str, pattern: str) -> bool:
    return regex(pattern).fullmatch(normalised(path)) is not None


def normalised(text: str) -> str:
    posix = PurePosixPath(text).as_posix()
    return "" if posix == "." else posix


def segments(pattern: str) -> list[str]:
    normal = normalised(pattern)
    return normal.split("/") if normal else []


@lru_cache(maxsize=4096)
def regex(pattern: str) -> re.Pattern[str]:
    parts = segments(pattern)
    expression = ""
    separator_due = False
    for index, segment in enumerate(parts):
        last = index == len(parts) - 1
        if segment == "**":
            if last:
                expression += "/.*" if separator_due else ".*"
            else:
                expression += ("/" if separator_due else "") + ANY_SEGMENTS
            separator_due = False
            continue
        expression += ("/" if separator_due else "") + _segment(segment)
        separator_due = True
    return re.compile(expression)


def _segment(segment: str) -> str:
    if segment == "*":
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
    end = start + 1
    if end < len(segment) and segment[end] == "!":
        end += 1
    if end < len(segment) and segment[end] == "]":
        end += 1
    while end < len(segment) and segment[end] != "]":
        end += 1
    if end >= len(segment):
        return re.escape("["), start
    translated = fnmatch.translate(segment[start : end + 1])
    return translated[len("(?s:") : -len(")\\Z")], end
