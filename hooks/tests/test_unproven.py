import json
from datetime import UTC, datetime

import pytest
from hypothesis import given
from hypothesis import strategies as st

import unproven
from unproven import Deep, DeepRun, Landed

SIDE = "ee" * 20
CHAIN = tuple(
    Landed(f"{index:02d}" * 20, f"merge {index}", frozenset({SIDE} if index == 2 else ()))
    for index in range(6)
)
OFF = "ff" * 20
START = "start a deep run on the tip"
EARLY, LATE = "2026-11-20T09:00:00Z", "2026-11-20T10:00:00Z"


def on(index: int | str | None, state: str, *, at: str = EARLY) -> DeepRun:
    sha = OFF if index is None else SIDE if isinstance(index, str) else CHAIN[index].commit
    when = datetime.fromisoformat(at)
    return DeepRun(sha, state, when, f"run-{index}-{state}-{at[11:13]}")  # type: ignore[arg-type]


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
    (
        "19 a red off the line, brought onto it by T2",
        [on("side", "red"), on(4, "green")],
        True,
        None,
        "tier2",
        [SIDE[:12], sha(2), sha(3), "run-side-red"],
        [sha(1), sha(4)],
    ),
    (
        "20 a red off the line, after a green on the merge that brought it",
        [on(2, "green"), on("side", "red", at=LATE)],
        True,
        None,
        None,
        [],
        [],
    ),
    (
        "21 a red off the line, a green nearer the tip",
        [on(1, "green"), on("side", "red", at=LATE)],
        True,
        None,
        None,
        [],
        [],
    ),
    (
        "22 a green off the line covers nothing, a bound",
        [on("side", "green"), on(0, "running")],
        True,
        1,
        "unproven",
        ["newest 6", "run-0-running"],
        [],
    ),
    (
        "22 a green off the line covers nothing, a red below",
        [on("side", "green", at=LATE), on(3, "red")],
        True,
        None,
        "tier2",
        [sha(3), sha(5)],
        [sha(2)],
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
        pytest.param([on(4, "green")], on("side", "red"), CHAIN[2:4], id="row 19"),
        pytest.param([], on("side", "green"), (), id="a green off the line"),
    ],
)
def test_a_run_speaks_for_its_commit_down_to_the_last_green(
    runs: list[DeepRun], run: DeepRun, spoken: tuple[Landed, ...]
) -> None:
    assert unproven.speaks_for(CHAIN, Deep(tuple(runs), START), run) == spoken


def test_a_red_lists_the_nearest_merges_it_speaks_for_and_counts_the_rest() -> None:
    line = tuple(Landed(f"{index:040x}", f"merge {index}") for index in range(40))
    red = DeepRun(line[0].commit, "red", datetime(2026, 11, 20, 9, tzinfo=UTC), "the red")
    deep = Deep((red,), START)
    hold = unproven.held(line, deep, gated=True, bound=None)
    assert hold is not None
    assert [landed.subject for landed in line if f"{landed.subject};" in hold.text] == [
        f"merge {index}" for index in range(10)
    ]
    assert hold.text.endswith("merge 9; and 30 more, and the owner merges what fixes it")
    assert unproven.speaks_for(line, deep, red) == line


def test_a_run_speaks_for_nothing_when_the_forge_cannot_be_read() -> None:
    assert unproven.speaks_for(CHAIN, Deep(None, START), on(0, "red")) == ()


SPECS = st.lists(
    st.tuples(st.integers(0, len(CHAIN) + 1), st.sampled_from(unproven.STATES), st.integers(0, 5)),
    max_size=8,
)


def built(specs: list[tuple[int, str, int]]) -> tuple[DeepRun, ...]:
    return tuple(
        DeepRun(
            CHAIN[index].commit if index < len(CHAIN) else (OFF, SIDE)[index - len(CHAIN)],
            state,  # type: ignore[arg-type]
            datetime(2026, 11, 20, hour, tzinfo=UTC),
            f"run {number}",
        )
        for number, (index, state, hour) in enumerate(specs)
    )


def decider(runs: tuple[DeepRun, ...], landed: Landed) -> DeepRun | None:
    finished = [run for run in runs if run.commit == landed.commit and run.state != "running"]
    finished = finished or [
        run for run in runs if run.commit in landed.merged and run.state == "red"
    ]
    return max(finished, key=lambda run: (run.at, run.state == "red", run.name), default=None)


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
    red = DeepRun(CHAIN[index].commit, "red", datetime(2026, 11, 20, 9, 30, tzinfo=UTC), "the red")
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


def test_an_answer_reads_back_as_it_was_written() -> None:
    deep = Deep((on(0, "red"), on(1, "running"), on(2, "green")), START)
    assert unproven.answer(unproven.written(deep).encode()) == deep
    assert unproven.answer(unproven.written(Deep(None, START)).encode()) == Deep(None, START)


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
        json.dumps(
            {"runs": [{"commit": OFF, "state": "red", "at": EARLY, "name": "\ud800"}], "start": ""}
        ),
        json.dumps(
            {"runs": [{"commit": OFF, "state": "red", "at": EARLY, "name": "a\nb"}], "start": ""}
        ),
        json.dumps({"runs": [], "start": "a start\udcff"}),
        json.dumps({"runs": [], "start": "a start\nbehind: a line"}),
    ],
)
def test_an_answer_that_does_not_read_is_not_known(text: str | None) -> None:
    assert unproven.answer(None if text is None else text.encode()).runs is None


@pytest.mark.parametrize(
    ("text", "seen"),
    [
        ("2026-11-20T09:00:00Z", "2026-11-20T09:00:00+00:00"),
        ("2026-11-20T09:00:00+00:00", "2026-11-20T09:00:00+00:00"),
        ("2026-11-20T09:00:00.5Z", "2026-11-20T09:00:00.500000+00:00"),
        ("2026-11-20T09:00:00.123456-05:30", "2026-11-20T09:00:00.123456-05:30"),
        ("2026-11-20T09:00:00", None),
        ("2026-W47-5T09:00:00Z", None),
        ("2026-324T09:00:00Z", None),
        ("20261120T090000Z", None),
        ("2026-11-20 09:00:00Z", None),
        ("2026-11-20t09:00:00Z", None),
        ("2026-11-20T09:00Z", None),
        ("2026-11-20T09Z", None),
        ("2026-11-20T09:00:00,5Z", None),
        ("2026-11-20T09:00:00.1234567Z", None),
        ("2026-11-20T09:00:00+0000", None),
        ("2026-11-20T09:00:00+05:30:15", None),
        ("2026-11-20T09:00:00Z\n", None),
        ("\uff12026-11-20T09:00:00Z", None),
        ("2026-13-20T09:00:00Z", None),
        (None, None),
    ],
)
def test_a_time_is_an_instant_only_in_the_one_form_the_adapter_writes(
    text: str | None, seen: str | None
) -> None:
    when = unproven.instant(text)
    assert (None if when is None else when.isoformat()) == seen
