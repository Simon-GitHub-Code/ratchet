"""``parallel.py``: gates run at once, each one's output whole, every red named."""

import argparse
import json
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

import budget
import gatelib
import parallel
from tests.servers import gone

PYTHON = shlex.quote(sys.executable)
PATIENCE = 60.0


def gate(code: str, *args: object) -> str:
    """A gate entry that runs ``code`` in this interpreter, with ``args`` as ``sys.argv``."""
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
    assert out.endswith("parallel: green\n")
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
    monkeypatch: pytest.MonkeyPatch, value: str
) -> None:
    monkeypatch.setenv("RATCHET_JOBS", value)
    assert gatelib.default_jobs() == (os.cpu_count() or 1)
    monkeypatch.setattr(os, "cpu_count", lambda: None)
    assert gatelib.default_jobs() == 1


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
    options = argparse.Namespace(root=Path(), jobs=1, fail_fast=False, report=None, alone=[])
    scheduler = parallel.Scheduler([], [], options)
    scheduler.finished = [
        parallel.Finished("fast", 0.2, 0, ""),
        parallel.Finished("slow gate", 9.5, 0, ""),
    ]
    assert parallel.closing(scheduler, 10.0) == []
    assert capsys.readouterr().out == (
        "parallel: 2 of 2 gates finished in 10.0s (9.7s of gate time): 2 judged and green;"
        " slowest: slow gate 9.5s\n"
    )


def test_a_gate_that_says_later_or_none_is_counted_apart_from_the_judged_ones(
    capsys: pytest.CaptureFixture[str],
) -> None:
    later, none, green, red = (
        gate(SAY, "later: code:source", ""),
        gate(SAY, "none: not a service", ""),
        gate(SAY, "later: code:source", "and then judged something"),
        gate("print('later: code:source'); raise SystemExit(2)"),
    )
    assert run("--", later, none, green, red) == 1
    out = capsys.readouterr().out
    assert f"parallel: {later}: judged nothing (later: code:source) in " in out
    assert f"parallel: {none}: judged nothing (none: not a service) in " in out
    assert f"parallel: {green}: green in " in out
    assert f"parallel: {red}: red (exit 2) in " in out
    assert (
        "(gate time): 1 judged and green, 1 red, 1 not asked yet (later: each one's line names"
        " what it waits for), 1 not applicable (none); slowest: "
    ) in re.sub(r"\(\d+\.\ds of gate time\)", "(gate time)", out)
    assert out.endswith(f"{red}: red (exit 2)\nparallel: red\n")


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
    budget.report(report, budget.Outcome("lint", 1.0, None, 0))
    template = json.loads(report.read_text())
    records = [json.loads(line) for line in ours.read_text().splitlines()]
    assert {record["key"] for record in records} == {green, red}
    assert all(set(record) == set(template) for record in records)
    by_key = {record["key"]: record for record in records}
    assert by_key[green]["status"] == "unbounded"
    assert by_key[green]["exit"] == 0
    assert by_key[red]["status"] == "failed"
    assert by_key[red]["exit"] == 2
    assert all(record["ceiling"] is None and record["seconds"] >= 0 for record in records)


def test_a_runner_that_cannot_start_is_a_red_gate(capsys: pytest.CaptureFixture[str]) -> None:
    assert run("--runner", "/no/such/runner", "--", "lint", "types") == 1
    out = capsys.readouterr().out
    assert out.count("cannot run /no/such/runner") == 2
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
