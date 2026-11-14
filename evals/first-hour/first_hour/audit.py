"""The two readings of a finished run that need its repository and its transcript.

``architecture`` reads the default branch's files (the binding, the decision records, each
feature's spec) and the transcript's review results. ``stack_leaks`` reads the transcript for the
plugin's own text and the persona's and the owner's words. Each is logged as one event, so that
``reduce`` folds them as it folds everything else and a run reduced again gives the same numbers.
"""

from __future__ import annotations

import json
import re
from typing import TYPE_CHECKING, Protocol

from first_hour import architecture, leaks

if TYPE_CHECKING:
    from collections.abc import Iterable
    from pathlib import Path

    from first_hour.persona import Persona

WANTED = re.compile(
    r"^(?:docs/process/binding\.md|docs/adr/\d{4}-[^/]+\.md|docs/features/[^/]+/spec\.md)$"
)


class Show(Protocol):
    """One git command in the owner's project: its output, or ``None`` when it fails."""

    def __call__(self, *args: str) -> str | None:
        """Run git with ``args``."""


def files_of(show: Show, branch: str) -> dict[str, str]:
    """The text of each file the architecture reading needs, on ``branch``.

    ``show`` runs one command in the owner's project and gives its output, or ``None`` when it
    fails: the run's own, so that every read goes through the sandbox.
    """
    listed = show("ls-tree", "-r", "--name-only", branch) or ""
    return {
        path: text
        for path in listed.splitlines()
        if WANTED.match(path) and (text := show("show", f"{branch}:{path}")) is not None
    }


def _lines(path: Path) -> list[str]:
    return path.read_text(encoding="utf-8").splitlines() if path.is_file() else []


def _records(lines: Iterable[str]) -> list[dict[str, object]]:
    return [json.loads(line) for line in lines if line.strip()]


def architecture_event(files: dict[str, str], transcript: Path) -> dict[str, object]:
    """What the repository and the transcript show of the architecture."""
    blobs = architecture.transcript_texts(_lines(transcript))
    return {**architecture.inspect(files), "reviews": architecture.review_axis(blobs)}


def leak_event(persona: Persona, answers: Iterable[str], transcript: Path) -> dict[str, object]:
    """The plugin's own text that named a stack the owner did not say."""
    said = leaks.stated(persona.idea, persona.profile, persona.stack, *answers)
    return leaks.scan(leaks.plugin_text(_records(_lines(transcript))), said)
