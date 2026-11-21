from __future__ import annotations

import contextlib
import hashlib
import os
import secrets
import signal
import subprocess
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pytest

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator

pytest_plugins = ["pytester"]

EXITING = 5.0
OWNER = "RATCHET_TEST_OWNER"
TOKEN = pytest.StashKey[str]()
BEFORE = pytest.StashKey[frozenset[int]]()
PROC = Path("/proc")
STARTERS = frozenset({"spawn", "sessions_ended"})


def living() -> dict[int, tuple[int, bytes]]:
    found: dict[int, tuple[int, bytes]] = {}
    for entry in PROC.iterdir():
        with contextlib.suppress(OSError, ValueError):
            fields = (entry / "stat").read_bytes().rpartition(b")")[2].split()
            if fields[0] != b"Z":
                found[int(entry.name)] = int(fields[1]), (entry / "environ").read_bytes()
    return found


def descendants(table: dict[int, tuple[int, bytes]]) -> set[int]:
    found = {os.getpid()}
    while grown := {pid for pid, (parent, _) in table.items() if parent in found} - found:
        found |= grown
    return found - {os.getpid()}


def started_by(token: str, before: frozenset[int]) -> set[int]:
    table = living()
    mark = f"{OWNER}={token}".encode()
    marked = {pid for pid, (_, env) in table.items() if mark in env.split(b"\0")}
    return (marked | descendants(table)) - before - {os.getpid()}


def command_of(pid: int) -> str:
    try:
        line = (PROC / str(pid) / "cmdline").read_bytes()
    except OSError:
        return "(exited)"
    return line.replace(b"\0", b" ").strip().decode(errors="replace")


def left_running(token: str, before: frozenset[int]) -> dict[int, str]:
    deadline = time.monotonic() + EXITING
    while (found := started_by(token, before)) and time.monotonic() < deadline:
        time.sleep(0.05)
    named = {pid: command_of(pid) for pid in found}
    for pid in found:
        with contextlib.suppress(ProcessLookupError):
            os.kill(pid, signal.SIGKILL)
    return named


def starts_processes(item: pytest.Item) -> bool:
    return bool(STARTERS & set(getattr(item, "fixturenames", ())))


@pytest.hookimpl(wrapper=True)
def pytest_runtest_setup(item: pytest.Item) -> Iterator[None]:
    if starts_processes(item):
        item.stash[BEFORE] = frozenset(descendants(living()))
        item.stash[TOKEN] = os.environ[OWNER] = f"{os.getpid()}-{secrets.token_hex(8)}"
    return (yield)


@pytest.hookimpl(wrapper=True)
def pytest_runtest_teardown(item: pytest.Item) -> Iterator[None]:
    if TOKEN not in item.stash:
        return (yield)
    try:
        done = yield
    finally:
        os.environ.pop(OWNER, None)
        left = left_running(item.stash[TOKEN], item.stash[BEFORE])
    if left:
        named = "".join(f"\n  {pid} {line}" for pid, line in sorted(left.items()))
        pytest.fail(f"the test left {len(left)} running, ended now:{named}", pytrace=False)
    return done


def group_alive(group: int) -> bool:
    try:
        os.killpg(group, 0)
    except ProcessLookupError:
        return False
    return True


def end_group(process: subprocess.Popen[Any]) -> None:
    with contextlib.suppress(ProcessLookupError):
        os.killpg(process.pid, signal.SIGTERM)
    deadline = time.monotonic() + EXITING
    while process.poll() is None or group_alive(process.pid):
        if time.monotonic() > deadline:
            break
        time.sleep(0.02)
    with contextlib.suppress(ProcessLookupError):
        os.killpg(process.pid, signal.SIGKILL)
    process.wait()
    for stream in (process.stdin, process.stdout, process.stderr):
        if stream is not None:
            stream.close()


@pytest.fixture
def spawn() -> Iterator[Callable[..., subprocess.Popen[Any]]]:
    begun: list[subprocess.Popen[Any]] = []

    def start(argv: list[str], **options: Any) -> subprocess.Popen[Any]:  # noqa: ANN401 # -- Popen's own options
        process = subprocess.Popen(argv, start_new_session=True, **options)  # noqa: S603 # -- the test's own argv
        begun.append(process)
        return process

    yield start
    for process in reversed(begun):
        end_group(process)


def shard_of(nodeid: str, shards: int) -> int:
    return int.from_bytes(hashlib.sha256(nodeid.encode()).digest()[:8], "big") % shards + 1


def shard_of_item(item: pytest.Item, shards: int) -> int:
    slot = item.get_closest_marker("shard_slot")
    if slot is None:
        return shard_of(item.nodeid, shards)
    (place,) = slot.args
    return int(place) % shards + 1


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line(
        "markers", "shard_slot(n): case n of a long parametrised test takes the shards in turn"
    )


def _count(name: str) -> int:
    raw = os.environ.get(name, "")
    if not raw:
        return 1
    if not raw.isdigit() or int(raw) < 1:
        msg = f"{name}={raw!r}: expected a whole number of at least 1"
        raise pytest.UsageError(msg)
    return int(raw)


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    shards = _count("SHARDS")
    shard = _count("SHARD")
    if shard > shards:
        msg = f"SHARD={shard} is past SHARDS={shards}"
        raise pytest.UsageError(msg)
    if shards == 1:
        return
    kept = [item for item in items if shard_of_item(item, shards) == shard]
    kept_ids = {id(item) for item in kept}
    config.hook.pytest_deselected(items=[item for item in items if id(item) not in kept_ids])
    items[:] = kept
