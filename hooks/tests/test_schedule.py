import random
from collections import deque
from collections.abc import Sequence
from pathlib import Path
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


def test_a_run_keeps_the_gates_it_runs_and_every_restriction_either_statement_makes() -> None:
    read = schedule.stated(SPEED.format(wide="- Wide: none\n"))
    run = read.among(["a11y", "e2e", "load", "lint", "perf", "fmt"])
    assert run.groups == {"Apart": (("e2e", "a11y"),), "Alone": (("perf",), ("load",)), "Wide": ()}
    given = schedule.Schedule(
        {"Apart": (("a11y", "e2e"), ("lint", "fmt")), "Alone": (("lint",),), "Wide": (("fmt",),)}
    )
    kept = given.joined(run)
    assert kept.groups == {
        "Apart": (("a11y", "e2e"), ("lint", "fmt")),
        "Alone": (("lint",), ("perf",), ("load",)),
        "Wide": (("fmt",),),
    }
    none = schedule.stated("## Speed\n\n- Apart: none\n- Alone: none\n")
    assert not given.joined(none).beside("lint", "fmt")
    assert not none.joined(given).beside("a11y", "e2e")


def test_what_the_flags_keep_that_the_binding_does_not_is_what_it_leaves_unstated() -> None:
    read = schedule.stated(SPEED.format(wide="- Wide: `test`\n"))
    given = schedule.Schedule(
        {
            "Apart": (("a11y", "e2e"), ("perf", "lint"), ("fmt", "lint"), ("solo",)),
            "Alone": (("load",), ("bench",)),
            "Wide": (("test",), ("floor",)),
        }
    )
    assert read.unstated(given).groups == {
        "Apart": (("fmt", "lint"),),
        "Alone": (("bench",),),
        "Wide": (("floor",),),
    }
    assert read.unstated(read.among(["e2e", "a11y", "perf", "test"])).groups == {
        "Apart": (),
        "Alone": (),
        "Wide": (),
    }


RECIPES = """\
runner := "python3 x/scripts/parallel.py --runner just"
jobs := env_var_or_default("RATCHET_JOBS", "4")  # every core
alone := "--alone perf load"
serial := '--serial e2e a11y'
run := runner + " --jobs " + jobs + " " + alone + ' --report "a b"'
tagged := "--serial" + " " + "tag one"
loop := loop
escaped := "--alone a\\tb"
quoted := "--alone \\"x\\""

check: install
    {{run}} {{serial}} -- lint e2e a11y perf load

# a comment line
fast *args:
    @{{run}} --fail-fast {{args}} -- lint

odd:
    {{run}} --alone {{ if os() == "linux" { "x" } else { "y" } }} -- x

split:
    {{runner}} --wide test --serial=c d \\
        --serial f g --root . --jobs=3 -- f g; echo --serial c no

other:
    echo not the runner --serial no
    {{runner}} 'unbalanced

looped:
    {{runner}} {{loop}} -- x
    {{runner}} {{tagged}} > out -- tag one

escapes:
    {{runner}} {{escaped}} -- x
    {{runner}} {{quoted}} -- x

install:
    true
    python3 -m pytest tests/test_parallel.py --serial not a gate -- x
"""


def test_the_recipes_flags_are_read_through_their_variables_as_the_runner_gets_them() -> None:
    kept, unread = schedule.recipes(RECIPES)
    assert kept.groups == {
        "Apart": (("e2e", "a11y"), ("c", "d"), ("f", "g"), ("tag", "one")),
        "Alone": (("perf",), ("load",)),
        "Wide": (("test",),),
    }
    assert [(missed.recipe, missed.gates) for missed in unread] == [
        ("fast", ("lint",)),
        ("odd", ("x",)),
        ("other", None),
        ("looped", ("x",)),
        ("escapes", ("x",)),
        ("escapes", ("x",)),
    ]
    assert unread[0].words == ("{{ args }}",)
    assert unread[-1].words == ('{{ "--alone \\"x\\"" }}',)


EMPTY = schedule.Schedule({"Apart": (), "Alone": (), "Wide": ()})


def test_a_recipe_whose_every_flag_is_text_is_read_whole_and_one_that_is_not_is_named() -> None:
    plain = "run := 'python3 parallel.py --jobs ' + num_cpus()\n\ncheck:\n    {{run}} -- a\n"
    assert schedule.recipes(plain) == (EMPTY, [])
    assert schedule.recipes("check:\n    {{cmd}} --alone x -- x\n") == (schedule.Schedule(), [])
    named = "check:\n    python3 parallel.py {{ cmd }} -- x\n"
    assert schedule.recipes(named)[1] == [schedule.Unread("check", ("{{ cmd }}",), ("x",))]


SHELL_FLAGS = ("$FLAGS", "${FLAGS}", "`cat flags.txt`", "$(cat flags.txt)", "<(cat flags.txt)")


@pytest.mark.parametrize("words", SHELL_FLAGS)
def test_a_flag_the_shell_hands_the_runner_leaves_the_recipe_unread_with_the_gates_it_runs(
    words: str,
) -> None:
    text = f"check:\n    python3 scripts/parallel.py --runner just {words} --alone c -- a b c\n"
    kept, unread = schedule.recipes(text)
    assert unread == [schedule.Unread("check", (words,), ("a", "b", "c"))]
    assert kept.alone == {"c"}
    valued = f"check:\n    python3 scripts/parallel.py --jobs {words} -- a\n"
    assert schedule.recipes(valued)[1] == [schedule.Unread("check", (words,), ("a",))]


def test_an_unread_recipe_s_gates_are_unknown_when_the_shell_or_just_names_them_too() -> None:
    text = (
        "check:\n    python3 parallel.py $FLAGS -- a $MORE\n"
        "fast:\n    python3 parallel.py `x` --serial a b\n"
        "deep:\n    python3 parallel.py $X -- a {{ gates() }}\n"
        "odd:\n    python3 parallel.py $X -- a ( b\n"
    )
    assert [missed.gates for missed in schedule.recipes(text)[1]] == [None, None, None, None]


def test_what_follows_the_runner_s_own_command_is_not_its_flags_or_gates() -> None:
    text = "check:\n    python3 parallel.py -- a b; echo $HOME `date` $(id)\n"
    assert schedule.recipes(text) == (EMPTY, [])


def test_the_justfile_is_read_with_what_it_imports(tmp_path: Path) -> None:
    assert schedule.justfile(tmp_path) == ""
    (tmp_path / "justfile").write_text("import 'more.just'\nimport? 'gone.just'\na := '1'\n")
    (tmp_path / "more.just").write_text("import 'justfile'\nb := '2'\n")
    read = schedule.justfile(tmp_path)
    assert read.count("a := '1'") == 1
    assert read.count("b := '2'") == 1
