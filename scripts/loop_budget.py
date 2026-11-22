from __future__ import annotations

import math
import os
import statistics
import tomllib
from dataclasses import dataclass
from enum import Enum
from types import MappingProxyType
from typing import TYPE_CHECKING

import gatelib
import machine
import plugin_pin
import report as report_lib
import runrecord

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence
    from pathlib import Path

RECENT = 10
MIN_GROWTH_RATIO = 1.1
MIN_GROWTH_SECONDS = 0.1
DEEP_HOLD_FLAG = "--deep"
FORCED_CAUSE = "plugin-pin"
PROOF_GATE = "bite"


class Kind(Enum):
    LOOP = "loop"
    SECONDS = "seconds"
    WORK = "work"


class Place(Enum):
    LOCAL = "local"
    PULL_REQUEST = "pull-request"
    DEFAULT_BRANCH = "default-branch"
    DEEP = "deep"


class Reason(Enum):
    MACHINE = "the machine explains the time"
    NO_BUDGET = "no budget is declared"
    UNREAD = "the run's times could not be read"


@dataclass(frozen=True, slots=True)
class Within:
    pass


@dataclass(frozen=True, slots=True)
class Over:
    pass


@dataclass(frozen=True, slots=True)
class NotJudged:
    reason: Reason


Judgement = Within | Over | NotJudged
WITHIN = Within()
OVER = Over()
ALL_JUDGEMENTS: tuple[Judgement, ...] = (
    WITHIN,
    OVER,
    NotJudged(Reason.MACHINE),
    NotJudged(Reason.NO_BUDGET),
    NotJudged(Reason.UNREAD),
)


@dataclass(frozen=True, slots=True)
class Forced:
    cause: str
    record: str
    seconds: float


@dataclass(frozen=True, slots=True)
class Reading:
    seconds: float | None
    m: float
    machine_read: bool
    forced: Forced | None = None


@dataclass(frozen=True, slots=True)
class GateTime:
    name: str
    seconds: float
    green: bool = True


@dataclass(frozen=True, slots=True)
class Effect:
    exit_code: int | None
    words: str
    annotation: bool = False

    def exit_for(self, gates_exit: int) -> int:
        return gates_exit if self.exit_code is None else self.exit_code


def _build_effects() -> Mapping[tuple[Kind, Place, Judgement], Effect]:
    effects: dict[tuple[Kind, Place, Judgement], Effect] = {
        (Kind.LOOP, Place.LOCAL, WITHIN): Effect(None, "gates' exit"),
        (Kind.LOOP, Place.LOCAL, OVER): Effect(1, "1"),
        (Kind.LOOP, Place.LOCAL, NotJudged(Reason.MACHINE)): Effect(None, "gates' exit"),
        (Kind.LOOP, Place.LOCAL, NotJudged(Reason.NO_BUDGET)): Effect(None, "gates' exit"),
        (Kind.LOOP, Place.LOCAL, NotJudged(Reason.UNREAD)): Effect(
            None, "does not arise: check times itself"
        ),
        (Kind.LOOP, Place.PULL_REQUEST, WITHIN): Effect(0, "0"),
        (Kind.LOOP, Place.PULL_REQUEST, OVER): Effect(1, "1, red on the pull request"),
        (Kind.LOOP, Place.DEFAULT_BRANCH, WITHIN): Effect(0, "0"),
        (Kind.LOOP, Place.DEFAULT_BRANCH, OVER): Effect(
            0, "0 with a warning, reported", annotation=True
        ),
        (Kind.SECONDS, Place.DEEP, WITHIN): Effect(None, "gate's exit"),
        (Kind.SECONDS, Place.DEEP, OVER): Effect(1, "1, a red tier 2 that holds the next merge"),
        (Kind.SECONDS, Place.DEEP, NotJudged(Reason.MACHINE)): Effect(
            None, "gate's exit with a warning", annotation=True
        ),
        (Kind.SECONDS, Place.DEEP, NotJudged(Reason.NO_BUDGET)): Effect(
            None, "gate's exit; no ceiling is recorded"
        ),
        (Kind.SECONDS, Place.DEEP, NotJudged(Reason.UNREAD)): Effect(
            None, "does not arise: budget.py times its own child"
        ),
    }
    for place in (Place.PULL_REQUEST, Place.DEFAULT_BRANCH):
        for reason in Reason:
            effects[(Kind.LOOP, place, NotJudged(reason))] = Effect(
                0, "0 with a warning", annotation=True
            )
    for judgement in ALL_JUDGEMENTS:
        effects[(Kind.LOOP, Place.DEEP, judgement)] = Effect(None, "not judged")
    for place in (Place.LOCAL, Place.PULL_REQUEST, Place.DEFAULT_BRANCH):
        for judgement in ALL_JUDGEMENTS:
            effects[(Kind.SECONDS, place, judgement)] = Effect(
                None, "reported, never red", annotation=judgement == OVER
            )
    for place in Place:
        effects[(Kind.WORK, place, WITHIN)] = Effect(None, "gates' exit")
        effects[(Kind.WORK, place, OVER)] = Effect(1, "1, red everywhere")
    return MappingProxyType(effects)


EFFECT: Mapping[tuple[Kind, Place, Judgement], Effect] = _build_effects()


@dataclass(frozen=True, slots=True)
class Result:
    budget: float | None
    reading: Reading
    judgement: Judgement
    effect: Effect
    streak: int
    message: str

    def exit_for(self, gates_exit: int) -> int:
        return self.effect.exit_for(gates_exit)

    def line(self) -> str:
        return self.message

    def tier_fields(self) -> dict[str, object]:
        fields: dict[str, object] = {
            "budget": self.budget,
            "judgement": label_of(self.judgement),
        }
        if isinstance(self.judgement, NotJudged):
            fields["reason"] = self.judgement.reason.value
        if self.reading.forced is not None:
            fields["forced"] = {
                "cause": self.reading.forced.cause,
                "record": self.reading.forced.record,
                "seconds": round(self.reading.forced.seconds, 3),
            }
        return fields


def label_of(judgement: Judgement) -> str:
    if isinstance(judgement, Within):
        return "within"
    if isinstance(judgement, Over):
        return "over"
    return "not judged"


def finite_seconds(value: object) -> float | None:
    if isinstance(value, (int, float)) and not isinstance(value, bool) and 0 <= value < math.inf:
        return float(value)
    return None


def effective_m(reading: Reading) -> float:
    if not reading.machine_read or not math.isfinite(reading.m) or reading.m < machine.NOISE_FLOOR:
        return 1.0
    return reading.m


def judged_seconds(reading: Reading) -> float | None:
    if reading.seconds is None or not math.isfinite(reading.seconds) or reading.seconds < 0:
        return None
    forced = reading.forced.seconds if reading.forced is not None else 0.0
    return max(0.0, reading.seconds - forced)


def explained_seconds(reading: Reading) -> float:
    net = judged_seconds(reading)
    if net is None:
        return 0.0
    return net * (1.0 - 1.0 / effective_m(reading))


def judge(budget: float | None, reading: Reading) -> Judgement:
    if budget is None:
        return NotJudged(Reason.NO_BUDGET)
    net = judged_seconds(reading)
    if net is None:
        return NotJudged(Reason.UNREAD)
    if net <= budget:
        return WITHIN
    if net - explained_seconds(reading) > budget:
        return OVER
    return NotJudged(Reason.MACHINE)


def budget_of(root: Path) -> float | None:
    try:
        data = gatelib.thresholds(root)
    except tomllib.TOMLDecodeError:
        return None
    loop = gatelib.table(data, "ceilings", "loop")
    value = finite_seconds(loop.get("seconds"))
    return value if value is not None and value > 0 else None


def pin_changed(root: Path) -> bool:
    base = gatelib.base_commit(root)
    if base is None:
        return False
    binding = str(gatelib.BINDING)
    old = gatelib.show(root, base, binding)
    new = gatelib.read_text(root / binding)
    if old is None or new is None:
        return False
    return plugin_pin.PIN_LINE.findall(old) != plugin_pin.PIN_LINE.findall(new)


def forced_tag(raw: object) -> tuple[str, str] | None:
    if not isinstance(raw, dict):
        return None
    cause, record = raw.get("cause"), raw.get("record")
    if isinstance(cause, str) and cause and isinstance(record, str) and record:
        return cause, record
    return None


def read_forced(root: Path, report: Path | None, *, run: str, tree: str) -> Forced | None:
    if (
        report is None
        or not run
        or run == runrecord.NO_RUN
        or not tree
        or tree == runrecord.NO_TREE
    ):
        return None
    cause_info: tuple[str, str] | None = None
    bite_seconds: float | None = None
    forced_seconds = 0.0
    for rec in runrecord.read(report):
        if rec.get("run") != run or rec.get("tree") != tree:
            continue
        seconds = finite_seconds(rec.get("seconds"))
        if "kind" not in rec and rec.get("key") == PROOF_GATE and seconds is not None:
            bite_seconds = seconds
        if (tag := forced_tag(rec.get("forced"))) is not None and seconds is not None:
            cause_info = tag
            forced_seconds += seconds
    if cause_info is None or cause_info[0] != FORCED_CAUSE or not pin_changed(root):
        return None
    total = bite_seconds if bite_seconds is not None else forced_seconds
    return Forced(cause_info[0], cause_info[1], total)


def has_deep_hold(root: Path) -> bool:
    workflows = root / ".github/workflows"
    merge_yml = gatelib.read_text(workflows / "merge.yml")
    if merge_yml is not None:
        return (workflows / "deep.yml").is_file() and DEEP_HOLD_FLAG in merge_yml
    return (
        gatelib.git(root, "remote", "get-url", "origin") is None
        and gatelib.entry_point(root, "merge") is not None
    )


def not_judged_streak(records: Sequence[Mapping[str, object]], reason: Reason) -> int:
    streak = 0
    for rec in reversed(records):
        if rec.get("kind") != runrecord.TIER or "judgement" not in rec:
            continue
        if rec.get("judgement") == "not judged" and rec.get("reason") == reason.value:
            streak += 1
            continue
        break
    return streak


def trailing_not_judged(records: Sequence[Mapping[str, object]]) -> tuple[Reason, int] | None:
    for rec in reversed(records):
        if rec.get("kind") != runrecord.TIER or "judgement" not in rec:
            continue
        if rec.get("judgement") != "not judged":
            return None
        try:
            reason = Reason(rec.get("reason"))
        except ValueError:
            return None
        return reason, not_judged_streak(records, reason)
    return None


def classify_runs(
    records: Sequence[Mapping[str, object]], *, current_run: str
) -> tuple[set[str], set[str], set[str], bool]:
    within_runs: set[str] = set()
    over_runs: set[str] = set()
    excluded_runs: set[str] = set()
    prior_history = False
    for rec in records:
        if rec.get("kind") != runrecord.TIER:
            continue
        run_id = rec.get("run")
        if run_id == current_run and current_run != runrecord.NO_RUN:
            continue
        prior_history = True
        if not isinstance(run_id, str) or run_id == runrecord.NO_RUN:
            continue
        judgement = rec.get("judgement")
        if judgement == "within" or ("judgement" not in rec and rec.get("status") == "green"):
            within_runs.add(run_id)
        elif judgement == "over":
            over_runs.add(run_id)
        else:
            excluded_runs.add(run_id)
    return within_runs, over_runs, excluded_runs, prior_history


def gate_medians(
    records: Sequence[Mapping[str, object]], *, current_run: str
) -> tuple[dict[str, float], bool]:
    within_runs, over_runs, excluded_runs, prior_history = classify_runs(
        records, current_run=current_run
    )
    within_times: dict[str, list[float]] = {}
    fallback_times: dict[str, list[float]] = {}
    for rec in records:
        if "kind" in rec or rec.get("status") != "green":
            continue
        key, seconds, run_id = rec.get("key"), finite_seconds(rec.get("seconds")), rec.get("run")
        if not isinstance(key, str) or seconds is None:
            continue
        if run_id == current_run and current_run != runrecord.NO_RUN:
            continue
        if isinstance(run_id, str) and run_id in excluded_runs:
            continue
        if (not isinstance(run_id, str) or run_id in within_runs or not within_runs) and (
            not isinstance(run_id, str) or run_id not in over_runs
        ):
            within_times.setdefault(key, []).append(seconds)
            continue
        fallback_times.setdefault(key, []).append(seconds)
    source = within_times or fallback_times
    return {key: statistics.median(times[-RECENT:]) for key, times in source.items()}, (
        prior_history or bool(source)
    )


def check_run_times(
    records: Sequence[Mapping[str, object]], *, tier1_gates: frozenset[str] = frozenset()
) -> list[float]:
    judged = [
        seconds
        for rec in records
        if rec.get("kind") == runrecord.TIER
        and "judgement" in rec
        and (seconds := finite_seconds(rec.get("seconds"))) is not None
        and seconds > 0
    ]
    if judged:
        return judged[-RECENT:]
    plain: list[float] = []
    for rec in records:
        if rec.get("kind") != runrecord.TIER:
            continue
        seconds = finite_seconds(rec.get("seconds"))
        gates = rec.get("gates")
        if seconds is None or seconds <= 0:
            continue
        if tier1_gates and isinstance(gates, list) and not tier1_gates.intersection(gates):
            continue
        plain.append(seconds)
    return plain[-RECENT:]


def forced_note(reading: Reading) -> str:
    if reading.forced is None or reading.seconds is None:
        return ""
    net = judged_seconds(reading) or 0.0
    return (
        f" ({net:.1f}s without {reading.forced.seconds:.1f}s forced by"
        f" {reading.forced.cause}, {reading.forced.record})"
    )


def growth_summary(
    gates: Sequence[GateTime],
    medians: Mapping[str, float],
    *,
    prior_history: bool,
    exclude: str | None,
) -> str:
    candidates = [gate for gate in gates if gate.name != exclude]
    grew: list[tuple[float, GateTime, float | None]] = []
    for gate in candidates:
        median = medians.get(gate.name)
        if median is None:
            if prior_history and gate.seconds >= MIN_GROWTH_SECONDS:
                grew.append((gate.seconds, gate, None))
            continue
        if gate.seconds > median * MIN_GROWTH_RATIO and gate.seconds - median >= MIN_GROWTH_SECONDS:
            grew.append((gate.seconds - median, gate, median))
    if grew:
        grew.sort(key=lambda item: item[0], reverse=True)
        parts = [
            f"{gate.name} ({gate.seconds:.1f}s vs {median:.1f}s median)"
            if median is not None
            else f"{gate.name} ({gate.seconds:.1f}s, new)"
            for _diff, gate, median in grew
        ]
        return f"grew: {', '.join(parts)}"
    if prior_history:
        return (
            "no gate grew against its median; the loop was already over before this change,"
            " so report it as a blocker and do not absorb it"
        )
    if candidates:
        slowest = max(candidates, key=lambda gate: gate.seconds)
        return f"slowest: {slowest.name} ({slowest.seconds:.1f}s)"
    return "no gate ran"


def way_out(root: Path) -> str:
    tier2 = ", or move it to tier 2" if has_deep_hold(root) else ""
    return (
        "way out: narrow the gate to what the change reaches (## What each gate reads)"
        f"{tier2}, or make it less work"
    )


def format_not_judged(
    budget: float | None, reading: Reading, reason: Reason, *, streak: int
) -> str:
    note = forced_note(reading)
    if reason is Reason.NO_BUDGET:
        head = f"{reading.seconds:.1f}s{note}; " if reading.seconds is not None else ""
        return (
            f"loop: not judged: no budget: {head}no budget is declared, so the owner is"
            f" asked loop.wait; {streak} in a row"
        )
    if reason is Reason.UNREAD:
        return f"loop: not judged: times unread: {Reason.UNREAD.value}; {streak} in a row"
    explained = explained_seconds(reading)
    return (
        f"loop: not judged: the machine: {reading.seconds:.1f}s of {budget:g}s{note};"
        f" the machine explains {explained:.1f}s; {streak} in a row"
    )


def format_over(root: Path, budget: float | None, reading: Reading, *, blame: str) -> str:
    note = forced_note(reading)
    explained = explained_seconds(reading)
    parts = [
        f"loop: over: {reading.seconds:.1f}s of {budget:g}s{note}",
        f"{explained:.1f}s explained",
    ]
    if not reading.machine_read:
        parts.append("the machine could not be read, so a busy machine is a possible cause")
    parts.append(blame)
    parts.append(way_out(root))
    return "; ".join(parts)


def format_message(
    root: Path,
    budget: float | None,
    reading: Reading,
    *,
    judgement: Judgement,
    details: tuple[int, str],
) -> str:
    streak, blame = details
    if isinstance(judgement, Within):
        return f"loop: within: {reading.seconds:.1f}s of {budget:g}s{forced_note(reading)}"
    if isinstance(judgement, NotJudged):
        return format_not_judged(budget, reading, judgement.reason, streak=streak)
    return format_over(root, budget, reading, blame=blame)


def evaluate(
    root: Path,
    *,
    reading: Reading,
    report: Path | None = None,
    tree: str | None = None,
    gates: Sequence[GateTime] = (),
) -> Result:
    run = os.environ.get(report_lib.RUN) or runrecord.NO_RUN
    tree_hash = tree if tree is not None else runrecord.tree_id(root)
    forced = reading.forced or read_forced(root, report, run=run, tree=tree_hash)
    resolved = Reading(reading.seconds, reading.m, reading.machine_read, forced)
    budget = budget_of(root)
    judgement = judge(budget, resolved)
    records = list(runrecord.read(report)) if report is not None else []
    streak = (
        not_judged_streak(records, judgement.reason) + 1 if isinstance(judgement, NotJudged) else 0
    )
    blame = ""
    if isinstance(judgement, Over):
        medians, prior_history = gate_medians(records, current_run=run)
        blame = growth_summary(
            gates,
            medians,
            prior_history=prior_history,
            exclude=PROOF_GATE if forced is not None else None,
        )
    message = format_message(root, budget, resolved, judgement=judgement, details=(streak, blame))
    return Result(
        budget=budget,
        reading=resolved,
        judgement=judgement,
        effect=EFFECT[(Kind.LOOP, Place.LOCAL, judgement)],
        streak=streak,
        message=message,
    )
