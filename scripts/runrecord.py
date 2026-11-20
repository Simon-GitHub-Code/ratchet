from __future__ import annotations

import errno
import json
import math
import os
import stat
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
WINDOW = 2**20
FOLDER = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC
READ = os.O_RDONLY | os.O_NONBLOCK | os.O_NOCTTY | os.O_CLOEXEC
WRITE = os.O_WRONLY | os.O_APPEND | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_NOCTTY
WRITE |= os.O_CLOEXEC
UNRECORDED: set[Path] = set()


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


def written(path: Path, line: bytes) -> str | None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        folder = os.open(path.parent, FOLDER)
        try:
            handle = os.open(path.name, WRITE, 0o666, dir_fd=folder)
        finally:
            os.close(folder)
        try:
            if not stat.S_ISREG(os.fstat(handle).st_mode):
                return "not a regular file"
            os.write(handle, line)
        finally:
            os.close(handle)
    except OSError as error:
        return "a symbolic link" if error.errno == errno.ELOOP else error.strerror or str(error)
    return None


def append(path: Path, record: dict[str, object], *, who: str = "ratchet") -> None:
    why = written(path, (json.dumps(record) + "\n").encode())
    if why is not None and path not in UNRECORDED:
        UNRECORDED.add(path)
        print(f"{who}: not recorded: {path}: {why}", flush=True)


def tail(path: Path) -> list[bytes]:
    try:
        handle = os.open(path, READ)
    except OSError:
        return []
    try:
        held = os.fstat(handle)
        if not stat.S_ISREG(held.st_mode):
            return []
        start = max(0, held.st_size - WINDOW - 1)
        lines = os.pread(handle, WINDOW + 1, start).split(b"\n")
    except OSError:
        return []
    finally:
        os.close(handle)
    return lines[1:] if start else lines


def well_formed(record: dict[str, object]) -> bool:
    if record.get("kind") != FAULT:
        return True
    seconds = record.get("seconds")
    return (
        isinstance(record.get("gate"), str)
        and isinstance(record.get("bite"), str)
        and isinstance(seconds, int | float)
        and not isinstance(seconds, bool)
        and 0 <= seconds < math.inf
        and isinstance(record.get("narrowed"), bool)
    )


def read(path: Path) -> list[dict[str, object]]:
    records = []
    for line in tail(path):
        try:
            record = json.loads(line.decode("utf-8", errors="replace"))
        except (ValueError, RecursionError):
            continue
        if isinstance(record, dict):
            records.append(record)
    return list(filter(well_formed, records))


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
