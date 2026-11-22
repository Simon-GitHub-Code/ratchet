#!/usr/bin/env python3
"""The jobs tier 1 runs as in CI, read from the justfile's `tier1_jobs`.

Each word of `tier1_jobs` is a job: a gate of `tier1_gates` alone is a job of its own,
`NAME=A,B` is the job NAME that runs the gates A and B, and `rest` runs every gate of the tier
that no other job names. A gate whose every row of the binding's Gates table says `none:` or
`later:` is green by its row and runs in no job; one line names each such gate.

`--output FILE` writes the CI shape job's outputs to FILE: the shard `count` and `list` from the
binding's `- Shards: N` line, the jobs `tier1`, and `tier1_long`, the jobs of one sharded gate.
It refuses a list that drops a gate, names one twice, names a gate the tier lacks, or names two
jobs alike. `--run NAME` runs the job NAME: a gate alone through the runner, a group through
`parallel.py`, which names every red gate, with the schedule the justfile's recipes state.
"""

from __future__ import annotations

import argparse
import json
import re
import shlex
import subprocess
import sys
import tomllib
from collections import Counter
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import TYPE_CHECKING

import application_types
import budget
import gatelib
import justfile_text
import markdown_text
import parallel
import project_bite
import schedule

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator

GATES, SHARDED, JOBS = "tier1_gates", "tier1_sharded", "tier1_jobs"
REST = "rest"
OWN_JOBS = "Own jobs"
SPEED = "## Speed"
THRESHOLDS = Path("quality/thresholds.toml")
WORKFLOW = Path(".github/workflows/ci.yml")
SHARDS = re.compile(r"^- Shards: *([1-9][0-9]*) *$", re.MULTILINE)
QUOTED_WORD = re.compile(r"""["']([\w-]+)["']""")
WAITS = (application_types.NONE, application_types.LATER)


class Kind(Enum):
    NAME = "name"
    UNKNOWN = "unknown"
    TWICE = "twice"
    DROPPED = "dropped"
    GROUPED = "grouped"
    DRIFT = "drift"


STRUCTURAL = frozenset({Kind.NAME, Kind.UNKNOWN, Kind.TWICE, Kind.DROPPED})


@dataclass(frozen=True)
class Job:
    name: str
    gates: tuple[str, ...]

    @property
    def alone(self) -> bool:
        return self.gates == (self.name,)


@dataclass(frozen=True)
class Lists:
    gates: tuple[str, ...]
    sharded: frozenset[str]
    words: tuple[str, ...]


@dataclass(frozen=True)
class Facts:
    ceilings: frozenset[str] = frozenset()
    shards: int = 1
    own: frozenset[str] = frozenset()
    set_up: frozenset[str] = frozenset()


@dataclass(frozen=True)
class Problem:
    kind: Kind
    gate: str
    text: str


@dataclass(frozen=True)
class Plan:
    jobs: tuple[Job, ...]
    idle: dict[str, str] = field(default_factory=dict)


def lists(justfile: str) -> Lists | None:
    if not all(justfile_text.defines_variable(justfile, name) for name in (GATES, SHARDED, JOBS)):
        return None
    return Lists(
        justfile_text.ordered(justfile, GATES),
        frozenset(justfile_text.ordered(justfile, SHARDED)),
        justfile_text.ordered(justfile, JOBS),
    )


def named(words: tuple[str, ...]) -> list[tuple[str, tuple[str, ...] | None]]:
    jobs: list[tuple[str, tuple[str, ...] | None]] = []
    for word in words:
        name, joined, gates = word.partition("=")
        if word == REST:
            jobs.append((REST, None))
        elif joined:
            jobs.append((name, tuple(gate for gate in gates.split(",") if gate)))
        else:
            jobs.append((word, (word,)))
    return jobs


def resolved(found: Lists) -> tuple[Job, ...]:
    jobs = named(found.words)
    taken = {gate for _, gates in jobs for gate in gates or ()}
    rest = tuple(gate for gate in found.gates if gate not in taken)
    return tuple(Job(name, rest if gates is None else gates) for name, gates in jobs)


def held_alone(gate: str, found: Lists, facts: Facts) -> str | None:
    if gate in facts.ceilings or (gate == budget.PROOF and budget.FULL_PROOF in facts.ceilings):
        return (
            "its own ceiling under `[ceilings.seconds]` is found by the name of the job that"
            " runs it, so a group does not hold it"
        )
    if gate in found.sharded and facts.shards > 1:
        return f"it runs as {facts.shards} shards, which would shard the whole group"
    if gate in facts.set_up:
        return "the workflow sets up a step for it alone by `matrix.gate`, which a group skips"
    return None


def has_reason(gate: str, found: Lists, facts: Facts) -> bool:
    return gate == budget.PROOF or gate in facts.own or held_alone(gate, found, facts) is not None


def problems(found: Lists, facts: Facts) -> list[Problem]:
    jobs = named(found.words)
    return [
        *misnamed(jobs, found),
        *misplaced(jobs, found),
        *ungrouped(found, facts),
    ]


def misnamed(jobs: list[tuple[str, tuple[str, ...] | None]], found: Lists) -> Iterator[Problem]:
    for name, count in Counter(name for name, _ in jobs).items():
        if count > 1:
            yield Problem(Kind.NAME, name, f"`{JOBS}` names the job `{name}` {count} times")
    for name, gates in jobs:
        if gates is not None and gates != (name,) and (name in found.gates or name == REST):
            what = "every gate no other job names" if name == REST else "a gate of the tier"
            yield Problem(
                Kind.NAME,
                name,
                f"the group `{name}` of `{JOBS}` has the name of {what}, whose ceiling would hold"
                " it; give the group a name of its own",
            )


def misplaced(jobs: list[tuple[str, tuple[str, ...] | None]], found: Lists) -> Iterator[Problem]:
    seen: dict[str, str] = {}
    for name, gate in ((name, gate) for name, gates in jobs for gate in gates or ()):
        if gate not in found.gates:
            yield Problem(
                Kind.UNKNOWN,
                gate,
                f"the job `{name}` of `{JOBS}` runs `{gate}`, which `{GATES}` does not list;"
                " name only the gates of the tier",
            )
        elif seen.setdefault(gate, name) != name:
            yield Problem(
                Kind.TWICE,
                gate,
                f"`{gate}` runs in the jobs `{seen[gate]}` and `{name}` of `{JOBS}`;"
                " name it in one",
            )
    for gate in () if REST in found.words else found.gates:
        if gate not in seen:
            yield Problem(
                Kind.DROPPED,
                gate,
                f"`{gate}` is in `{GATES}` and no job of `{JOBS}` runs it; add `{REST}`, the job of"
                " every gate no other job names",
            )


def ungrouped(found: Lists, facts: Facts) -> Iterator[Problem]:
    for job in resolved(found):
        for gate in () if job.alone else job.gates:
            why = held_alone(gate, found, facts)
            if why is not None:
                yield Problem(
                    Kind.GROUPED,
                    gate,
                    f"`{gate}` runs in the group `{job.name}`, but {why}; give it a job of its own"
                    f" in `{JOBS}`",
                )
        if job.alone and job.name in found.gates and not has_reason(job.name, found, facts):
            yield Problem(
                Kind.DRIFT,
                job.name,
                f"`{job.name}` runs in a job of its own with no reason the check can read: no"
                " ceiling of its own, no shard count over 1, no step the workflow sets up for it"
                f" alone, and no `- {OWN_JOBS}:` line of the binding's Speed section naming it;"
                f" move it into `{REST}`",
            )


def default(gates: tuple[str, ...], sharded: frozenset[str], facts: Facts) -> tuple[str, ...]:
    found = Lists(gates, sharded, ())
    return (*(gate for gate in gates if has_reason(gate, found, facts)), REST)


def given_own(gate: str) -> Callable[[list[str]], list[str]]:
    def change(words: list[str]) -> list[str]:
        kept: list[str] = []
        for word in words:
            name, joined, gates = word.partition("=")
            left = [one for one in gates.split(",") if one and one != gate]
            if not joined:
                kept.append(word)
            elif left:
                kept.append(f"{name}={','.join(left)}")
        if gate in kept:
            return kept
        at = kept.index(REST) if REST in kept else len(kept)
        return [*kept[:at], gate, *kept[at:]]

    return change


def into_rest(gate: str) -> Callable[[list[str]], list[str]]:
    def change(words: list[str]) -> list[str]:
        return with_rest([word for word in words if word != gate])

    return change


def with_rest(words: list[str]) -> list[str]:
    return words if REST in words else [*words, REST]


def waiting(binding: str) -> dict[str, str]:
    rows: dict[str, list[str]] = {}
    for row in project_bite.gate_rows(binding):
        if row.gate:
            rows.setdefault(row.gate, []).append(row.tool)
    return {
        gate: tools[0]
        for gate, tools in rows.items()
        if all(any(wait.match(tool) for wait in WAITS) for tool in tools)
    }


def planned(jobs: tuple[Job, ...], idle: dict[str, str]) -> Plan:
    kept = tuple(
        Job(job.name, gates)
        for job in jobs
        if (gates := tuple(gate for gate in job.gates if gate not in idle))
    )
    shown = {gate: idle[gate] for job in jobs for gate in job.gates if gate in idle}
    return Plan(kept, shown)


def idle_line(idle: dict[str, str]) -> str:
    each = ", ".join(f"`{gate}` ({tool.strip('`')})" for gate, tool in idle.items())
    return (
        "ci_jobs: no runner for the gates whose every row of the binding's Gates table says"
        f" none: or later:, so each is green by its row: {each}"
    )


def shard_count(binding: str) -> int | None:
    found = SHARDS.search(markdown_text.section(binding, SPEED))
    return int(found.group(1)) if found else None


def facts(root: Path) -> Facts:
    binding = gatelib.read_text(root / gatelib.BINDING) or ""
    try:
        table = tomllib.loads(gatelib.read_text(root / THRESHOLDS) or "").get("ceilings", {})
    except tomllib.TOMLDecodeError:
        table = {}
    seconds = table.get("seconds", {}) if isinstance(table, dict) else {}
    own = markdown_text.bullets(markdown_text.section(binding, SPEED)).get(OWN_JOBS, "")
    workflow = gatelib.read_text(root / WORKFLOW) or ""
    set_up = {
        word
        for line in workflow.splitlines()
        if "matrix.gate" in line and justfile_text.BINDS not in line
        for word in QUOTED_WORD.findall(line)
    }
    return Facts(
        frozenset(seconds) if isinstance(seconds, dict) else frozenset(),
        shard_count(binding) or 1,
        frozenset(() if own.startswith("<") else markdown_text.ticked(own)),
        frozenset(set_up),
    )


def say(text: str) -> None:
    print(text, flush=True)


def shape(root: Path, output: Path) -> int:
    found = lists(schedule.justfile(root))
    binding = gatelib.read_text(root / gatelib.BINDING) or ""
    count = shard_count(binding)
    if found is None:
        say(f"justfile: no `{GATES}`, `{SHARDED}` and `{JOBS}` to read the jobs from")
        return 1
    if count is None:
        say(f"{gatelib.BINDING}: no '- Shards: N' line")
        return 1
    broken = [one for one in problems(found, Facts(shards=count)) if one.kind in STRUCTURAL]
    for one in broken:
        say(f"justfile: {one.text}")
    if broken:
        return 1
    plan = planned(resolved(found), waiting(binding))
    if plan.idle:
        say(idle_line(plan.idle))
    long = [job.name for job in plan.jobs if job.alone and job.name in found.sharded]
    lines = {
        "count": str(count),
        "list": json.dumps(list(range(1, count + 1)), separators=(",", ":")),
        "tier1": json.dumps(
            [j.name for j in plan.jobs if j.name not in long], separators=(",", ":")
        ),
        "tier1_long": json.dumps(long, separators=(",", ":")),
    }
    with output.open("a", encoding="utf-8") as handle:
        handle.writelines(f"{key}={value}\n" for key, value in lines.items())
    return 0


def run(root: Path, name: str, options: argparse.Namespace) -> int:
    justfile = schedule.justfile(root)
    found = lists(justfile)
    job = next((job for job in resolved(found) if job.name == name), None) if found else None
    if job is None:
        say(f"justfile: `{JOBS}` names no job `{name}`")
        return 2
    plan = planned((job,), waiting(gatelib.read_text(root / gatelib.BINDING) or ""))
    if plan.idle:
        say(idle_line(plan.idle))
    if not plan.jobs:
        return 0
    gates = plan.jobs[0].gates
    if job.alone:
        command = [*shlex.split(options.runner), name]
        return subprocess.run(command, cwd=root, check=False).returncode
    return parallel.main(["parallel.py", *flags(justfile, gates, options), "--", *gates])


def flags(justfile: str, gates: tuple[str, ...], options: argparse.Namespace) -> list[str]:
    given = schedule.recipes(justfile)[0].among(gates)
    alone = [gate for gate in gates if gate in given.alone]
    groups: list[set[str]] = []
    for group in given.apart:
        joined = set(group) - set(alone)
        for other in [other for other in groups if other & joined]:
            joined |= other
            groups.remove(other)
        groups.append(joined)
    out = ["--runner", options.runner, "--root", str(options.root)]
    out += ["--jobs", str(options.jobs)] if options.jobs else []
    out += ["--report", options.report] if options.report else []
    for kept in groups:
        out += ["--serial", *(gate for gate in gates if gate in kept)] if len(kept) > 1 else []
    out += ["--alone", *alone] if alone else []
    wide = [gate for gate in gates if gate in given.wide]
    return out + (["--wide", *wide] if wide else [])


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="ci_jobs.py", description=__doc__)
    parser.add_argument("--root", type=Path, default=Path())
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument("--output", type=Path)
    action.add_argument("--run", metavar="NAME")
    parser.add_argument("--runner", default="just")
    parser.add_argument("--jobs", type=int)
    parser.add_argument("--report")
    options = parser.parse_args(argv[1:])
    options.root = options.root.resolve()
    if options.output is not None:
        return shape(options.root, options.output)
    return run(options.root, options.run, options)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
