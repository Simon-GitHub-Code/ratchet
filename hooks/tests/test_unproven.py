import json
from pathlib import Path

import pytest
from hypothesis import given
from hypothesis import strategies as st

import unproven
from tests.repos import branch, commit, git, repository
from unproven import Deep, DeepRun, Landed

CHAIN = tuple(Landed(f"{index:02d}" * 20, f"merge {index}") for index in range(6))
OFF = "ff" * 20
START = "start a deep run on the tip"
EARLY, LATE = "2026-11-20T09:00:00Z", "2026-11-20T10:00:00Z"


def on(index: int | None, state: str, *, at: str = EARLY) -> DeepRun:
    sha = OFF if index is None else CHAIN[index].commit
    return DeepRun(sha, state, at, f"run-{index}-{state}-{at[11:13]}")  # type: ignore[arg-type]


def sha(index: int) -> str:
    return CHAIN[index].commit[:12]


def held(*runs: DeepRun, gated: bool = True, bound: int | None = None) -> unproven.Hold | None:
    return unproven.held(CHAIN, Deep(runs, START), gated=gated, bound=bound)


ROWS = [
    (
        "1 a red, no tier 2 gate bound",
        [on(0, "red"), on(2, "green")],
        False,
        None,
        "tier2",
        [sha(0), sha(1), "run-0-red"],
        [sha(2)],
    ),
    ("2 no tier 2 gate bound, past a bound", [on(3, "green")], False, 1, None, [], []),
    ("3 no bound, runs in flight", [on(0, "running"), on(3, "green")], True, None, None, [], []),
    (
        "4 no bound, a red, a newer run in flight",
        [on(0, "running"), on(2, "red"), on(4, "green")],
        True,
        None,
        "tier2",
        [sha(2), sha(3), "run-2-red", "run-0-running"],
        [sha(1), sha(4)],
    ),
    ("5 a bound, no run finished", [], True, 1, "unproven", ["newest 6", START], []),
    ("6 a run in flight at the tip", [on(0, "running"), on(1, "green")], True, 1, None, [], []),
    (
        "7 a run in flight one behind",
        [on(1, "running"), on(2, "green")],
        True,
        1,
        "unproven",
        ["newest 2", "allows 1", "run-1-running"],
        [START],
    ),
    (
        "8 a bound exceeded, nothing running",
        [on(2, "green")],
        True,
        1,
        "unproven",
        ["newest 2", START],
        [],
    ),
    (
        "9 an unknown status read as running, a bound",
        [on(0, "running"), on(2, "green")],
        True,
        1,
        "unproven",
        ["run-0-running"],
        [START],
    ),
    (
        "9 an unknown status read as running, no bound",
        [on(0, "running"), on(2, "green")],
        True,
        None,
        None,
        [],
        [],
    ),
    ("10 a red older than a green", [on(1, "green"), on(2, "red")], True, None, None, [], []),
    ("10 a red older than a green, a bound", [on(1, "green"), on(2, "red")], True, 1, None, [], []),
    (
        "11 a nightly red after a green on the tip",
        [on(0, "green"), on(0, "red", at=LATE), on(3, "green")],
        True,
        None,
        "tier2",
        [sha(0), sha(1), sha(2), "run-0-red-10"],
        [sha(3), "run-0-green"],
    ),
    (
        "12 an older run on the tip started again, red",
        [on(0, "green", at=LATE), on(0, "red")],
        True,
        None,
        None,
        [],
        [],
    ),
    ("13 a merge that changes the deep workflow", [on(0, "green")], True, 0, None, [], []),
    (
        "14 a force-moved branch, a bound",
        [on(None, "green"), on(0, "running")],
        True,
        1,
        "unproven",
        ["run-0-running"],
        [START],
    ),
    (
        "14 a force-moved branch, no bound",
        [on(None, "green"), on(0, "running")],
        True,
        None,
        None,
        [],
        [],
    ),
    (
        "16 the fix for a red",
        [on(0, "running"), on(1, "red")],
        True,
        None,
        "tier2",
        [sha(1), "run-1-red", "run-0-running"],
        [],
    ),
    (
        "17 a red after a red, no bound",
        [on(0, "red"), on(1, "red"), on(2, "green")],
        True,
        None,
        "tier2",
        [sha(0), sha(1), "run-0-red"],
        [sha(2)],
    ),
    (
        "17 a red after a red, a bound",
        [on(0, "red"), on(1, "red"), on(2, "green")],
        True,
        1,
        "tier2",
        [sha(0), sha(1), "run-0-red"],
        [sha(2)],
    ),
    (
        "18 a merge that deferred its proof, before #307",
        [on(0, "running"), on(1, "green")],
        True,
        None,
        None,
        [],
        [],
    ),
]


@pytest.mark.parametrize("row", ROWS, ids=[row[0] for row in ROWS])
def test_each_row_of_the_truth_table(row: tuple[object, ...]) -> None:
    _, runs, gated, bound, code, named, unnamed = row
    hold = held(*runs, gated=gated, bound=bound)
    assert (hold.code if hold else None) == code, hold
    text = hold.text if hold else ""
    assert [name for name in named if name not in text] == [], text
    assert [name for name in unnamed if name in text] == [], text


@pytest.mark.parametrize(("gated", "bound"), [(False, None), (True, None), (True, 0), (True, 9)])
def test_15_a_forge_that_cannot_be_read_is_not_known(gated: bool, bound: int | None) -> None:  # noqa: FBT001
    hold = unproven.held(CHAIN, Deep(None, START), gated=gated, bound=bound)
    assert hold is not None
    assert hold.code == "tier2"
    assert "not known" in hold.text


@pytest.mark.parametrize(
    ("runs", "run", "spoken"),
    [
        pytest.param([on(0, "red"), on(2, "green")], on(0, "red"), CHAIN[0:2], id="row 1"),
        pytest.param(
            [on(0, "running"), on(2, "red"), on(4, "green")], on(2, "red"), CHAIN[2:4], id="row 4"
        ),
        pytest.param(
            [on(0, "green"), on(0, "red", at=LATE), on(3, "green")],
            on(0, "red", at=LATE),
            CHAIN[0:3],
            id="row 11",
        ),
        pytest.param(
            [on(0, "red"), on(1, "red"), on(2, "green")], on(0, "red"), CHAIN[0:2], id="row 17 B"
        ),
        pytest.param(
            [on(0, "red"), on(1, "red"), on(2, "green")], on(1, "red"), CHAIN[1:2], id="row 17 A"
        ),
        pytest.param([on(3, "red")], on(3, "red"), CHAIN[3:], id="no green below"),
        pytest.param([on(2, "green")], on(0, "green"), CHAIN[0:2], id="a run not yet listed"),
        pytest.param([on(0, "running")], on(0, "running"), (), id="a running run"),
        pytest.param([on(None, "red")], on(None, "red"), (), id="off the chain"),
    ],
)
def test_a_run_speaks_for_its_commit_down_to_the_last_green(
    runs: list[DeepRun], run: DeepRun, spoken: tuple[Landed, ...]
) -> None:
    assert unproven.speaks_for(CHAIN, Deep(tuple(runs), START), run) == spoken


def test_a_run_speaks_for_nothing_when_the_forge_cannot_be_read() -> None:
    assert unproven.speaks_for(CHAIN, Deep(None, START), on(0, "red")) == ()


SPECS = st.lists(
    st.tuples(st.integers(0, len(CHAIN)), st.sampled_from(unproven.STATES), st.integers(0, 5)),
    max_size=8,
)


def built(specs: list[tuple[int, str, int]]) -> tuple[DeepRun, ...]:
    return tuple(
        DeepRun(
            CHAIN[index].commit if index < len(CHAIN) else OFF,
            state,  # type: ignore[arg-type]
            f"2026-11-20T0{hour}:00:00Z",
            f"run {number}",
        )
        for number, (index, state, hour) in enumerate(specs)
    )


def decider(runs: tuple[DeepRun, ...], landed: Landed) -> DeepRun | None:
    finished = [run for run in runs if run.commit == landed.commit and run.state != "running"]
    return max(finished, key=lambda run: (run.at, run.name), default=None)


BOUNDS = st.none() | st.integers(0, len(CHAIN) + 1)


@given(SPECS, st.booleans(), BOUNDS)
def test_a_red_deciding_the_nearest_covered_commit_always_holds(
    specs: list[tuple[int, str, int]],
    gated: bool,  # noqa: FBT001
    bound: int | None,
) -> None:
    runs = built(specs)
    nearest = next((d for landed in CHAIN if (d := decider(runs, landed)) is not None), None)
    hold = unproven.held(CHAIN, Deep(runs, START), gated=gated, bound=bound)
    if nearest is not None and nearest.state == "red":
        assert hold is not None
        assert hold.code == "tier2"
        assert nearest.name in hold.text


@given(SPECS, st.integers(0, len(CHAIN) - 1))
def test_a_red_speaks_down_to_the_nearest_older_green_and_never_past_it(
    specs: list[tuple[int, str, int]], index: int
) -> None:
    runs = built(specs)
    red = DeepRun(CHAIN[index].commit, "red", "2026-11-20T09:30:00Z", "the red")
    spoken = unproven.speaks_for(CHAIN, Deep(runs, START), red)
    end = index + len(spoken)
    assert spoken == CHAIN[index:end]
    assert all(getattr(decider(runs, landed), "state", None) != "green" for landed in spoken[1:])
    if end < len(CHAIN):
        assert getattr(decider(runs, CHAIN[end]), "state", None) == "green"


@given(specs=SPECS, index=st.integers(0, len(CHAIN)), gated=st.booleans(), bound=BOUNDS)
def test_a_run_not_finished_never_turns_a_hold_into_a_pass(
    *,
    specs: list[tuple[int, str, int]],
    index: int,
    gated: bool,
    bound: int | None,
) -> None:
    runs = built(specs)
    before = unproven.held(CHAIN, Deep(runs, START), gated=gated, bound=bound)
    more = (*runs, *built([(index, "running", 0)]))
    after = unproven.held(CHAIN, Deep(more, START), gated=gated, bound=bound)
    assert (before is None) == (after is None)
    assert getattr(before, "code", None) == getattr(after, "code", None)


@given(SPECS, st.booleans(), BOUNDS)
def test_with_no_bound_only_a_red_or_an_unread_forge_holds(
    specs: list[tuple[int, str, int]],
    gated: bool,  # noqa: FBT001
    bound: int | None,
) -> None:
    deep = Deep(built(specs), START)
    assert unproven.held(CHAIN, deep, gated=gated, bound=None) == unproven.held(
        CHAIN, deep, gated=False, bound=bound
    )


def test_the_chain_is_the_first_parent_line_tip_first(tmp_path: Path) -> None:
    repository(tmp_path)
    first = git(tmp_path, "rev-parse", "HEAD")
    branch(tmp_path, "side")
    aside = commit(tmp_path, "feat(core): on the side")
    git(tmp_path, "switch", "-q", "main")
    second = commit(tmp_path, "feat(core): second")
    git(tmp_path, "merge", "-q", "--no-ff", "-m", "Merge side", "side")
    merged = git(tmp_path, "rev-parse", "HEAD")
    chain = unproven.chain(tmp_path, "main")
    assert chain == (
        Landed(merged, "Merge side"),
        Landed(second, "feat(core): second"),
        Landed(first, "feat(core): the clean state"),
    )
    assert aside not in [landed.commit for landed in chain]
    assert unproven.chain(tmp_path, "nowhere") == ()


def test_an_answer_reads_back_as_it_was_written() -> None:
    deep = Deep((on(0, "red"), on(1, "running"), on(2, "green")), START)
    assert unproven.answer(unproven.written(deep)) == deep
    assert unproven.answer(unproven.written(Deep(None, START))) == Deep(None, START)


@pytest.mark.parametrize(
    "text",
    [
        None,
        "",
        "not json",
        "[]",
        json.dumps({"runs": [], "start": 3}),
        json.dumps({"runs": {}, "start": START}),
        json.dumps({"runs": ["a run"], "start": START}),
        json.dumps({"runs": [{"commit": OFF, "state": "red", "at": EARLY}], "start": START}),
        json.dumps(
            {"runs": [{"commit": OFF, "state": "amber", "at": EARLY, "name": "n"}], "start": START}
        ),
    ],
)
def test_an_answer_that_does_not_read_is_not_known(text: str | None) -> None:
    assert unproven.answer(text).runs is None
