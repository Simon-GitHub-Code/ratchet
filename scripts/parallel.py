#!/usr/bin/env python3
"""The parallel runner: run a tier's gates at once, keep each one's output whole, name every red.

A tier is a set of independent gates, so its local wall clock should be the slowest gate, not
the sum. CI already runs one job per gate; this gives a developer's machine the same shape. It
knows nothing about the stack: each gate is an entry point of the repository's command runner
(a ``just`` recipe, a ``make`` target, a package script), and the runner's command prefix is
yours to give. Python 3.11 standard library only, Linux and macOS.

Usage::

    parallel.py [--runner "just"] [--jobs N] [--serial A B]... [--alone GATE...]
                [--fail-fast] [--root DIR] [--report PATH] -- GATE...

* Each ``GATE`` becomes ``RUNNER GATE`` and runs in ``--root`` (the current directory). The
  entry is split like a shell line, so ``"test-shard 1/4"`` passes two words. With no
  ``--runner`` each entry is a whole command: ``-- "ruff check ." "mypy --strict ."``.
* At most ``--jobs`` gates run at a time: ``RATCHET_JOBS`` by default, else the CPU count. A
  gate that is itself parallel shares the cores with the rest; the operating system schedules
  them. On a machine that is shared, or where each gate uses every core, give a smaller number.
* ``--alone GATE...`` names gates that are measured alone: each runs once every other gate has
  finished, one at a time, with nothing beside it. It is for a gate that judges elapsed time (a
  benchmark, a load run), whose reading the other gates would disturb. A name that is not among
  this run's gates is ignored, so one list serves every tier.
* Each gate's output (stdout and stderr, in the order it wrote them) is held until the gate
  finishes and is then printed whole under a ``==> name <==`` header, followed by one status
  line with its seconds. Gates never interleave. Output is plain lines whether or not a terminal
  is attached: no colour, no cursor movement, flushed gate by gate, so a CI log reads as a
  terminal does. A gate's output is captured, so a tool that colours only for a terminal prints
  plain text; give the recipe the tool's own force-colour switch to colour the replay.
* A gate that exits 0 and whose whole output is one ``later: <trigger>`` line judged nothing:
  it is the body the template gives a question not asked yet (ADR 0028), and it is not run yet
  whatever its row says. One whose whole output is one ``none: <reason>`` line, the body of a
  question that does not apply, is not applicable, unless the binding at ``--root`` binds the
  gate to a tool: a tool that prints ``none: ...`` ran. The gate is matched to its Gates row by
  its last word, the entry point's name. Its status line says ``not run yet`` or ``not
  applicable`` in place of green, and the closing lines count the checks that ran and passed
  apart from these, so a run of mostly ``later:`` gates never reads as all green: the last line
  is ``parallel: green (26 ran, 39 not run yet)``, or ``parallel: no check ran (...)`` when
  none did. Such a gate is not red: the exit code is unchanged.
* The exit code is 0 when every gate is green and 1 otherwise, and the closing lines name EVERY
  red gate, so one run shows all of them. 128 plus the signal after an interrupt; 2 for a
  usage error. ``--fail-fast`` stops at the first red instead, for an inner loop: it stops the
  gates still running, starts no more, and names what it stopped and what never started.
* ``--serial A B`` declares a group: its gates run one after another, in the order written,
  inside the parallel run, while other gates run beside it. The group takes one job at a time.
  Repeat the option for more groups. A gate is in at most one group.
* ``--report PATH`` appends one JSON line per finished gate, in the record ``budget.py``
  writes (``key``, ``seconds``, ``ceiling``, ``status``, ``exit``), so one reader serves both.
  The ceiling is null: a parallel run's seconds include contention for the machine, so ceilings
  are judged by ``budget.py`` where one gate runs alone, as CI does. The slowest gate is named
  in the closing lines either way.
* SIGINT, SIGTERM and SIGHUP stop every running gate's whole process group (each gate runs in a
  session of its own), then exit 128 plus the signal. Nothing is left running except what a gate
  detached from its own group; SIGKILL cannot be caught by anything.
* A gate's processes end with it. When a gate exits and something it started is still running
  in its process group (a service a test booted and never stopped), the runner ends it and says
  so under the gate's output. The verdict is the gate's own exit code.

Gates that cannot share a working tree at the same time must be declared, because the runner
cannot see what a command touches. Put them in one ``--serial`` group:

* two tools that write the same cache directory, lock file or coverage data file;
* a gate that mutates the tree (a formatter that rewrites files, a code generator, a migration)
  together with every gate that reads what it writes, since a reader would see half a write;
* a gate that binds a fixed port. Better: boot the service on ``{port}`` through
  ``with_service.py``, which takes a free port per run and needs no group.

An undeclared clash shows up as a gate that is green alone and red in company. Give each tool a
cache directory of its own, or declare the group.
"""

from __future__ import annotations

import argparse
import contextlib
import os
import re
import shlex
import signal
import subprocess
import sys
import tempfile
import time
from collections import Counter, deque
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import IO

import application_types
import budget
import gatelib
import project_bite

POLL = 0.05
# The job a gate that is measured alone runs in; the units count from 0.
ALONE = -1
# Why: the template gives a question not asked yet the recipe `@echo "later: <trigger>"`, and one
# that does not apply `@echo "none: <reason>"`, so that one line is all such a gate says.
WAITING = re.compile(r"(?P<standing>later|none): \S.*")


class Standing(Enum):
    """How a finished gate stands, worded as the closing line counts it."""

    GREEN = "ran and passed"
    RED = "failed"
    LATER = "not run yet, each waiting for what its line names"
    NONE = "not applicable, as its line says"


def tools(root: Path) -> frozenset[str]:
    """The entry points the binding at ``root`` binds to a tool: a row neither ``none`` nor later.

    Why: a tool whose whole output reads ``none: ...`` judged something, so a ``none:`` line is a
    gate that judged nothing only where the binding gives the gate no tool.
    """
    rows = project_bite.gate_rows(gatelib.read_text(root / gatelib.BINDING) or "")
    waits = (application_types.NONE, application_types.LATER)
    return frozenset(
        row.gate for row in rows if row.tool and not any(w.match(row.tool) for w in waits)
    )


@dataclass(frozen=True, slots=True)
class Finished:
    """One gate that ran to its end: what it printed, how it ended and how long it took.

    ``tool`` says the binding binds the gate to a tool.
    """

    name: str
    seconds: float
    exit_code: int
    output: str
    tool: bool = False

    @property
    def green(self) -> bool:
        """Whether the gate exited 0."""
        return self.exit_code == 0

    @property
    def standing(self) -> Standing:
        """Red, ran and passed, or green having judged nothing: ``later:`` or ``none:``."""
        if not self.green:
            return Standing.RED
        waiting = WAITING.fullmatch(self.output.strip())
        if waiting is None or (waiting["standing"] == "none" and self.tool):
            return Standing.GREEN
        return Standing[waiting["standing"].upper()]

    def line(self) -> str:
        """The one status line the gate gets."""
        match self.standing:
            case Standing.GREEN:
                how = "green"
            case Standing.RED:
                how = f"red (exit {self.exit_code})"
            case Standing.LATER:
                how = f"not run yet ({self.output.strip()})"
            case _:
                how = f"not applicable ({self.output.strip()})"
        return f"parallel: {self.name}: {how} in {self.seconds:.1f}s"


@dataclass(slots=True)
class Live:
    """A running gate: its process, where its output goes and when it started."""

    name: str
    process: subprocess.Popen[bytes]
    log: IO[bytes]
    started: float
    tool: bool


def say(text: str) -> None:
    print(text, flush=True)


def plan(
    gates: list[str], serial: list[list[str]], alone: frozenset[str] = frozenset()
) -> list[tuple[str, ...]]:
    """The units of work in launch order: each ``--serial`` group once, other gates singly.

    A unit is the gates that share one job; a unit launches where its first gate stands in
    ``gates``. The gates in ``alone`` are left out: they run after the units. Raises
    ``ValueError`` with the reason when the gates or groups are unusable, which includes a gate
    that does not split like a shell line.
    """
    if not gates:
        message = "no gates: name them after '--'"
        raise ValueError(message)
    if len(set(gates)) != len(gates):
        message = "a gate is named twice; each runs once"
        raise ValueError(message)
    if not all(shlex.split(name) for name in gates):
        message = "a gate is empty"
        raise ValueError(message)
    groups = [tuple(group) for group in serial]
    grouped = [name for group in groups for name in group]
    for name in grouped:
        if name not in gates:
            message = f"--serial names {name!r}, which is not among the gates"
            raise ValueError(message)
    if len(set(grouped)) != len(grouped):
        message = "a gate is in two --serial groups; a gate belongs to at most one"
        raise ValueError(message)
    if alone.intersection(grouped):
        message = "a gate is in --alone and in a --serial group; it runs alone already"
        raise ValueError(message)
    single = [(name,) for name in gates if name not in grouped and name not in alone]
    return sorted([*groups, *single], key=lambda unit: min(gates.index(name) for name in unit))


class Scheduler:
    """Runs units of gates up to ``jobs`` at a time and collects what finished."""

    def __init__(
        self, units: list[tuple[str, ...]], runner: list[str], options: argparse.Namespace
    ) -> None:
        """Queue every unit; nothing runs until ``run``."""
        self.pending = [deque(unit) for unit in units]
        self.alone: deque[str] = deque(options.alone)
        self.runner = runner
        self.root: Path = options.root
        self.jobs: int = options.jobs
        self.fail_fast: bool = options.fail_fast
        self.report_to: Path | None = options.report
        self.tools = tools(self.root)
        self.live: dict[int, Live] = {}
        self.finished: list[Finished] = []
        self.stopped: list[str] = []
        self.halted = False

    def run(self) -> None:
        """Start, watch and settle gates until every unit is empty or a red halts the run."""
        try:
            while True:
                self.start_ready()
                self.start_alone()
                if not self.live:
                    # Why: a gate that could not start is settled at once and leaves no job
                    # running, while gates after it still wait.
                    if self.halted or not self.not_started():
                        return
                    continue
                time.sleep(POLL)
                self.settle_exited()
                if self.halted:
                    self.stop_running()
        except BaseException:
            self.stop_running()
            raise

    def not_started(self) -> list[str]:
        """The gates a halted run never began."""
        return [*(name for queue in self.pending for name in queue), *self.alone]

    def start_ready(self) -> None:
        """Start the next gate of every idle unit while a job is free."""
        for index, queue in enumerate(self.pending):
            if self.halted or len(self.live) >= self.jobs:
                return
            if queue and index not in self.live:
                self.begin(index, queue.popleft())

    def start_alone(self) -> None:
        """Start the next gate that is measured alone, once nothing else runs or waits."""
        if self.alone and not (self.halted or self.live or any(self.pending)):
            self.begin(ALONE, self.alone.popleft())

    def begin(self, index: int, name: str) -> None:
        """Launch one gate in the job ``index``; one that cannot start is settled at once."""
        with gatelib.signals_held():
            started = self.launch(name)
            if isinstance(started, Finished):
                self.settle(started)
            else:
                self.live[index] = started

    def launch(self, name: str) -> Live | Finished:
        """The gate started in a session of its own, or its failure to start."""
        command = [*self.runner, *shlex.split(name)]
        log = tempfile.TemporaryFile()  # noqa: SIM115 # -- held until the gate is settled
        say(f"parallel: {name}: started")
        try:
            process = subprocess.Popen(
                command,
                cwd=self.root,
                stdin=subprocess.DEVNULL,
                stdout=log,
                stderr=subprocess.STDOUT,
                start_new_session=True,
            )
        except OSError as error:
            log.close()
            return Finished(name, 0.0, 127, f"cannot run {command[0]}: {error}\n")
        return Live(name, process, log, time.monotonic(), shlex.split(name)[-1] in self.tools)

    def settle_exited(self) -> None:
        """Settle every running gate whose process has exited."""
        for index, gate in list(self.live.items()):
            code = gate.process.poll()
            if code is not None:
                del self.live[index]
                self.settle(collect(gate, code))

    def settle(self, result: Finished) -> None:
        """Print the gate whole, record it, and halt the run if it is red under ``--fail-fast``."""
        if result.output:
            say(f"==> {result.name} <==")
            say(result.output.rstrip("\n"))
        say(result.line())
        self.finished.append(result)
        if self.report_to is not None:
            outcome = budget.Outcome(result.name, result.seconds, None, result.exit_code)
            budget.report(self.report_to, outcome)
        if self.fail_fast and not result.green:
            self.halted = True

    def stop_running(self) -> None:
        """Stop every running gate's whole process group; they are neither red nor green."""
        gates = list(self.live.values())
        self.live.clear()
        for gate in gates:
            with contextlib.suppress(ProcessLookupError):
                os.killpg(gate.process.pid, signal.SIGTERM)
        for gate in gates:
            gatelib.stop_group(gate.process)
            gate.log.close()
            self.stopped.append(gate.name)


def collect(gate: Live, code: int) -> Finished:
    """The finished gate: its output read back, its signal death written as a shell would."""
    seconds = time.monotonic() - gate.started
    left = gatelib.end_group(gate.process.pid)
    gate.log.seek(0)
    output = gate.log.read().decode("utf-8", errors="replace")
    gate.log.close()
    if left:
        output += f"parallel: {gate.name}: {gatelib.LEFT_RUNNING}\n"
    return Finished(gate.name, seconds, 128 - code if code < 0 else code, output, gate.tool)


def closing(scheduler: Scheduler, wall: float) -> list[str]:
    """What is wrong with the run, after the slowest gate is named."""
    slowest = max(scheduler.finished, key=lambda result: result.seconds)
    work = sum(result.seconds for result in scheduler.finished)
    total = len(scheduler.finished) + len(scheduler.stopped) + len(scheduler.not_started())
    counts = Counter(result.standing for result in scheduler.finished)
    ran = f"{counts[Standing.GREEN]} {Standing.GREEN.value}"
    failed = f", {counts[Standing.RED]} {Standing.RED.value}" if counts[Standing.RED] else ""
    waits = [f"{counts[each]} {each.value}" for each in (Standing.LATER, Standing.NONE)]
    tally = "; ".join([ran + failed, *(part for part in waits if not part.startswith("0 "))])
    finished = len(scheduler.finished)
    checks = f"{total} checks" if finished == total else f"{finished} of {total} checks finished"
    say(
        f"parallel: {checks} in {wall:.1f}s ({work:.1f}s of check time): {tally}; "
        f"slowest: {slowest.name} {slowest.seconds:.1f}s"
    )
    problems = [
        f"{result.name}: red (exit {result.exit_code})"
        for result in scheduler.finished
        if not result.green
    ]
    if scheduler.stopped:
        problems.append(f"stopped by --fail-fast: {', '.join(scheduler.stopped)}")
    if scheduler.not_started():
        problems.append(f"not started, --fail-fast: {', '.join(scheduler.not_started())}")
    return problems


def verdict(scheduler: Scheduler, problems: list[str]) -> str:
    """The last line: red, green with what ran and what waits, or that no check ran."""
    if problems:
        return "parallel: red"
    counts = Counter(result.standing for result in scheduler.finished)
    waits = [
        f"{counts[each]} {words}"
        for each, words in ((Standing.LATER, "not run yet"), (Standing.NONE, "not applicable"))
        if counts[each]
    ]
    if not counts[Standing.GREEN]:
        # Why: a new repository whose every question waits is not failing, and not green either.
        return f"parallel: no check ran ({', '.join(waits)})"
    return f"parallel: green ({', '.join([f'{counts[Standing.GREEN]} ran', *waits])})"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="parallel.py", usage="%(prog)s [options] -- GATE...", description=__doc__
    )
    parser.add_argument("--runner", default="", help="command prefix that runs one entry point")
    parser.add_argument("--jobs", type=int, default=gatelib.default_jobs(), help="gates at a time")
    parser.add_argument("--serial", action="append", nargs="+", default=[], metavar="GATE")
    parser.add_argument("--alone", nargs="+", default=[], metavar="GATE", help="measured alone")
    parser.add_argument("--fail-fast", action="store_true", help="stop at the first red gate")
    parser.add_argument("--root", type=Path, default=Path(), help="directory the gates run in")
    parser.add_argument("--report", type=Path, help="append one budget.py record per gate")
    return parser


def main(argv: list[str]) -> int:
    parser = build_parser()
    rest = argv[1:]
    split = rest.index("--") if "--" in rest else len(rest)
    options = parser.parse_args(rest[:split])
    gates = rest[split + 1 :]
    options.root = options.root.resolve()
    if options.report is not None:
        options.report = options.root / options.report
    if options.jobs < 1:
        parser.error("--jobs is at least 1")
    options.alone = [name for name in gates if name in options.alone]
    try:
        runner = shlex.split(options.runner)
        units = plan(gates, options.serial, frozenset(options.alone))
    except ValueError as error:
        parser.error(str(error))
    scheduler = Scheduler(units, runner, options)
    started = time.monotonic()
    try:
        with gatelib.interrupts_raise():
            scheduler.run()
    except gatelib.Interrupted as stop:
        say(f"parallel: {stop}; stopped {', '.join(scheduler.stopped) or 'nothing'}")
        return 128 + stop.signum
    problems = closing(scheduler, time.monotonic() - started)
    for line in problems:
        say(line)
    say(verdict(scheduler, problems))
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
