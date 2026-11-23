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
import threading
import time
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import IO, TYPE_CHECKING, cast

import loop_budget
import machine
import runrecord

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

CEILINGS_TABLE = "ceilings.seconds"
WORK_CEILINGS_PREFIX = "ceilings.work"
DEFAULT_HEADROOM = 1.25
WORK_HEADROOM = 1.1
MIN_RUNS = 5
MAX_RUNS = 10
LOWER_SHARE = 0.8
WARN_SHARE = 75.0
FULL_PROOF = "bite-all"
PROOF = "bite"
GROUP = "group"
GROUP_ENV = "RATCHET_BUDGET_GROUP"
_DEFAULT_HEADROOM = object()
_TIMED_ENV: contextvars.ContextVar[dict[str, str] | None] = contextvars.ContextVar(
    "timed_env", default=None
)
_MEASURED: contextvars.ContextVar[list[machine.Machine] | None] = contextvars.ContextVar(
    "measured", default=None
)
_WORK: contextvars.ContextVar[dict[str, float] | None] = contextvars.ContextVar(
    "work", default=None
)
WORK_LINE = re.compile(r"^ratchet-work:\s+([A-Za-z][A-Za-z0-9_.-]*)\s+([0-9]+(?:\.[0-9]+)?)\s*$")
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


@dataclass(frozen=True, slots=True)
class WorkOutcome:
    gate: str
    count: str
    measured: float | None
    ceiling: float

    @property
    def judgement(self) -> loop_budget.Judgement:
        return loop_budget.judge_work(self.ceiling, self.measured)

    @property
    def status(self) -> str:
        return "red" if self.judgement is loop_budget.OVER else "green"

    @property
    def verdict(self) -> int:
        return loop_budget.EFFECT[
            (loop_budget.Kind.WORK, loop_budget.Place.LOCAL, self.judgement)
        ].exit_for(0)

    def line(self) -> str:
        if self.measured is None or self.measured <= 0:
            return (
                f"ratchet-budget: work {self.gate}.{self.count} missing"
                f" (ceiling {self.ceiling:g}): red"
            )
        return (
            f"ratchet-budget: work {self.gate}.{self.count} was {self.measured:g}"
            f" (ceiling {self.ceiling:g}): {self.status}"
        )


def parse_work(text: str) -> dict[str, float]:
    found: dict[str, float] = {}
    for line in text.splitlines():
        if match := WORK_LINE.match(line):
            value = float(match.group(2))
            if math.isfinite(value):
                found[match.group(1)] = value
    return found


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


def _ceilings_table(text: str) -> dict[str, object]:
    try:
        table = tomllib.loads(text).get("ceilings", {})
    except tomllib.TOMLDecodeError:
        return {}
    return table if isinstance(table, dict) else {}


def _finite_numbers(mapping: object) -> dict[str, float]:
    if not isinstance(mapping, dict):
        return {}
    return {
        key: float(value)
        for key, value in mapping.items()
        if isinstance(value, (int, float)) and not isinstance(value, bool) and 0 <= value < math.inf
    }


def seconds_ceilings(text: str) -> dict[str, float]:
    return _finite_numbers(_ceilings_table(text).get("seconds"))


def work_ceilings(text: str, gate: str) -> dict[str, float]:
    work = _ceilings_table(text).get("work")
    return _finite_numbers(work.get(gate) if isinstance(work, dict) else {})


def work_outcomes(text: str, gate: str, measured: Mapping[str, float]) -> list[WorkOutcome]:
    return [
        WorkOutcome(gate, count, measured.get(count), ceiling)
        for count, ceiling in sorted(work_ceilings(text, gate).items())
    ]


def ceiling_of(text: str, key: str) -> float | None:
    return seconds_ceilings(text).get(key)


def _toml_key(key: str) -> str:
    return key if re.fullmatch(r"[A-Za-z0-9_-]+", key) else json.dumps(key)


def _with_table_entry(text: str, table: str, *, key: str, value: float) -> str:
    line = f"{_toml_key(key)} = {value:g}"
    header = re.search(rf"^\[{re.escape(table)}\]\s*$", text, re.MULTILINE)
    if header is None:
        joiner = (
            "" if not text or text.endswith("\n\n") else ("\n" if text.endswith("\n") else "\n\n")
        )
        return f"{text}{joiner}[{table}]\n{line}\n"
    start = header.end()
    following = _ANY_HEADER.search(text, start)
    end = len(text) if following is None else following.start()
    body = text[start:end]
    pattern = re.compile(rf"^{re.escape(_toml_key(key))}\s*=.*$", re.MULTILINE)
    if pattern.search(body):
        body = pattern.sub(line, body, count=1)
    else:
        body = body.rstrip("\n") + f"\n{line}\n" + ("\n" if following is not None else "")
    return text[:start] + body + text[end:]


def with_ceiling(text: str, key: str, seconds: float) -> str:
    return _with_table_entry(text, CEILINGS_TABLE, key=key, value=seconds)


def with_work_ceiling(text: str, gate: str, *, count: str, value: float) -> str:
    return _with_table_entry(
        text, f"{WORK_CEILINGS_PREFIX}.{_toml_key(gate)}", key=count, value=value
    )


def lowered_ceiling(existing: float | None, measured: float, headroom: float) -> float | None:
    proposed = float(math.ceil(measured * headroom))
    return proposed if existing is None or proposed < existing else None


def _pump_stdout(stream: IO[bytes], work: dict[str, float]) -> None:
    buffer = getattr(sys.stdout, "buffer", None)
    for raw in stream:
        if buffer is not None:
            buffer.write(raw)
            buffer.flush()
        else:
            sys.stdout.write(raw.decode("utf-8", errors="replace"))
            sys.stdout.flush()
        work.update(parse_work(raw.decode("utf-8", errors="replace").rstrip("\r\n")))


def timed(command: list[str], cwd: Path) -> tuple[float, int]:
    started = time.monotonic()
    work: dict[str, float] = {}
    with machine.Monitor() as monitor:
        with monitor.spawning():
            process = subprocess.Popen(
                command, cwd=cwd, env=_TIMED_ENV.get(), stdout=subprocess.PIPE
            )
            monitor.register(process.pid)
        stream = cast("IO[bytes]", process.stdout)
        reader = threading.Thread(target=_pump_stdout, args=(stream, work), daemon=True)
        reader.start()
        try:
            code = process.wait()
        except BaseException:
            process.kill()
            process.wait()
            raise
        finally:
            monitor.unregister(process.pid)
            reader.join()
            stream.close()
    if (holder := _MEASURED.get()) is not None:
        holder.append(monitor.result())
    if (work_holder := _WORK.get()) is not None:
        work_holder.update(work)
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


def qualifying_work(
    all_records: Sequence[Mapping[str, object]],
    gate: str,
    count: str,
    *,
    ceiling: float | None = None,
) -> list[float]:
    found: list[float] = []
    for record in all_records:
        if (
            "kind" in record
            or record.get("key") != gate
            or record.get("status") not in ("green", "unbounded")
            or type(record.get("exit")) is not int
            or record.get("exit") != 0
        ):
            continue
        value = _finite_numbers(record.get("work")).get(count)
        if value is not None and value > 0 and (ceiling is None or value <= ceiling):
            found.append(value)
    return found[-MAX_RUNS:]


def _lowering_offer(
    runs: list[float], existing: float, headroom: float
) -> tuple[float, int] | None:
    if len(runs) < MIN_RUNS:
        return None
    proposed = float(math.ceil(max(runs) * headroom))
    if proposed <= existing * LOWER_SHARE and proposed < existing:
        return proposed, len(runs)
    return None


def work_lowering_offer(
    gate: str,
    count: str,
    *,
    existing: float,
    all_records: Sequence[Mapping[str, object]],
) -> tuple[float, int] | None:
    return _lowering_offer(
        qualifying_work(all_records, gate, count, ceiling=existing), existing, WORK_HEADROOM
    )


def lowering_offer(
    key: str, existing: float, all_records: Sequence[Mapping[str, object]]
) -> tuple[float, int] | None:
    return _lowering_offer(qualifying_seconds(all_records, key), existing, DEFAULT_HEADROOM)


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


GateRun = tuple[str, Timed | None, dict[str, float]]


def grouped(this_run: list[dict[str, object]], job: str) -> list[GateRun] | None:
    ran = [
        gates
        for record in this_run
        if record.get("kind") == GROUP
        and record.get("job") == job
        and isinstance(gates := record.get("gates"), list)
    ]
    if not ran:
        return None
    times: dict[str, tuple[Timed | None, dict[str, float]]] = {
        key: (timing(record), _finite_numbers(record.get("work")))
        for record in this_run
        if "kind" not in record and isinstance(key := record.get("key"), str)
    }
    return [(gate, *times.get(gate, (None, {}))) for gate in map(str, ran[-1])]


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
) -> dict[str, tuple[Timed | None, dict[str, float]]]:
    present = {json.dumps(record, sort_keys=True) for record in all_records}
    times: dict[str, tuple[Timed | None, dict[str, float]]] = {}
    for record in manifest_records:
        if (
            "kind" not in record
            and json.dumps(record, sort_keys=True) in present
            and isinstance(key := record.get("key"), str)
            and (measured := timing(record)) is not None
            and key not in times
        ):
            times[key] = (measured, _finite_numbers(record.get("work")))
    return times


def run_gates(
    report_to: Path | None,
    earlier: int,
    *,
    job: str,
    manifest: Path,
) -> list[GateRun] | None:
    all_records = records(report_to)
    manifest_records = records(manifest)
    if (gates := manifest_gates(manifest_records, job)) is not None:
        times = manifest_times(manifest_records, all_records)
        return [(gate, *times.get(gate, (None, {}))) for gate in gates]
    return grouped(all_records[earlier:], job)


def judged(
    job: Outcome, gates: list[GateRun], *, text: str, tier: str | None
) -> list[Outcome | NotJudged | WorkOutcome]:
    outcomes: list[Outcome | NotJudged | WorkOutcome] = []
    for gate, seconds, work in gates:
        if seconds is None:
            outcomes.append(NotJudged(gate, job.key))
        else:
            ceiling, whose = held(text, gate, tier)
            outcomes.append(Outcome(gate, seconds[0], ceiling, seconds[1], whose, job.blocks))
        outcomes.extend(work_outcomes(text, gate, work))
    return outcomes


def said(outcome: Outcome | NotJudged | WorkOutcome) -> None:
    print(outcome.line())
    if isinstance(outcome, WorkOutcome):
        return
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


def set_work_ceiling(
    thresholds: Path,
    *,
    gate: str,
    count: str,
    headroom: float,
    report_path: Path,
) -> str:
    text = thresholds.read_text(encoding="utf-8") if thresholds.is_file() else ""
    existing = work_ceilings(text, gate).get(count)
    runs = qualifying_work(records(report_path), gate, count, ceiling=existing)
    if len(runs) < MIN_RUNS:
        print(
            f"ratchet-budget: {WORK_CEILINGS_PREFIX}.{gate}.{count} needs {MIN_RUNS} green runs"
            f" to set, has {len(runs)}"
        )
        return text
    new = lowered_ceiling(existing, max(runs), headroom)
    if new is not None:
        text = with_work_ceiling(text, gate, count=count, value=new)
        thresholds.parent.mkdir(parents=True, exist_ok=True)
        thresholds.write_text(text, encoding="utf-8")
        print(f"ratchet-budget: {WORK_CEILINGS_PREFIX}.{gate}.{count} set to {new:g}")
    return text


def offer_seconds_removal(text: str, *, key: str, tier: str | None) -> None:
    if tier == "tier2" or not work_ceilings(text, key):
        return
    old = ceiling_of(text, key)
    if old is not None:
        print(
            f"ratchet-budget: {CEILINGS_TABLE}.{key} can be removed before tier 2 because"
            f" {WORK_CEILINGS_PREFIX}.{key} guards it; record `Relaxes: {CEILINGS_TABLE}.{key}"
            f" from {old:g} to removed` in an ADR citing ADR 0088"
        )


def offer_lowering(text: str, *, outcome: Outcome, report_path: Path) -> None:
    all_records = records(report_path)
    key = outcome.key
    own = ceiling_of(text, key)
    if (
        outcome.status == "green"
        and own is not None
        and (offer := lowering_offer(key, own, all_records))
    ):
        print(
            f"ratchet-budget: {CEILINGS_TABLE}.{key} can be lowered from"
            f" {own:g}s to {offer[0]:g}s ({offer[1]} green runs)"
        )
    if outcome.status in ("green", "unbounded"):
        for count, current in sorted(work_ceilings(text, key).items()):
            work_offer = work_lowering_offer(key, count, existing=current, all_records=all_records)
            if work_offer is not None:
                print(
                    f"ratchet-budget: {WORK_CEILINGS_PREFIX}.{key}.{count} can be lowered from"
                    f" {current:g} to {work_offer[0]:g} ({work_offer[1]} green runs)"
                )


def apply_configured_set(
    thresholds: Path,
    text: str,
    *,
    options: argparse.Namespace,
    report_path: Path,
) -> str:
    if options.work:
        for count in options.work:
            text = set_work_ceiling(
                thresholds,
                gate=options.key,
                count=count,
                headroom=options.headroom,
                report_path=report_path,
            )
        return text
    apply_set(
        thresholds,
        text,
        key=options.key,
        headroom=options.headroom,
        report_path=report_path,
    )
    return text


def parse(argv: list[str]) -> tuple[argparse.Namespace, list[str] | None]:
    parser = argparse.ArgumentParser()
    parser.add_argument("key")
    parser.add_argument("--root", default=".")
    parser.add_argument("--file", default="quality/thresholds.toml")
    parser.add_argument("--report")
    parser.add_argument("--tier")
    parser.add_argument("--over", choices=("block", "report"), default="block")
    parser.add_argument("--work", action="append", default=[], metavar="COUNT")
    parser.add_argument("--set", nargs="?", const=_DEFAULT_HEADROOM, type=float, dest="headroom")
    if "--" in argv:
        split = argv.index("--")
        options, command = parser.parse_args(argv[1:split]), argv[split + 1 :]
    else:
        options, command = parser.parse_args(argv[1:]), None
        if options.headroom is None:
            parser.error("the command to time follows '--'")
    if options.headroom is _DEFAULT_HEADROOM:
        options.headroom = WORK_HEADROOM if options.work else DEFAULT_HEADROOM
    return options, command


def settle_ceiling(
    thresholds: Path,
    text: str,
    *,
    options: argparse.Namespace,
    outcome: Outcome,
    report_path: Path | None,
) -> None:
    if options.headroom is not None and report_path is not None:
        if outcome.exit_code == 0 or (options.key == PROOF and not options.work):
            text = apply_configured_set(thresholds, text, options=options, report_path=report_path)
    elif outcome.exit_code == 0 and report_path is not None:
        offer_lowering(text, outcome=outcome, report_path=report_path)
    offer_seconds_removal(text, key=options.key, tier=options.tier)


def _timed_run(
    command: list[str],
    *,
    root: Path,
    key: str,
    report_to: Path | None,
    earlier: int,
) -> tuple[float, int, machine.Machine, dict[str, float], list[GateRun] | None]:
    holder: list[machine.Machine] = []
    work_counts: dict[str, float] = {}
    with tempfile.TemporaryDirectory() as directory:
        manifest = Path(directory) / "group.jsonl"
        env_token = _TIMED_ENV.set({**os.environ, GROUP_ENV: str(manifest)})
        measured_token = _MEASURED.set(holder)
        work_token = _WORK.set(work_counts)
        try:
            seconds, exit_code = timed(command, root)
        finally:
            _WORK.reset(work_token)
            _MEASURED.reset(measured_token)
            _TIMED_ENV.reset(env_token)
        gates = run_gates(report_to, earlier, job=key, manifest=manifest)
    measured = holder[-1] if holder else machine.unavailable()
    return seconds, exit_code, measured, work_counts, gates


def main(argv: list[str]) -> int:
    options, command = parse(argv)
    root = Path(options.root).resolve()
    thresholds = root / options.file
    text = thresholds.read_text(encoding="utf-8") if thresholds.is_file() else ""
    if command is None:
        report_path = root / (options.report or runrecord.TIMING)
        text = apply_configured_set(thresholds, text, options=options, report_path=report_path)
        offer_seconds_removal(text, key=options.key, tier=options.tier)
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
    seconds, exit_code, measured, work_counts, gates = _timed_run(
        command, root=root, key=options.key, report_to=report_to, earlier=earlier
    )
    ceiling, tier = held(text, options.key, None if gates is not None else options.tier)
    extra: dict[str, object] = {
        **measured.fields(),
        **({"tier": options.tier} if options.tier else {}),
        **({"work": work_counts} if work_counts else {}),
    }
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
    each_work = work_outcomes(text, options.key, work_counts)
    for each in (*each_gate, *each_work):
        said(each)
    if report_to:
        report(report_to, outcome, runrecord.stamp(root, tree), root=root)
    settle_ceiling(thresholds, text, options=options, outcome=outcome, report_path=report_to)
    return next((each.verdict for each in (outcome, *each_gate, *each_work) if each.verdict), 0)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
