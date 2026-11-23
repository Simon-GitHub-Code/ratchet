#!/usr/bin/env python3
from __future__ import annotations

import argparse
import contextlib
import contextvars
import json
import math
import os
import re
import subprocess
import sys
import tempfile
import time
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

import loop_budget
import machine
import runrecord

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

CEILINGS_TABLE = "ceilings.seconds"
DEFAULT_HEADROOM = 1.25
MIN_RUNS = 5
MAX_RUNS = 10
LOWER_SHARE = 0.8
WARN_SHARE = 75.0
FULL_PROOF = "bite-all"
PROOF = "bite"
GROUP = "group"
GROUP_ENV = "RATCHET_BUDGET_GROUP"
_TIMED_ENV: contextvars.ContextVar[dict[str, str] | None] = contextvars.ContextVar(
    "timed_env", default=None
)
_MEASURED: contextvars.ContextVar[list[machine.Machine] | None] = contextvars.ContextVar(
    "measured", default=None
)
_TABLE_HEADER = re.compile(r"^\[ceilings\.seconds\]\s*$", re.MULTILINE)
_ANY_HEADER = re.compile(r"^\[", re.MULTILINE)


@dataclass(frozen=True, slots=True)
class Outcome:
    key: str
    seconds: float
    ceiling: float | None
    exit_code: int
    tier: str | None = None
    blocks: bool = True
    extra: Mapping[str, object] | None = None
    explained: float | None = None

    @property
    def over(self) -> bool:
        return self.ceiling is not None and self.seconds > self.ceiling

    @property
    def share(self) -> float | None:
        return None if not self.ceiling else 100.0 * self.seconds / self.ceiling

    @property
    def near(self) -> bool:
        return self.status == "green" and (self.share or 0.0) >= WARN_SHARE

    @property
    def status(self) -> str:
        if self.exit_code != 0:
            return "failed"
        if self.ceiling is None:
            return "unbounded"
        if self.explained is not None:
            return "not judged"
        return "red" if self.over else "green"

    @property
    def verdict(self) -> int:
        if self.exit_code != 0:
            return self.exit_code
        return int(self.over and self.blocks and self.explained is None)

    def line(self) -> str:
        whose = f" of {self.tier}" if self.tier else ""
        bound = "no ceiling recorded" if self.ceiling is None else f"ceiling {self.ceiling:g}s"
        if self.explained is not None:
            return (
                f"ratchet-budget: {self.key} took {self.seconds:.1f}s ({bound}{whose}):"
                f" not judged: the machine (the machine explains {self.explained:.1f}s)"
            )
        green = self.status == "green" and self.share is not None
        used = f", {self.share:.0f}% used" if green else ""
        reported = self.status == "red" and not self.blocks
        tail = "red, reported and not blocking (--over report)" if reported else self.status
        return f"ratchet-budget: {self.key} took {self.seconds:.1f}s ({bound}{whose}{used}): {tail}"


@dataclass(frozen=True, slots=True)
class NotJudged:
    key: str
    job: str

    @property
    def verdict(self) -> int:
        return 0

    def line(self) -> str:
        return (
            f"ratchet-budget: {self.key} ran in {self.job}, and this run's record holds no time"
            " of it: not judged"
        )


def held(text: str, key: str, tier: str | None) -> tuple[float | None, str | None]:
    own = ceiling_of(text, key)
    shared = ceiling_of(text, tier) if tier else None
    if key == PROOF:
        full = ceiling_of(text, FULL_PROOF)
        if own is not None and (full is None or own <= full):
            return own, None
        if full is not None:
            return full, FULL_PROOF
        return (shared, tier) if shared is not None else (None, None)
    if shared is not None and (own is None or shared < own):
        return shared, tier
    return own, None


def seconds_ceilings(text: str) -> dict[str, float]:
    try:
        table = tomllib.loads(text).get("ceilings", {})
    except tomllib.TOMLDecodeError:
        return {}
    seconds = table.get("seconds", {}) if isinstance(table, dict) else {}
    if not isinstance(seconds, dict):
        return {}
    return {
        key: float(value)
        for key, value in seconds.items()
        if isinstance(value, (int, float)) and not isinstance(value, bool) and 0 <= value < math.inf
    }


def ceiling_of(text: str, key: str) -> float | None:
    return seconds_ceilings(text).get(key)


def with_ceiling(text: str, key: str, seconds: float) -> str:
    line = f"{key} = {seconds:g}"
    header = _TABLE_HEADER.search(text)
    if header is None:
        joiner = (
            "" if not text or text.endswith("\n\n") else ("\n" if text.endswith("\n") else "\n\n")
        )
        return f"{text}{joiner}[{CEILINGS_TABLE}]\n{line}\n"
    start = header.end()
    following = _ANY_HEADER.search(text, start)
    end = len(text) if following is None else following.start()
    body = text[start:end]
    pattern = re.compile(rf"^{re.escape(key)}\s*=.*$", re.MULTILINE)
    if pattern.search(body):
        body = pattern.sub(line, body, count=1)
    else:
        body = body.rstrip("\n") + f"\n{line}\n" + ("\n" if following is not None else "")
    return text[:start] + body + text[end:]


def lowered_ceiling(existing: float | None, measured: float, headroom: float) -> float | None:
    proposed = float(math.ceil(measured * headroom))
    return proposed if existing is None or proposed < existing else None


def timed(command: list[str], cwd: Path) -> tuple[float, int]:
    started = time.monotonic()
    with machine.Monitor() as monitor:
        with monitor.spawning():
            process = subprocess.Popen(command, cwd=cwd, env=_TIMED_ENV.get())
            monitor.register(process.pid)
        try:
            code = process.wait()
        except BaseException:
            process.kill()
            process.wait()
            raise
        finally:
            monitor.unregister(process.pid)
    if (holder := _MEASURED.get()) is not None:
        holder.append(monitor.result())
    return time.monotonic() - started, code


Timed = tuple[float, int]


def records(path: Path | None) -> list[dict[str, object]]:
    return [] if path is None else list(runrecord.read(path))


def timing(record: Mapping[str, object]) -> Timed | None:
    seconds, code = record.get("seconds"), record.get("exit")
    if isinstance(seconds, bool) or not isinstance(seconds, int | float):
        return None
    return (float(seconds), code) if 0 <= seconds < math.inf and type(code) is int else None


def qualifying_seconds(all_records: Sequence[Mapping[str, object]], key: str) -> list[float]:
    found = [
        measured[0]
        for record in all_records
        if "kind" not in record
        and record.get("key") == key
        and record.get("status") in ("green", "unbounded")
        and (measured := timing(record)) is not None
        and measured[1] == 0
    ]
    return found[-MAX_RUNS:]


def lowering_offer(
    key: str, existing: float, all_records: Sequence[Mapping[str, object]]
) -> tuple[float, int] | None:
    runs = qualifying_seconds(all_records, key)
    if len(runs) < MIN_RUNS:
        return None
    proposed = float(math.ceil(max(runs) * DEFAULT_HEADROOM))
    if proposed <= existing * LOWER_SHARE and proposed < existing:
        return proposed, len(runs)
    return None


def lowering_offers(text: str, report_path: Path | None) -> list[tuple[str, float, float, int]]:
    ceilings = seconds_ceilings(text)
    if not ceilings:
        return []
    all_records = records(report_path)
    offers: list[tuple[str, float, float, int]] = []
    for key, current in sorted(ceilings.items()):
        if (offer := lowering_offer(key, current, all_records)) is not None:
            offers.append((key, current, offer[0], offer[1]))
    return offers


def grouped(this_run: list[dict[str, object]], job: str) -> list[tuple[str, Timed | None]] | None:
    ran = [
        gates
        for record in this_run
        if record.get("kind") == GROUP
        and record.get("job") == job
        and isinstance(gates := record.get("gates"), list)
    ]
    if not ran:
        return None
    times = {
        key: timing(record)
        for record in this_run
        if "kind" not in record and isinstance(key := record.get("key"), str)
    }
    return [(gate, times.get(gate)) for gate in map(str, ran[-1])]


def manifest_gates(manifest_records: list[dict[str, object]], job: str) -> list[str] | None:
    ran = [
        list(gates)
        for record in manifest_records
        if record.get("kind") == GROUP
        and record.get("job") == job
        and isinstance(gates := record.get("gates"), list)
        and all(isinstance(gate, str) for gate in gates)
    ]
    return ran[-1] if ran else None


def manifest_times(
    manifest_records: list[dict[str, object]], all_records: list[dict[str, object]]
) -> dict[str, Timed | None]:
    present = {json.dumps(record, sort_keys=True) for record in all_records}
    times: dict[str, Timed | None] = {}
    for record in manifest_records:
        if (
            "kind" not in record
            and json.dumps(record, sort_keys=True) in present
            and isinstance(key := record.get("key"), str)
            and (measured := timing(record)) is not None
            and key not in times
        ):
            times[key] = measured
    return times


def run_gates(
    report_to: Path | None,
    earlier: int,
    *,
    job: str,
    manifest: Path,
) -> list[tuple[str, Timed | None]] | None:
    all_records = records(report_to)
    manifest_records = records(manifest)
    if (gates := manifest_gates(manifest_records, job)) is not None:
        times = manifest_times(manifest_records, all_records)
        return [(gate, times.get(gate)) for gate in gates]
    return grouped(all_records[earlier:], job)


def judged(
    job: Outcome, gates: list[tuple[str, Timed | None]], *, text: str, tier: str | None
) -> list[Outcome | NotJudged]:
    outcomes: list[Outcome | NotJudged] = []
    for gate, seconds in gates:
        if seconds is None:
            outcomes.append(NotJudged(gate, job.key))
            continue
        ceiling, whose = held(text, gate, tier)
        outcomes.append(Outcome(gate, seconds[0], ceiling, seconds[1], whose, job.blocks))
    return outcomes


def said(outcome: Outcome | NotJudged) -> None:
    print(outcome.line())
    if isinstance(outcome, NotJudged):
        print(f"::warning title=ratchet-budget::{outcome.line()}")
        return
    if outcome.explained is not None or (outcome.status == "red" and not outcome.blocks):
        print(f"::warning title=ratchet-budget::{outcome.line()}")
    if outcome.near:
        print(
            f"::warning title=ratchet-budget::ratchet-budget: {outcome.key} used"
            f" {outcome.share:.0f}% of its ceiling, over the {WARN_SHARE:g}% a green run may take"
            " before runner variance turns it red"
        )


def report(
    path: Path,
    outcome: Outcome,
    stamp: dict[str, str],
    *,
    root: Path,
    group_log: Path | None = None,
) -> None:
    record: dict[str, object] = {
        "key": outcome.key,
        "seconds": round(outcome.seconds, 3),
        "ceiling": outcome.ceiling,
        "status": outcome.status,
        "exit": outcome.exit_code,
        **(outcome.extra or {}),
        **stamp,
    }
    if runrecord.append(path, record, root=root) and group_log is not None:
        with contextlib.suppress(OSError), group_log.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record) + "\n")


def machine_excuse(
    measured: machine.Machine,
    *,
    seconds: float,
    ceiling: float | None,
    exit_code: int,
    blocks: bool,
) -> float | None:
    if exit_code != 0 or not blocks or ceiling is None or seconds <= ceiling:
        return None
    if not measured.machine_read or measured.m < machine.NOISE_FLOOR:
        return None
    excuse = loop_budget.explained_seconds(
        loop_budget.Reading(seconds, measured.m, machine_read=True)
    )
    return excuse if seconds - excuse <= ceiling else None


def apply_set(
    thresholds: Path,
    text: str,
    *,
    key: str,
    headroom: float,
    report_path: Path,
) -> None:
    if key == PROOF:
        print(f"ratchet-budget: the owner records {CEILINGS_TABLE}.{PROOF} by hand; --set skips it")
        return
    runs = qualifying_seconds(records(report_path), key)
    if len(runs) < MIN_RUNS:
        print(
            f"ratchet-budget: {CEILINGS_TABLE}.{key} needs {MIN_RUNS} green runs to set,"
            f" has {len(runs)}"
        )
        return
    new = lowered_ceiling(ceiling_of(text, key), max(runs), headroom)
    if new is not None:
        thresholds.parent.mkdir(parents=True, exist_ok=True)
        thresholds.write_text(with_ceiling(text, key, new), encoding="utf-8")
        print(f"ratchet-budget: {CEILINGS_TABLE}.{key} set to {new:g}")


def parse(argv: list[str]) -> tuple[argparse.Namespace, list[str] | None]:
    parser = argparse.ArgumentParser()
    parser.add_argument("key")
    parser.add_argument("--root", default=".")
    parser.add_argument("--file", default="quality/thresholds.toml")
    parser.add_argument("--report")
    parser.add_argument("--tier")
    parser.add_argument("--over", choices=("block", "report"), default="block")
    parser.add_argument("--set", nargs="?", const=DEFAULT_HEADROOM, type=float, dest="headroom")
    if "--" in argv:
        split = argv.index("--")
        return parser.parse_args(argv[1:split]), argv[split + 1 :]
    options = parser.parse_args(argv[1:])
    if options.headroom is None:
        parser.error("the command to time follows '--'")
    return options, None


def settle_ceiling(
    thresholds: Path,
    text: str,
    *,
    options: argparse.Namespace,
    outcome: Outcome,
    report_path: Path | None,
) -> None:
    if options.headroom is not None and report_path is not None:
        if outcome.exit_code == 0 or options.key == PROOF:
            apply_set(
                thresholds,
                text,
                key=options.key,
                headroom=options.headroom,
                report_path=report_path,
            )
    elif outcome.status == "green" and report_path is not None:
        own = ceiling_of(text, options.key)
        if own is not None and (offer := lowering_offer(options.key, own, records(report_path))):
            print(
                f"ratchet-budget: {CEILINGS_TABLE}.{options.key} can be lowered from"
                f" {own:g}s to {offer[0]:g}s ({offer[1]} green runs)"
            )


def main(argv: list[str]) -> int:
    options, command = parse(argv)
    root = Path(options.root).resolve()
    thresholds = root / options.file
    text = thresholds.read_text(encoding="utf-8") if thresholds.is_file() else ""
    if command is None:
        apply_set(
            thresholds,
            text,
            key=options.key,
            headroom=options.headroom,
            report_path=root / (options.report or runrecord.TIMING),
        )
        return 0
    if not command:
        print("ratchet-budget: no command to time")
        return 2
    report_to = (
        root / (options.report or runrecord.TIMING)
        if (options.report or options.headroom is not None)
        else None
    )
    tree = runrecord.tree_id(root) if report_to else None
    earlier = len(records(report_to))
    holder: list[machine.Machine] = []
    with tempfile.TemporaryDirectory() as directory:
        manifest = Path(directory) / "group.jsonl"
        env_token = _TIMED_ENV.set({**os.environ, GROUP_ENV: str(manifest)})
        measured_token = _MEASURED.set(holder)
        try:
            seconds, exit_code = timed(command, root)
        finally:
            _MEASURED.reset(measured_token)
            _TIMED_ENV.reset(env_token)
        gates = run_gates(report_to, earlier, job=options.key, manifest=manifest)
    ceiling, tier = held(text, options.key, None if gates is not None else options.tier)
    measured = holder[-1] if holder else machine.unavailable()
    extra = {**measured.fields(), **({"tier": options.tier} if options.tier else {})}
    explained = machine_excuse(
        measured,
        seconds=seconds,
        ceiling=ceiling,
        exit_code=exit_code,
        blocks=options.over == "block",
    )
    outcome = Outcome(
        options.key,
        seconds,
        ceiling,
        exit_code,
        tier,
        options.over == "block",
        extra=extra,
        explained=explained,
    )
    said(outcome)
    each_gate = judged(outcome, gates or [], text=text, tier=options.tier)
    for each in each_gate:
        said(each)
    if report_to:
        report(report_to, outcome, runrecord.stamp(root, tree), root=root)
    settle_ceiling(thresholds, text, options=options, outcome=outcome, report_path=report_to)
    return next((each.verdict for each in (outcome, *each_gate) if each.verdict), 0)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
