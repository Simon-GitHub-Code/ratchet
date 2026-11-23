from __future__ import annotations

import contextlib
import contextvars
import errno
import hashlib
import json
import math
import os
import stat
import subprocess
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING

import gatelib
import markdown_text
import plugin_pin

if TYPE_CHECKING:
    from collections.abc import Iterator

RUN = "RATCHET_RUN"
NO_RUN = "none: RATCHET_RUN is not set"
NO_TREE = "unknown: the tree's content could not be read"
TIER = "tier"
PRODUCER = "report-producer"
CACHE_MISSING = "cache-missing"
CACHE_RESTORE = "cache-restore"
COLD_BUILD = "cold-build"
FAULT = "fault"
MACHINE_FIELDS = ("cores", "load", "k", "share", "m", "machine_read")
GATE_FIELDS = ("lane", "start", "cpu", "alone")
TIMING = gatelib.STATE / "timing.jsonl"
SPEED = "## Speed"
CACHE = "Cache"
LOCAL_CACHES = "Local caches"
LINE = 2**20
FOLDER = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC
READ = os.O_RDONLY | os.O_NONBLOCK | os.O_NOCTTY | os.O_CLOEXEC
APPEND = os.O_APPEND | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_NOCTTY | os.O_CLOEXEC
TOO_LONG = "the record is too long to keep; shorten the name it carries"
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


def content_id(root: Path) -> str:
    listed = subprocess.run(
        ["git", "-C", str(root), "ls-files", "-z", "--cached", "--others", "--exclude-standard"],
        capture_output=True,
        check=True,
    ).stdout.decode()
    digest = hashlib.sha1(usedforsecurity=False)
    for name in sorted(set(listed.split("\0")) - {""}):
        if Path(name).parts[0] == gatelib.STATE.name:
            continue
        path = root / name
        try:
            mode = path.lstat().st_mode
        except FileNotFoundError:
            continue
        if stat.S_ISLNK(mode):
            kind, body = b"120000", os.fsencode(path.readlink())
        elif stat.S_ISREG(mode):
            kind, body = (b"100755" if mode & stat.S_IXUSR else b"100644"), path.read_bytes()
        else:
            continue
        digest.update(kind + b" " + os.fsencode(name) + b"\0" + plugin_pin.object_id(b"blob", body))
    return digest.hexdigest()


def tree_id(root: Path) -> str:
    try:
        return content_id(root)
    except (subprocess.CalledProcessError, OSError):
        return NO_TREE


def stamp(root: Path, tree: str | None = None) -> dict[str, str]:
    return {
        "at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "run": os.environ.get(RUN) or NO_RUN,
        "tree": tree if tree is not None else tree_id(root),
    }


def kept(line: bytes) -> bool:
    return len(line) < LINE


def opened(folder: int, name: str) -> tuple[int, bool]:
    try:
        return os.open(name, os.O_RDWR | APPEND, 0o666, dir_fd=folder), True
    except PermissionError:
        return os.open(name, os.O_WRONLY | APPEND, 0o666, dir_fd=folder), False


def written(path: Path, line: bytes) -> str | None:
    if not kept(line):
        return TOO_LONG
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        folder = os.open(path.parent, FOLDER)
        try:
            handle, readable = opened(folder, path.name)
        finally:
            os.close(folder)
        try:
            held = os.fstat(handle)
            if not stat.S_ISREG(held.st_mode):
                return SPECIAL
            if held.st_size and not (readable and os.pread(handle, 1, held.st_size - 1) == b"\n"):
                line = b"\n" + line
            if os.write(handle, line) < len(line):
                return FULL
        finally:
            os.close(handle)
    except OSError as error:
        cure = "remove or replace it, or make it writable"
        return REFUSED.get(error.errno or 0, f"{error.strerror or error}; {cure}")
    return None


def append(path: Path, record: dict[str, object], *, root: Path, who: str = "ratchet") -> bool:
    why = written(path, (json.dumps(record) + "\n").encode())
    if why is not None and (path, why) not in UNRECORDED:
        UNRECORDED.add((path, why))
        print(f"{who}: not recorded: {os.path.relpath(path, root)}: {why}", flush=True)
    return why is None


def lines(path: Path) -> Iterator[bytes]:
    try:
        with os.fdopen(os.open(path, READ), "rb") as handle:
            if not stat.S_ISREG(os.fstat(handle.fileno()).st_mode):
                return
            while line := handle.readline(LINE):
                if kept(line):
                    yield line
                elif not line.endswith(b"\n"):
                    while (rest := handle.readline(LINE)) and not rest.endswith(b"\n"):
                        pass
    except OSError:
        return


def finite(value: object) -> bool:
    return isinstance(value, int | float) and not isinstance(value, bool) and 0 <= value < math.inf


def well_formed(record: dict[str, object]) -> bool:
    if record.get("kind") != FAULT:
        return True
    return (
        isinstance(record.get("gate"), str)
        and isinstance(record.get("bite"), str)
        and finite(record.get("seconds"))
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


@dataclass
class Facts:
    seconds: dict[str, float] = field(default_factory=dict)
    missing: list[object] | None = None
    faults: dict[tuple[object, object], tuple[float, bool]] = field(default_factory=dict)
    restored: dict[tuple[str, ...], int] = field(default_factory=dict)
    producer_counts: dict[tuple[str, str], int] = field(default_factory=dict)
    latest_producer_run: dict[str, str] = field(default_factory=dict)


HELD: contextvars.ContextVar[dict[Path, Facts] | None] = contextvars.ContextVar(
    "held", default=None
)


@contextlib.contextmanager
def read_once() -> Iterator[None]:
    token = HELD.set({})
    try:
        yield
    finally:
        HELD.reset(token)


def record_restore(record: dict[str, object], found: Facts) -> None:
    size, raw_paths = record.get("bytes"), record.get("paths", [])
    if (
        isinstance(size, int)
        and not isinstance(size, bool)
        and size >= 0
        and isinstance(raw_paths, list)
        and all(isinstance(item, str) for item in raw_paths)
    ):
        found.restored[tuple(raw_paths)] = size


def record_producer(record: dict[str, object], found: Facts) -> None:
    run_id = record.get("run")
    if not isinstance(run_id, str) or not run_id or run_id == NO_RUN:
        return
    kind = record.get("kind")
    if kind == PRODUCER:
        name = record.get("producer")
    elif "kind" not in record:
        name = record.get("key")
    else:
        return
    if isinstance(name, str) and name:
        pair = (run_id, name)
        found.producer_counts[pair] = found.producer_counts.get(pair, 0) + 1
        found.latest_producer_run[name] = run_id


def facts(path: Path) -> Facts:
    held = HELD.get()
    if held is not None and path in held:
        return held[path]
    found = Facts()
    for record in read(path):
        kind, key, seconds = record.get("kind"), record.get("key"), record.get("seconds")
        record_producer(record, found)
        if isinstance(seconds, int | float):
            if isinstance(key, str):
                found.seconds[key] = float(seconds)
            if kind == FAULT:
                place = (record.get("gate"), record.get("bite"))
                found.faults[place] = (float(seconds), record.get("narrowed") is True)
        if kind == TIER:
            found.missing = []
        elif kind == CACHE_MISSING and found.missing is not None:
            found.missing.append(record.get("path"))
        elif kind == CACHE_RESTORE:
            record_restore(record, found)
    if held is not None:
        held[path] = found
    return found


def cache_paths(binding: str) -> list[str]:
    line = markdown_text.bullets(markdown_text.section(binding, SPEED)).get(LOCAL_CACHES, "")
    if line.startswith("<"):
        return []
    return [span for span in markdown_text.ticked(line) if " " not in span]


def ci_cache_paths(binding: str) -> list[str]:
    line = markdown_text.bullets(markdown_text.section(binding, SPEED)).get(CACHE, "")
    if line.startswith("<") or line.lower().startswith("none:"):
        return []
    return [span for span in markdown_text.ticked(line) if " " not in span]


def exists(root: Path, path: str) -> bool:
    place = root / Path(path).expanduser()
    anchor = Path(place.anchor)
    return any(anchor.glob(str(place.relative_to(anchor))))


def missing_cache(root: Path) -> list[str]:
    binding = gatelib.read_text(root / gatelib.BINDING) or ""
    return [path for path in cache_paths(binding) if not exists(root, path)]
