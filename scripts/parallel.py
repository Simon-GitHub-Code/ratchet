#!/usr/bin/env python3
"""The parallel runner: run a tier's gates at once, keep each one's output whole, name every red.

A tier is a set of independent gates, so its local wall clock should be the slowest gate, not
the sum. CI runs a tier's jobs at once and each group job through this runner; this gives a
developer's machine the same shape. It
knows nothing about the stack: each gate is an entry point of the repository's command runner
(a ``just`` recipe, a ``make`` target, a package script), and the runner's command prefix is
yours to give. Python 3.11 standard library only, Linux and macOS.

Usage::

    parallel.py [--runner "just"] [--jobs N] [--serial A B]... [--alone GATE...]
                [--wide GATE...] [--after GATE:DEP]... [--first GATE...]
                [--fail-fast] [--partial] [--root DIR] [--report PATH] -- GATE...

* Each ``GATE`` becomes ``RUNNER GATE`` and runs in ``--root`` (the current directory). The
  entry is split like a shell line, so ``"test-shard 1/4"`` passes two words. With no
  ``--runner`` each entry is a whole command: ``-- "./lint ." "./types --strict"``.
* At most ``--jobs`` gates run at a time: ``RATCHET_JOBS`` by default, else git's
  ``ratchet.jobs``, else the CPU count. On a machine that is shared, give a smaller number.
* ``--wide GATE...`` names gates that are themselves parallel, such as a test suite across every
  core. The jobs are split evenly between the wide gates that run beside each other, at least one
  each, and each is told its share in ``RATCHET_JOBS``; one measured alone is told all of them.
  The gate hands that number to its tool's own jobs setting, so two suites on one machine never
  start a worker per core each. A gate not named keeps the environment it was given, and a name
  that is not among this run's gates is ignored, so one list serves every tier.
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
  Repeat the option for more groups. A gate is in at most one group. After every tier, one line
  explains what set the wall clock (whether the lanes were full, one gate or ``--serial`` group
  set it while other lanes stood idle, or the order lost seconds against the best schedule), and
  when the run is at least 1.3 times its usual median on an uncontended machine, a second line
  names the gates that grew most. It is never red.
* The ``- Apart:``, ``- Alone:`` and ``- Wide:`` lines of the ``## Speed`` section of the
  binding at ``--root`` add to the flags (ADR 0086): each code span of ``Apart`` is a group,
  names split by spaces, that never runs at once, like a ``--serial`` group; ``Alone`` adds to
  ``--alone`` and ``Wide`` to ``--wide``, kept to this run's gates. A run keeps everything the
  flags and the lines say, and no line or flag takes away what another says. Groups that share a
  gate, and a gate both alone and in a group, are kept as written. When the binding states any
  of these lines and a flag says something they do not, one line before the gates start names
  it and the line that would state it for good; it is never red.
* ``--report PATH`` appends one JSON line per finished gate, in the record ``budget.py``
  writes (``key``, ``seconds``, ``ceiling``, ``status``, ``exit``, and ``at``, ``run`` and
  ``tree`` from ``runrecord.py``), so one reader serves both. The ceiling is null: a parallel
  run's seconds include contention for the machine, so ceilings are judged by ``budget.py`` where
  one gate runs alone, as CI does. The slowest gate is named in the closing lines either way.
  When the run ends, one more line (``kind`` ``tier``) holds the wall time of the whole run and the
  gate names in the order given.
* The gates start longest first, so a long gate given last does not wait behind short ones: a
  gate is expected to take the median of its last five times in the last MiB of the
  ``--report`` file, and a ``--serial`` group the sum of its gates. Those times include the ones
  ``budget.py`` measured alone, since the record does not tell them apart. A line that does not
  give a gate a finite, positive number of seconds says nothing, so no record can stop a run or
  change its verdict. A gate with no time there starts before the rest, and equal times keep the
  order given, so with no record the gates start in the order given. With ``--fail-fast`` the
  gates start in the order given whatever the record says, so the first red comes as soon as it
  did without a record. ``--alone`` gates still run last, in the order given.
* After the closing lines, each path the binding's ``Local caches`` bullet of ``## Speed`` names
  (its code spans) that does not exist is said in one line, and with ``--report`` has a line of
  its own (``kind`` ``cache-missing``, ``path``) after the tier's, unless ``--partial`` is given
  for a subset run that does not run every gate owning a cache.
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
import json
import math
import os
import re
import shlex
import signal
import statistics
import subprocess
import sys
import tempfile
import time
from collections import Counter
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from types import MappingProxyType
from typing import IO, TYPE_CHECKING

import application_types
import budget
import build_cache
import gatelib
import loop_budget
import machine
import markdown_text
import pages_lane
import project_bite
import runrecord
import schedule

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping, Sequence
    from collections.abc import Set as AbstractSet

POLL = 0.05
RECENT = 5
MIN_HINT_GATES = 2
MIN_ORDER_LOSS = 0.25
ORDER_LOSS_SHARE = 0.05
IDLE_LANE_SHARE = 0.25
BOTTLENECK_WALL_SHARE = 0.5
MIN_BOTTLENECK_GAP = 0.2
BOTTLENECK_GAP_SHARE = 0.1
SLOWER_RATIO = 1.3
SLOWER_HISTORY = 10
SLOWER_TOP_GATES = 3
PROFILE_BULLET = "Profile"
WINDOW = 2**20
UNREADABLE = (ValueError, TypeError, KeyError, OverflowError, RecursionError)
NO_RECORD: Mapping[str, float] = MappingProxyType({})
NO_DEPS: Mapping[str, frozenset[str]] = MappingProxyType({})
WAITING = re.compile(r"(?P<standing>later|none): \S.*")


@dataclass(frozen=True, slots=True)
class Constraints:
    deps: Mapping[str, frozenset[str]] = field(default_factory=dict)
    first: frozenset[str] = frozenset()


class Standing(Enum):
    GREEN = "ran and passed"
    RED = "failed"
    UNJUDGED = "could not judge"
    LATER = "not run yet, each waiting for what its line names"
    NONE = "not applicable, as its line says"


def tools(root: Path) -> frozenset[str]:
    rows = project_bite.gate_rows(gatelib.read_text(root / gatelib.BINDING) or "")
    waits = (application_types.NONE, application_types.LATER)
    return frozenset(
        row.gate for row in rows if row.tool and not any(w.match(row.tool) for w in waits)
    )


def profile_commands(root: Path) -> dict[str, str]:
    body = markdown_text.section(gatelib.read_text(root / gatelib.BINDING) or "", schedule.SPEED)
    line = markdown_text.bullets(body).get(PROFILE_BULLET, "")
    if not line or line.startswith("<"):
        return {}
    commands: dict[str, str] = {}
    for span in markdown_text.ticked(line):
        gate, colon, command = span.partition(":")
        gate, command = gate.strip(), command.strip()
        if colon and gate and command:
            commands[gate] = command
    return commands


def profile_hint(scheduler: Scheduler) -> str | None:
    if len(scheduler.finished) < MIN_HINT_GATES:
        return None
    slowest = max(scheduler.finished, key=lambda result: result.seconds)
    median = statistics.median(result.seconds for result in scheduler.finished)
    if slowest.seconds <= median:
        return None
    gate = shlex.split(slowest.name)[-1]
    if gate in scheduler.profile_commands:
        command = scheduler.profile_commands[gate]
        return (
            f"parallel: {slowest.name} took {slowest.seconds:.1f}s (tier median {median:.1f}s);"
            f" profile it with `{command}` to see where its time goes"
        )
    if gate in scheduler.tools:
        return (
            f"parallel: {slowest.name} took {slowest.seconds:.1f}s (tier median {median:.1f}s);"
            f" run its tool with its profiling flag (and record `{gate}: <command>` under"
            " Speed -> Profile in binding.md) to see where its time goes"
        )
    return None


def standing_of(output: str, *, tool: bool) -> Standing:
    waiting = WAITING.fullmatch(output.strip())
    if waiting is None or (waiting["standing"] == "none" and tool):
        return Standing.GREEN
    return Standing[waiting["standing"].upper()]


@dataclass(frozen=True, slots=True)
class Finished:
    name: str
    seconds: float
    exit_code: int
    output: str
    tool: bool = False
    lane: int = 0
    start: float = 0.0
    cpu: float | None = None
    work: Mapping[str, float] = field(default_factory=dict)

    @property
    def green(self) -> bool:
        return self.exit_code == 0

    @property
    def unjudged(self) -> bool:
        return self.exit_code == gatelib.UNJUDGED

    @property
    def standing(self) -> Standing:
        if self.green:
            return standing_of(self.output, tool=self.tool)
        if self.unjudged:
            return Standing.UNJUDGED
        return Standing.RED

    def line(self) -> str:
        match self.standing:
            case Standing.GREEN:
                how = "green"
            case Standing.UNJUDGED:
                how = f"could not judge (exit {self.exit_code})"
            case Standing.RED:
                how = f"red (exit {self.exit_code})"
            case Standing.LATER:
                how = f"not run yet ({self.output.strip()})"
            case _:
                how = f"not applicable ({self.output.strip()})"
        return f"parallel: {self.name}: {how} in {self.seconds:.1f}s"


@dataclass(slots=True)
class Live:
    name: str
    process: subprocess.Popen[bytes]
    log: IO[bytes]
    started: float
    tool: bool
    lane: int = 0
    offset: float = 0.0
    cpu: float | None = None


def say(text: str) -> None:
    print(text, flush=True)


def plan(
    gates: list[str],
    serial: list[list[str]],
    alone: frozenset[str] = frozenset(),
    *,
    seconds: Mapping[str, float] = NO_RECORD,
) -> list[tuple[str, ...]]:
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

    def longest_first(unit: tuple[str, ...]) -> tuple[float, int]:
        known = all(name in seconds for name in unit)
        expected = sum(seconds[name] for name in unit) if known else math.inf
        return -expected, min(gates.index(name) for name in unit)

    return sorted([*groups, *single], key=longest_first)


def recent_lines(report: Path) -> list[bytes]:
    try:
        if not report.is_file():
            return []
        with report.open("rb") as handle:
            start = max(0, handle.seek(0, os.SEEK_END) - WINDOW - 1)
            handle.seek(start)
            lines = handle.read(WINDOW + 1).split(b"\n")
    except OSError:
        return []
    return lines[1:] if start else lines


def measured(line: bytes) -> tuple[str, float] | None:
    try:
        record = json.loads(line)
        key, value = record["key"], record["seconds"]
        number = isinstance(value, int | float) and not isinstance(value, bool)
        seconds = float(value) if number else math.nan
    except UNREADABLE:
        return None
    if isinstance(key, str) and 0 < seconds < math.inf:
        return key, seconds
    return None


def expected_seconds(report: Path) -> dict[str, float]:
    runs: dict[str, list[float]] = {}
    for found in filter(None, map(measured, recent_lines(report))):
        runs.setdefault(found[0], []).append(found[1])
    return {key: statistics.median(times[-RECENT:]) for key, times in runs.items()}


def shares(
    units: list[tuple[str, ...]], wide: list[str], *, alone: list[str], jobs: int
) -> dict[str, int]:
    beside = sum(1 for unit in units if set(wide).intersection(unit))
    each = max(1, jobs // beside) if beside else jobs
    return {name: jobs if name in alone else each for name in wide}


def matches_gate(name: str, gate: str) -> bool:
    if name == gate:
        return True
    try:
        words = shlex.split(name)
    except ValueError:
        return False
    return bool(words) and words[-1] == gate


def match_gate(target: str, gates: Sequence[str]) -> str | None:
    if target in gates:
        return target
    return next((name for name in gates if matches_gate(name, target)), None)


def parse_after(raw_after: Sequence[str], gates: AbstractSet[str]) -> dict[str, set[str]]:
    edges: dict[str, set[str]] = {}
    for item in raw_after:
        matched = next(
            (
                (item[:idx], item[idx + 1 :])
                for idx, ch in enumerate(item)
                if ch == ":" and item[:idx] in gates and item[idx + 1 :] in gates
            ),
            None,
        )
        if matched is not None:
            edges.setdefault(matched[0], set()).add(matched[1])
            continue
        gate, sep, dep = item.partition(":")
        if not sep or not gate.strip() or not dep.strip():
            message = f"--after {item!r}: expected GATE:DEP"
            raise ValueError(message)
    return edges


def build_spec_deps(
    spec: build_cache.BuildSpec | None, gates: Sequence[str]
) -> dict[str, set[str]]:
    if spec is None or not spec.consumers:
        return {}
    producer = match_gate(spec.gate, gates)
    if producer is None:
        return {}
    edges: dict[str, set[str]] = {}
    for raw_consumer in spec.consumers:
        consumer = match_gate(raw_consumer, gates)
        if consumer is not None and consumer != producer:
            edges.setdefault(consumer, set()).add(producer)
    return edges


def check_acyclic(gates: Sequence[str], edges: Mapping[str, AbstractSet[str]]) -> None:
    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(node: str) -> None:
        if node in visiting:
            message = f"cyclic dependency involving {node!r}"
            raise ValueError(message)
        if node in visited:
            return
        visiting.add(node)
        for dep in sorted(edges.get(node, ())):
            visit(dep)
        visiting.remove(node)
        visited.add(node)

    for gate in gates:
        if gate in edges:
            visit(gate)


def resolve_deps(
    gates: Sequence[str],
    raw_after: Sequence[str],
    spec: build_cache.BuildSpec | None,
    *,
    first: frozenset[str] = frozenset(),
) -> dict[str, frozenset[str]]:
    edges = build_spec_deps(spec, gates)
    for gate, deps in parse_after(raw_after, frozenset(gates)).items():
        edges.setdefault(gate, set()).update(deps)
    check_acyclic(gates, edges)
    resolved = {gate: frozenset(deps) for gate, deps in edges.items()}
    for gate in sorted(first):
        missing = resolved.get(gate, frozenset()) - first
        if missing:
            dep = min(missing)
            message = f"--first gate {gate!r} depends on {dep!r}, which is not in --first"
            raise ValueError(message)
    return resolved


def take_ready(board: schedule.Board[str], is_ready: Callable[[str], bool]) -> str | None:
    all_pending = list(board.pending)
    board.pending[:] = [name for name in all_pending if is_ready(name)]
    picked = board.take()
    if picked is not None:
        all_pending.remove(picked)
    board.pending[:] = all_pending
    return picked


class Scheduler:
    def __init__(
        self,
        units: list[tuple[str, ...]],
        runner: list[str],
        options: argparse.Namespace,
        *,
        kept: schedule.Schedule,
        group_log: Path | None = None,
    ) -> None:
        grouped = [unit for unit in units if len(unit) > 1]
        self.groups = [*grouped, *(group for group in kept.apart if group not in units)]
        self.measured_alone = kept.alone
        placed = [name for unit in units for name in unit]
        order = [*placed, *(name for name in options.alone if name not in placed)]
        self.board = schedule.Board(order, kept, str)
        self.runner = runner
        self.root: Path = options.root
        self.jobs: int = options.jobs
        self.shares = shares(units, options.wide, alone=options.alone, jobs=options.jobs)
        self.fail_fast: bool = options.fail_fast
        self.partial: bool = bool(getattr(options, "partial", False))
        self.report_to: Path | None = options.report
        self.tree = runrecord.tree_id(self.root) if options.report else None
        self.tools = tools(self.root)
        self.profile_commands = profile_commands(self.root)
        self.group_log = group_log
        raw_constraints = getattr(options, "constraints", None)
        constraints = raw_constraints if isinstance(raw_constraints, Constraints) else Constraints()
        self.deps: dict[str, frozenset[str]] = {
            gate: frozenset(reqs) for gate, reqs in constraints.deps.items() if reqs
        }
        self.first: frozenset[str] = constraints.first
        self.first_remaining: set[str] = set(self.first)
        self.first_failed = False
        self.green_done: set[str] = set()
        self.skipped_deps: list[tuple[str, str]] = []
        self.build_spec = build_cache.parse_build_spec(
            gatelib.read_text(self.root / gatelib.BINDING) or ""
        )
        self.prior_build_green = (
            has_prior_green(self.report_to, self.build_spec.gate, self.tree)
            if (self.report_to is not None and self.build_spec is not None)
            else False
        )
        self.live: dict[str, Live] = {}
        self.finished: list[Finished] = []
        self.stopped: list[str] = []
        self.halted = False
        self.tier_started = time.monotonic()
        self.monitor = machine.Monitor()

    def run(self) -> None:
        self.tier_started = time.monotonic()
        with self.monitor:
            try:
                while True:
                    self.start_ready()
                    if not self.live:
                        return
                    time.sleep(POLL)
                    self.settle_exited()
                    if self.halted:
                        self.stop_running()
            except BaseException:
                self.stop_running()
                raise

    def not_started(self) -> list[str]:
        return list(self.board.pending)

    def _is_ready(self, name: str) -> bool:
        if (self.first_remaining or self.first_failed) and name not in self.first:
            return False
        return self.deps.get(name, frozenset()) <= self.green_done

    def start_ready(self) -> None:
        while not self.halted and len(self.live) < self.jobs:
            name = (
                take_ready(self.board, self._is_ready)
                if (self.deps or self.first)
                else self.board.take()
            )
            if name is None:
                return
            self.begin(name)

    def begin(self, name: str) -> None:
        with gatelib.signals_held():
            started = self.launch(name)
            if isinstance(started, Finished):
                self.settle(started)
            else:
                self.live[name] = started

    def launch(self, name: str) -> Live | Finished:
        command = [*self.runner, *shlex.split(name)]
        log = tempfile.TemporaryFile()  # noqa: SIM115 # -- held until the gate is settled
        say(f"parallel: {name}: started")
        env = {key: value for key, value in os.environ.items() if key != budget.GROUP_ENV}
        if name in self.shares:
            env["RATCHET_JOBS"] = str(self.shares[name])
        used = {gate.lane for gate in self.live.values()}
        lane = next(i for i in range(self.jobs) if i not in used)
        started = time.monotonic()
        offset = max(0.0, started - self.tier_started)
        with self.monitor.spawning():
            try:
                process = subprocess.Popen(
                    command,
                    cwd=self.root,
                    env=env,
                    stdin=subprocess.DEVNULL,
                    stdout=log,
                    stderr=subprocess.STDOUT,
                    start_new_session=True,
                )
            except OSError as error:
                log.close()
                return Finished(
                    name,
                    0.0,
                    127,
                    f"cannot run {command[0]}: {error}\n",
                    lane=lane,
                    start=offset,
                    cpu=0.0,
                )
            self.monitor.register(process.pid)
        return Live(name, process, log, started, shlex.split(name)[-1] in self.tools, lane, offset)

    def settle_exited(self) -> None:
        for name, gate in list(self.live.items()):
            gate.cpu = machine.process_cpu(gate.process.pid)
            code = gate.process.poll()
            if code is not None:
                self.monitor.unregister(gate.process.pid)
                del self.live[name]
                self.settle(collect(gate, code))

    def _record_gate(self, result: Finished, *, alone: bool) -> None:
        if self.report_to is None:
            return
        extra: dict[str, object] = {
            "lane": result.lane,
            "start": round(result.start, 3),
            "cpu": None if result.cpu is None else round(result.cpu, 3),
            "alone": alone,
        }
        if result.work:
            extra["work"] = dict(result.work)
        outcome = budget.Outcome(result.name, result.seconds, None, result.exit_code, extra=extra)
        stamp = runrecord.stamp(self.root, self.tree)
        budget.report(
            self.report_to,
            outcome,
            stamp,
            root=self.root,
            group_log=self.group_log,
        )

    def _skip_dependents(self, failed_gate: str) -> None:
        if not self.deps:
            return
        failed = {failed_gate}
        while failed:
            next_failed: set[str] = set()
            for pending in list(self.board.pending):
                if self.deps.get(pending, frozenset()) & failed:
                    self.board.pending.remove(pending)
                    self.first_remaining.discard(pending)
                    self.skipped_deps.append((pending, failed_gate))
                    next_failed.add(pending)
            failed = next_failed

    def _advance_state(self, result: Finished) -> None:
        self.first_remaining.discard(result.name)
        if result.green:
            self.green_done.add(result.name)
            return
        if result.name in self.first:
            self.first_failed = True
        self._skip_dependents(result.name)

    def settle(self, result: Finished) -> None:
        alone = not self.board.done(result.name)
        if result.output:
            say(f"==> {result.name} <==")
            say(result.output.rstrip("\n"))
        say(result.line())
        self.finished.append(result)
        self._record_gate(result, alone=alone)
        self._advance_state(result)
        if self.fail_fast and not result.green:
            self.halted = True

    def stop_running(self) -> None:
        gates = list(self.live.values())
        self.live.clear()
        for gate in gates:
            if gate.process.pid > 0:
                with contextlib.suppress(ProcessLookupError):
                    os.killpg(gate.process.pid, signal.SIGTERM)
        for gate in gates:
            if gate.process.pid > 0:
                gatelib.stop_group(gate.process)
            self.monitor.unregister(gate.process.pid)
            gate.log.close()
            self.stopped.append(gate.name)


def collect(gate: Live, code: int) -> Finished:
    seconds = time.monotonic() - gate.started
    left = gate.process.pid > 0 and gatelib.end_group(gate.process.pid)
    gate.log.seek(0)
    output = gate.log.read().decode("utf-8", errors="replace")
    gate.log.close()
    if left:
        output += f"parallel: {gate.name}: {gatelib.LEFT_RUNNING}\n"
    return Finished(
        gate.name,
        seconds,
        128 - code if code < 0 else code,
        output,
        gate.tool,
        gate.lane,
        gate.offset,
        gate.cpu,
        budget.parse_work(output),
    )


def _simulate_board(
    names: AbstractSet[str],
    *,
    groups: Sequence[tuple[str, ...]],
    seconds: Mapping[str, float],
    jobs: int,
    constraints: Constraints | None = None,
) -> float:
    deps = constraints.deps if constraints is not None else NO_DEPS
    first = constraints.first if constraints is not None else frozenset()
    board = schedule.Board(
        sorted(names, key=lambda name: (-seconds[name], name)),
        schedule.Schedule({schedule.APART: tuple(groups)}),
        str,
    )
    running: list[tuple[float, str]] = []
    done: set[str] = set()
    first_left = set(first & names)
    now = 0.0
    while True:
        while len(running) < jobs:
            picked = (
                take_ready(
                    board,
                    lambda name: (
                        (not first_left or name in first) and deps.get(name, frozenset()) <= done
                    ),
                )
                if (deps or first_left)
                else board.take()
            )
            if picked is None:
                break
            running.append((now + seconds[picked], picked))
        if not running:
            return now
        now = min(end for end, _ in running)
        finished_now = [name for end, name in running if end <= now]
        running = [(end, name) for end, name in running if end > now]
        for name in finished_now:
            board.done(name)
            done.add(name)
            first_left.discard(name)


def best_schedule(
    finished: Sequence[Finished],
    *,
    groups: Sequence[tuple[str, ...]],
    alone: AbstractSet[str],
    jobs: int,
    constraints: Constraints | None = None,
) -> float:
    seconds = {result.name: result.seconds for result in finished}
    alone_wall = sum(result.seconds for result in finished if result.name in alone)
    normal_names = {result.name for result in finished if result.name not in alone}
    if not normal_names:
        return alone_wall
    active_groups = [
        members
        for group in groups
        if len(members := tuple(name for name in group if name in normal_names)) > 1
    ]
    deps = constraints.deps if constraints is not None else NO_DEPS
    first = constraints.first if constraints is not None else frozenset()
    active_deps = {
        name: req & normal_names
        for name, req in deps.items()
        if name in normal_names and req & normal_names
    }
    active_first = first & normal_names
    grouped_list = [name for group in active_groups for name in group]
    if active_deps or active_first or len(set(grouped_list)) != len(grouped_list):
        return alone_wall + _simulate_board(
            normal_names,
            groups=active_groups,
            seconds=seconds,
            jobs=jobs,
            constraints=Constraints(deps=active_deps, first=active_first),
        )
    grouped = set(grouped_list)
    units = [*active_groups, *((name,) for name in normal_names if name not in grouped)]
    durations = sorted((sum(seconds[name] for name in unit) for unit in units), reverse=True)
    lanes = [0.0] * jobs
    for duration in durations:
        lane = lanes.index(min(lanes))
        lanes[lane] += duration
    return alone_wall + max(lanes)


def serial_bottleneck(
    scheduler: Scheduler, seconds: Mapping[str, float]
) -> tuple[tuple[str, ...], float] | None:
    candidates = [
        (sum(seconds[name] for name in members), members)
        for group in scheduler.groups
        if len(
            members := tuple(
                name for name in group if name in seconds and name not in scheduler.measured_alone
            )
        )
        > 1
    ]
    if not candidates:
        return None
    total, members = max(candidates, key=lambda item: item[0])
    return members, total


def dominates(top: float, other: float) -> bool:
    return top - other >= max(MIN_BOTTLENECK_GAP, BOTTLENECK_GAP_SHARE * other)


def idle_bottleneck_line(scheduler: Scheduler, wall: float, idle: float) -> str | None:
    jobs = scheduler.jobs
    seconds = {result.name: result.seconds for result in scheduler.finished}
    slowest = max(scheduler.finished, key=lambda result: result.seconds)
    group = serial_bottleneck(scheduler, seconds)
    if group is not None:
        members, chain = group
        beside = max((r.seconds for r in scheduler.finished if r.name not in members), default=0.0)
        if (
            chain >= slowest.seconds
            and chain > BOTTLENECK_WALL_SHARE * wall
            and dominates(chain, beside)
        ):
            return (
                f"parallel: the --serial group {', '.join(members)} ({chain:.1f}s chained) set the"
                f" wall clock while other lanes stood idle ({idle:.1f}s idle across {jobs} lanes),"
                " time without it if they can run together"
            )
    second = (
        sorted((r.seconds for r in scheduler.finished), reverse=True)[1]
        if len(scheduler.finished) > 1
        else 0.0
    )
    if len(scheduler.finished) == 1 or (
        slowest.seconds > BOTTLENECK_WALL_SHARE * wall and dominates(slowest.seconds, second)
    ):
        alone_note = ", --alone" if slowest.name in scheduler.measured_alone else ""
        return (
            f"parallel: one gate {slowest.name} ({slowest.seconds:.1f}s{alone_note}) set the wall"
            f" clock while other lanes stood idle ({idle:.1f}s idle across {jobs} lanes)"
        )
    alone_total = sum(
        result.seconds for result in scheduler.finished if result.name in scheduler.measured_alone
    )
    if alone_total > BOTTLENECK_WALL_SHARE * wall and alone_total >= MIN_BOTTLENECK_GAP:
        return (
            f"parallel: --alone gates ({alone_total:.1f}s) set the wall clock while other lanes"
            f" stood idle ({idle:.1f}s idle across {jobs} lanes)"
        )
    return None


def wall_clock_line(scheduler: Scheduler, wall: float) -> str:
    work = sum(result.seconds for result in scheduler.finished)
    jobs = scheduler.jobs
    if jobs == 1:
        return f"parallel: the lanes were full (1 lane, {work:.1f}s of check time in {wall:.1f}s)"
    best = best_schedule(
        scheduler.finished,
        groups=scheduler.groups,
        alone=scheduler.measured_alone,
        jobs=jobs,
        constraints=Constraints(deps=scheduler.deps, first=scheduler.first),
    )
    lost = wall - best
    if lost >= max(MIN_ORDER_LOSS, ORDER_LOSS_SHARE * wall):
        return (
            f"parallel: the order lost {lost:.1f}s against the best schedule"
            f" ({best:.1f}s across {jobs} lanes)"
        )
    capacity = wall * jobs
    idle = max(0.0, capacity - work)
    if capacity > 0 and idle >= IDLE_LANE_SHARE * capacity:
        line = idle_bottleneck_line(scheduler, wall, idle)
        if line is not None:
            return line
    return f"parallel: the lanes were full ({jobs} lanes, {work:.1f}s of check time in {wall:.1f}s)"


def qualifying_tiers(
    records: Sequence[Mapping[str, object]],
    *,
    gate_set: AbstractSet[str],
    current_run: str,
) -> list[tuple[int, Mapping[str, object], float]]:
    matched: list[tuple[int, Mapping[str, object], float]] = []
    for idx, rec in enumerate(records):
        if rec.get("kind") != runrecord.TIER or rec.get("status") != "green":
            continue
        if rec.get("judgement") == "over":
            continue
        if current_run != runrecord.NO_RUN and rec.get("run") == current_run:
            continue
        rec_gates = rec.get("gates")
        if (
            not isinstance(rec_gates, list)
            or not all(isinstance(gate, str) for gate in rec_gates)
            or frozenset(rec_gates) != gate_set
        ):
            continue
        factor = loop_budget.finite_seconds(rec.get("m"))
        seconds = loop_budget.finite_seconds(rec.get("seconds"))
        if (
            rec.get("machine_read") is False
            or factor is None
            or factor >= machine.NOISE_FLOOR
            or seconds is None
            or seconds <= 0
        ):
            continue
        matched.append((idx, rec, seconds))
    return matched[-SLOWER_HISTORY:]


def prior_gate_medians(
    records: Sequence[Mapping[str, object]],
    *,
    qualifying: Sequence[tuple[int, Mapping[str, object], float]],
    gate_set: AbstractSet[str],
    current_run: str,
) -> dict[str, float]:
    qualifying_runs = {
        run_id
        for _, rec, _ in qualifying
        if isinstance(run_id := rec.get("run"), str) and run_id != runrecord.NO_RUN
    }
    has_no_run = any(
        not isinstance(rec.get("run"), str) or rec.get("run") == runrecord.NO_RUN
        for _, rec, _ in qualifying
    )
    cutoff = qualifying[-1][0]
    gate_times: dict[str, list[float]] = {}
    for idx, rec in enumerate(records):
        if "kind" in rec or rec.get("status") not in ("green", "unbounded"):
            continue
        key = rec.get("key")
        seconds = loop_budget.finite_seconds(rec.get("seconds"))
        if not isinstance(key, str) or key not in gate_set or seconds is None:
            continue
        run_id = rec.get("run")
        if current_run != runrecord.NO_RUN and run_id == current_run:
            continue
        in_real = isinstance(run_id, str) and run_id in qualifying_runs
        in_plain = (
            has_no_run
            and idx <= cutoff
            and (not isinstance(run_id, str) or run_id == runrecord.NO_RUN)
        )
        if in_real or in_plain:
            gate_times.setdefault(key, []).append(seconds)
    return {key: statistics.median(times[-SLOWER_HISTORY:]) for key, times in gate_times.items()}


def slower_than_usual(scheduler: Scheduler, wall: float, *, gates: list[str]) -> str | None:
    if (
        scheduler.halted
        or scheduler.stopped
        or scheduler.not_started()
        or scheduler.report_to is None
        or not scheduler.report_to.is_file()
    ):
        return None
    monitor = scheduler.monitor.result()
    if not monitor.machine_read or not math.isfinite(monitor.m) or monitor.m >= machine.NOISE_FLOOR:
        return None
    records = list(runrecord.read(scheduler.report_to))
    gate_set = frozenset(gates)
    current_run = os.environ.get(runrecord.RUN) or runrecord.NO_RUN
    qualifying = qualifying_tiers(records, gate_set=gate_set, current_run=current_run)
    if not qualifying:
        return None
    median_wall = statistics.median(seconds for _, _, seconds in qualifying)
    if wall < SLOWER_RATIO * median_wall:
        return None
    medians = prior_gate_medians(
        records, qualifying=qualifying, gate_set=gate_set, current_run=current_run
    )
    grew: list[tuple[float, str, float, float]] = []
    for result in scheduler.finished:
        median = medians.get(result.name)
        if median is not None and result.seconds - median > 0:
            grew.append((result.seconds - median, result.name, result.seconds, median))
    grew.sort(key=lambda item: item[0], reverse=True)
    top = grew[:SLOWER_TOP_GATES]
    if not top:
        return f"parallel: slower than usual: {wall:.1f}s vs {median_wall:.1f}s median"
    parts = ", ".join(f"{name} ({cur:.1f}s vs {med:.1f}s median)" for _diff, name, cur, med in top)
    return f"parallel: slower than usual: {wall:.1f}s vs {median_wall:.1f}s median; grew: {parts}"


def closing(scheduler: Scheduler, wall: float) -> list[str]:
    slowest = max(scheduler.finished, key=lambda result: result.seconds)
    work = sum(result.seconds for result in scheduler.finished)
    total = (
        len(scheduler.finished)
        + len(scheduler.stopped)
        + len(scheduler.not_started())
        + len(scheduler.skipped_deps)
    )
    counts = Counter(result.standing for result in scheduler.finished)
    ran = f"{counts[Standing.GREEN]} {Standing.GREEN.value}"
    failed = f", {counts[Standing.RED]} {Standing.RED.value}" if counts[Standing.RED] else ""
    unjudged = (
        f", {counts[Standing.UNJUDGED]} {Standing.UNJUDGED.value}"
        if counts[Standing.UNJUDGED]
        else ""
    )
    waits = [f"{counts[each]} {each.value}" for each in (Standing.LATER, Standing.NONE)]
    tally = "; ".join(
        [ran + failed + unjudged, *(part for part in waits if not part.startswith("0 "))]
    )
    finished = len(scheduler.finished)
    checks = f"{total} checks" if finished == total else f"{finished} of {total} checks finished"
    say(
        f"parallel: {checks} in {wall:.1f}s ({work:.1f}s of check time): {tally}; "
        f"slowest: {slowest.name} {slowest.seconds:.1f}s"
    )
    say(wall_clock_line(scheduler, wall))
    if hint := profile_hint(scheduler):
        say(hint)
    problems = [
        (
            f"{result.name}: {'could not judge' if result.unjudged else 'red'} "
            f"(exit {result.exit_code})"
        )
        for result in scheduler.finished
        if not result.green
    ]
    thresholds = gatelib.read_text(scheduler.root / gatelib.THRESHOLDS) or ""
    for result in scheduler.finished:
        for work_outcome in budget.work_outcomes(thresholds, result.name, result.work):
            if work_outcome.verdict:
                problems.append(work_outcome.line())
            else:
                say(work_outcome.line())
    if scheduler.stopped:
        problems.append(f"stopped by --fail-fast: {', '.join(scheduler.stopped)}")
    for consumer, dep in scheduler.skipped_deps:
        problems.append(f"{consumer}: skipped (dependency {dep} failed)")
    if not_started := scheduler.not_started():
        if scheduler.first_failed and not scheduler.halted:
            say(
                f"parallel: stopped before heavy gates after fast failure: {', '.join(not_started)}"
            )
        else:
            problems.append(f"not started, --fail-fast: {', '.join(not_started)}")
    return problems


def has_prior_green(report_to: Path, gate: str, tree: str | None) -> bool:
    if not tree or tree == runrecord.NO_TREE:
        return False
    for rec in runrecord.read(report_to):
        if "kind" in rec or rec.get("tree") != tree:
            continue
        if rec.get("status") not in ("green", "unbounded") or rec.get("exit", 0) != 0:
            continue
        key = rec.get("key")
        if isinstance(key, str) and matches_gate(key, gate):
            return True
    return False


def build_cache_exists(
    root: Path, reuse: build_cache.ReusePolicy, cache_paths: tuple[str, ...]
) -> bool:
    if reuse.kind is build_cache.ReuseKind.NONE:
        return False
    if reuse.kind is build_cache.ReuseKind.MOVED:
        return any((root / path).exists() for path in cache_paths)
    env_val = os.environ.get(reuse.env_name, "").strip()
    if not env_val:
        return False
    shared_dir = Path(env_val).expanduser()
    if not shared_dir.is_absolute():
        shared_dir = root / shared_dir
    return shared_dir.exists()


def cold_build_cause(scheduler: Scheduler, spec: build_cache.BuildSpec) -> str:
    if not build_cache_exists(scheduler.root, spec.reuse, spec.cache_paths):
        return "no cache"
    return "cache rejected" if scheduler.prior_build_green else "fresh tree"


def cold_build_info(
    scheduler: Scheduler,
) -> tuple[build_cache.BuildSpec, Finished, str] | None:
    spec = scheduler.build_spec
    if spec is None or spec.cold_seconds <= spec.warm_seconds:
        return None
    result = next((r for r in scheduler.finished if r.name == spec.gate), None)
    if result is None:
        result = next((r for r in scheduler.finished if matches_gate(r.name, spec.gate)), None)
    if result is None or result.seconds < (spec.warm_seconds + spec.cold_seconds) / 2.0:
        return None
    return spec, result, cold_build_cause(scheduler, spec)


def append_cold_build(
    report_to: Path,
    cold: tuple[build_cache.BuildSpec, Finished, str],
    stamp: Mapping[str, str],
    *,
    root: Path,
) -> None:
    spec, result, cause = cold
    entry: dict[str, object] = {
        "kind": runrecord.COLD_BUILD,
        "gate": spec.gate,
        "seconds": round(result.seconds, 3),
        "warm": spec.warm_seconds,
        "cold": spec.cold_seconds,
        "cause": cause,
        **stamp,
    }
    runrecord.append(report_to, entry, root=root)


def record(
    scheduler: Scheduler,
    wall: float,
    *,
    exit_code: int,
    gates: list[str],
    extra: Mapping[str, object] | None = None,
) -> None:
    missing = [] if scheduler.partial else runrecord.missing_cache(scheduler.root)
    for path in missing:
        say(
            f"parallel: the binding's Local caches bullet names {path}, which does not exist"
            " after this run, so the tool that owns it ran without its cache"
        )
    cold = cold_build_info(scheduler)
    if cold is not None:
        spec, result, cause = cold
        say(
            f"parallel: cold build: {spec.gate} took {result.seconds:.1f}s"
            f" (recorded warm {spec.warm_seconds:g}s, cold {spec.cold_seconds:g}s): {cause}"
        )
    if scheduler.report_to is None:
        return
    stamp = runrecord.stamp(scheduler.root, scheduler.tree)
    whole: dict[str, object] = {
        "kind": runrecord.TIER,
        "seconds": round(wall, 3),
        "status": (
            "not judged" if exit_code == gatelib.UNJUDGED else ("red" if exit_code else "green")
        ),
        "exit": exit_code,
        "gates": gates,
        **scheduler.monitor.result().fields(),
        **(extra or {}),
        **stamp,
    }
    runrecord.append(scheduler.report_to, whole, root=scheduler.root)
    for path in missing:
        found: dict[str, object] = {"kind": runrecord.CACHE_MISSING, "path": path, **stamp}
        runrecord.append(scheduler.report_to, found, root=scheduler.root)
    if cold is not None:
        append_cold_build(scheduler.report_to, cold, stamp, root=scheduler.root)


def verdict(scheduler: Scheduler, problems: list[str], *, exit_code: int = 1) -> str:
    if problems:
        return "parallel: could not judge" if exit_code == gatelib.UNJUDGED else "parallel: red"
    counts = Counter(result.standing for result in scheduler.finished)
    waits = [
        f"{counts[each]} {words}"
        for each, words in ((Standing.LATER, "not run yet"), (Standing.NONE, "not applicable"))
        if counts[each]
    ]
    if not counts[Standing.GREEN]:
        return f"parallel: no check ran ({', '.join(waits)})"
    return f"parallel: green ({', '.join([f'{counts[Standing.GREEN]} ran', *waits])})"


def unstated(stated: schedule.Schedule, given: schedule.Schedule) -> list[str]:
    if not stated.groups:
        return []
    return [
        f"parallel: the recipe passes {schedule.FLAGS[kind]} {schedule.spans(groups)} to this run"
        " and the binding's Speed section does not say it; this run keeps both, and the line"
        f" - {kind}: {schedule.spans((*stated.groups.get(kind, ()), *groups))} there would state"
        " it for good"
        for kind, groups in stated.unstated(given).groups.items()
        if groups
    ]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="parallel.py", usage="%(prog)s [options] -- GATE...", description=__doc__
    )
    parser.add_argument("--runner", default="", help="command prefix that runs one entry point")
    parser.add_argument("--jobs", type=int, default=gatelib.default_jobs(), help="gates at a time")
    parser.add_argument("--serial", action="append", nargs="+", default=[], metavar="GATE")
    parser.add_argument("--alone", nargs="+", default=[], metavar="GATE", help="measured alone")
    parser.add_argument("--wide", nargs="+", default=[], metavar="GATE", help="split the jobs")
    parser.add_argument(
        "--after",
        action="append",
        default=[],
        metavar="GATE:DEP",
        help="run GATE only after DEP passes",
    )
    parser.add_argument(
        "--first",
        nargs="+",
        default=[],
        metavar="GATE",
        help="run these gates first and stop before the rest on failure",
    )
    parser.add_argument("--fail-fast", action="store_true", help="stop at the first red gate")
    parser.add_argument(
        "--partial",
        action="store_true",
        help="subset run; skip checking Local caches paths owned by other jobs",
    )
    parser.add_argument(
        "--full",
        action="store_true",
        help="run every gate even on a pages-only change",
    )
    parser.add_argument("--root", type=Path, default=Path(), help="directory the gates run in")
    parser.add_argument("--report", type=Path, help="append one budget.py record per gate")
    parser.add_argument("--budget", choices=("loop",), help="judge the run against a budget")
    return parser


def judge_loop(
    scheduler: Scheduler, options: argparse.Namespace, wall: float, *, wall_clock: str = ""
) -> loop_budget.Result | None:
    if options.budget != "loop" or scheduler.stopped or scheduler.not_started():
        return None
    monitor = scheduler.monitor.result()
    reading = loop_budget.Reading(wall, monitor.m, monitor.machine_read)
    gates = [
        loop_budget.GateTime(result.name, result.seconds, result.green)
        for result in scheduler.finished
    ]
    return loop_budget.evaluate_run(
        scheduler.root,
        reading=reading,
        report=scheduler.report_to,
        gates=gates,
        options=loop_budget.EvalOptions(tree=scheduler.tree, wall_clock=wall_clock),
    )


def gates_exit_of(scheduler: Scheduler, problems: list[str]) -> int:
    if not problems:
        return 0
    unjudged_count = sum(1 for result in scheduler.finished if result.unjudged)
    only_unjudged = unjudged_count > 0 and len(problems) == unjudged_count + len(
        scheduler.skipped_deps
    )
    return gatelib.UNJUDGED if only_unjudged else 1


def finish_run(
    scheduler: Scheduler,
    options: argparse.Namespace,
    wall: float,
    *,
    gates: list[str],
) -> int:
    wall_clock = wall_clock_line(scheduler, wall)
    problems = closing(scheduler, wall)
    if slower := slower_than_usual(scheduler, wall, gates=gates):
        say(slower)
    for line in problems:
        say(line)
    gates_exit = gates_exit_of(scheduler, problems)
    judged = judge_loop(scheduler, options, wall, wall_clock=wall_clock)
    exit_code = judged.exit_for(gates_exit) if judged is not None else gates_exit
    record(
        scheduler,
        wall,
        exit_code=exit_code,
        gates=gates,
        extra=None if judged is None else judged.tier_fields(),
    )
    say(verdict(scheduler, problems, exit_code=exit_code))
    if judged is not None:
        say(judged.line())
    return exit_code


def apply_pages_lane(options: argparse.Namespace, gates: list[str]) -> tuple[list[str], bool]:
    if options.full:
        return gates, False
    lane = pages_lane.classify(options.root, gates)
    kept, skipped = pages_lane.filter_gates(gates, lane)
    if not skipped:
        return gates, False
    say(pages_lane.summary(lane, skipped))
    options.partial = True
    kept_set = frozenset(kept)
    options.serial = [
        filtered
        for group in options.serial
        if len(filtered := [name for name in group if name in kept_set]) > 1
    ]
    options.after = [
        rule for rule in options.after if all(part in kept_set for part in rule.partition(":")[::2])
    ]
    return kept, not kept


def main(argv: list[str], *, group_log: Path | None = None) -> int:
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
    gates, all_skipped = apply_pages_lane(options, gates)
    if all_skipped:
        return 0
    options.alone = [name for name in gates if name in options.alone]
    given = schedule.Schedule(
        {
            schedule.APART: tuple(map(tuple, options.serial)),
            schedule.ALONE: tuple((name,) for name in options.alone),
            schedule.WIDE: tuple((name,) for name in gates if name in options.wide),
        }
    )
    binding_text = gatelib.read_text(options.root / gatelib.BINDING) or ""
    stated = schedule.stated(binding_text)
    kept = given.joined(stated.among(gates))
    spec = build_cache.parse_build_spec(binding_text)
    timings: Path | None = None if options.fail_fast else options.report
    seconds = NO_RECORD if timings is None else expected_seconds(timings)
    try:
        runner = shlex.split(options.runner)
        units = plan(gates, options.serial, frozenset(options.alone), seconds=seconds)
        first = frozenset(name for name in gates if name in options.first)
        deps = resolve_deps(gates, options.after, spec, first=first)
    except ValueError as error:
        parser.error(str(error))
    options.alone = [name for name in gates if name in kept.alone]
    options.wide = [name for name in gates if name in kept.wide]
    for line in unstated(stated, given):
        say(line)
    options.constraints = Constraints(deps=deps, first=first)
    scheduler = Scheduler(units, runner, options, kept=kept, group_log=group_log)
    started = time.monotonic()
    try:
        with gatelib.interrupts_raise():
            scheduler.run()
    except gatelib.Interrupted as stop:
        say(f"parallel: {stop}; stopped {', '.join(scheduler.stopped) or 'nothing'}")
        return 128 + stop.signum
    wall = time.monotonic() - started
    return finish_run(scheduler, options, wall, gates=gates)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
