"""Shard the suite: ``SHARD`` of ``SHARDS`` runs one deterministic slice of every test.

CI's long gates (``test``, ``test-floor``) run as one job per shard, each held to the gate's
ceiling in ``quality/thresholds.toml``. A test belongs to the shard its node id hashes to, so the
slices are stable between runs and machines, need no timing file to rot and no new dependency, and
spread the slow tests as evenly as the hash spreads ids. A local run sets neither variable and is
shard 1 of 1: the whole suite. ``just shard-check`` proves the slices partition the collection.
"""

from __future__ import annotations

import hashlib
import os

import pytest


def shard_of(nodeid: str, shards: int) -> int:
    """The 1-based shard a test runs in."""
    return int.from_bytes(hashlib.sha256(nodeid.encode()).digest()[:8], "big") % shards + 1


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
    kept = [item for item in items if shard_of(item.nodeid, shards) == shard]
    kept_ids = {id(item) for item in kept}
    config.hook.pytest_deselected(items=[item for item in items if id(item) not in kept_ids])
    items[:] = kept
