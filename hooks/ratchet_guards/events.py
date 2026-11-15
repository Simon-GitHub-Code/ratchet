"""The guard's event log: one JSON line for each tool call it denies or asks about (ADR 0060).

It lets a reader see, with no forge, whether work reached the default branch past the guard. The
file is ``LOG_NAME`` in the repository's git common directory: it never shows in ``git status``,
and every worktree of one repository appends to the same file. It is local and never committed.

A line holds the time in UTC, the decision, the tool as the harness named it, the rule, the rule's
detail as the hook prints it (a guard path relative to its checkout with its group, or the words
of the command that matched), and the branch of the session's checkout (``HEAD`` when detached).
It holds no file contents, no command output, no environment and no part of a command the hook
does not print. A line that would reach ``LIMIT`` bytes has each text field cut to ``CLIP``
characters and is marked ``truncated``.

Each line is one ``write`` to a file opened with ``O_APPEND``, so agents in several worktrees
append at once without mixing their lines. A symbolic link at the log path is not followed and a
FIFO is not waited on (ADR 0024): a planted name must neither make the guard write another file
nor hold its answer. The caller drops any failure, so the log never changes a verdict.
"""

import json
import os
from dataclasses import asdict, dataclass
from pathlib import Path

LOG_NAME = "ratchet-guard-events.jsonl"
# Why: below the size of one page, which local filesystems append in one piece.
LIMIT = 4096
# Why: an escaped character takes at most 12 bytes, so every text field cut to this fits LIMIT.
CLIP = 100
_FLAGS = os.O_WRONLY | os.O_APPEND | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK


@dataclass(frozen=True, slots=True)
class Event:
    """One deny or ask, as the log keeps it."""

    at: str
    decision: str
    tool: str | None
    rule: str
    detail: str
    branch: str | None

    def line(self) -> bytes:
        """The event as one line of JSON, under ``LIMIT`` bytes with its newline."""
        fields = asdict(self)
        text = json.dumps(fields)
        if len(text) >= LIMIT:
            cut = {key: v[:CLIP] if isinstance(v, str) else v for key, v in fields.items()}
            text = json.dumps({**cut, "truncated": True})
        return (text + "\n").encode()


def append(log: Path, line: bytes) -> None:
    """Append ``line`` to ``log`` in one ``write``; an ``OSError`` reaches the caller."""
    descriptor = os.open(log, _FLAGS, 0o600)
    try:
        os.write(descriptor, line)
    finally:
        os.close(descriptor)
