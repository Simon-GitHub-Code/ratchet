from __future__ import annotations

import errno
import json
import math
import os
import stat
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING

import gatelib
import markdown_text
import report

if TYPE_CHECKING:
    from collections.abc import Iterator

NO_RUN = "none: RATCHET_RUN is not set"
NO_TREE = "unknown: the tree's content could not be read"
TIER = "tier"
CACHE_MISSING = "cache-missing"
FAULT = "fault"
TIMING = gatelib.STATE / "timing.jsonl"
SPEED = "## Speed"
LOCAL_CACHES = "Local caches"
LINE = 2**20
FOLDER = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC
READ = os.O_RDONLY | os.O_NONBLOCK | os.O_NOCTTY | os.O_CLOEXEC
WRITE = os.O_RDWR | os.O_APPEND | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_NOCTTY
WRITE |= os.O_CLOEXEC
SPECIAL = "it is not a regular file; remove it"
FULL = "no space left for it; free some space"
NOT_WRITABLE = "permission denied; make it and its folder writable"
NOT_A_FOLDER = "its folder is not a folder; remove what is there"
REFUSED = {
    errno.ELOOP: "it or its folder is a symbolic link; remove the link",
    errno.EISDIR: "it is a directory; remove it",
    errno.EEXIST: NOT_A_FOLDER,
    errno.ENOTDIR: NOT_A_FOLDER,
    errno.EACCES: NOT_WRITABLE,
    errno.EPERM: NOT_WRITABLE,
    errno.ENOSPC: FULL,
    errno.EDQUOT: FULL,
    errno.EFBIG: FULL,
}
UNRECORDED: set[tuple[Path, str]] = set()


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
            held = os.fstat(handle)
            if not stat.S_ISREG(held.st_mode):
                return SPECIAL
            if held.st_size and os.pread(handle, 1, held.st_size - 1) != b"\n":
                line = b"\n" + line
            if os.write(handle, line) < len(line):
                return FULL
        finally:
            os.close(handle)
    except OSError as error:
        cure = "remove or replace it, or make it writable"
        return REFUSED.get(error.errno or 0, f"{error.strerror or error}; {cure}")
    return None


def append(path: Path, record: dict[str, object], *, root: Path, who: str = "ratchet") -> None:
    why = written(path, (json.dumps(record) + "\n").encode())
    if why is not None and (path, why) not in UNRECORDED:
        UNRECORDED.add((path, why))
        print(f"{who}: not recorded: {os.path.relpath(path, root)}: {why}", flush=True)


def lines(path: Path) -> Iterator[bytes]:
    try:
        with os.fdopen(os.open(path, READ), "rb") as handle:
            if not stat.S_ISREG(os.fstat(handle.fileno()).st_mode):
                return
            while line := handle.readline(LINE):
                if line.endswith(b"\n") or len(line) < LINE:
                    yield line
                    continue
                while (rest := handle.readline(LINE)) and not rest.endswith(b"\n"):
                    pass
    except OSError:
        return


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


def read(path: Path) -> Iterator[dict[str, object]]:
    for line in lines(path):
        try:
            record = json.loads(line.decode("utf-8", errors="replace"))
        except (ValueError, RecursionError):
            continue
        if isinstance(record, dict) and well_formed(record):
            yield record


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
