#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import math
import re
import statistics
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import TYPE_CHECKING

import budget
import gatelib
import justfile_text
import markdown_text
import project_bite
import runrecord

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

WARN_SHARE = 0.80
TARGET_SHARE = 0.60
RECENT_RUNS = 5
BITE_ALL = "bite-all"
TIER2 = "tier2"
TIER2_SHARDED = "tier2_sharded"
SPEED_SECTION = "## Speed"
SHARDS_BULLET = "Shards"
SHARDS_VALUE = re.compile(r"[1-9][0-9]*")


@dataclass(frozen=True)
class Projection:
    gate: str
    projected: float
    ceiling: float
    share: float
    sharded: bool
    current_shards: int
    recommended_shards: int
    source: str

    def action(self) -> str:
        if self.sharded:
            return (
                f"raise `- Shards:` from {self.current_shards} to {self.recommended_shards}"
                " in `docs/process/binding.md`"
            )
        return (
            f"add `{self.gate}` to `{TIER2_SHARDED}` in `justfile`"
            f" and ensure `- Shards:` >= {self.recommended_shards} in `docs/process/binding.md`"
        )

    def line(self) -> str:
        pct = self.share * 100
        return (
            f"tier2-projection: {self.gate} projected at {self.projected:.1f}s"
            f" of {self.ceiling:g}s ceiling ({pct:.0f}%, source={self.source});"
            f" {self.action()}"
        )

    def proposal(self) -> str:
        pct = self.share * 100
        return (
            f"tier 2 gate `{self.gate}` is projected at {self.projected:.1f}s"
            f" ({pct:.0f}% of {self.ceiling:g}s ceiling, from {self.source});"
            f" {self.action()}"
        )


@dataclass(frozen=True)
class Config:
    thresholds: str
    sharded: frozenset[str]
    shards: int
    runs: Mapping[str, list[float]]
    faults: Mapping[tuple[object, object], tuple[float, bool]]


def tier2_gates(binding: str) -> list[str]:
    return list(
        dict.fromkeys(
            row.gate
            for row in project_bite.gate_rows(binding)
            if row.tier == "2"
            and (row.bound or project_bite.SHIPPED.fullmatch(row.tool) is not None)
            and row.gate
        )
    )


def shard_count(binding: str) -> int:
    value = markdown_text.bullets(markdown_text.section(binding, SPEED_SECTION)).get(
        SHARDS_BULLET, ""
    )
    return int(value) if SHARDS_VALUE.fullmatch(value) else 1


def sharded_gates(justfile: str) -> set[str]:
    return justfile_text.listed(justfile, TIER2_SHARDED)


def green_runs(timing_path: Path) -> dict[str, list[float]]:
    runs: dict[str, list[float]] = {}
    for record in runrecord.read(timing_path):
        key = record.get("key")
        measured = budget.timing(record) if "kind" not in record else None
        if isinstance(key, str) and measured is not None and measured[1] == 0:
            runs.setdefault(key, []).append(measured[0])
    return {key: values[-RECENT_RUNS:] for key, values in runs.items()}


def fault_projection(
    faults: Mapping[tuple[object, object], tuple[float, bool]],
    *,
    sharded: bool,
    shards: int,
) -> float | None:
    if not faults:
        return None
    total = sum(seconds for seconds, _ in faults.values())
    concurrency = min(project_bite.MAX_LANES, len(faults))
    unsharded = total / concurrency
    return unsharded / shards if sharded else unsharded


def projected_seconds(
    runs: Sequence[float],
    fault_projected: float | None,
) -> tuple[float, str] | None:
    if not runs:
        return None if fault_projected is None else (fault_projected, "faults")
    direct = statistics.median(runs)
    if fault_projected is not None and fault_projected > direct:
        return fault_projected, "faults"
    return direct, "runs"


def build_projection(
    gate: str,
    estimate: tuple[float, str],
    ceiling: float,
    *,
    sharded: bool,
    shards: int,
) -> Projection | None:
    projected, source = estimate
    share = projected / ceiling
    if share < WARN_SHARE:
        return None
    current_shards = shards if sharded else 1
    unsharded_seconds = projected * current_shards
    recommended_shards = max(
        current_shards + 1,
        math.ceil(unsharded_seconds / (TARGET_SHARE * ceiling)),
    )
    return Projection(
        gate=gate,
        projected=projected,
        ceiling=ceiling,
        share=share,
        sharded=sharded,
        current_shards=current_shards,
        recommended_shards=recommended_shards,
        source=source,
    )


def project_gate(gate: str, config: Config) -> Projection | None:
    ceiling, _ = budget.held(config.thresholds, gate, TIER2)
    if ceiling is None or ceiling <= 0:
        return None
    is_sharded = gate in config.sharded
    from_faults = (
        fault_projection(config.faults, sharded=is_sharded, shards=config.shards)
        if gate == BITE_ALL
        else None
    )
    estimate = projected_seconds(config.runs.get(gate, ()), from_faults)
    if estimate is None:
        return None
    return build_projection(gate, estimate, ceiling, sharded=is_sharded, shards=config.shards)


def projections(root: Path) -> list[Projection]:
    binding = gatelib.read_text(root / gatelib.BINDING) or ""
    gates = tier2_gates(binding)
    thresholds_text = gatelib.read_text(root / gatelib.THRESHOLDS) or ""
    if not gates or not thresholds_text:
        return []
    timing_path = root / runrecord.TIMING
    config = Config(
        thresholds=thresholds_text,
        sharded=frozenset(sharded_gates(gatelib.read_text(root / "justfile") or "")),
        shards=shard_count(binding),
        runs=green_runs(timing_path),
        faults=runrecord.facts(timing_path).faults if BITE_ALL in gates else {},
    )
    found = [item for gate in gates if (item := project_gate(gate, config)) is not None]
    found.sort(key=lambda item: (-item.share, item.gate))
    return found


def proposals(root: Path) -> list[str]:
    return [item.proposal() for item in projections(root)]


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", default=".")
    parser.add_argument("--json", action="store_true", dest="as_json")
    parser.add_argument("--strict", action="store_true")
    options = parser.parse_args(argv[1:])
    root = Path(options.root).resolve()
    found = projections(root)
    if options.as_json:
        print(json.dumps([asdict(item) for item in found]))
    elif found:
        for item in found:
            print(item.line())
    else:
        print("tier2-projection: green")
    return 1 if options.strict and found else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
