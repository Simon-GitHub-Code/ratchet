#!/usr/bin/env python3
"""The run's time, stated with its result (ADR 0073, point 5).

The feature preflight stamps the ticket's start into ``.ratchet/run-time.json`` and ``end`` stamps
its end, both in UTC. ``line`` reads the timing lines written between the two stamps and prints
one line: how long the ticket took, how many ``check`` runs it held and their seconds, and how many
of them ran on a tree an earlier green ``check`` run had covered. It reports and never stops a run.

A ``check`` run is a tier line whose gate set is the project's ``check`` gate set: the gates of the
justfile's ``tier0_gates`` and ``tier1_gates``. When the start stamp holds a run id, only lines of
that run count.

Usage: ``run_time.py end|line [--root DIR]``.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import UTC, datetime
from pathlib import Path

import conformance
import gatelib
import justfile_text
import queue_notes
import report
import runrecord

STAMPS = "run-time.json"
FORMAT = "%Y-%m-%dT%H:%M:%SZ"
CHECK_TIERS = ("tier0_gates", "tier1_gates")
GREEN = "green"
MINUTE = 60
HOUR = 3600


def now() -> str:
    return datetime.now(UTC).strftime(FORMAT)


def stamps(root: Path) -> dict[str, object]:
    return queue_notes.loaded(gatelib.read_text(root / gatelib.STATE / STAMPS))


def write(root: Path, data: dict[str, object]) -> None:
    gatelib.state_dir(root)
    (root / gatelib.STATE / STAMPS).write_text(json.dumps(data), encoding="utf-8")


def stamp_start(root: Path, branch: str) -> None:
    held = stamps(root)
    if held.get("branch") == branch and "start" in held and "end" not in held:
        return
    data: dict[str, object] = {"branch": branch, "start": now()}
    run = os.environ.get(report.RUN)
    if run:
        data["run"] = run
    write(root, data)


def stamp_end(root: Path) -> None:
    write(root, {**stamps(root), "end": now()})


def moment(value: object) -> datetime | None:
    try:
        return datetime.strptime(str(value), FORMAT).replace(tzinfo=UTC)
    except ValueError:
        return None


def spell(seconds: float) -> str:
    hours, rest = divmod(round(seconds), HOUR)
    minutes, secs = divmod(rest, MINUTE)
    parts = [f"{hours}h"] if hours else []
    parts += [f"{minutes}m"] if hours or minutes else []
    return " ".join([*parts, f"{secs}s"])


def check_gates(root: Path) -> frozenset[str]:
    justfile = conformance.read_justfile(root) or ""
    return frozenset().union(*(justfile_text.listed(justfile, name) for name in CHECK_TIERS))


def tier_lines(root: Path) -> list[dict[str, object]]:
    text = gatelib.read_text(root / gatelib.STATE / "timing.jsonl") or ""
    rows = [queue_notes.loaded(line) for line in text.splitlines()]
    return [row for row in rows if row.get("kind") == runrecord.TIER]


def check_runs(
    root: Path, held: dict[str, object], gates: frozenset[str]
) -> list[dict[str, object]]:
    run = held.get("run")
    return [
        row
        for row in tier_lines(root)
        if set(queue_notes.texts(row.get("gates"))) == gates
        and isinstance(row.get("at"), str)
        and str(held["start"]) <= str(row["at"]) <= str(held["end"])
        and isinstance(row.get("seconds"), int | float)
        and (run is None or row.get("run") == run)
    ]


def covered(runs: list[dict[str, object]]) -> int:
    return sum(
        any(
            earlier.get("status") == GREEN
            and earlier.get("tree") == row.get("tree")
            and str(earlier["at"]) < str(row["at"])
            for earlier in runs
        )
        for row in runs
        if row.get("tree") != runrecord.NO_TREE
    )


def checks_said(runs: list[dict[str, object]]) -> str:
    if not runs:
        return "check did not run"
    seconds = spell(sum(float(str(row["seconds"])) for row in runs))
    if len(runs) == 1:
        return f"check ran once, {seconds}"
    repeats = covered(runs)
    tail = f"{repeats} of them ran" if repeats else "none ran"
    return (
        f"check ran {len(runs)} times, {seconds} in all; {tail}"
        " on a tree a green check had already covered"
    )


def line(root: Path) -> str:
    held = stamps(root)
    begun, ended = moment(held.get("start")), moment(held.get("end"))
    if begun is None:
        return "run time: not measured, since the run has no start stamp"
    if ended is None:
        return "run time: not measured, since the run has no end stamp"
    took = spell((ended - begun).total_seconds())
    gates = check_gates(root)
    if not gates:
        return f"run time: {took}; check runs not counted, since the justfile names no check gates"
    return f"run time: {took}; {checks_said(check_runs(root, held, gates))}"


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=("end", "line"))
    parser.add_argument("--root", default=".")
    options = parser.parse_args(argv[1:])
    root = Path(options.root).resolve()
    if options.command == "end":
        stamp_end(root)
    else:
        print(line(root))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
