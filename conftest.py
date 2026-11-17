"""Shard the suite: ``SHARD`` of ``SHARDS`` runs one deterministic slice of every test.

CI's long gates (``test``, ``test-floor``) run as one job per shard, each held to the gate's
ceiling in ``quality/thresholds.toml``. A test belongs to the shard its node id hashes to, so the
slices are stable between runs and machines, need no timing file to rot and no new dependency, and
spread the slow tests as evenly as the hash spreads ids. A hash spreads a few long tests badly, so
a parametrised test whose cases are each long marks each case ``shard_slot(n)``: case ``n`` runs
in shard ``n`` modulo the count, plus one, and the cases take the shards in turn. A local run sets
neither variable and is shard 1 of 1: the whole suite. ``just shard-check`` proves the slices
partition the collection.
"""

from __future__ import annotations

import hashlib
import os
from pathlib import Path

import pytest

import coverage_settle


def shard_of(nodeid: str, shards: int) -> int:
    """The 1-based shard a test runs in."""
    return int.from_bytes(hashlib.sha256(nodeid.encode()).digest()[:8], "big") % shards + 1


def shard_of_item(item: pytest.Item, shards: int) -> int:
    """The 1-based shard an item runs in: its ``shard_slot`` in turn, else its node id's hash."""
    slot = item.get_closest_marker("shard_slot")
    if slot is None:
        return shard_of(item.nodeid, shards)
    (place,) = slot.args
    return int(place) % shards + 1


def pytest_configure(config: pytest.Config) -> None:
    """Name the ``shard_slot`` marker, so no run warns that it is unknown."""
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
    """Keep the tests whose node id hashes to this shard; a whole-suite run keeps every one."""
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


SETTLE_SECONDS = 30.0


@pytest.hookimpl(wrapper=True, trylast=True)
def pytest_runtestloop(session: pytest.Session) -> object:
    """Let every process still writing coverage data finish before the run combines it."""
    result = yield
    if not hasattr(session.config, "workerinput"):
        coverage_settle.settle(Path.cwd(), SETTLE_SECONDS)
    return result
