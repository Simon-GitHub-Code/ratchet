"""``run_time``: the run's time, stamped at its start and end, stated as one line."""

import json
from pathlib import Path

import pytest

import gatelib
import report
import run_time
import runrecord
from tests import repos

JUSTFILE = (
    'tier0_tree_gates := "fmt lint"\n'
    'tier0_gates := tier0_tree_gates + " commits"\n'
    'tier1_gates := "test build"\n'
    'tier2_gates := "e2e"\n'
    "check:\n    true\n"
)
CHECK = ["fmt", "lint", "commits", "test", "build"]
START = "2026-11-17T10:00:00Z"
END = "2026-11-17T10:41:12Z"


@pytest.fixture
def work(tmp_path: Path) -> Path:
    return repos.repository(tmp_path, {"README.md": "# A\n", "justfile": JUSTFILE})


def stamped(root: Path, **stamps: str) -> None:
    gatelib.state_dir(root)
    (root / gatelib.STATE / run_time.STAMPS).write_text(json.dumps(stamps), encoding="utf-8")


def timed(root: Path, *rows: dict[str, object]) -> None:
    gatelib.state_dir(root)
    text = "".join(json.dumps(row) + "\n" for row in rows)
    (root / gatelib.STATE / "timing.jsonl").write_text(text, encoding="utf-8")


def tier(at: str, tree: str, **over: object) -> dict[str, object]:
    status = over.get("status", "green")
    return {
        "kind": "tier",
        "seconds": 60,
        "status": status,
        "exit": 0 if status == "green" else 1,
        "gates": CHECK,
        "at": at,
        "run": "r1",
        "tree": tree,
        **{k: v for k, v in over.items() if k != "status"},
    }


def test_the_line_says_how_long_the_ticket_took_and_how_often_check_ran(work: Path) -> None:
    stamped(work, start=START, end=END)
    timed(
        work,
        tier("2026-11-17T10:05:00Z", "a", seconds=120.4, status="red"),
        tier("2026-11-17T10:10:00Z", "b", seconds=300),
        tier("2026-11-17T10:20:00Z", "b", seconds=200.6),
    )
    assert run_time.line(work) == (
        "run time: 41m 12s; check ran 3 times, 10m 21s in all;"
        " 1 of them ran on a tree a green check had already covered"
    )


def test_a_check_that_ran_once_is_said_once(work: Path) -> None:
    stamped(work, start=START, end="2026-11-17T12:03:04Z")
    timed(work, tier("2026-11-17T10:05:00Z", "a", seconds=75))
    assert run_time.line(work) == "run time: 2h 3m 4s; check ran once, 1m 15s"


def test_no_check_run_is_said_so(work: Path) -> None:
    stamped(work, start=START, end="2026-11-17T10:00:09Z")
    assert run_time.line(work) == "run time: 9s; check did not run"
    timed(work, {"key": "lint", "seconds": 1.0, "at": "2026-11-17T10:00:01Z"})
    assert run_time.line(work) == "run time: 9s; check did not run", "a gate line is no tier line"


def test_no_stamps_is_said_and_never_guessed(work: Path) -> None:
    assert run_time.line(work) == "run time: not measured, since the run has no start stamp"
    stamped(work, end=END)
    assert run_time.line(work) == "run time: not measured, since the run has no start stamp"
    stamped(work, start="yesterday", end=END)
    assert run_time.line(work) == "run time: not measured, since the run has no start stamp"


def test_a_start_and_no_end_is_said(work: Path) -> None:
    stamped(work, start=START)
    timed(work, tier("2026-11-17T10:05:00Z", "a"))
    assert run_time.line(work) == "run time: not measured, since the run has no end stamp"


def test_a_second_check_on_a_tree_a_green_one_covered_counts_as_covered(work: Path) -> None:
    stamped(work, start=START, end=END)
    timed(
        work,
        tier("2026-11-17T10:05:00Z", "same"),
        tier("2026-11-17T10:15:00Z", "same"),
    )
    assert run_time.line(work).endswith(
        "check ran 2 times, 2m 0s in all; 1 of them ran on a tree a green check had already covered"
    )


def test_a_red_earlier_run_is_no_cover(work: Path) -> None:
    stamped(work, start=START, end=END)
    timed(
        work,
        tier("2026-11-17T10:05:00Z", "same", status="red"),
        tier("2026-11-17T10:15:00Z", "same"),
    )
    assert run_time.line(work).endswith(
        "check ran 2 times, 2m 0s in all; none ran on a tree a green check had already covered"
    )


def test_a_run_at_the_same_second_or_on_an_unread_tree_is_no_cover(work: Path) -> None:
    stamped(work, start=START, end=END)
    timed(
        work,
        tier("2026-11-17T10:05:00Z", "same"),
        tier("2026-11-17T10:05:00Z", "same"),
        tier("2026-11-17T10:06:00Z", runrecord.NO_TREE),
        tier("2026-11-17T10:07:00Z", runrecord.NO_TREE),
    )
    assert "4 times, 4m 0s in all; none ran on a tree" in run_time.line(work)


def test_lines_of_another_run_are_ignored_once_the_start_names_a_run(work: Path) -> None:
    stamped(work, start=START, end=END, run="mine")
    timed(
        work,
        tier("2026-11-17T10:05:00Z", "same", run="mine"),
        tier("2026-11-17T10:06:00Z", "same", run="other"),
        tier("2026-11-17T10:07:00Z", "same", run="other"),
    )
    assert run_time.line(work).endswith("check ran once, 1m 0s")


def test_only_a_tier_run_of_the_checks_own_gates_inside_the_stamps_is_a_check_run(
    work: Path,
) -> None:
    stamped(work, start=START, end=END)
    odd = tier("2026-11-17T10:05:00Z", "a")
    timed(
        work,
        tier("2026-11-17T09:59:59Z", "a"),
        tier("2026-11-17T10:41:13Z", "a"),
        tier("2026-11-17T10:06:00Z", "a", gates=["fmt", "lint", "commits"]),
        tier("2026-11-17T10:07:00Z", "a", gates=[*CHECK, "e2e"]),
        {**odd, "at": 5},
        {**odd, "seconds": "slow"},
        {"kind": "cache-missing", "path": "x", "at": "2026-11-17T10:08:00Z"},
        odd,
    )
    assert run_time.line(work).endswith("check ran once, 1m 0s")


def test_the_gate_order_of_a_tier_line_does_not_matter(work: Path) -> None:
    stamped(work, start=START, end=END)
    timed(work, tier("2026-11-17T10:05:00Z", "a", gates=list(reversed(CHECK))))
    assert run_time.line(work).endswith("check ran once, 1m 0s")


def test_check_is_read_from_the_justfile_never_guessed(tmp_path: Path) -> None:
    root = repos.repository(tmp_path, {"README.md": "# A\n"})
    stamped(root, start=START, end=END)
    timed(root, tier("2026-11-17T10:05:00Z", "a"))
    assert run_time.line(root) == (
        "run time: 41m 12s; check runs not counted, since the justfile names no check gates"
    )


def test_the_end_stamp_keeps_the_start(work: Path) -> None:
    stamped(work, start=START, branch="b")
    run_time.stamp_end(work)
    held = run_time.stamps(work)
    assert held["start"] == START
    assert run_time.moment(held["end"]) is not None


def test_the_start_stamp_is_utc_and_names_the_branch_and_the_run(
    work: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(report.RUN, "r9")
    run_time.stamp_start(work, "feature/1-a")
    held = run_time.stamps(work)
    assert (held["branch"], held["run"]) == ("feature/1-a", "r9")
    assert run_time.moment(held["start"]) is not None
    assert str(held["start"]).endswith("Z")
    assert (work / gatelib.STATE / run_time.STAMPS).is_file()


def test_the_start_stamp_names_no_run_when_none_is_set(
    work: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv(report.RUN, raising=False)
    run_time.stamp_start(work, "feature/1-a")
    assert "run" not in run_time.stamps(work)


def test_a_second_preflight_of_the_same_unfinished_run_keeps_the_first_start(work: Path) -> None:
    stamped(work, branch="feature/1-a", start=START)
    run_time.stamp_start(work, "feature/1-a")
    assert run_time.stamps(work) == {"branch": "feature/1-a", "start": START}


def test_another_branch_or_a_finished_run_starts_again(work: Path) -> None:
    stamped(work, branch="feature/1-a", start=START)
    run_time.stamp_start(work, "feature/2-b")
    assert run_time.stamps(work)["start"] != START
    stamped(work, branch="feature/2-b", start=START, end=END)
    run_time.stamp_start(work, "feature/2-b")
    held = run_time.stamps(work)
    assert held["start"] != START
    assert "end" not in held


def test_the_command_stamps_the_end_and_prints_the_line(
    work: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    stamped(work, start="2026-11-17T10:00:00Z", branch="b")
    assert run_time.main(["run_time", "end", "--root", str(work)]) == 0
    assert "end" in run_time.stamps(work)
    assert run_time.main(["run_time", "line", "--root", str(work)]) == 0
    assert capsys.readouterr().out.startswith("run time: ")
