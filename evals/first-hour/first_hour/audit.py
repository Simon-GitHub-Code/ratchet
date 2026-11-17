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


class SandboxedGit(Protocol):
    def __call__(self, *args: str) -> str | None: ...


def files_of(show: SandboxedGit, branch: str) -> dict[str, str]:
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
    blobs = architecture.transcript_texts(_lines(transcript))
    return {**architecture.inspect(files), "reviews": architecture.review_axis(blobs)}


def leak_event(persona: Persona, answers: Iterable[str], transcript: Path) -> dict[str, object]:
    said = leaks.stated(persona.idea, persona.profile, persona.stack, *answers)
    return leaks.scan(leaks.plugin_text(_records(_lines(transcript))), said)
