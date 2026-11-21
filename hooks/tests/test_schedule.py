import random
from collections import deque
from collections.abc import Sequence
from typing import Protocol

import pytest

import schedule

SEEDS = range(200)
LANES = 4
GATES = ("one", "two", "three", "four", "five", "six", "seven", "eight", "nine")
TASKS = (*GATES, "one", "six")
GROUPS = (("one", "two", "three"), ("four", "five"))
STATEMENT = schedule.Schedule({schedule.APART: GROUPS, schedule.ALONE: (("six",),)})


class Scheduling(Protocol):
    @property
    def pending(self) -> Sequence[int]: ...

    def take(self) -> int | None: ...

    def done(self, task: int) -> tuple[str, ...]: ...


class Queue:
    def __init__(self, tasks: Sequence[int]) -> None:
        self.queue = deque(tasks)

    @property
    def pending(self) -> Sequence[int]:
        return self.queue

    def take(self) -> int | None:
        return self.queue.popleft() if self.queue else None

    def done(self, _task: int) -> tuple[str, ...]:
        return ()


Interval = tuple[int, int, int, int]


def lanes(board: Scheduling, seed: int) -> list[Interval]:
    rng = random.Random(seed)  # noqa: S311 # -- a seeded order of moves, not a secret
    idle = list(range(LANES))
    busy: dict[int, tuple[int, int]] = {}
    ran: list[Interval] = []
    for step in range(10_000):
        moves = [("take", lane) for lane in idle] + [("done", lane) for lane in busy]
        if not moves:
            return ran
        move, lane = rng.choice(moves)
        if move == "done":
            task, started = busy.pop(lane)
            board.done(task)
            ran.append((lane, task, started, step))
            idle.append(lane)
            continue
        task = board.take()
        if task is not None:
            idle.remove(lane)
            busy[lane] = (task, step)
        elif not board.pending:
            idle.remove(lane)
    message = "the lanes never finished"
    raise AssertionError(message)


def overlapping(ran: list[Interval]) -> set[frozenset[int]]:
    return {
        frozenset((one[1], other[1]))
        for one in ran
        for other in ran
        if one is not other and one[2] < other[3] and other[2] < one[3]
    }


def board(statement: schedule.Schedule) -> schedule.Board[int]:
    return schedule.Board(range(len(TASKS)), statement, TASKS.__getitem__)


def test_two_gates_of_one_apart_group_never_run_at_once_and_an_alone_gate_beside_nothing() -> None:
    met: set[frozenset[str]] = set()
    for seed in SEEDS:
        ran = lanes(board(STATEMENT), seed)
        assert sorted(task for _, task, _, _ in ran) == list(range(len(TASKS)))
        for pair in overlapping(ran):
            gates = sorted(TASKS[task] for task in pair)
            apart = any(set(gates) <= set(group) for group in GROUPS)
            assert not apart, f"seed {seed}: {gates} ran at once"
            assert "six" not in gates
            met.add(frozenset(gates))
    assert frozenset(("one", "four")) in met
    assert frozenset(("seven", "eight")) in met


def test_with_no_statement_the_lanes_take_the_gates_in_main_s_order_on_every_seed() -> None:
    for seed in SEEDS:
        queued = lanes(Queue(range(len(TASKS))), seed)
        assert lanes(board(schedule.Schedule()), seed) == queued


def test_a_task_reports_every_gate_that_ran_beside_it_while_it_ran() -> None:
    plan = schedule.Board(["first", "second", "third"], schedule.Schedule(), str)
    first, second = plan.take(), plan.take()
    assert (first, second) == ("first", "second")
    assert plan.done("second") == ("first",)
    assert plan.take() == "third"
    assert plan.done("first") == ("second", "third")
    assert plan.done("third") == ("first",)


def test_an_alone_gate_waits_for_the_lanes_to_empty_and_runs_after_the_rest() -> None:
    statement = schedule.Schedule({schedule.ALONE: (("timed",),)})
    plan = schedule.Board(["timed", "plain", "other"], statement, str)
    assert plan.pending == ["plain", "other", "timed"]
    assert plan.take() == "plain"
    assert plan.take() == "other"
    assert plan.take() is None
    plan.done("plain")
    assert plan.take() is None
    plan.done("other")
    assert plan.take() == "timed"
    assert plan.take() is None


@pytest.mark.parametrize(
    "case", [("plain", 16, 4, 4), ("plain", 7, 2, 3), ("plain", 2, 4, 1), ("timed", 16, 4, 16)]
)
def test_a_lane_is_told_its_share_of_the_cores_and_an_alone_gate_all_of_them(
    case: tuple[str, int, int, int],
) -> None:
    gate, cores, lanes, told = case
    statement = schedule.Schedule({schedule.ALONE: (("timed",),)})
    assert statement.share(gate, cores=cores, lanes=lanes) == told


SPEED = (
    "# Binding\n\n## Speed\n\n- Parallel: every core\n"
    "- Apart: `e2e a11y` `coverage mutation-changed`\n"
    "- Alone: `perf` `load`\n"
    "{wide}"
    "\n## Shared reports\n\n- Apart: `not this section`\n"
)


def test_the_binding_states_each_kind_once_in_its_speed_section() -> None:
    read = schedule.stated(SPEED.format(wide="- Wide: none\n"))
    assert read.groups == {
        "Apart": (("e2e", "a11y"), ("coverage", "mutation-changed")),
        "Alone": (("perf",), ("load",)),
        "Wide": (),
    }
    assert read.alone == {"perf", "load"}
    assert read.wide == frozenset()


def test_a_kind_left_out_or_left_as_its_placeholder_is_not_stated() -> None:
    assert schedule.WIDE not in schedule.stated(SPEED.format(wide="")).groups
    placeholder = SPEED.format(wide="- Wide: <each gate that is itself parallel>\n")
    assert schedule.WIDE not in schedule.stated(placeholder).groups
    assert schedule.stated("# Binding\n").groups == {}


def test_a_run_keeps_the_gates_it_runs_and_the_stated_kinds_win_over_the_given() -> None:
    read = schedule.stated(SPEED.format(wide=""))
    run = read.among(["a11y", "e2e", "load", "lint"])
    assert run.groups == {"Apart": (("e2e", "a11y"),), "Alone": (("load",),)}
    given = schedule.Schedule({"Alone": (("lint",),), "Wide": (("lint",),)})
    assert run.over(given).groups == {**run.groups, "Wide": (("lint",),)}
