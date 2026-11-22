#!/usr/bin/env python3
from __future__ import annotations

import argparse
import math
import re
import subprocess
import sys
import time
import tomllib
from dataclasses import dataclass
from pathlib import Path

import runrecord

CEILINGS_TABLE = "ceilings.seconds"
DEFAULT_HEADROOM = 1.5
WARN_SHARE = 75.0
FULL_PROOF = "bite-all"
PROOF = "bite"
TIER_BUDGETS = {"tier0": 60.0, "tier1": 900.0}
GROUP = "group"
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
        return "red" if self.over else "green"

    @property
    def verdict(self) -> int:
        return self.exit_code if self.exit_code != 0 else int(self.over and self.blocks)

    def line(self) -> str:
        whose = f" of {self.tier}" if self.tier else ""
        bound = "no ceiling recorded" if self.ceiling is None else f"ceiling {self.ceiling:g}s"
        green = self.status == "green" and self.share is not None
        used = f", {self.share:.0f}% used" if green else ""
        reported = self.status == "red" and not self.blocks
        tail = "red, reported and not blocking (--over report)" if reported else self.status
        return f"ratchet-budget: {self.key} took {self.seconds:.1f}s ({bound}{whose}{used}): {tail}"


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


def ceiling_of(text: str, key: str) -> float | None:
    table = tomllib.loads(text).get("ceilings", {})
    seconds = table.get("seconds", {}) if isinstance(table, dict) else {}
    value = seconds.get(key) if isinstance(seconds, dict) else None
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else None


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


def lowered_ceiling(
    existing: float | None, measured: float, headroom: float, *, floor: float = 0.0
) -> float | None:
    proposed = max(float(math.ceil(measured * headroom)), floor)
    return proposed if existing is None or proposed < existing else None


def timed(command: list[str], cwd: Path) -> tuple[float, int]:
    started = time.monotonic()
    completed = subprocess.run(command, cwd=cwd, check=False)
    return time.monotonic() - started, completed.returncode


Timed = tuple[float, int]


def records(path: Path | None) -> list[dict[str, object]]:
    return [] if path is None else list(runrecord.read(path))


def timing(record: dict[str, object]) -> Timed | None:
    seconds, code = record.get("seconds"), record.get("exit")
    if isinstance(seconds, bool) or not isinstance(seconds, int | float):
        return None
    return (float(seconds), code) if 0 <= seconds < math.inf and isinstance(code, int) else None


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


def judged(
    job: Outcome, gates: list[tuple[str, Timed | None]], *, text: str, tier: str | None
) -> list[Outcome]:
    outcomes = []
    for gate, seconds in gates:
        if seconds is None:
            line = (
                f"ratchet-budget: {gate} ran in {job.key}, and this run's record holds no time"
                " of it: not judged"
            )
            print(line)
            print(f"::warning title=ratchet-budget::{line}")
            continue
        ceiling, whose = held(text, gate, tier)
        outcomes.append(Outcome(gate, seconds[0], ceiling, seconds[1], whose, job.blocks))
    return outcomes


def said(outcome: Outcome) -> None:
    print(outcome.line())
    if outcome.status == "red" and not outcome.blocks:
        print(f"::warning title=ratchet-budget::{outcome.line()}")
    if outcome.near:
        print(
            f"::warning title=ratchet-budget::ratchet-budget: {outcome.key} used"
            f" {outcome.share:.0f}% of its ceiling, over the {WARN_SHARE:g}% a green run may take"
            " before runner variance turns it red"
        )


def report(path: Path, outcome: Outcome, stamp: dict[str, str], *, root: Path) -> None:
    record: dict[str, object] = {
        "key": outcome.key,
        "seconds": round(outcome.seconds, 3),
        "ceiling": outcome.ceiling,
        "status": outcome.status,
        "exit": outcome.exit_code,
        **stamp,
    }
    runrecord.append(path, record, root=root)


def parse(argv: list[str]) -> tuple[argparse.Namespace, list[str]]:
    parser = argparse.ArgumentParser()
    parser.add_argument("key")
    parser.add_argument("--root", default=".")
    parser.add_argument("--file", default="quality/thresholds.toml")
    parser.add_argument("--report")
    parser.add_argument("--tier")
    parser.add_argument("--over", choices=("block", "report"), default="block")
    parser.add_argument("--set", nargs="?", const=DEFAULT_HEADROOM, type=float, dest="headroom")
    if "--" not in argv:
        parser.error("the command to time follows '--'")
    split = argv.index("--")
    return parser.parse_args(argv[1:split]), argv[split + 1 :]


def main(argv: list[str]) -> int:
    options, command = parse(argv)
    if not command:
        print("ratchet-budget: no command to time")
        return 2
    root = Path(options.root).resolve()
    thresholds = root / options.file
    text = thresholds.read_text(encoding="utf-8") if thresholds.is_file() else ""
    report_to = root / options.report if options.report else None
    tree = runrecord.tree_id(root) if report_to else None
    earlier = len(records(report_to))
    seconds, exit_code = timed(command, root)
    gates = grouped(records(report_to)[earlier:], options.key)
    ceiling, tier = held(text, options.key, None if gates is not None else options.tier)
    outcome = Outcome(options.key, seconds, ceiling, exit_code, tier, options.over == "block")
    said(outcome)
    each_gate = judged(outcome, gates or [], text=text, tier=options.tier)
    for each in each_gate:
        said(each)
    if report_to:
        report(report_to, outcome, runrecord.stamp(root, tree), root=root)
    if options.headroom is not None and options.key == PROOF:
        print(f"ratchet-budget: the owner records {CEILINGS_TABLE}.{PROOF} by hand; --set skips it")
    elif options.headroom is not None and exit_code == 0:
        floor = TIER_BUDGETS.get(options.key, 0.0)
        own = ceiling_of(text, options.key)
        new = lowered_ceiling(own, seconds, options.headroom, floor=floor)
        if new is not None:
            thresholds.parent.mkdir(parents=True, exist_ok=True)
            thresholds.write_text(with_ceiling(text, options.key, new), encoding="utf-8")
            print(f"ratchet-budget: {CEILINGS_TABLE}.{options.key} set to {new:g}")
    return next((each.verdict for each in (outcome, *each_gate) if each.verdict), 0)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
