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
FAULT = "fault"
TIMING = gatelib.STATE / "timing.jsonl"
SPEED = "## Speed"
LOCAL_CACHES = "Local caches"


def tree_id(root: Path) -> str:
    try:
        return report.content_id(root)
    except (subprocess.CalledProcessError, OSError):
        return NO_TREE


def stamp(root: Path, tree: str | None = None) -> dict[str, str]:
    return {
        "at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "run": os.environ.get(report.RUN) or NO_RUN,
        "tree": tree if tree is not None else tree_id(root),
    }


def append(path: Path, record: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record) + "\n")


def read(path: Path) -> list[dict[str, object]]:
    records = []
    for line in (gatelib.read_text(path) or "").splitlines():
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(record, dict):
            records.append(record)
    return records


def cache_paths(binding: str) -> list[str]:
    line = markdown_text.bullets(markdown_text.section(binding, SPEED)).get(LOCAL_CACHES, "")
    if line.startswith("<"):
        return []
    return [span for span in markdown_text.ticked(line) if " " not in span]


def exists(root: Path, path: str) -> bool:
    place = root / Path(path).expanduser()
    anchor = Path(place.anchor)
    return any(anchor.glob(str(place.relative_to(anchor))))


def missing_cache(root: Path) -> list[str]:
    binding = gatelib.read_text(root / gatelib.BINDING) or ""
    return [path for path in cache_paths(binding) if not exists(root, path)]
