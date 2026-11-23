import argparse
import contextlib
import io
import json
import math
import os
import re
import shlex
import signal
import subprocess
import sys
import threading
import time
from collections.abc import Callable
from pathlib import Path

import pytest
from hypothesis import given
from hypothesis import strategies as st

import budget
import gatelib
import machine
import parallel
import runrecord
import schedule
from tests.repos import git
from tests.servers import gone

PYTHON = shlex.quote(sys.executable)
PATIENCE = 60.0


def gate(code: str, *args: object) -> str:
    return shlex.join([sys.executable, "-c", code, *map(str, args)])


EXIT = "import sys; sys.exit(int(sys.argv[1]))"
SAY = "import sys; print(sys.argv[1], flush=True); print(sys.argv[2], file=sys.stderr, flush=True)"
MEET = (
    "import pathlib, sys, time\n"
    "mine, theirs = map(pathlib.Path, sys.argv[1:3])\n"
    "mine.touch()\n"
    f"deadline = time.monotonic() + {PATIENCE}\n"
    "while not theirs.exists():\n"
    "    if time.monotonic() > deadline:\n"
    "        sys.exit(9)\n"
    "    time.sleep(0.01)\n"
)
LOG = (
    "import os, sys, time\n"
    "log, name = sys.argv[-2:]\n"
    "os.write(os.open(log, os.O_WRONLY | os.O_APPEND | os.O_CREAT), f'+{name}\\n'.encode())\n"
    "time.sleep(0.15)\n"
    "os.write(os.open(log, os.O_WRONLY | os.O_APPEND), f'-{name}\\n'.encode())\n"
)
HOLD = (
    "import os, pathlib, sys, time\n"
    "pathlib.Path(sys.argv[1]).write_text(str(os.getpid()))\n"
    f"time.sleep({PATIENCE})\n"
)
RED_ONCE_HELD = (
    "import pathlib, sys, time\n"
    "held = pathlib.Path(sys.argv[1])\n"
    "while not (held.is_file() and held.read_text()):\n"
    "    time.sleep(0.01)\n"
    "sys.exit(1)\n"
)
BURST = (
    "import sys, time\n"
    "for n in range(12):\n"
    "    print(f'{sys.argv[1]} line {n}', flush=True)\n"
    "    time.sleep(0.01)\n"
)


def run(*arguments: str) -> int:
    return parallel.main(["parallel", *arguments])


def until(predicate: Callable[[], bool]) -> bool:
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.02)
    return False


def pid_of(path: Path) -> int:
    assert until(lambda: path.is_file() and path.read_text() != "")
    return int(path.read_text())


def test_all_green_prints_each_gate_whole_then_one_line_with_its_seconds(
    capsys: pytest.CaptureFixture[str],
) -> None:
    code = run("--", gate(SAY, "out-a", "err-a"), gate(SAY, "out-b", "err-b"))
    out = capsys.readouterr().out
    assert code == 0
    assert out.endswith("parallel: green (2 ran)\n")
    for tag in "ab":
        assert f"out-{tag}\nerr-{tag}\n" in out
    assert out.count(": green in ") == 2
    assert "\x1b" not in out


def test_every_red_gate_is_named_among_green_ones(capsys: pytest.CaptureFixture[str]) -> None:
    red_a, green, red_b = gate(EXIT, 3), gate(EXIT, 0), gate(EXIT, 5)
    assert run("--", red_a, green, red_b) == 1
    out = capsys.readouterr().out
    assert f"{red_a}: red (exit 3)\n" in out
    assert f"{red_b}: red (exit 5)\n" in out
    assert f"{green}: red" not in out
    assert out.endswith("parallel: red\n")


def test_a_gate_killed_by_a_signal_is_red_with_the_shell_s_code(
    capsys: pytest.CaptureFixture[str],
) -> None:
    killed = gate("import os, signal; os.kill(os.getpid(), signal.SIGKILL)")
    assert run("--", killed) == 1
    assert f"{killed}: red (exit 137)" in capsys.readouterr().out


def test_gates_run_at_the_same_time(tmp_path: Path) -> None:
    a, b = tmp_path / "a", tmp_path / "b"
    assert run("--jobs", "2", "--", gate(MEET, a, b), gate(MEET, b, a)) == 0


def test_ratchet_jobs_caps_how_many_run_at_once_when_no_jobs_is_given(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    log = tmp_path / "log"
    monkeypatch.setenv("RATCHET_JOBS", "1")
    assert run("--", *(gate(LOG, log, name) for name in "ab")) == 0
    assert log.read_text().splitlines() == ["+a", "-a", "+b", "-b"]


@pytest.mark.parametrize("value", ["", "many", "0", "-2"])
def test_a_ratchet_jobs_that_is_no_count_leaves_every_core(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, value: str
) -> None:
    monkeypatch.setenv("RATCHET_JOBS", value)
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", os.devnull)
    monkeypatch.setenv("GIT_CONFIG_SYSTEM", os.devnull)
    monkeypatch.chdir(tmp_path)
    assert gatelib.default_jobs() == (os.cpu_count() or 1)
    monkeypatch.setattr(os, "cpu_count", lambda: None)
    assert gatelib.default_jobs() == 1


def configured_repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, value: str | None) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    git(repo, "init", "-q")
    if value is not None:
        git(repo, "config", "ratchet.jobs", value)
    monkeypatch.delenv("RATCHET_JOBS", raising=False)
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", os.devnull)
    monkeypatch.setenv("GIT_CONFIG_SYSTEM", os.devnull)
    monkeypatch.chdir(repo)
    return repo


def test_the_git_config_caps_the_gates_when_the_environment_says_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = configured_repo(tmp_path, monkeypatch, "3")
    assert gatelib.default_jobs() == 3
    assert gatelib.default_jobs(repo) == 3


def test_the_environment_wins_over_the_git_config(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    configured_repo(tmp_path, monkeypatch, "3")
    monkeypatch.setenv("RATCHET_JOBS", "2")
    assert gatelib.default_jobs() == 2


@pytest.mark.parametrize("value", ["many", "0", "-2", ""])
def test_a_git_config_that_is_no_count_leaves_every_core(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, value: str
) -> None:
    configured_repo(tmp_path, monkeypatch, value)
    assert gatelib.default_jobs() == (os.cpu_count() or 1)


def test_an_unset_git_config_leaves_every_core(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    configured_repo(tmp_path, monkeypatch, None)
    assert gatelib.default_jobs() == (os.cpu_count() or 1)


def test_outside_a_repository_every_core_is_left_and_nothing_is_printed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capfd: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.delenv("RATCHET_JOBS", raising=False)
    monkeypatch.setenv("GIT_CEILING_DIRECTORIES", str(tmp_path))
    monkeypatch.chdir(tmp_path)
    assert gatelib.default_jobs() == (os.cpu_count() or 1)
    assert capfd.readouterr() == ("", "")


def test_a_missing_git_leaves_every_core(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("RATCHET_JOBS", raising=False)
    monkeypatch.setenv("PATH", str(tmp_path))
    assert gatelib.default_jobs(tmp_path) == (os.cpu_count() or 1)


def test_jobs_caps_how_many_run_at_once(tmp_path: Path) -> None:
    log = tmp_path / "log"
    names = ["a", "b", "c"]
    assert run("--jobs", "1", "--", *(gate(LOG, log, name) for name in names)) == 0
    lines = log.read_text().splitlines()
    assert lines == ["+a", "-a", "+b", "-b", "+c", "-c"]


def test_a_serial_group_runs_in_order_one_at_a_time_beside_other_gates(tmp_path: Path) -> None:
    log, a, b = tmp_path / "log", tmp_path / "a", tmp_path / "b"
    head = gate(MEET + LOG, a, b, log, "head")
    second = gate(LOG, log, "second")
    beside = gate(MEET, b, a)
    assert run("--jobs", "3", "--serial", head, second, "--", head, beside, second) == 0
    assert log.read_text().splitlines() == ["+head", "-head", "+second", "-second"]


def test_a_serial_group_member_after_a_red_one_still_runs(
    capsys: pytest.CaptureFixture[str],
) -> None:
    red, green = gate(EXIT, 1), gate(SAY, "later", "")
    assert run("--serial", red, green, "--", red, green) == 1
    out = capsys.readouterr().out
    assert "later\n" in out
    assert f"{red}: red (exit 1)\n" in out


def test_outputs_are_never_interleaved(capsys: pytest.CaptureFixture[str]) -> None:
    assert run("--", gate(BURST, "a"), gate(BURST, "b"), gate(BURST, "c")) == 0
    lines = capsys.readouterr().out.splitlines()
    tags = [line[0] for line in lines if re.fullmatch(r"[abc] line \d+", line)]
    runs = [tag for index, tag in enumerate(tags) if index == 0 or tags[index - 1] != tag]
    assert sorted(runs) == ["a", "b", "c"]
    assert len(tags) == 36


def test_the_closing_lines_name_the_slowest_gate(capsys: pytest.CaptureFixture[str]) -> None:
    options = argparse.Namespace(
        root=Path(), jobs=1, fail_fast=False, report=None, alone=[], wide=[]
    )
    scheduler = parallel.Scheduler([], [], options, kept=schedule.Schedule())
    scheduler.finished = [
        parallel.Finished("fast", 0.2, 0, ""),
        parallel.Finished("slow gate", 9.5, 0, ""),
    ]
    assert parallel.closing(scheduler, 10.0) == []
    assert capsys.readouterr().out.splitlines() == [
        (
            "parallel: 2 checks in 10.0s (9.7s of check time): 2 ran and passed;"
            " slowest: slow gate 9.5s"
        ),
        "parallel: the lanes were full (1 lane, 9.7s of check time in 10.0s)",
    ]


def test_a_group_whose_chain_outlasts_every_gate_beside_it_is_named(
    capsys: pytest.CaptureFixture[str],
) -> None:
    options = argparse.Namespace(
        root=Path(), jobs=4, fail_fast=False, report=None, alone=[], wide=[]
    )
    units = [("coverage", "mutation-changed"), ("lint",), ("fmt", "types")]
    kept = schedule.Schedule({"Apart": (units[0], units[2])})
    scheduler = parallel.Scheduler(units, [], options, kept=kept)
    scheduler.board.pending.clear()
    scheduler.finished = [
        parallel.Finished("coverage", 106.0, 0, ""),
        parallel.Finished("mutation-changed", 206.4, 0, ""),
        parallel.Finished("lint", 182.0, 0, ""),
        parallel.Finished("fmt", 1.0, 0, ""),
        parallel.Finished("types", 2.0, 0, ""),
    ]
    assert parallel.closing(scheduler, 320.0) == []
    assert capsys.readouterr().out.splitlines()[1:] == [
        (
            "parallel: the --serial group coverage, mutation-changed (312.4s chained) set the"
            " wall clock while other lanes stood idle (782.6s idle across 4 lanes), time without"
            " it if they can run together"
        )
    ]


def test_a_run_with_no_gate_outside_every_group_prints_full_lanes(
    capsys: pytest.CaptureFixture[str],
) -> None:
    options = argparse.Namespace(
        root=Path(), jobs=1, fail_fast=False, report=None, alone=[], wide=[]
    )
    scheduler = parallel.Scheduler([("a", "b")], [], options, kept=schedule.Schedule())
    scheduler.board.pending.clear()
    scheduler.finished = [parallel.Finished("a", 5.0, 0, ""), parallel.Finished("b", 5.0, 0, "")]
    assert parallel.closing(scheduler, 10.0) == []
    assert capsys.readouterr().out.splitlines()[1:] == [
        "parallel: the lanes were full (1 lane, 10.0s of check time in 10.0s)"
    ]


def binding(root: Path, rows: dict[str, str], *, speed: str = "") -> Path:
    table = "".join(
        f"| {name} question | {tool} | 1 | | `just {name}` |\n" for name, tool in rows.items()
    )
    text = (
        "# Binding\n\n## Gates\n\n| Question | Tool | Tier | Threshold source | Entry point |\n"
        f"|---|---|---|---|---|\n{table}\n## Speed\n{speed}"
    )
    (root / "docs/process").mkdir(parents=True)
    (root / "docs/process/binding.md").write_text(text, encoding="utf-8")
    return root


def named(code: str, *args: object, name: str) -> str:
    return gate(code, *args, name)


def test_a_gate_the_binding_leaves_for_later_or_none_is_counted_apart_from_those_that_ran(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = binding(
        tmp_path,
        {
            "waits": "later: code:source",
            "skipped": "none: not a service",
            "bound": "later: code:source",
            "real": "a tool",
        },
    )
    later = named(SAY, "later: code:source", "", name="waits")
    none = named(SAY, "none: not a service", "", name="skipped")
    ran = named(SAY, "later: code:source", "and then judged something", name="bound")
    real = named(SAY, "none: 0 problems found", "", name="real")
    red = named("print('later: code:source'); raise SystemExit(2)", name="waits-red")
    assert run("--root", str(root), "--", later, none, ran, real, red) == 1
    out = capsys.readouterr().out
    assert f"parallel: {later}: not run yet (later: code:source) in " in out
    assert f"parallel: {none}: not applicable (none: not a service) in " in out
    assert f"parallel: {ran}: green in " in out
    assert f"parallel: {real}: green in " in out
    assert f"parallel: {red}: red (exit 2) in " in out
    assert (
        "(check time): 2 ran and passed, 1 failed; 1 not run yet, each waiting for what its line"
        " names; 1 not applicable, as its line says; slowest: "
    ) in re.sub(r"\(\d+\.\ds of check time\)", "(check time)", out)
    assert out.endswith(f"{red}: red (exit 2)\nparallel: red\n")


@pytest.mark.parametrize(
    "case",
    [
        ("`later: code:source`", "later: code:other", "not run yet"),
        ("ruff", "later: code:source", "not run yet"),
        (None, "later: code:source", "not run yet"),
        ("`later: code:source`", "  later: code:source  ", "not run yet"),
        ("`later: code:source`", "later: code:source\nlater: code:source", "green"),
        ("none: not a service", "none: not a service", "not applicable"),
        (None, "none: not a service", "not applicable"),
        ("ruff", "none: 0 problems found", "green"),
    ],
    ids=[
        "a later line its row does not name",
        "a later line from a row bound to a tool",
        "a later line from no row",
        "a later line with spaces around",
        "two later lines",
        "a none line from a none row",
        "a none line from no row",
        "a none line from a tool",
    ],
)
def test_one_later_line_has_not_run_whatever_the_row_and_a_none_line_ran_only_from_a_tool(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], case: tuple[str | None, str, str]
) -> None:
    tool, output, says = case
    root = binding(tmp_path, {"waits": tool} if tool else {"other": "ruff"})
    entry = named("import sys; print(sys.argv[1])", output, name="waits")
    assert run("--root", str(root), "--", entry) == 0
    assert f"parallel: {entry}: {says}" in capsys.readouterr().out


def test_a_run_where_no_check_ran_says_so_is_not_red_and_never_says_green(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = binding(tmp_path, {"one": "ruff", "two": "none: not a service"})
    one = named(SAY, "later: code:source", "", name="one")
    two = named(SAY, "none: not a service", "", name="two")
    assert run("--root", str(root), "--", one, two) == 0
    out = capsys.readouterr().out
    assert (
        ": 0 ran and passed; 1 not run yet, each waiting for what its line names;"
        " 1 not applicable, as its line says; slowest: "
    ) in out
    assert out.endswith("\nparallel: no check ran (1 not run yet, 1 not applicable)\n")
    assert "green" not in out.splitlines()[-1]


def test_the_last_line_counts_what_ran_and_what_waits(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = binding(tmp_path, {"one": "later: code:source"})
    one = named(SAY, "later: code:source", "", name="one")
    assert run("--root", str(root), "--", one, gate(EXIT, 0), gate(SAY, "ok", "")) == 0
    assert capsys.readouterr().out.endswith("\nparallel: green (2 ran, 1 not run yet)\n")


def test_fail_fast_stops_the_running_and_names_what_never_started(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    pid_file = tmp_path / "pid"
    sleeper, red, later = gate(HOLD, pid_file), gate(RED_ONCE_HELD, pid_file), gate(EXIT, 0)
    code = run("--jobs", "2", "--fail-fast", "--", sleeper, red, later)
    out = capsys.readouterr().out
    assert code == 1
    assert f"stopped by --fail-fast: {sleeper}" in out
    assert f"not started, --fail-fast: {later}" in out
    assert f"{red}: red (exit 1)" in out
    assert gone(int(pid_file.read_text()))


def test_fail_fast_does_not_start_the_rest_of_a_serial_group(
    capsys: pytest.CaptureFixture[str],
) -> None:
    red, rest = gate(EXIT, 1), gate(SAY, "never", "")
    assert run("--fail-fast", "--serial", red, rest, "--", red, rest) == 1
    out = capsys.readouterr().out
    assert "never\n" not in out
    assert f"not started, --fail-fast: {rest}" in out


def test_without_fail_fast_a_red_gate_leaves_the_others_to_finish(
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert run("--jobs", "1", "--", gate(EXIT, 1), gate(SAY, "ran", "")) == 1
    assert "ran\n" in capsys.readouterr().out


def test_the_runner_prefix_and_a_multi_word_entry_reach_the_command(
    capsys: pytest.CaptureFixture[str],
) -> None:
    echo = f"{PYTHON} -c 'import sys; print(*sys.argv[1:])'"
    assert run("--runner", echo, "--", "lint", "test-shard 1/4") == 0
    out = capsys.readouterr().out
    assert "lint\n" in out
    assert "test-shard 1/4\n" in out


def test_gates_run_in_the_root_and_the_report_lands_there(tmp_path: Path) -> None:
    where = gate("import os, pathlib; pathlib.Path('cwd').write_text(os.getcwd())")
    assert run("--root", str(tmp_path), "--report", "out/timing.jsonl", "--", where) == 0
    assert (tmp_path / "cwd").read_text() == os.path.realpath(tmp_path)
    assert (tmp_path / "out/timing.jsonl").is_file()


def test_the_report_has_the_records_budget_writes(tmp_path: Path) -> None:
    report = tmp_path / "timing.jsonl"
    ours = tmp_path / "ours.jsonl"
    green, red = gate(EXIT, 0), gate(EXIT, 2)
    assert run("--report", str(ours), "--", green, red) == 1
    outcome = budget.Outcome("lint", 1.0, None, 0)
    budget.report(report, outcome, runrecord.stamp(tmp_path, "tree"), root=tmp_path)
    template = json.loads(report.read_text())
    records = [
        json.loads(line) for line in ours.read_text().splitlines() if "key" in json.loads(line)
    ]
    assert {record["key"] for record in records} == {green, red}
    expected_keys = set(template) | {"lane", "start", "cpu", "alone"}
    assert all(set(record) == expected_keys for record in records)
    by_key = {record["key"]: record for record in records}
    assert by_key[green]["status"] == "unbounded"
    assert by_key[green]["exit"] == 0
    assert by_key[red]["status"] == "failed"
    assert by_key[red]["exit"] == 2
    assert all(record["ceiling"] is None and record["seconds"] >= 0 for record in records)
    rows = [json.loads(line) for line in ours.read_text().splitlines()]
    tiers = [row for row in rows if row.get("kind") == "tier"]
    assert len(tiers) == 1
    assert {"cores", "load", "k", "share", "m", "machine_read"} <= set(tiers[0])


def test_a_runner_that_cannot_start_is_a_red_gate(capsys: pytest.CaptureFixture[str]) -> None:
    assert run("--runner", "/no/such/runner", "--", "lint", "types") == 1
    out = capsys.readouterr().out
    assert out.count("cannot run /no/such/runner") == 2
    assert "lint: red (exit 127)" in out
    assert "types: red (exit 127)" in out


def test_a_gate_that_cannot_start_does_not_stop_the_gates_waiting_behind_it(
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert (
        run("--serial", "lint", "types", "--runner", "/no/such/runner", "--", "lint", "types") == 1
    )
    out = capsys.readouterr().out
    assert "lint: red (exit 127)" in out
    assert "types: red (exit 127)" in out


def test_a_gate_reads_nothing_from_the_terminal(capsys: pytest.CaptureFixture[str]) -> None:
    asks = gate("import sys; print(repr(sys.stdin.read()))")
    assert run("--", asks) == 0
    assert "''\n" in capsys.readouterr().out


def test_a_non_utf8_byte_in_the_output_is_replaced_not_fatal(
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert run("--", gate("import sys; sys.stdout.buffer.write(b'caf\\xe9\\n')")) == 0
    assert "caf�" in capsys.readouterr().out


@pytest.mark.parametrize(
    ("arguments", "message"),
    [
        (["--"], "no gates"),
        ([], "no gates"),
        (["--", "a", "a"], "named twice"),
        (["--", "a", "  "], "empty"),
        (["--", "a 'b"], "quotation"),
        (["--runner", "'", "--", "a"], "quotation"),
        (["--serial", "a", "x", "--", "a", "b"], "'x', which is not among"),
        (["--serial", "a", "b", "--serial", "b", "c", "--", "a", "b", "c"], "two --serial groups"),
        (["--jobs", "0", "--", "a"], "--jobs is at least 1"),
        (["--serial", "a", "b", "--alone", "b", "--", "a", "b"], "it runs alone already"),
    ],
)
def test_unusable_arguments_are_a_usage_error(
    arguments: list[str], message: str, capsys: pytest.CaptureFixture[str]
) -> None:
    with pytest.raises(SystemExit) as caught:
        run(*arguments)
    assert caught.value.code == 2
    assert message in capsys.readouterr().err


def test_a_gate_measured_alone_runs_after_the_others_with_nothing_beside_it(
    tmp_path: Path,
) -> None:
    log, a, b = tmp_path / "log", tmp_path / "a", tmp_path / "b"
    timed, also = gate(LOG, log, "timed"), gate(LOG, log, "also")
    first, beside = gate(MEET + LOG, a, b, log, "first"), gate(MEET, b, a)
    alone = ["--alone", timed, also, "not-in-this-tier"]
    assert run("--jobs", "4", *alone, "--", timed, first, also, beside) == 0
    assert log.read_text().splitlines() == [
        "+first",
        "-first",
        "+timed",
        "-timed",
        "+also",
        "-also",
    ]


JOBS = "import os, sys; print(sys.argv[1], os.environ.get('RATCHET_JOBS', 'unset'))"


def test_wide_gates_split_the_jobs_and_every_other_gate_keeps_its_environment(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("RATCHET_JOBS", raising=False)
    suite, floor, first, then = (gate(JOBS, name) for name in ("suite", "floor", "first", "then"))
    plain, timed = gate(JOBS, "plain"), gate(JOBS, "timed")
    wide = ["--wide", suite, floor, first, then, timed, "not-in-this-tier"]
    serial = ["--serial", first, then]
    arguments = ["--jobs", "7", *wide, *serial, "--alone", timed]
    assert run(*arguments, "--", suite, floor, first, then, plain, timed) == 0
    out = capsys.readouterr().out
    for said in ("suite 2", "floor 2", "first 2", "then 2", "plain unset", "timed 7"):
        assert f"\n{said}\n" in out


def stating(root: Path, lines: str) -> Path:
    (root / gatelib.BINDING).parent.mkdir(parents=True)
    (root / gatelib.BINDING).write_text(f"# Binding\n\n## Speed\n\n{lines}", encoding="utf-8")
    return root


def ran_at_once(log: Path) -> set[frozenset[str]]:
    running: set[str] = set()
    met: set[frozenset[str]] = set()
    for line in log.read_text().splitlines():
        sign, name = line[0], line[1:]
        if sign == "-":
            running.discard(name)
            continue
        met.update(frozenset((name, other)) for other in running)
        running.add(name)
    return met


PAIRED = (
    "import os, pathlib, sys, time\n"
    "log, name = sys.argv[-2:]\n"
    "marks = pathlib.Path(log).with_suffix('.marks')\n"
    "marks.mkdir(exist_ok=True)\n"
    "partner = {'a': 'b', 'b': 'a', 'e': 'f', 'f': 'e'}.get(name, 'nobody')\n"
    "os.write(os.open(log, os.O_WRONLY | os.O_APPEND | os.O_CREAT), f'+{name}\\n'.encode())\n"
    "(marks / name).touch()\n"
    "deadline = time.monotonic() + 1\n"
    "while not (marks / partner).exists() and time.monotonic() < deadline:\n"
    "    time.sleep(0.01)\n"
    "os.write(os.open(log, os.O_WRONLY | os.O_APPEND), f'-{name}\\n'.encode())\n"
)


def logged(tmp_path: Path, lines: str, *flags: str) -> set[frozenset[str]]:
    root, log = stating(tmp_path / "root", lines), tmp_path / "log"
    runner = shlex.join([sys.executable, "-c", PAIRED, str(log)])
    gates = ["a", "b", "c", "d", "e", "f"]
    assert run("--runner", runner, "--root", str(root), "--jobs", "6", *flags, "--", *gates) == 0
    return ran_at_once(log)


def never_beside(met: set[frozenset[str]], apart: list[str], alone: str) -> None:
    assert frozenset("ef") in met, "the log shows two gates that ran at once"
    for pair in apart:
        assert frozenset(pair) not in met, f"{pair} ran at once"
    assert not [pair for pair in met if alone in pair], f"{alone} ran beside another gate"


@pytest.mark.parametrize(
    "lines", ["", "- Apart: none\n- Alone: none\n", "- Apart: `c d`\n- Alone: `d`\n"]
)
def test_no_line_of_the_binding_takes_away_what_the_run_s_flags_keep(
    tmp_path: Path, lines: str
) -> None:
    met = logged(tmp_path, lines, "--serial", "a", "b", "--alone", "c")
    never_beside(met, ["ab", "cd"] if "`c d`" in lines else ["ab"], "c")


@pytest.mark.parametrize(
    ("lines", "flags", "apart", "alone"),
    [
        ("- Apart: `a b` `b c`\n", [], ["ab", "bc"], "none"),
        ("- Apart: `a b`\n- Alone: `c` `a`\n", [], ["ab"], "a"),
        ("- Apart: `a b`\n", ["--alone", "a"], ["ab"], "a"),
    ],
)
def test_groups_that_share_a_gate_and_a_gate_alone_and_in_a_group_are_kept_and_not_refused(
    tmp_path: Path, *, lines: str, flags: list[str], apart: list[str], alone: str
) -> None:
    never_beside(logged(tmp_path, lines, *flags), apart, alone)


def test_a_flag_the_binding_does_not_state_is_named_with_the_line_that_would(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = stating(tmp_path, "- Apart: `a b`\n- Alone: `x`\n- Wide: none\n")
    flags = ["--runner", "echo", "--root", str(root), "--serial", "b", "a", "--serial", "c", "d"]
    assert run(*flags, "--alone", "x", "e", "--wide", "c", "--", *"abcdex") == 0
    said = [line for line in capsys.readouterr().out.splitlines() if "does not say it" in line]
    assert said == [
        f"parallel: the recipe passes {flag} {passed} to this run and the binding's Speed section"
        f" does not say it; this run keeps both, and the line - {line} there would state it for"
        " good"
        for flag, passed, line in (
            ("--serial", "`c d`", "Apart: `a b` `c d`"),
            ("--alone", "`e`", "Alone: `x` `e`"),
            ("--wide", "`c`", "Wide: `c`"),
        )
    ]


@pytest.mark.parametrize("lines", ["", "- Apart: `b a`\n- Alone: `e`\n- Wide: <none yet>\n"])
def test_flags_the_binding_agrees_with_or_a_binding_that_states_nothing_are_said_nothing_of(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], lines: str
) -> None:
    root = stating(tmp_path, lines)
    flags = ["--runner", "echo", "--root", str(root), "--serial", "a", "b", "--alone", "e"]
    assert run(*flags, "--serial", "c", "d", "--wide", "c", "--", *"abcde") == 0
    out = capsys.readouterr().out
    assert ("does not say it" in out) is bool(lines)
    assert "--serial `a b`" not in out
    assert "--alone" not in out


def test_a_gate_the_binding_states_wide_is_told_its_share_of_the_jobs(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("RATCHET_JOBS", raising=False)
    root = stating(tmp_path, "- Wide: `a b`\n")
    runner = shlex.join([sys.executable, "-c", JOBS])
    assert run("--runner", runner, "--root", str(root), "--jobs", "6", "--", "a", "b", "c") == 0
    out = capsys.readouterr().out
    for said in ("a 3", "b 3", "c unset"):
        assert f"\n{said}\n" in out


def test_a_wide_gate_gets_at_least_one_job(capsys: pytest.CaptureFixture[str]) -> None:
    a, b = gate(JOBS, "a"), gate(JOBS, "b")
    assert run("--jobs", "1", "--wide", a, b, "--", a, b) == 0
    out = capsys.readouterr().out
    assert "\na 1\n" in out
    assert "\nb 1\n" in out


def test_fail_fast_names_a_gate_measured_alone_that_never_started(
    capsys: pytest.CaptureFixture[str],
) -> None:
    red, timed = gate(EXIT, 1), gate(SAY, "timed", "")
    assert run("--fail-fast", "--alone", timed, "--", timed, red) == 1
    out = capsys.readouterr().out
    assert f"not started, --fail-fast: {timed}" in out
    assert "timed\n" not in out


def test_a_gate_that_cannot_start_alone_is_red_and_the_next_one_runs(
    capsys: pytest.CaptureFixture[str],
) -> None:
    after = gate(SAY, "after", "")
    assert run("--alone", "no-such-command-here", after, "--", "no-such-command-here", after) == 1
    out = capsys.readouterr().out
    assert "no-such-command-here: red (exit 127)" in out
    assert "after\n" in out


def test_what_a_gate_left_running_is_ended_with_it_and_said(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    forked = tmp_path / "forked"
    leaky = f"sh -c 'sleep {PATIENCE:g} > /dev/null 2>&1 & echo $! > {forked}'"
    tidy = gate(SAY, "tidy", "")
    assert run("--", leaky, tidy) == 0
    out = capsys.readouterr().out
    assert gone(int(forked.read_text()))
    assert f"parallel: {leaky}: something it started was still running when it exited" in out
    assert f"parallel: {tidy}: something" not in out


def test_a_group_the_runner_may_not_signal_is_left_and_not_reported(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def refuse(_pid: int, _signum: int) -> None:
        raise PermissionError

    monkeypatch.setattr(os, "killpg", refuse)
    assert not gatelib.end_group(os.getpid())


def test_a_serial_group_launches_where_its_first_gate_stands() -> None:
    units = parallel.plan(["a", "b", "c", "d"], [["c", "a"]])
    assert units == [("c", "a"), ("b",), ("d",)]


TICKET_TIER = {
    "lint": 36.4,
    "fmt-check": 13.4,
    "types": 12.4,
    "imports": 2.4,
    "deadcode": 4.6,
    "lint-workflows": 3.4,
    "secrets": 1.2,
    "spelling": 2.1,
    "links": 1.8,
    "licenses": 1.5,
    "markers": 1.2,
    "sizes": 1.1,
    "decisions": 0.9,
    "headers": 0.7,
    "env": 0.6,
    "limits": 21.3,
}


def ends(units: list[tuple[str, ...]], seconds: dict[str, float], lanes: int) -> float:
    free = [0.0] * lanes
    for unit in units:
        lane = free.index(min(free))
        free[lane] += sum(seconds[name] for name in unit)
    return max(free)


def test_a_tier_whose_longest_gate_is_declared_last_ends_near_the_bound_on_two_lanes() -> None:
    declared = list(TICKET_TIER)
    assert declared[-1] == "limits"
    bound = max(sum(TICKET_TIER.values()) / 2, *TICKET_TIER.values())
    today = ends(parallel.plan(declared, [], seconds={}), TICKET_TIER, 2)
    planned = ends(parallel.plan(declared, [], seconds=TICKET_TIER), TICKET_TIER, 2)
    assert today > bound + 10, f"declared order ends at {today:.1f}s"
    assert planned <= bound + min(TICKET_TIER.values()), (
        f"longest first ends at {planned:.1f}s, the bound is {bound:.1f}s"
    )


def test_with_no_record_the_gates_start_in_the_order_given() -> None:
    declared = list(TICKET_TIER)
    assert parallel.plan(declared, [], seconds={}) == [(name,) for name in declared]


def test_gates_with_the_same_recorded_time_start_in_the_order_given() -> None:
    seconds = {"a": 5.0, "b": 5.0, "c": 5.0, "d": 9.0}
    assert parallel.plan(["a", "b", "c", "d"], [], seconds=seconds) == [
        ("d",),
        ("a",),
        ("b",),
        ("c",),
    ]


def test_a_gate_with_no_record_starts_before_the_recorded_ones_in_the_order_given() -> None:
    seconds = {"b": 1.0, "d": 30.0}
    units = parallel.plan(["a", "b", "c", "d", "e"], [], seconds=seconds)
    assert units == [("a",), ("c",), ("e",), ("d",), ("b",)]


def test_a_serial_group_counts_as_the_sum_of_its_gates_and_keeps_its_order() -> None:
    seconds = {"a": 6.0, "b": 2.0, "c": 4.0, "d": 5.0}
    units = parallel.plan(["a", "b", "c", "d"], [["d", "b"]], seconds=seconds)
    assert units == [("d", "b"), ("a",), ("c",)]


def recorded(path: Path, *runs: tuple[str, float]) -> None:
    for name, seconds in runs:
        outcome = budget.Outcome(name, seconds, None, 0)
        budget.report(path, outcome, runrecord.stamp(path.parent, "tree"), root=path.parent)


def test_a_tier_starts_the_gates_its_record_says_take_longest_first(tmp_path: Path) -> None:
    log, report = tmp_path / "log", tmp_path / "timing.jsonl"
    short, middle, long = (gate(LOG, log, name) for name in ("short", "middle", "long"))
    recorded(report, (short, 1.0), (middle, 2.0), (long, 3.0))
    assert run("--jobs", "1", "--report", str(report), "--", short, middle, long) == 0
    starts = [line for line in log.read_text().splitlines() if line.startswith("+")]
    assert starts == ["+long", "+middle", "+short"]


def test_a_gate_s_expected_time_is_the_median_of_its_last_runs(tmp_path: Path) -> None:
    log, report = tmp_path / "log", tmp_path / "timing.jsonl"
    once, steady = gate(LOG, log, "once"), gate(LOG, log, "steady")
    recorded(report, *((once, 90.0),) * 5, (steady, 3.0))
    recorded(report, (once, 1.0), (once, 60.0), (once, 1.0), (once, 1.0), (once, 1.0))
    assert run("--jobs", "1", "--report", str(report), "--", once, steady) == 0
    starts = [line for line in log.read_text().splitlines() if line.startswith("+")]
    assert starts == ["+steady", "+once"]


DEEP = b"[" * 100_000 + b"]" * 100_000


def seconds_of_a(value: bytes) -> bytes:
    return b'{"key": FIRST, "seconds": ' + value + b"}\n"


@pytest.mark.parametrize(
    "content",
    [
        None,
        b"",
        b"\0\xff\xfe not a record\n",
        (
            b'{not json\n[1, 2]\n{"seconds": 9}\n'
            b'{"key": 3, "seconds": 9}\n{"key": "x", "seconds": "slow"}\n'
        ),
        DEEP + b"\n",
        seconds_of_a(DEEP),
        seconds_of_a(b"9" * 309),
        seconds_of_a(b"9" * 5000),
        seconds_of_a(b"true"),
        seconds_of_a(b"NaN") + seconds_of_a(b"Infinity") + seconds_of_a(b"-Infinity"),
        seconds_of_a(b"-5"),
        seconds_of_a(b"0"),
    ],
    ids=[
        "missing",
        "empty",
        "binary",
        "garbled",
        "nested-deeply",
        "seconds-nested-deeply",
        "seconds-past-a-float",
        "seconds-past-the-digit-limit",
        "seconds-a-boolean",
        "seconds-not-finite",
        "seconds-negative",
        "seconds-zero",
    ],
)
def test_a_record_that_cannot_be_read_leaves_the_order_given(
    tmp_path: Path, content: bytes | None, capsys: pytest.CaptureFixture[str]
) -> None:
    log, report = tmp_path / "log", tmp_path / "timing.jsonl"
    names = ["a", "b", "c"]
    gates = [gate(LOG, log, name) for name in names]
    if content is not None:
        report.write_bytes(content.replace(b"FIRST", json.dumps(gates[0]).encode()))
    assert run("--jobs", "1", "--report", str(report), "--", *gates) == 0
    starts = [line for line in log.read_text().splitlines() if line.startswith("+")]
    assert starts == ["+a", "+b", "+c"]
    assert capsys.readouterr().out.endswith("parallel: green (3 ran)\n")


def test_gates_measured_alone_run_last_in_the_order_given_whatever_the_record(
    tmp_path: Path,
) -> None:
    log, report = tmp_path / "log", tmp_path / "timing.jsonl"
    timed, also = gate(LOG, log, "timed"), gate(LOG, log, "also")
    short, long = gate(LOG, log, "short"), gate(LOG, log, "long")
    recorded(report, (timed, 50.0), (also, 90.0), (short, 1.0), (long, 2.0))
    arguments = ["--jobs", "1", "--report", str(report), "--alone", timed, also]
    assert run(*arguments, "--", timed, short, also, long) == 0
    starts = [line for line in log.read_text().splitlines() if line.startswith("+")]
    assert starts == ["+long", "+short", "+timed", "+also"]


JSON = st.recursive(
    st.none() | st.booleans() | st.integers() | st.floats() | st.text(),
    lambda inner: st.lists(inner) | st.dictionaries(st.text(), inner),
    max_leaves=8,
)
NOT_A_TIME = JSON.filter(
    lambda value: (
        isinstance(value, bool) or not isinstance(value, int | float) or not 0 < value < math.inf
    )
)
LINES = st.lists(
    st.binary()
    | JSON.map(json.dumps).map(str.encode)
    | NOT_A_TIME.map(lambda value: json.dumps({"key": "a", "seconds": value}).encode()),
    max_size=8,
)


@given(lines=LINES)
def test_no_record_stops_a_tier_and_no_line_that_is_not_a_time_gives_one(
    tmp_path_factory: pytest.TempPathFactory, lines: list[bytes]
) -> None:
    report = tmp_path_factory.mktemp("record") / "timing.jsonl"
    report.write_bytes(b"\n".join(lines))
    seconds = parallel.expected_seconds(report)
    assert "a" not in seconds, f"a line that is not a time gave a {seconds['a']}s"
    assert all(math.isfinite(value) and value > 0 for value in seconds.values()), seconds
    units = parallel.plan(["a", "b", "c"], [], seconds=seconds)
    assert sorted(name for unit in units for name in unit) == ["a", "b", "c"]


RED_LOG = LOG + "sys.exit(1)\n"


def test_fail_fast_starts_the_gates_in_the_order_given_whatever_the_record(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    log, report = tmp_path / "log", tmp_path / "timing.jsonl"
    red = gate(RED_LOG, log, "red")
    middle, long = gate(LOG, log, "middle"), gate(LOG, log, "long")
    recorded(report, (red, 0.1), (middle, 2.0), (long, 3.0))
    assert run("--jobs", "1", "--fail-fast", "--report", str(report), "--", red, middle, long) == 1
    starts = [line for line in log.read_text().splitlines() if line.startswith("+")]
    assert starts == ["+red"]
    assert f"not started, --fail-fast: {middle}, {long}\n" in capsys.readouterr().out


def test_a_record_far_larger_than_its_last_runs_is_read_only_at_its_end(tmp_path: Path) -> None:
    log, report = tmp_path / "log", tmp_path / "timing.jsonl"
    early, a, b = (gate(LOG, log, name) for name in ("early", "a", "b"))
    recorded(report, *((early, 0.5),) * 50)
    with report.open("r+b") as handle:
        handle.truncate(handle.seek(0, os.SEEK_END) + 64 * 2**20)
    with report.open("ab") as handle:
        handle.write(b"\n")
    recorded(report, (a, 3.0), (b, 2.0))
    assert run("--jobs", "1", "--report", str(report), "--", a, b, early) == 0
    starts = [line for line in log.read_text().splitlines() if line.startswith("+")]
    assert starts == ["+early", "+a", "+b"], "a time 64 MiB before the end was read"


def test_a_record_whose_window_starts_on_a_line_keeps_that_line(tmp_path: Path) -> None:
    log, report = tmp_path / "log", tmp_path / "timing.jsonl"
    first, other = gate(LOG, log, "first"), gate(LOG, log, "other")
    lines = b"".join(
        json.dumps({"key": name, "seconds": seconds}).encode() + b"\n"
        for name, seconds in ((first, 1.0), (other, 3.0))
    )
    report.write_bytes(b"{}\n" + lines + b"\n" * (parallel.WINDOW - len(lines)))
    assert run("--jobs", "1", "--report", str(report), "--", first, other) == 0
    starts = [line for line in log.read_text().splitlines() if line.startswith("+")]
    assert starts == ["+other", "+first"], "the whole first line of the window was dropped"


def test_a_record_that_may_not_be_read_leaves_the_order_given(tmp_path: Path) -> None:
    log, report = tmp_path / "log", tmp_path / "timing.jsonl"
    short, long = gate(LOG, log, "short"), gate(LOG, log, "long")
    recorded(report, (short, 1.0), (long, 3.0))
    report.chmod(0o200)
    try:
        assert run("--jobs", "1", "--report", str(report), "--", short, long) == 0
    finally:
        report.chmod(0o600)
    starts = [line for line in log.read_text().splitlines() if line.startswith("+")]
    assert starts == ["+short", "+long"]


def test_a_record_that_is_a_pipe_leaves_the_order_given(tmp_path: Path) -> None:
    log, report = tmp_path / "log", tmp_path / "timing.jsonl"
    gates = [gate(LOG, log, name) for name in ("a", "b", "c")]
    os.mkfifo(report)
    seen: list[list[str]] = []
    held: list[int] = []

    def lines() -> list[str]:
        return log.read_text().splitlines() if log.exists() else []

    def release() -> None:
        until(lambda: any(line.startswith("+") for line in lines()))
        seen.append([line for line in lines() if line.startswith("+")])
        held.append(os.open(report, os.O_RDWR | os.O_NONBLOCK))

    thread = threading.Thread(target=release)
    thread.start()
    try:
        assert run("--jobs", "1", "--report", str(report), "--", *gates) == 0
    finally:
        thread.join()
        for handle in held:
            os.close(handle)
    assert seen[0] == ["+a"], f"gates started before the pipe was opened: {seen[0]}"
    assert [line for line in lines() if line.startswith("+")] == ["+a", "+b", "+c"]


def test_a_signal_stops_every_gate_and_what_they_forked(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    held, forked = tmp_path / "held", tmp_path / "forked"
    child = f"sh -c 'sleep {PATIENCE:g} & echo $! > {forked}; wait'"
    ready = [held, forked]

    def interrupt() -> None:
        assert until(lambda: all(p.is_file() and p.read_text() != "" for p in ready))
        os.kill(os.getpid(), signal.SIGINT)

    thread = threading.Thread(target=interrupt)
    thread.start()
    code = run("--", gate(HOLD, held), child)
    thread.join()
    out = capsys.readouterr().out
    assert code == 128 + signal.SIGINT
    assert "interrupted by signal" in out
    assert gate(HOLD, held) in out
    assert gone(int(held.read_text()))
    assert gone(int(forked.read_text()))


def test_the_script_run_for_real_stops_its_gates_on_sigterm(tmp_path: Path) -> None:
    held = tmp_path / "held"
    script = Path(parallel.__file__).resolve()
    process = subprocess.Popen(  # noqa: S603 # -- a fixed argv, no shell
        [sys.executable, str(script), "--", gate(HOLD, held)],
        stdout=subprocess.PIPE,
        text=True,
    )
    pid = pid_of(held)
    process.send_signal(signal.SIGTERM)
    out, _ = process.communicate(timeout=30)
    assert process.returncode == 128 + signal.SIGTERM
    assert "interrupted by signal 15" in out
    assert gone(pid)


def test_parallel_records_lane_start_cpu_and_alone_for_each_gate(tmp_path: Path) -> None:
    record = tmp_path / "timing.jsonl"
    a, b = tmp_path / "a", tmp_path / "b"
    g1, g2 = gate(MEET, a, b), gate(MEET, b, a)
    solo = gate(EXIT, 0)
    assert run("--jobs", "2", "--alone", solo, "--report", str(record), "--", g1, g2, solo) == 0
    rows = [json.loads(line) for line in record.read_text().splitlines()]
    by_key = {row["key"]: row for row in rows if "key" in row}
    assert {by_key[g1]["lane"], by_key[g2]["lane"]} == {0, 1}
    assert by_key[g1]["alone"] is False
    assert by_key[g2]["alone"] is False
    assert by_key[solo]["alone"] is True
    assert by_key[solo]["lane"] == 0
    assert all(isinstance(by_key[k]["start"], float) and by_key[k]["start"] >= 0.0 for k in by_key)
    assert all(by_key[k]["cpu"] is None or by_key[k]["cpu"] >= 0.0 for k in by_key)


def test_gate_exit3_beside_detached_work_keeps_tier_red_with_exit3(tmp_path: Path) -> None:
    record = tmp_path / "timing.jsonl"
    detached = tmp_path / "detached.pid"
    detach_gate = gate(
        "import os, pathlib, sys, time\n"
        "if os.fork() == 0:\n"
        "    os.setsid()\n"
        "    pathlib.Path(sys.argv[1]).write_text(str(os.getpid()))\n"
        "    time.sleep(0.2)\n"
        "    os._exit(0)\n",
        detached,
    )
    red_gate = gate("import time; time.sleep(0.05); raise SystemExit(3)")
    try:
        assert run("--jobs", "2", "--report", str(record), "--", detach_gate, red_gate) == 1
        rows = [json.loads(line) for line in record.read_text().splitlines()]
        by_key = {row["key"]: row for row in rows if "key" in row}
        assert by_key[red_gate]["exit"] == 3
        assert by_key[red_gate]["status"] == "failed"
        [tier] = [row for row in rows if row.get("kind") == "tier"]
        assert (tier["status"], tier["exit"]) == ("red", 1)
    finally:
        if detached.is_file():
            with contextlib.suppress(ProcessLookupError, ValueError):
                os.kill(int(detached.read_text()), signal.SIGKILL)


def test_collect_takes_two_arguments_and_stop_running_skips_nonpositive_pid(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FakeProcess:
        pid = 0

    gate_live = parallel.Live(
        "fake",
        FakeProcess(),  # type: ignore[arg-type]
        io.BytesIO(b"hello\n"),
        time.monotonic(),
        tool=False,
    )
    gate_live.cpu = 0.25
    finished = parallel.collect(gate_live, 0)
    assert finished.name == "fake"
    assert finished.exit_code == 0
    assert finished.cpu == 0.25

    options = argparse.Namespace(
        root=Path(), jobs=1, fail_fast=False, report=None, alone=[], wide=[]
    )
    scheduler = parallel.Scheduler([("fake",)], [], options, kept=schedule.Schedule())
    log = io.BytesIO()
    scheduler.live["fake"] = parallel.Live(
        "fake",
        FakeProcess(),  # type: ignore[arg-type]
        log,
        time.monotonic(),
        tool=False,
    )
    signaled: list[int] = []
    monkeypatch.setattr(os, "killpg", lambda pid, _sig: signaled.append(pid))
    monkeypatch.setattr(gatelib, "stop_group", lambda proc: signaled.append(proc.pid))
    scheduler.stop_running()
    assert signaled == []
    assert scheduler.stopped == ["fake"]
    assert scheduler.live == {}
    assert log.closed


def test_budget_loop_judges_within_over_and_not_judged_as_the_last_line(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    report = tmp_path / ".ratchet/timing.jsonl"
    ok = gate(EXIT, 0)
    assert (
        run(
            "--root",
            str(tmp_path),
            "--report",
            ".ratchet/timing.jsonl",
            "--budget",
            "loop",
            "--",
            ok,
        )
        == 0
    )
    lines = capsys.readouterr().out.splitlines()
    assert lines[-2] == "parallel: green (1 ran)"
    assert lines[-1].startswith("loop: not judged: no budget:")
    tier = [json.loads(line) for line in report.read_text().splitlines()][-1]
    assert tier["judgement"] == "not judged"
    assert tier["reason"] == "no budget is declared"
    assert tier["budget"] is None

    (tmp_path / "quality").mkdir()
    (tmp_path / "quality/thresholds.toml").write_text(
        "[ceilings.loop]\nseconds = 60\n", encoding="utf-8"
    )
    assert (
        run(
            "--root",
            str(tmp_path),
            "--report",
            ".ratchet/timing.jsonl",
            "--budget",
            "loop",
            "--",
            ok,
        )
        == 0
    )
    lines = capsys.readouterr().out.splitlines()
    assert lines[-2] == "parallel: green (1 ran)"
    assert lines[-1].startswith("loop: within:")

    (tmp_path / "quality/thresholds.toml").write_text(
        "[ceilings.loop]\nseconds = 0.05\n", encoding="utf-8"
    )
    slow = gate("import time; time.sleep(0.15)")
    assert (
        run(
            "--root",
            str(tmp_path),
            "--report",
            ".ratchet/timing.jsonl",
            "--budget",
            "loop",
            "--",
            slow,
        )
        == 1
    )
    lines = capsys.readouterr().out.splitlines()
    assert lines[-2] == "parallel: green (1 ran)"
    assert lines[-1].startswith("loop: over:")
    tier = [json.loads(line) for line in report.read_text().splitlines()][-1]
    assert (tier["status"], tier["exit"], tier["judgement"]) == ("red", 1, "over")


def test_budget_loop_skips_early_fail_fast_and_preserves_non_budget_behavior(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    (tmp_path / "quality").mkdir()
    (tmp_path / "quality/thresholds.toml").write_text(
        "[ceilings.loop]\nseconds = 0.01\n", encoding="utf-8"
    )
    report = tmp_path / ".ratchet/timing.jsonl"
    slow = gate("import time; time.sleep(0.05)")
    assert run("--root", str(tmp_path), "--report", ".ratchet/timing.jsonl", "--", slow) == 0
    lines = capsys.readouterr().out.splitlines()
    assert lines[-1] == "parallel: green (1 ran)"
    assert not any(line.startswith("loop:") for line in lines)
    tier = [json.loads(line) for line in report.read_text().splitlines()][-1]
    assert "judgement" not in tier

    (tmp_path / "quality/thresholds.toml").write_text(
        "[ceilings.loop]\nseconds = 60\n", encoding="utf-8"
    )
    red = gate(EXIT, 3)
    assert (
        run(
            "--root",
            str(tmp_path),
            "--fail-fast",
            "--jobs",
            "1",
            "--budget",
            "loop",
            "--",
            red,
            slow,
        )
        == 1
    )
    out = capsys.readouterr().out
    assert "loop:" not in out
    assert run("--root", str(tmp_path), "--fail-fast", "--budget", "loop", "--", gate(EXIT, 0)) == 0
    assert capsys.readouterr().out.splitlines()[-1].startswith("loop: within:")
    assert run("--root", str(tmp_path), "--budget", "loop", "--", red) == 1
    lines = capsys.readouterr().out.splitlines()
    assert lines[-2] == "parallel: red"
    assert lines[-1].startswith("loop: within:")


def test_slowest_gate_over_median_with_declared_profile_command_prints_hint_before_verdict_and_loop(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    speed = (
        "- Profile: `lint: linter --stats` `no-colon` `: no-gate` `sast:` `sast: scanner --time`\n"
    )
    root = binding(tmp_path, {"lint": "linter", "sast": "scanner"}, speed=speed)
    (root / "quality").mkdir()
    (root / "quality/thresholds.toml").write_text(
        "[ceilings.loop]\nseconds = 60\n", encoding="utf-8"
    )
    options = argparse.Namespace(
        root=root, jobs=2, fail_fast=False, report=None, alone=[], wide=[], budget="loop"
    )
    scheduler = parallel.Scheduler([], [], options, kept=schedule.Schedule())
    scheduler.finished = [
        parallel.Finished("lint", 1.0, 0, ""),
        parallel.Finished("sast", 5.0, 0, ""),
    ]
    assert parallel.finish_run(scheduler, options, 5.0, gates=["lint", "sast"]) == 0
    lines = capsys.readouterr().out.splitlines()
    hint = (
        "parallel: sast took 5.0s (tier median 3.0s);"
        " profile it with `scanner --time` to see where its time goes"
    )
    assert hint in lines
    assert lines.index(hint) < lines.index("parallel: green (2 ran)")
    assert lines[-1].startswith("loop: within:")


@pytest.mark.parametrize(
    "speed",
    [
        "",
        "- Profile: <each slow gate's profiling command as `gate: command`; leave while none>\n",
        "- Profile: `other: other --time`\n",
    ],
)
def test_bound_slowest_gate_without_profile_command_prints_fallback_hint(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], speed: str
) -> None:
    root = binding(tmp_path, {"lint": "linter", "sast": "scanner"}, speed=speed)
    (root / "quality").mkdir()
    (root / "quality/thresholds.toml").write_text(
        "[ceilings.loop]\nseconds = 60\n", encoding="utf-8"
    )
    options = argparse.Namespace(
        root=root, jobs=2, fail_fast=False, report=None, alone=[], wide=[], budget="loop"
    )
    scheduler = parallel.Scheduler([], [], options, kept=schedule.Schedule())
    scheduler.finished = [
        parallel.Finished("lint", 1.0, 0, ""),
        parallel.Finished("sast", 5.0, 0, ""),
    ]
    assert parallel.finish_run(scheduler, options, 5.0, gates=["lint", "sast"]) == 0
    lines = capsys.readouterr().out.splitlines()
    hint = (
        "parallel: sast took 5.0s (tier median 3.0s);"
        " run its tool with its profiling flag (and record `sast: <command>` under"
        " Speed -> Profile in binding.md) to see where its time goes"
    )
    assert hint in lines
    assert lines.index(hint) < lines.index("parallel: green (2 ran)")
    assert lines[-1].startswith("loop: within:")


def test_single_gate_or_at_or_below_median_prints_no_profile_hint(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = binding(
        tmp_path,
        {"lint": "linter", "sast": "scanner"},
        speed="- Profile: `sast: scanner --time`\n",
    )
    options = argparse.Namespace(root=root, jobs=2, fail_fast=False, report=None, alone=[], wide=[])
    single = parallel.Scheduler([], [], options, kept=schedule.Schedule())
    single.finished = [parallel.Finished("sast", 5.0, 0, "")]
    assert parallel.closing(single, 5.0) == []
    assert "profile" not in capsys.readouterr().out

    tied = parallel.Scheduler([], [], options, kept=schedule.Schedule())
    tied.finished = [
        parallel.Finished("lint", 4.0, 0, ""),
        parallel.Finished("sast", 4.0, 0, ""),
    ]
    assert parallel.closing(tied, 4.0) == []
    assert "profile" not in capsys.readouterr().out


@pytest.mark.parametrize(
    "tool_cell",
    ["none: not a service", "later: code:source", ""],
)
def test_waiting_unbound_or_missing_binding_prints_no_profile_hint(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], tool_cell: str
) -> None:
    bare = tmp_path / "bare"
    bare.mkdir()
    bare_options = argparse.Namespace(
        root=bare, jobs=2, fail_fast=False, report=None, alone=[], wide=[]
    )
    bare_scheduler = parallel.Scheduler([], [], bare_options, kept=schedule.Schedule())
    bare_scheduler.finished = [
        parallel.Finished("fast", 1.0, 0, ""),
        parallel.Finished("slow", 5.0, 0, ""),
    ]
    assert parallel.closing(bare_scheduler, 5.0) == []
    assert "profile" not in capsys.readouterr().out

    bound = binding(tmp_path / "bound", {"fast": "linter", "slow": tool_cell})
    bound_options = argparse.Namespace(
        root=bound, jobs=2, fail_fast=False, report=None, alone=[], wide=[]
    )
    bound_scheduler = parallel.Scheduler([], [], bound_options, kept=schedule.Schedule())
    bound_scheduler.finished = [
        parallel.Finished("fast", 1.0, 0, ""),
        parallel.Finished("slow", 5.0, 0, ""),
    ]
    assert parallel.closing(bound_scheduler, 5.0) == []
    assert "profile" not in capsys.readouterr().out


def test_main_prints_profile_hint_for_slow_bound_gate_before_verdict(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = binding(
        tmp_path,
        {"lint": "linter", "sast": "scanner"},
        speed="- Profile: `sast: scanner --time`\n",
    )
    fast = named(EXIT, 0, name="lint")
    slow = named("import sys, time; time.sleep(0.15)", name="sast")
    assert run("--root", str(root), "--", fast, slow) == 0
    lines = capsys.readouterr().out.splitlines()
    hints = [line for line in lines if "; profile it with `scanner --time` " in line]
    assert len(hints) == 1
    assert hints[0].startswith(f"parallel: {slow} took ")
    assert lines.index(hints[0]) < lines.index("parallel: green (2 ran)")


@pytest.mark.parametrize(
    ("speed", "expected"),
    [
        ("", {}),
        ("- Profile:\n", {}),
        ("- Profile: <placeholder `sast: scanner --time`>\n", {}),
        ("- Profile: none\n", {}),
        (
            "- Profile: `lint: linter --stats` `bad` `: missing` `sast:` `sast: scanner --time`\n",
            {"lint": "linter --stats", "sast": "scanner --time"},
        ),
    ],
)
def test_profile_commands_parses_valid_entries_and_ignores_placeholders(
    tmp_path: Path, speed: str, expected: dict[str, str]
) -> None:
    root = binding(tmp_path, {"lint": "linter"}, speed=speed)
    assert parallel.profile_commands(root) == expected


def test_wall_clock_line_covers_single_gate_alone_order_loss_and_full_lanes(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    options = argparse.Namespace(
        root=tmp_path, jobs=4, fail_fast=False, report=None, alone=[], wide=[]
    )
    single_gate = parallel.Scheduler([("lint",)], [], options, kept=schedule.Schedule())
    single_gate.finished = [parallel.Finished("lint", 10.0, 0, "")]
    assert parallel.wall_clock_line(single_gate, 10.0) == (
        "parallel: one gate lint (10.0s) set the wall clock while other lanes stood idle"
        " (30.0s idle across 4 lanes)"
    )

    alone_opts = argparse.Namespace(
        root=tmp_path, jobs=4, fail_fast=False, report=None, alone=["perf"], wide=[]
    )
    alone_sched = parallel.Scheduler(
        [("lint",)], [], alone_opts, kept=schedule.Schedule({"Alone": (("perf",),)})
    )
    alone_sched.finished = [
        parallel.Finished("lint", 1.0, 0, ""),
        parallel.Finished("perf", 20.0, 0, ""),
    ]
    assert parallel.wall_clock_line(alone_sched, 21.0) == (
        "parallel: one gate perf (20.0s, --alone) set the wall clock while other lanes stood"
        " idle (63.0s idle across 4 lanes)"
    )

    multi_alone_opts = argparse.Namespace(
        root=tmp_path, jobs=4, fail_fast=False, report=None, alone=["p1", "p2"], wide=[]
    )
    multi_alone = parallel.Scheduler(
        [("lint",)],
        [],
        multi_alone_opts,
        kept=schedule.Schedule({"Alone": (("p1",), ("p2",))}),
    )
    multi_alone.finished = [
        parallel.Finished("lint", 1.0, 0, ""),
        parallel.Finished("p1", 15.0, 0, ""),
        parallel.Finished("p2", 15.0, 0, ""),
    ]
    assert parallel.wall_clock_line(multi_alone, 31.0) == (
        "parallel: --alone gates (30.0s) set the wall clock while other lanes stood idle"
        " (93.0s idle across 4 lanes)"
    )

    only_alone = parallel.Scheduler(
        [],
        [],
        multi_alone_opts,
        kept=schedule.Schedule({"Alone": (("p1",), ("p2",))}),
    )
    only_alone.finished = [
        parallel.Finished("p1", 15.0, 0, ""),
        parallel.Finished("p2", 15.0, 0, ""),
    ]
    assert (
        parallel.best_schedule(
            only_alone.finished, groups=[], alone=only_alone.measured_alone, jobs=4
        )
        == 30.0
    )

    two_lane_opts = argparse.Namespace(
        root=tmp_path, jobs=2, fail_fast=False, report=None, alone=[], wide=[]
    )
    order_lost = parallel.Scheduler(
        [("a",), ("b",), ("c",)], [], two_lane_opts, kept=schedule.Schedule()
    )
    order_lost.finished = [
        parallel.Finished("a", 10.0, 0, ""),
        parallel.Finished("b", 10.0, 0, ""),
        parallel.Finished("c", 20.0, 0, ""),
    ]
    assert parallel.wall_clock_line(order_lost, 30.0) == (
        "parallel: the order lost 10.0s against the best schedule (20.0s across 2 lanes)"
    )

    saturated = parallel.Scheduler(
        [("a",), ("b",), ("c",), ("d",)], [], two_lane_opts, kept=schedule.Schedule()
    )
    saturated.finished = [
        parallel.Finished("a", 10.0, 0, ""),
        parallel.Finished("b", 10.0, 0, ""),
        parallel.Finished("c", 10.0, 0, ""),
        parallel.Finished("d", 10.0, 0, ""),
    ]
    assert parallel.wall_clock_line(saturated, 20.0) == (
        "parallel: the lanes were full (2 lanes, 40.0s of check time in 20.0s)"
    )

    overlap_kept = schedule.Schedule({"Apart": (("a", "b"), ("b", "c"))})
    overlap_best = parallel.best_schedule(
        [
            parallel.Finished("a", 4.0, 0, ""),
            parallel.Finished("b", 6.0, 0, ""),
            parallel.Finished("c", 3.0, 0, ""),
        ],
        groups=list(overlap_kept.apart),
        alone=frozenset(),
        jobs=2,
    )
    assert overlap_best == 10.0

    (tmp_path / "quality").mkdir()
    (tmp_path / "quality/thresholds.toml").write_text(
        "[ceilings.loop]\nseconds = 10\n", encoding="utf-8"
    )
    over_opts = argparse.Namespace(
        root=tmp_path, jobs=4, fail_fast=False, report=None, alone=[], wide=[], budget="loop"
    )
    over_sched = parallel.Scheduler([], [], over_opts, kept=schedule.Schedule())
    over_sched.finished = [parallel.Finished("slow", 20.0, 0, "")]
    assert parallel.finish_run(over_sched, over_opts, 20.0, gates=["slow"]) == 1
    out_lines = capsys.readouterr().out.splitlines()
    assert out_lines[1].startswith("parallel: one gate slow (20.0s) set the wall clock")
    assert "one gate slow (20.0s) set the wall clock while other lanes stood idle" in out_lines[-1]


def test_slower_than_usual_prints_only_when_qualifying_and_preserves_loop_last(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "quality").mkdir()
    (tmp_path / "quality/thresholds.toml").write_text(
        "[ceilings.loop]\nseconds = 50\n", encoding="utf-8"
    )
    report = tmp_path / ".ratchet/timing.jsonl"
    report.parent.mkdir()
    prior_rows: list[dict[str, object]] = [
        {"key": "lint", "seconds": 2.0, "status": "unbounded", "exit": 0, "run": "r1"},
        {"key": "test", "seconds": 8.0, "status": "unbounded", "exit": 0, "run": "r1"},
        {"key": "other", "seconds": 99.0, "status": "unbounded", "exit": 0, "run": "r1"},
        {
            "kind": runrecord.TIER,
            "seconds": 10.0,
            "status": "green",
            "judgement": "within",
            "gates": ["lint", "test"],
            "m": 1.0,
            "machine_read": True,
            "run": "r1",
        },
        {"key": "lint", "seconds": 4.0, "status": "green", "exit": 0, "run": "r2"},
        {"key": "test", "seconds": 10.0, "status": "green", "exit": 0, "run": "r2"},
        {
            "kind": runrecord.TIER,
            "seconds": 12.0,
            "status": "green",
            "judgement": "within",
            "gates": ["test", "lint"],
            "m": 1.05,
            "machine_read": True,
            "run": "r2",
        },
        {"key": "lint", "seconds": 99.0, "status": "unbounded", "exit": 0, "run": "r_over"},
        {
            "kind": runrecord.TIER,
            "seconds": 99.0,
            "status": "green",
            "judgement": "over",
            "gates": ["lint", "test"],
            "m": 1.0,
            "machine_read": True,
            "run": "r_over",
        },
        {"key": "lint", "seconds": 99.0, "status": "unbounded", "exit": 0, "run": "r_busy"},
        {
            "kind": runrecord.TIER,
            "seconds": 99.0,
            "status": "green",
            "judgement": "not judged",
            "gates": ["lint", "test"],
            "m": 1.25,
            "machine_read": True,
            "run": "r_busy",
        },
        {"key": "lint", "seconds": 99.0, "status": "unbounded", "exit": 0, "run": "r_unread"},
        {
            "kind": runrecord.TIER,
            "seconds": 99.0,
            "status": "green",
            "gates": ["lint", "test"],
            "m": 1.0,
            "machine_read": False,
            "run": "r_unread",
        },
        {
            "kind": runrecord.TIER,
            "seconds": 1.0,
            "status": "green",
            "gates": ["lint"],
            "m": 1.0,
            "machine_read": True,
            "run": "r_other",
        },
        {"key": "lint", "seconds": 99.0, "status": "unbounded", "exit": 0, "run": "r_now"},
        {
            "kind": runrecord.TIER,
            "seconds": 1.0,
            "status": "green",
            "gates": ["lint", "test"],
            "m": 1.0,
            "machine_read": True,
            "run": "r_now",
        },
    ]
    report.write_text("\n".join(json.dumps(r) for r in prior_rows) + "\n", encoding="utf-8")

    monkeypatch.setenv(runrecord.RUN, "r_now")
    options = argparse.Namespace(
        root=tmp_path, jobs=2, fail_fast=False, report=report, alone=[], wide=[], budget="loop"
    )
    idle_machine = machine.Machine(4, 0.5, 1.0, 1.0, 1.0, machine_read=True)
    sched = parallel.Scheduler([], [], options, kept=schedule.Schedule())
    monkeypatch.setattr(sched.monitor, "result", lambda: idle_machine)
    sched.finished = [
        parallel.Finished("lint", 9.0, 0, ""),
        parallel.Finished("test", 18.0, 0, ""),
    ]
    assert parallel.finish_run(sched, options, 18.0, gates=["lint", "test"]) == 0
    lines = capsys.readouterr().out.splitlines()
    slower_lines = [line for line in lines if line.startswith("parallel: slower than usual:")]
    assert len(slower_lines) == 1
    assert "18.0s vs 11.0s median" in slower_lines[0]
    assert "test (18.0s vs 9.0s median)" in slower_lines[0]
    assert "lint (9.0s vs 3.0s median)" in slower_lines[0]
    assert lines[-2] == "parallel: green (2 ran)"
    assert lines[-1].startswith("loop: within:")

    normal_sched = parallel.Scheduler([], [], options, kept=schedule.Schedule())
    monkeypatch.setattr(normal_sched.monitor, "result", lambda: idle_machine)
    normal_sched.finished = [
        parallel.Finished("lint", 3.0, 0, ""),
        parallel.Finished("test", 9.0, 0, ""),
    ]
    assert parallel.slower_than_usual(normal_sched, 12.0, gates=["lint", "test"]) is None

    busy_machine = machine.Machine(4, 2.0, 1.5, 1.5, 1.5, machine_read=True)
    busy_sched = parallel.Scheduler([], [], options, kept=schedule.Schedule())
    monkeypatch.setattr(busy_sched.monitor, "result", lambda: busy_machine)
    busy_sched.finished = list(sched.finished)
    assert parallel.slower_than_usual(busy_sched, 18.0, gates=["lint", "test"]) is None

    unread_machine = machine.Machine(4, 0.5, 1.0, 1.0, 1.0, machine_read=False)
    unread_sched = parallel.Scheduler([], [], options, kept=schedule.Schedule())
    monkeypatch.setattr(unread_sched.monitor, "result", lambda: unread_machine)
    unread_sched.finished = list(sched.finished)
    assert parallel.slower_than_usual(unread_sched, 18.0, gates=["lint", "test"]) is None

    halted_sched = parallel.Scheduler([], [], options, kept=schedule.Schedule())
    monkeypatch.setattr(halted_sched.monitor, "result", lambda: idle_machine)
    halted_sched.halted = True
    halted_sched.finished = [parallel.Finished("lint", 18.0, 1, "")]
    assert parallel.slower_than_usual(halted_sched, 18.0, gates=["lint", "test"]) is None


def test_slower_than_usual_handles_no_run_and_no_gate_growth(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv(runrecord.RUN, raising=False)
    report = tmp_path / "timing.jsonl"
    rows: list[dict[str, object]] = [
        {"key": "a", "seconds": 5.0, "status": "unbounded", "run": runrecord.NO_RUN},
        {
            "kind": runrecord.TIER,
            "seconds": 5.0,
            "status": "green",
            "gates": ["a", "b"],
            "m": 1.0,
            "machine_read": True,
            "run": runrecord.NO_RUN,
        },
        {"key": "a", "seconds": 50.0, "status": "unbounded", "run": runrecord.NO_RUN},
    ]
    report.write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")
    options = argparse.Namespace(
        root=tmp_path, jobs=2, fail_fast=False, report=report, alone=[], wide=[]
    )
    idle_machine = machine.Machine(4, 0.5, 1.0, 1.0, 1.0, machine_read=True)
    sched = parallel.Scheduler([], [], options, kept=schedule.Schedule())
    monkeypatch.setattr(sched.monitor, "result", lambda: idle_machine)
    sched.finished = [parallel.Finished("a", 5.0, 0, ""), parallel.Finished("b", 5.0, 0, "")]
    line = parallel.slower_than_usual(sched, 10.0, gates=["a", "b"])
    assert line == "parallel: slower than usual: 10.0s vs 5.0s median"


def test_parallel_records_work_counts_and_judges_work_ceilings_red_everywhere(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    (tmp_path / "quality").mkdir()
    report = tmp_path / ".ratchet/timing.jsonl"
    plain = "plain"
    worker = "mutation"
    script = (
        "import pathlib, sys\n"
        "g = sys.argv[1]\n"
        "if g == 'mutation':\n"
        "    print(pathlib.Path('work.txt').read_text(), end='')\n"
        "else:\n"
        "    print('ok')\n"
    )
    script_runner = shlex.join([sys.executable, "-c", script])
    (tmp_path / "work.txt").write_text("ratchet-work: tests-per-mutant 10.5\n", encoding="utf-8")
    assert (
        run(
            "--root",
            str(tmp_path),
            "--report",
            ".ratchet/timing.jsonl",
            "--runner",
            script_runner,
            "--",
            plain,
            worker,
        )
        == 0
    )
    capsys.readouterr()
    rows = [json.loads(line) for line in report.read_text().splitlines()]
    by_key = {row["key"]: row for row in rows if "key" in row}
    assert by_key[worker]["work"] == {"tests-per-mutant": 10.5}
    assert "work" not in by_key[plain]

    (tmp_path / "quality/thresholds.toml").write_text(
        "[ceilings.work.mutation]\ntests-per-mutant = 12\n", encoding="utf-8"
    )
    assert (
        run(
            "--root",
            str(tmp_path),
            "--report",
            ".ratchet/timing.jsonl",
            "--runner",
            script_runner,
            "--",
            plain,
            worker,
        )
        == 0
    )
    ok_out = capsys.readouterr().out
    assert "ratchet-budget: work mutation.tests-per-mutant was 10.5 (ceiling 12): green" in ok_out

    (tmp_path / "work.txt").write_text("ratchet-work: tests-per-mutant 19.03\n", encoding="utf-8")
    assert (
        run(
            "--root",
            str(tmp_path),
            "--report",
            ".ratchet/timing.jsonl",
            "--runner",
            script_runner,
            "--",
            plain,
            worker,
        )
        == 1
    )
    over_out = capsys.readouterr().out
    assert "ratchet-budget: work mutation.tests-per-mutant was 19.03 (ceiling 12): red" in over_out
    assert over_out.endswith("parallel: red\n")
    tier = [json.loads(line) for line in report.read_text().splitlines()][-1]
    assert (tier["status"], tier["exit"]) == ("red", 1)

    for text in ("nothing here\n", "ratchet-work: tests-per-mutant 0\n"):
        (tmp_path / "work.txt").write_text(text, encoding="utf-8")
        assert (
            run(
                "--root",
                str(tmp_path),
                "--report",
                ".ratchet/timing.jsonl",
                "--runner",
                script_runner,
                "--",
                plain,
                worker,
            )
            == 1
        )
        miss_out = capsys.readouterr().out
        assert "ratchet-budget: work mutation.tests-per-mutant missing (ceiling 12): red" in (
            miss_out
        )
