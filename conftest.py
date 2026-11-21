from __future__ import annotations

import contextlib
import hashlib
import os
import signal
import subprocess
import time
from typing import TYPE_CHECKING, Any

import pytest

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator

EXITING = 5.0


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
