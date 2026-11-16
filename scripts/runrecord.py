"""The run's record: the lines the gates append to ``.ratchet/timing.jsonl``, and what they carry.

Every line carries ``at`` (UTC, ISO 8601 to the second, when the line was written), ``run`` (the
run id of ADR 0071, ``RATCHET_RUN``) and ``tree`` (the content id of ADR 0071). A line written
outside a run says so in the field's value, so a reader never guesses.

Three kinds of line share the file. A gate line has ``key``. A tier's whole run has ``kind``
``tier``. A path the binding's Cache line names that does not exist after a tier's run has ``kind``
``cache-missing``, written after that run's tier line.
"""

from __future__ import annotations

import json
import os
import subprocess
from datetime import UTC, datetime
from pathlib import Path

import gatelib
import markdown_text
import report

NO_RUN = "none: RATCHET_RUN is not set"
NO_TREE = "unknown: the tree's content could not be read"
TIER = "tier"
CACHE_MISSING = "cache-missing"
SPEED = "## Speed"
CACHE = "Cache"


def tree_id(root: Path) -> str:
    """The content id of ``root``'s tree, or the words saying it could not be read."""
    try:
        return report.content_id(root)
    except (subprocess.CalledProcessError, OSError):
        return NO_TREE


def stamp(root: Path, tree: str | None = None) -> dict[str, str]:
    """The three fields every line carries, with ``tree`` taken as given when it was read before."""
    return {
        "at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "run": os.environ.get(report.RUN) or NO_RUN,
        "tree": tree if tree is not None else tree_id(root),
    }


def append(path: Path, record: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record) + "\n")


def cache_paths(binding: str) -> list[str]:
    """The paths the binding's Cache line names: its code spans, without a space in them."""
    line = markdown_text.bullets(markdown_text.section(binding, SPEED)).get(CACHE, "")
    return [span for span in markdown_text.ticked(line) if " " not in span]


def exists(root: Path, path: str) -> bool:
    place = root / Path(path).expanduser()
    anchor = Path(place.anchor)
    return any(anchor.glob(str(place.relative_to(anchor))))


def missing_cache(root: Path) -> list[str]:
    """The paths of the Cache line that match nothing on disk, ``~`` and globs expanded."""
    binding = gatelib.read_text(root / gatelib.BINDING) or ""
    return [path for path in cache_paths(binding) if not exists(root, path)]
