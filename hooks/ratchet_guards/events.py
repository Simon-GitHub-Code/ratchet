import argparse
import errno
import fcntl
import hashlib
import json
import os
import re
import stat
import subprocess
import sys
import tempfile
from collections.abc import Sequence
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import TypeGuard

LOG_NAME = "ratchet-guard-events.jsonl"
PLUGIN_RECORD = "ratchet-plugin"
FEATURE_STARTS = "ratchet-feature-starts"
LIMIT = 4096
CLIP = 64
GENESIS = "0" * 64
_HEX64 = re.compile(r"^[0-9a-f]{64}$")
_FLAGS = os.O_RDWR | os.O_APPEND | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK
_READ_FLAGS = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK


def _is_digest(value: object) -> TypeGuard[str]:
    return isinstance(value, str) and _HEX64.fullmatch(value) is not None


def digest_of(prev: str, fields: dict[str, object]) -> str:
    canonical = json.dumps({"prev": prev, "fields": fields}, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode()).hexdigest()


@dataclass(frozen=True, slots=True)
class Event:
    at: str
    decision: str
    tool: str | None
    rule: str
    detail: str
    branch: str | None

    def line(self, prev: str = GENESIS) -> bytes:
        fields = asdict(self)
        text = json.dumps({**fields, "prev": prev, "digest": digest_of(prev, fields)})
        if len(text) + 1 >= LIMIT:
            cut = {key: v[:CLIP] if isinstance(v, str) else v for key, v in fields.items()}
            truncated: dict[str, object] = {**cut, "truncated": True}
            text = json.dumps({**truncated, "prev": prev, "digest": digest_of(prev, truncated)})
        return (text + "\n").encode()


def _prev_from_tail(tail: bytes) -> str:
    for raw in reversed(tail.split(b"\n")):
        if not raw.strip():
            continue
        try:
            parsed: object = json.loads(raw)
        except (UnicodeDecodeError, ValueError):
            parsed = None
        digest = parsed.get("digest") if isinstance(parsed, dict) else None
        if _is_digest(digest):
            return digest
        return hashlib.sha256(raw).hexdigest()
    return GENESIS


def append(log: Path, item: Event | bytes) -> None:
    descriptor = os.open(log, _FLAGS, 0o600)
    try:
        if not stat.S_ISREG(os.fstat(descriptor).st_mode):
            raise OSError(errno.EINVAL, os.strerror(errno.EINVAL), str(log))
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        if isinstance(item, bytes):
            payload = item
        else:
            size = os.fstat(descriptor).st_size
            if size == 0:
                payload = item.line(GENESIS)
            else:
                tail = os.pread(descriptor, LIMIT, max(0, size - LIMIT))
                prefix = b"" if tail.endswith(b"\n") else b"\n"
                payload = prefix + item.line(_prev_from_tail(tail))
        os.write(descriptor, payload)
    finally:
        os.close(descriptor)


def _verify_lines(raw_lines: list[bytes]) -> list[str]:
    problems: list[str] = []
    expected_prev = GENESIS
    for number, raw_line in enumerate(raw_lines, 1):
        try:
            parsed: object = json.loads(raw_line)
        except (UnicodeDecodeError, ValueError):
            problems.append(f"line {number}: malformed JSON")
            expected_prev = hashlib.sha256(raw_line).hexdigest()
            continue
        if not isinstance(parsed, dict):
            problems.append(f"line {number}: entry is not a JSON object")
            expected_prev = hashlib.sha256(raw_line).hexdigest()
            continue
        prev_value = parsed.get("prev")
        digest_value = parsed.get("digest")
        if not _is_digest(prev_value) or not _is_digest(digest_value):
            problems.append(f"line {number}: missing or invalid digest fields")
            expected_prev = hashlib.sha256(raw_line).hexdigest()
            continue
        if prev_value != expected_prev:
            problems.append(
                f"line {number}: broken chain link"
                f" (expected prev {expected_prev}, got {prev_value})"
            )
        fields = {key: value for key, value in parsed.items() if key not in ("prev", "digest")}
        if digest_value != digest_of(prev_value, fields):
            problems.append(f"line {number}: digest mismatch (modified entry)")
        expected_prev = digest_value
    return problems


def _check(log: Path) -> tuple[list[str], int]:
    try:
        descriptor = os.open(log, _READ_FLAGS)
    except FileNotFoundError:
        return [], 0
    except OSError as error:
        return [f"{log}: cannot open ({error.strerror or error})"], 0
    try:
        if not stat.S_ISREG(os.fstat(descriptor).st_mode):
            return [f"{log}: not a regular file"], 0
        with os.fdopen(descriptor, "rb", closefd=False) as handle:
            raw_bytes = handle.read()
    finally:
        os.close(descriptor)
    if not raw_bytes:
        return [], 0
    raw_lines = raw_bytes.split(b"\n")
    if raw_lines[-1] == b"":
        raw_lines.pop()
    return _verify_lines(raw_lines), len(raw_lines)


def verify(log: Path) -> list[str]:
    return _check(log)[0]


def _common_directory(root: Path) -> Path | None:
    completed = subprocess.run(  # noqa: S603 # -- a fixed argv, no shell
        ["git", "-C", str(root), "rev-parse", "--path-format=absolute", "--git-common-dir"],  # noqa: S607 # -- git is resolved from PATH on purpose
        capture_output=True,
        text=True,
        check=False,
    )
    output = completed.stdout.strip()
    return Path(output) if completed.returncode == 0 and output else None


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Verify the Ratchet guard event log digest chain.")
    parser.add_argument("path", nargs="?", type=Path, help="Path to ratchet-guard-events.jsonl")
    parser.add_argument("--root", type=Path, default=Path(), help="Repository path")
    options = parser.parse_args(argv)
    target: Path | None = options.path
    if target is None:
        common = _common_directory(options.root.resolve())
        if common is None:
            sys.stdout.write(f"guard-log: red ({options.root}: not a git repository)\n")
            return 1
        target = common / LOG_NAME
    problems, count = _check(target)
    for problem in problems:
        sys.stdout.write(f"{problem}\n")
    if problems:
        sys.stdout.write(f"guard-log: red ({len(problems)} problem(s) in {count} lines)\n")
        return 1
    sys.stdout.write(f"guard-log: green ({count} events)\n")
    return 0


def replace(record: Path, text: str) -> None:
    descriptor, name = tempfile.mkstemp(dir=record.parent, prefix=f".{record.name}.")
    try:
        try:
            pending = memoryview(text.encode())
            while pending:
                pending = pending[os.write(descriptor, pending) :]
        finally:
            os.close(descriptor)
        Path(name).replace(record)
    except OSError:
        Path(name).unlink(missing_ok=True)
        raise


if __name__ == "__main__":
    raise SystemExit(main())
