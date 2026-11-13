#!/usr/bin/env python3
"""The speed gate: a tier or gate must finish under its recorded ceiling, and ceilings only fall.

Usage: ``budget.py KEY [--root DIR] [--file quality/thresholds.toml] [--report PATH]
[--tier TIER] [--over block|report] [--set [HEADROOM]] -- COMMAND...``. Runs ``COMMAND``,
measures its wall-clock seconds, and compares them with ``ceilings.seconds.KEY`` in the
thresholds file. Exit status: the command's own when it fails; 1 when it passed but ran over
its ceiling; 0 otherwise. A missing ceiling reports and passes: that is the first adoption.
``--report`` appends one JSON line per run so CI can publish the timings.

``--tier TIER`` is for CI, where a tier is one job per gate and its wall clock is therefore its
slowest gate: the gate is also held to ``ceilings.seconds.TIER``, and the lower of the two
ceilings is the one judged. So the tier ceilings adopt sets are measured in CI with no job that
adds the gates up. ``--over report`` is for a pull request, whose runner varies: a run over its
ceiling is printed, raised as a warning annotation, recorded as red and exits 0. The default,
``--over block``, is what the default branch, the nightly and a local run use.

``--set`` records the measured time times ``HEADROOM`` (default 1.5) as the key's own ceiling
when none exists or the new value is lower; it never raises one, because raising a ceiling is a
loosening and needs an ADR, like lowering a threshold. A tier's ceiling is never set under the
tier's budget in the method (tier 0 about a minute, tier 1 fifteen minutes): a skeleton's few
seconds would otherwise become the first feature's ceiling.
"""

from __future__ import annotations

import argparse
import json
import math
import re
import subprocess
import sys
import time
import tomllib
from dataclasses import dataclass
from pathlib import Path

CEILINGS_TABLE = "ceilings.seconds"
DEFAULT_HEADROOM = 1.5
TIER_BUDGETS = {"tier0": 60.0, "tier1": 900.0}
_TABLE_HEADER = re.compile(r"^\[ceilings\.seconds\]\s*$", re.MULTILINE)
_ANY_HEADER = re.compile(r"^\[", re.MULTILINE)


@dataclass(frozen=True, slots=True)
class Outcome:
    """One timed run: what ran, how long, against which ceiling, and how it ended."""

    key: str
    seconds: float
    ceiling: float | None
    exit_code: int
    tier: str | None = None
    blocks: bool = True

    @property
    def over(self) -> bool:
        """Whether the run passed its command but exceeded its ceiling."""
        return self.ceiling is not None and self.seconds > self.ceiling

    @property
    def status(self) -> str:
        """``failed``, ``unbounded`` (no ceiling), ``red`` (over) or ``green``."""
        if self.exit_code != 0:
            return "failed"
        if self.ceiling is None:
            return "unbounded"
        return "red" if self.over else "green"

    @property
    def verdict(self) -> int:
        """The exit status: the command's own failure, else 1 when over a ceiling that blocks."""
        return self.exit_code if self.exit_code != 0 else int(self.over and self.blocks)

    def line(self) -> str:
        """The one line the gate prints."""
        whose = f" of {self.tier}" if self.tier else ""
        bound = "no ceiling recorded" if self.ceiling is None else f"ceiling {self.ceiling:g}s"
        reported = self.status == "red" and not self.blocks
        tail = "red, reported and not blocking (--over report)" if reported else self.status
        return f"ratchet-budget: {self.key} took {self.seconds:.1f}s ({bound}{whose}): {tail}"


def held(text: str, key: str, tier: str | None) -> tuple[float | None, str | None]:
    """The ceiling ``key`` is judged against, and the tier it belongs to when it is the tier's."""
    own = ceiling_of(text, key)
    shared = ceiling_of(text, tier) if tier else None
    if shared is not None and (own is None or shared < own):
        return shared, tier
    return own, None


def ceiling_of(text: str, key: str) -> float | None:
    """``ceilings.seconds.KEY`` in a thresholds file, or ``None``."""
    table = tomllib.loads(text).get("ceilings", {})
    seconds = table.get("seconds", {}) if isinstance(table, dict) else {}
    value = seconds.get(key) if isinstance(seconds, dict) else None
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else None


def with_ceiling(text: str, key: str, seconds: float) -> str:
    """``text`` with ``ceilings.seconds.KEY = seconds``, the table added when absent."""
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
    """The ceiling to record: measured times headroom but not under ``floor``, if it lowers."""
    proposed = max(float(math.ceil(measured * headroom)), floor)
    return proposed if existing is None or proposed < existing else None


def timed(command: list[str], cwd: Path) -> tuple[float, int]:
    started = time.monotonic()
    completed = subprocess.run(command, cwd=cwd, check=False)
    return time.monotonic() - started, completed.returncode


def report(path: Path, outcome: Outcome) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    record = {
        "key": outcome.key,
        "seconds": round(outcome.seconds, 3),
        "ceiling": outcome.ceiling,
        "status": outcome.status,
        "exit": outcome.exit_code,
    }
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record) + "\n")


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
    seconds, exit_code = timed(command, root)
    ceiling, tier = held(text, options.key, options.tier)
    outcome = Outcome(options.key, seconds, ceiling, exit_code, tier, options.over == "block")
    print(outcome.line())
    if outcome.status == "red" and not outcome.blocks:
        # Why: a pull request's run stays green over its ceiling, so the line is raised where the
        # run's page and `gh run view` show it; printed alone, it waited for the default branch.
        print(f"::warning title=ratchet-budget::{outcome.line()}")
    if options.report:
        report(root / options.report, outcome)
    if options.headroom is not None and exit_code == 0:
        floor = TIER_BUDGETS.get(options.key, 0.0)
        own = ceiling_of(text, options.key)
        new = lowered_ceiling(own, seconds, options.headroom, floor=floor)
        if new is not None:
            thresholds.parent.mkdir(parents=True, exist_ok=True)
            thresholds.write_text(with_ceiling(text, options.key, new), encoding="utf-8")
            print(f"ratchet-budget: {CEILINGS_TABLE}.{options.key} set to {new:g}")
    return outcome.verdict


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
