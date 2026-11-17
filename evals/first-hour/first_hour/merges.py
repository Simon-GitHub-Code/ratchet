from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Iterable, Sequence

REFLOG_FORMAT = "%H%x09%gd%x09%gs%x09%s"
SUBJECT = re.compile(r"^Merge (?:branch|remote-tracking branch) '([^']+)'")
MERGED = re.compile(r"^merge (\S+): ")
SHA = re.compile(r"^[0-9a-f]{7,40}$")
HAND = re.compile(r"\bgit\b(?:\s+-\S+(?:\s+\S+)?)*\s+merge\b")
SLACK_S = 1.0


class How(StrEnum):
    HAND = "hand"
    ENTRY_POINT = "entry-point"
    AGENT = "agent"


@dataclass(frozen=True)
class Landing:
    sha: str
    at: float
    branch: str
    message: str


@dataclass(frozen=True)
class Window:
    command: str
    start: float
    end: float


def _moved(token: str) -> float | None:
    found = re.search(r"@\{(\d+)\}", token)
    return float(found.group(1)) if found else None


def landings(reflog: str) -> list[Landing]:
    found = []
    for line in reflog.splitlines():
        parts = line.split("\t", 3)
        if len(parts) < 4:  # noqa: PLR2004
            continue
        sha, token, message, subject = parts
        at = _moved(token)
        merged = MERGED.match(message)
        named = SUBJECT.match(subject)
        is_merge = merged or message == "merge.py" or message.startswith("commit (merge)")
        if at is None or not is_merge:
            continue
        branch = merged.group(1) if merged and not SHA.match(merged.group(1)) else ""
        branch = branch or (named.group(1) if named else subject)
        found.append(Landing(sha=sha, at=at, branch=branch, message=message))
    return list(reversed(found))


def _names(command: str, branch: str) -> bool:
    return bool(branch) and bool(re.search(rf"(?<![\w/.-]){re.escape(branch)}(?![\w/.-])", command))


def how(landing: Landing, windows: Iterable[Window]) -> How:
    for window in windows:
        during = window.start - SLACK_S <= landing.at <= window.end + SLACK_S
        if during and _names(window.command, landing.branch):
            return How.HAND if HAND.search(window.command) else How.ENTRY_POINT
    return How.AGENT


def new(seen: Sequence[Landing], now: Sequence[Landing]) -> list[Landing]:
    known = {(landing.sha, landing.at) for landing in seen}
    return [landing for landing in now if (landing.sha, landing.at) not in known]
