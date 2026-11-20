from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from typing import TYPE_CHECKING, Final, Literal, NamedTuple, cast

import gatelib

if TYPE_CHECKING:
    from collections.abc import Sequence
    from pathlib import Path

State = Literal["green", "red", "running"]
GREEN: Final = "green"
RED: Final = "red"
RUNNING: Final = "running"
STATES = (GREEN, RED, RUNNING)
TIER2: Final = "tier2"
UNPROVEN: Final = "unproven"
FIELDS = ("commit", "state", "at", "name")
SHORT = 12
UNKNOWN = (
    "tier 2 on the default branch is not known: its deep runs could not be read, so the next"
    " judge reads them again"
)


@dataclass(frozen=True)
class DeepRun:
    commit: str
    state: State
    at: str
    name: str


@dataclass(frozen=True)
class Deep:
    runs: tuple[DeepRun, ...] | None
    start: str


@dataclass(frozen=True)
class Landed:
    commit: str
    subject: str


class Hold(NamedTuple):
    code: Literal["tier2", "unproven"]
    text: str


class Reading(NamedTuple):
    nearest: int
    decider: DeepRun | None
    ender: DeepRun | None


def chain(root: Path, tip: str) -> tuple[Landed, ...]:
    listed = gatelib.git(root, "log", "--first-parent", "--format=%H %s", tip, "--") or ""
    return tuple(Landed(*line.partition(" ")[::2]) for line in listed.splitlines())


def places(line: Sequence[Landed]) -> dict[str, int]:
    return {landed.commit: index for index, landed in enumerate(line)}


def deciders(line: Sequence[Landed], runs: Sequence[DeepRun]) -> dict[int, DeepRun]:
    where = places(line)
    finished = sorted(
        (run for run in runs if run.state != RUNNING and run.commit in where),
        key=lambda run: (run.at, run.name),
    )
    return {where[run.commit]: run for run in finished}


def reading(line: Sequence[Landed], runs: Sequence[DeepRun]) -> Reading:
    where, decided = places(line), deciders(line, runs)
    nearest = min(decided, default=len(line))
    nearer = sorted(
        (run for run in runs if run.state == RUNNING and where.get(run.commit, nearest) < nearest),
        key=lambda run: (run.at, run.name),
    )
    return Reading(nearest, decided.get(nearest), next(iter(nearer), None))


def speaks_for(line: Sequence[Landed], deep: Deep, run: DeepRun) -> tuple[Landed, ...]:
    where = places(line)
    if deep.runs is None or run.state == RUNNING or run.commit not in where:
        return ()
    start, decided = where[run.commit], deciders(line, deep.runs)
    end = min((i for i, d in decided.items() if i > start and d.state == GREEN), default=len(line))
    return tuple(line[start:end])


def named(run: DeepRun) -> str:
    return f"{run.name} on `{run.commit[:SHORT]}`"


def merges(landed: Sequence[Landed]) -> str:
    return "; ".join(f"`{merge.commit[:SHORT]}` {merge.subject}" for merge in landed)


def held(line: Sequence[Landed], deep: Deep, *, gated: bool, bound: int | None) -> Hold | None:
    if deep.runs is None:
        return Hold(TIER2, UNKNOWN)
    seen = reading(line, deep.runs)
    ends = "" if seen.ender is None else f"; {named(seen.ender)} ends it if it is green"
    if seen.decider is not None and seen.decider.state == RED:
        text = (
            f"tier 2 is red on the default branch: {named(seen.decider)} speaks for"
            f" {merges(speaks_for(line, deep, seen.decider))}, and the owner merges what fixes"
            f" it{ends}"
        )
        return Hold(TIER2, text)
    if not gated or bound is None or seen.nearest <= bound:
        return None
    then = deep.start if seen.ender is None else f"wait for {named(seen.ender)}"
    text = (
        f"no finished deep run covers the newest {seen.nearest} of the default branch's merges,"
        f" and `ceilings.deep.unproven` allows {bound}: {then}"
    )
    return Hold(UNPROVEN, text)


def written(deep: Deep) -> str:
    runs = None if deep.runs is None else [asdict(run) for run in deep.runs]
    return json.dumps({"runs": runs, "start": deep.start}, indent=2) + "\n"


def parsed(item: object) -> DeepRun | None:
    if not isinstance(item, dict) or not all(isinstance(item.get(key), str) for key in FIELDS):
        return None
    if item["state"] not in STATES:
        return None
    state = cast("State", item["state"])
    return DeepRun(item["commit"], state, item["at"], item["name"])


def answer(text: str | None) -> Deep:
    try:
        data: object = json.loads(text or "")
    except ValueError:
        data = None
    found = data if isinstance(data, dict) else {}
    start, listed = found.get("start"), found.get("runs")
    runs = [parsed(item) for item in listed] if isinstance(listed, list) else [None]
    said = start if isinstance(start, str) else ""
    kept = tuple(run for run in runs if run is not None)
    return Deep(kept if len(kept) == len(runs) and said == start else None, said)
