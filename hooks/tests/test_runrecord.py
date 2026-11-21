import errno
import json
import os
import re
import shlex
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path

import pytest

import budget
import conformance
import drain
import gatelib
import parallel
import report
import runrecord
from tests import repos

CACHE_PROSE = (
    "`lint` and `lint-types` read `.cache/linter` through `ci/setup-action`, keyed on `deps.lock`"
    " and `config.json`; `.cache/linter`, `lint`, `lint-types`, `ci/setup-action`, `deps.lock`,"
    " `config.json`"
)
AT = re.compile(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ")
PASS = shlex.join([sys.executable, "-c", "pass"])
BINDING = (
    "## Speed\n\n- Parallel: all\n- Cache: " + CACHE_PROSE + "\n- Local caches: `deps.lock`,"
    " `.cache/checker`, `.cache/*.bin`, `~/.ratchet-nothing-here/x` and `a b`\n- Shards: 1\n"
)


def lines(path: Path) -> list[dict[str, object]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


@pytest.fixture
def work(tmp_path: Path) -> Path:
    return repos.repository(tmp_path, {"README.md": "# A\n", str(gatelib.BINDING): BINDING})


def test_a_stamp_holds_the_time_the_run_and_the_trees_content(
    work: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(report.RUN, "run-1")
    stamped = runrecord.stamp(work)
    assert AT.fullmatch(stamped["at"])
    assert stamped["run"] == "run-1"
    assert stamped["tree"] == report.content_id(work)


def test_a_stamp_outside_a_run_says_so_in_the_runs_value(
    work: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv(report.RUN, raising=False)
    stamped = runrecord.stamp(work)
    assert stamped["run"] == runrecord.NO_RUN
    assert stamped["tree"] == report.content_id(work), "what the line has, it carries"


def test_a_tree_that_cannot_be_read_says_so_in_the_trees_value(tmp_path: Path) -> None:
    assert runrecord.stamp(tmp_path)["tree"] == runrecord.NO_TREE


def test_a_stamp_takes_a_tree_read_before_as_given(work: Path) -> None:
    assert runrecord.stamp(work, "read-before")["tree"] == "read-before"


def test_the_cache_line_names_its_code_spans_without_a_space() -> None:
    assert runrecord.cache_paths(BINDING) == [
        "deps.lock",
        ".cache/checker",
        ".cache/*.bin",
        "~/.ratchet-nothing-here/x",
    ]
    assert runrecord.cache_paths("## Speed\n\n- Shards: 1\n") == []
    assert runrecord.cache_paths("") == []


def test_a_cache_path_that_matches_nothing_is_missing(work: Path) -> None:
    (work / ".cache").mkdir()
    (work / ".cache/checker").mkdir()
    assert runrecord.missing_cache(work) == [
        "deps.lock",
        ".cache/*.bin",
        "~/.ratchet-nothing-here/x",
    ]
    (work / "deps.lock").write_text("", encoding="utf-8")
    (work / ".cache/a.bin").write_text("", encoding="utf-8")
    assert runrecord.missing_cache(work) == ["~/.ratchet-nothing-here/x"]


def test_a_root_with_no_binding_names_no_cache_path(tmp_path: Path) -> None:
    assert runrecord.missing_cache(tmp_path) == []


def test_a_budget_line_holds_the_three_fields_and_keeps_its_own(
    work: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(report.RUN, "run-2")
    tree = report.content_id(work)
    argv = ["budget", "lint", "--root", str(work), "--report", ".ratchet/t.jsonl", "--", "true"]
    assert budget.main(argv) == 0
    [record] = lines(work / ".ratchet/t.jsonl")
    assert record["key"] == "lint"
    assert {"seconds", "ceiling", "status", "exit"} <= set(record)
    assert AT.fullmatch(str(record["at"]))
    assert (record["run"], record["tree"]) == ("run-2", tree)


def test_a_gate_line_and_the_tiers_line_hold_the_three_fields(
    work: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(report.RUN, "run-3")
    tree = report.content_id(work)
    record = work / ".ratchet/timing.jsonl"
    assert (
        parallel.main(["parallel", "--report", str(record), "--root", str(work), "--", PASS]) == 0
    )
    gate, tier = lines(record)[:2]
    assert gate["key"] == PASS
    assert tier["kind"] == runrecord.TIER
    assert (tier["status"], tier["exit"], tier["gates"]) == ("green", 0, [PASS])
    assert isinstance(tier["seconds"], float)
    assert isinstance(gate["seconds"], float)
    assert tier["seconds"] >= gate["seconds"]
    for found in (gate, tier):
        assert AT.fullmatch(str(found["at"]))
        assert (found["run"], found["tree"]) == ("run-3", tree)


def test_lines_written_outside_a_run_say_so_in_the_field(
    work: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv(report.RUN, raising=False)
    record = work / ".ratchet/timing.jsonl"
    assert (
        parallel.main(["parallel", "--report", str(record), "--root", str(work), "--", PASS]) == 0
    )
    assert {found["run"] for found in lines(record)} == {runrecord.NO_RUN}


def test_a_red_tier_is_a_red_line(work: Path) -> None:
    record = work / ".ratchet/timing.jsonl"
    bad = shlex.join([sys.executable, "-c", "raise SystemExit(3)"])
    assert parallel.main(["parallel", "--report", str(record), "--root", str(work), "--", bad]) == 1
    tier = lines(record)[1]
    assert (tier["status"], tier["exit"]) == ("red", 1)


def test_the_runner_says_each_cache_path_that_is_missing_and_records_it(
    work: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    record = work / ".ratchet/timing.jsonl"
    code = parallel.main(["parallel", "--report", str(record), "--root", str(work), "--", PASS])
    out = capsys.readouterr().out.splitlines()
    named = [line for line in out if "Local caches bullet names" in line]
    assert code == 0
    assert named[0] == (
        "parallel: the binding's Local caches bullet names deps.lock, which does not exist"
        " after this run, so the tool that owns it ran without its cache"
    )
    assert [line.split()[7] for line in named] == [
        "deps.lock,",
        ".cache/checker,",
        ".cache/*.bin,",
        "~/.ratchet-nothing-here/x,",
    ]
    assert out[-1].startswith("parallel: green"), "the verdict stays the last line"
    found = lines(record)[2:]
    assert [(each["kind"], each["path"]) for each in found] == [
        (runrecord.CACHE_MISSING, "deps.lock"),
        (runrecord.CACHE_MISSING, ".cache/checker"),
        (runrecord.CACHE_MISSING, ".cache/*.bin"),
        (runrecord.CACHE_MISSING, "~/.ratchet-nothing-here/x"),
    ]
    assert all(AT.fullmatch(str(each["at"])) and "run" in each and "tree" in each for each in found)


def test_without_a_report_the_cache_line_is_said_and_nothing_is_written(
    work: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert parallel.main(["parallel", "--root", str(work), "--", PASS]) == 0
    assert "Local caches bullet names deps.lock" in capsys.readouterr().out
    assert not (work / ".ratchet/timing.jsonl").exists()


def test_a_cache_path_that_exists_is_not_said(
    work: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    for name in ("deps.lock", ".cache/checker/x", ".cache/x.bin"):
        repos.write(work, name, "")
    record = work / ".ratchet/timing.jsonl"
    argv = ["parallel", "--report", str(record), "--root", str(work), "--", PASS]
    assert parallel.main(argv) == 0
    named = [line for line in capsys.readouterr().out.splitlines() if "Local caches bullet" in line]
    assert len(named) == 1
    assert [each.get("kind") for each in lines(record)] == [None, runrecord.TIER, "cache-missing"]


def test_the_readers_of_the_record_still_read_its_gate_lines(
    work: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(report.RUN, "run-4")
    record = work / ".ratchet/timing.jsonl"
    argv = ["budget", "bite-all", "--root", str(work), "--report", ".ratchet/timing.jsonl", "--"]
    assert budget.main([*argv, "true"]) == 0
    assert (
        parallel.main(["parallel", "--report", str(record), "--root", str(work), "--", PASS]) == 0
    )
    assert set(drain.timings(work)) == {"bite-all", PASS}, "the tier and cache lines are no gate"
    assert conformance.last_full_proof(work) is not None


def test_conformance_holds_the_last_runs_missing_cache_paths_as_findings(work: Path) -> None:
    def held() -> list[str]:
        return [found.problem for found in conformance.check_cache_written(work)]

    record = work / ".ratchet/timing.jsonl"
    assert held() == []
    record.parent.mkdir(exist_ok=True)
    gone = {"kind": runrecord.CACHE_MISSING, "path": "old.cache"}
    tier = {"kind": runrecord.TIER}
    here = {"kind": runrecord.CACHE_MISSING, "path": "deps.lock"}
    rows = [json.dumps(each) for each in (gone, tier, {"key": "lint"}, here)]
    record.write_text("\n".join([*rows[:1], "not json", "[1]", *rows[1:]]) + "\n", encoding="utf-8")
    assert held() == [
        "the Local caches bullet names deps.lock, which did not exist after the last tier run"
    ]
    record.write_text("\n".join([*rows, json.dumps(tier)]) + "\n", encoding="utf-8")
    assert held() == [], "a later tier run that left nothing missing clears them"
    record.write_text(rows[0] + "\n", encoding="utf-8")
    assert held() == [], "a line with no tier run before it belongs to no run"


def test_the_finding_is_raised_by_the_gate_from_the_record(work: Path) -> None:
    record = work / ".ratchet/timing.jsonl"
    record.parent.mkdir(exist_ok=True)
    rows = [{"kind": runrecord.TIER}, {"kind": runrecord.CACHE_MISSING, "path": "deps.lock"}]
    record.write_text("\n".join(json.dumps(each) for each in rows) + "\n", encoding="utf-8")
    [found] = [f for f in conformance.findings(work) if f.rule == "cache_written"]
    assert found.path == conformance.BINDING_PATH
    assert isinstance(found.fix, conformance.Agent)


def test_the_tiers_line_names_its_gates_in_the_order_given(work: Path) -> None:
    record = work / ".ratchet/timing.jsonl"
    slow = shlex.join([sys.executable, "-c", "import time; time.sleep(0.3)"])
    argv = ["parallel", "--report", str(record), "--root", str(work), "--", slow, PASS]
    assert parallel.main(argv) == 0
    [tier] = [each for each in lines(record) if each.get("kind") == runrecord.TIER]
    assert tier["gates"] == [slow, PASS], "the gate that finished first is not first"


def test_a_cache_line_of_prose_spans_alone_yields_no_runner_line_and_no_finding(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    prose = f"## Speed\n\n- Cache: {CACHE_PROSE}\n- Shards: 1\n"
    root = repos.repository(tmp_path, {str(gatelib.BINDING): prose})
    record = root / ".ratchet/timing.jsonl"
    argv = ["parallel", "--report", str(record), "--root", str(root), "--", PASS]
    assert parallel.main(argv) == 0
    assert "Local caches" not in capsys.readouterr().out
    assert [each.get("kind") for each in lines(record)] == [None, runrecord.TIER]
    assert list(conformance.check_local_caches(root)) == []
    assert list(conformance.check_cache_written(root)) == []


def test_a_placeholder_local_caches_bullet_names_nothing_and_is_no_finding(tmp_path: Path) -> None:
    text = "## Speed\n\n- Local caches: <the paths, each as a code span>\n"
    root = repos.repository(tmp_path, {str(gatelib.BINDING): text})
    assert runrecord.missing_cache(root) == []
    assert list(conformance.check_local_caches(root)) == []


def test_a_malformed_local_caches_bullet_is_a_finding(tmp_path: Path) -> None:
    for value in ("`a b`, `deps.lock`", "the caches the tools keep"):
        text = f"## Speed\n\n- Local caches: {value}\n"
        root = repos.repository(tmp_path / str(len(value)), {str(gatelib.BINDING): text})
        [found] = conformance.check_local_caches(root)
        assert found.problem == (
            "the Local caches bullet of the Speed section is not a list of paths, each as a code"
            " span with no space in it"
        )
    ok = "## Speed\n\n- Local caches: `deps.lock`, `.cache/checker`\n"
    root = repos.repository(tmp_path / "ok", {str(gatelib.BINDING): ok})
    assert list(conformance.check_local_caches(root)) == []
    assert list(conformance.check_local_caches(tmp_path / "ok" / "docs")) == []


FAULT = {
    "kind": runrecord.FAULT,
    "gate": "lint",
    "bite": "whole",
    "seconds": 9.0,
    "narrowed": False,
}
UNWRITABLE_BY_ROOT = pytest.mark.skipif(os.geteuid() == 0, reason="root writes through modes")


def test_the_reader_returns_only_records_of_the_shape_their_callers_read(tmp_path: Path) -> None:
    kept = [FAULT, {**FAULT, "seconds": 0}, {"key": "lint", "seconds": "9"}, {"kind": "tier"}]
    dropped = [
        {**FAULT, "gate": ["lint"]},
        {**FAULT, "bite": {"a": 1}},
        {**FAULT, "seconds": "9"},
        {**FAULT, "seconds": True},
        {**FAULT, "seconds": -1.0},
        {**FAULT, "narrowed": "false"},
        {key: value for key, value in FAULT.items() if key != "narrowed"},
        [FAULT],
        "a string",
    ]
    odd = [
        json.dumps(FAULT).replace("9.0", "NaN"),
        json.dumps(FAULT).replace("9.0", "Infinity"),
        "not json",
        "[" * 100_000,
        "1" * 5_000,
    ]
    rows = [*map(json.dumps, dropped), *odd, *map(json.dumps, kept)]
    path = tmp_path / "timing.jsonl"
    path.write_bytes(b"\xff\xfe\x80\n" + "\n".join(rows).encode() + b'\n{"kind": "fau')
    assert runrecord.read(path) == kept


def sized(count: int) -> list[bytes]:
    rows = []
    for number in range(count):
        row = {"key": f"k{number:05d}", "seconds": 1.0, "pad": ""}
        row["pad"] = "x" * (63 - len(json.dumps(row)))
        rows.append(json.dumps(row).encode() + b"\n")
    return rows


def test_the_reader_reads_the_last_window_of_the_record_and_no_line_it_cuts(
    tmp_path: Path,
) -> None:
    path = tmp_path / "timing.jsonl"
    rows = sized(runrecord.WINDOW // 64 + 2)
    full = runrecord.WINDOW // 64
    for held, tail, expected in (
        (rows[:full], b"", rows[:full]),
        (rows[: full + 1], b"", rows[1 : full + 1]),
        (rows, b'{"key": "k", "seconds": 1.0}\n'.rjust(32, b" "), rows[3:]),
    ):
        path.write_bytes(b"".join(held) + tail)
        got = runrecord.read(path)
        assert [json.dumps(each).encode() + b"\n" for each in got[: len(expected)]] == expected
        assert len(got) == len(expected) + bool(tail)


def test_the_reader_reads_only_a_regular_file(tmp_path: Path) -> None:
    assert runrecord.read(tmp_path) == []
    assert runrecord.read(tmp_path / "absent.jsonl") == []


def linked_record(path: Path, outside: Path) -> None:
    path.parent.mkdir()
    path.symlink_to(outside / "timing.jsonl")


def linked_folder(path: Path, outside: Path) -> None:
    path.parent.symlink_to(outside)


def read_only_record(path: Path, _: Path) -> None:
    path.parent.mkdir()
    path.write_text("", encoding="utf-8")
    path.chmod(0o444)


def folder_record(path: Path, _: Path) -> None:
    path.mkdir(parents=True)


@UNWRITABLE_BY_ROOT
@pytest.mark.parametrize(
    ("unwritable", "why"),
    [
        (linked_record, "a symbolic link"),
        (linked_folder, "a symbolic link"),
        (read_only_record, os.strerror(errno.EACCES)),
        (folder_record, os.strerror(errno.EISDIR)),
    ],
)
def test_the_writer_appends_only_to_a_regular_file_in_a_real_folder_and_says_so_once(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    *,
    unwritable: Callable[[Path, Path], None],
    why: str,
) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "timing.jsonl").write_text("", encoding="utf-8")
    path = tmp_path / "repo" / runrecord.TIMING
    path.parents[1].mkdir()
    unwritable(path, outside)
    runrecord.append(path, FAULT, who="bite")
    runrecord.append(path, FAULT, who="bite")
    assert capsys.readouterr().out == f"bite: not recorded: {path}: {why}\n"
    assert (outside / "timing.jsonl").read_text() == ""
    assert [each.name for each in outside.iterdir()] == ["timing.jsonl"]


def test_the_writer_refuses_a_fifo_another_process_reads(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = tmp_path / runrecord.TIMING
    path.parent.mkdir()
    os.mkfifo(path)
    reader = os.open(path, os.O_RDONLY | os.O_NONBLOCK)
    try:
        runrecord.append(path, FAULT)
        assert os.read(reader, 4096) == b""
    finally:
        os.close(reader)
    assert capsys.readouterr().out == f"ratchet: not recorded: {path}: not a regular file\n"


def test_the_writer_makes_the_state_folder_and_the_record(tmp_path: Path) -> None:
    path = tmp_path / runrecord.TIMING
    runrecord.append(path, FAULT)
    runrecord.append(path, {"kind": "tier"})
    assert lines(path) == [FAULT, {"kind": "tier"}]


def outputs(capsys: pytest.CaptureFixture[str]) -> list[str]:
    said = re.sub(r"; slowest: .*", "", capsys.readouterr().out)
    return [re.sub(r"\d+\.\d+s", "Ns", line) for line in said.splitlines()]


def stuck(path: Path) -> str:
    path.write_text("", encoding="utf-8")
    path.chmod(0o444)
    return f"ratchet: not recorded: {path}: {os.strerror(errno.EACCES)}"


@UNWRITABLE_BY_ROOT
@pytest.mark.parametrize("first", [PASS, shlex.join([sys.executable, "-c", "raise SystemExit(3)"])])
def test_the_runner_that_cannot_record_says_so_once_and_ends_as_it_would(
    work: Path, capsys: pytest.CaptureFixture[str], first: str
) -> None:
    gates = ["--root", str(work), "--", first, shlex.join([sys.executable, "-c", "print()"])]
    expected = parallel.main(["parallel", "--report", str(work / "kept.jsonl"), *gates])
    said = outputs(capsys)
    line = stuck(work / "stuck.jsonl")
    assert parallel.main(["parallel", "--report", str(work / "stuck.jsonl"), *gates]) == expected
    got = outputs(capsys)
    assert got.count(line) == 1, got
    assert sorted(each for each in got if each != line) == sorted(said), "in any order"
    assert (work / "stuck.jsonl").read_text() == ""


@UNWRITABLE_BY_ROOT
def test_budget_that_cannot_record_says_so_and_ends_as_it_would(
    work: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    line = stuck(work / "stuck.jsonl")
    timed = ["budget", "lint", "--root", str(work), "--report", "stuck.jsonl", "--", "true"]
    assert budget.main(timed) == 0
    assert capsys.readouterr().out.splitlines()[-1] == line


def test_the_runner_given_a_fifo_for_its_report_finishes_and_says_so(work: Path) -> None:
    fifo = work / "timing.jsonl"
    os.mkfifo(fifo)
    command = [sys.executable, parallel.__file__, "--root", str(work), "--report", str(fifo)]
    command += ["--", PASS]
    run = subprocess.run(  # noqa: S603 # -- this interpreter and the plugin's own runner
        command, capture_output=True, text=True, timeout=60, check=False
    )
    assert run.returncode == 0, run.stdout + run.stderr
    said = [line for line in run.stdout.splitlines() if "not recorded" in line]
    assert said == [f"ratchet: not recorded: {fifo}: {os.strerror(errno.ENXIO)}"]
