import contextlib
import io
import json
import os
import re
import resource
import shlex
import subprocess
import sys
import tempfile
import threading
from collections.abc import Callable
from pathlib import Path

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

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
    assert {"cores", "load", "k", "share", "m", "machine_read"} <= set(record)
    assert isinstance(record["machine_read"], bool)
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
    assert {"lane", "start", "cpu", "alone"} <= set(gate)
    assert gate["alone"] is True
    assert tier["kind"] == runrecord.TIER
    assert (tier["status"], tier["exit"], tier["gates"]) == ("green", 0, [PASS])
    assert {"cores", "load", "k", "share", "m", "machine_read"} <= set(tier)
    assert isinstance(tier["machine_read"], bool)
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
    assert list(runrecord.read(path)) == kept


LATER = {**FAULT, "bite": "later", "seconds": 0.2}


def recorded_before_mebibytes(root: Path, first: list[dict[str, object]], mebibytes: int) -> None:
    later = (json.dumps(LATER) + "\n").encode()
    path = root / runrecord.TIMING
    path.parent.mkdir(exist_ok=True)
    held = b"".join(json.dumps(each).encode() + b"\n" for each in first)
    path.write_bytes(held + later * (mebibytes * 2**20 // len(later) + 1))


def test_the_last_full_proof_is_named_however_many_records_follow_it(work: Path) -> None:
    recorded_before_mebibytes(work, [{"key": "bite-all", "seconds": 1234.0}], 3)
    problem = conformance.full_proof_problem(work, "[ceilings.seconds]\ntier2 = 3000\n")
    assert problem.endswith("; the last full proof took 1234s"), problem


def test_the_last_full_proof_is_the_last_one_recorded(work: Path) -> None:
    proofs = [{"key": "bite-all", "seconds": 1000}, {"key": "bite-all", "seconds": 1500.5}]
    recorded_before_mebibytes(work, proofs, 0)
    assert conformance.last_full_proof(work) == 1500.5


def test_a_missing_cache_is_held_however_many_records_follow_its_tier_run(work: Path) -> None:
    missing = {"kind": runrecord.CACHE_MISSING, "path": "deps.lock"}
    recorded_before_mebibytes(work, [{"kind": runrecord.TIER}, missing], 3)
    assert [found.problem for found in conformance.check_cache_written(work)] == [
        "the Local caches bullet names deps.lock, which did not exist after the last tier run"
    ]


def test_the_reader_passes_over_a_line_longer_than_its_bound_and_reads_on(tmp_path: Path) -> None:
    path = tmp_path / "timing.jsonl"
    record = json.dumps(FAULT).encode()
    longest = record.ljust(runrecord.LINE - 2) + b"\n"
    at_the_bound = record.ljust(runrecord.LINE - 1) + b"\n"
    long_lines = b" " * runrecord.LINE * 3 + record + b"\n" + longest + at_the_bound
    path.write_bytes(long_lines + record + b"\n")
    assert list(runrecord.read(path)) == [FAULT, FAULT]
    path.write_bytes(record + b" " * runrecord.LINE)
    assert list(runrecord.read(path)) == []


def finished(task: Callable[[], object]) -> bool:
    worker = threading.Thread(target=task, daemon=True)
    worker.start()
    worker.join(10)
    return not worker.is_alive()


def test_the_reader_reads_only_a_regular_file_and_never_blocks(tmp_path: Path) -> None:
    os.mkfifo(tmp_path / "fifo")
    (tmp_path / "zero").symlink_to("/dev/zero")
    (tmp_path / "urandom").symlink_to("/dev/urandom")
    for name in ("", "absent.jsonl", "fifo", "zero", "urandom"):
        got: list[object] = []
        path = tmp_path / name
        assert finished(lambda got=got, path=path: got.extend(runrecord.read(path))), name
        assert got == [], name


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


def state_file(path: Path, _: Path) -> None:
    path.parent.write_text("", encoding="utf-8")


LINKED = "it or its folder is a symbolic link; remove the link"
NOT_WRITABLE = "permission denied; make it and its folder writable"


@UNWRITABLE_BY_ROOT
@pytest.mark.parametrize(
    ("unwritable", "why"),
    [
        (linked_record, LINKED),
        (linked_folder, LINKED),
        (read_only_record, NOT_WRITABLE),
        (folder_record, "it is a directory; remove it"),
        (state_file, "its folder is not a folder; remove what is there"),
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
    root = tmp_path / "repo"
    path = root / runrecord.TIMING
    root.mkdir()
    unwritable(path, outside)
    runrecord.append(path, FAULT, root=root, who="bite")
    runrecord.append(path, FAULT, root=root, who="bite")
    assert capsys.readouterr().out == f"bite: not recorded: .ratchet/timing.jsonl: {why}\n"
    assert (outside / "timing.jsonl").read_text() == ""
    assert [each.name for each in outside.iterdir()] == ["timing.jsonl"]


@UNWRITABLE_BY_ROOT
def test_the_writer_says_each_reason_once_in_a_process(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = tmp_path / runrecord.TIMING
    read_only_record(path, tmp_path)
    assert runrecord.append(path, FAULT, root=tmp_path) is False
    assert runrecord.append(path, FAULT, root=tmp_path) is False
    path.unlink()
    path.mkdir()
    assert runrecord.append(path, FAULT, root=tmp_path) is False
    assert runrecord.append(path, FAULT, root=tmp_path) is False
    assert capsys.readouterr().out.splitlines() == [
        f"ratchet: not recorded: .ratchet/timing.jsonl: {NOT_WRITABLE}",
        "ratchet: not recorded: .ratchet/timing.jsonl: it is a directory; remove it",
    ]


SPECIAL = "ratchet: not recorded: .ratchet/timing.jsonl: it is not a regular file; remove it"


def test_the_writer_refuses_a_fifo_another_process_reads(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = tmp_path / runrecord.TIMING
    path.parent.mkdir()
    os.mkfifo(path)
    reader = os.open(path, os.O_RDONLY | os.O_NONBLOCK)
    try:
        runrecord.append(path, FAULT, root=tmp_path)
        assert os.read(reader, 4096) == b""
    finally:
        os.close(reader)
    assert capsys.readouterr().out == SPECIAL + "\n"


def test_the_writer_makes_the_state_folder_and_the_record(tmp_path: Path) -> None:
    path = tmp_path / runrecord.TIMING
    assert runrecord.append(path, FAULT, root=tmp_path) is True
    assert runrecord.append(path, {"kind": "tier"}, root=tmp_path) is True
    assert lines(path) == [FAULT, {"kind": "tier"}]


def test_read_once_caches_facts_for_a_path(tmp_path: Path) -> None:
    path = tmp_path / runrecord.TIMING
    assert runrecord.append(path, {"key": "lint", "seconds": 1.0}, root=tmp_path) is True
    with runrecord.read_once():
        first = runrecord.facts(path)
        assert runrecord.append(path, {"key": "lint", "seconds": 2.0}, root=tmp_path) is True
        second = runrecord.facts(path)
    assert first is second
    assert first.seconds == {"lint": 1.0}


def test_the_writer_never_appends_onto_an_unfinished_line(tmp_path: Path) -> None:
    path = tmp_path / runrecord.TIMING
    path.parent.mkdir()
    path.write_bytes(b'{"kind": "fau')
    runrecord.append(path, FAULT, root=tmp_path)
    assert list(runrecord.read(path)) == [FAULT]


FILLED = """
import resource, sys
from pathlib import Path
sys.path[:0] = sys.argv[3:]
import runrecord
path = Path(sys.argv[1])
room = path.stat().st_size + int(sys.argv[2])
resource.setrlimit(resource.RLIMIT_FSIZE, (room, room))
runrecord.append(path, {"pad": "x" * 200}, root=path.parents[1])
"""


def test_a_write_cut_short_is_said_and_the_next_record_still_parses(tmp_path: Path) -> None:
    path = tmp_path / runrecord.TIMING
    path.parent.mkdir()
    path.write_text(json.dumps(FAULT) + "\n", encoding="utf-8")
    places = [str(Path(runrecord.__file__).parent), str(Path(gatelib.__file__).parent)]
    command = [sys.executable, "-c", FILLED, str(path), "50", *places]
    run = subprocess.run(  # noqa: S603 # -- this interpreter and the plugin's own writer
        command, capture_output=True, text=True, timeout=60, check=False
    )
    assert run.returncode == 0, run.stderr
    assert run.stdout == (
        "ratchet: not recorded: .ratchet/timing.jsonl: no space left for it; free some space\n"
    )
    assert path.stat().st_size == len(json.dumps(FAULT)) + 1 + 50, "the write was cut short"
    runrecord.append(path, {"kind": "tier"}, root=tmp_path)
    assert list(runrecord.read(path)) == [FAULT, {"kind": "tier"}]


FULL = "ratchet: not recorded: .ratchet/timing.jsonl: no space left for it; free some space\n"


def test_a_write_cut_short_in_this_process_is_said_and_the_record_still_parses(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = tmp_path / runrecord.TIMING
    path.parent.mkdir()
    path.write_text(json.dumps(FAULT) + "\n", encoding="utf-8")
    soft, hard = resource.getrlimit(resource.RLIMIT_FSIZE)
    resource.setrlimit(resource.RLIMIT_FSIZE, (path.stat().st_size + 50, hard))
    try:
        runrecord.append(path, {"pad": "x" * 200}, root=tmp_path)
    finally:
        resource.setrlimit(resource.RLIMIT_FSIZE, (soft, hard))
    assert capsys.readouterr().out == FULL
    assert path.stat().st_size == len(json.dumps(FAULT)) + 1 + 50, "the write was cut short"
    assert list(runrecord.read(path)) == [FAULT]


def fault_of_length(size: int) -> dict[str, object]:
    bare = {**FAULT, "bite": ""}
    return {**bare, "bite": "b" * (size - len(json.dumps(bare)) - 1)}


TOO_LONG = (
    "bite: not recorded: .ratchet/timing.jsonl: the record is too long to keep; shorten the name"
    " it carries\n"
)


def test_a_record_one_byte_under_the_bound_is_kept_and_one_at_it_is_refused(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = tmp_path / runrecord.TIMING
    under, at = fault_of_length(runrecord.LINE - 1), fault_of_length(runrecord.LINE)
    assert len(json.dumps(at)) + 1 == runrecord.LINE
    runrecord.append(path, under, root=tmp_path, who="bite")
    held = path.read_bytes()
    assert capsys.readouterr().out == ""
    runrecord.append(path, at, root=tmp_path, who="bite")
    assert capsys.readouterr().out == TOO_LONG
    assert path.read_bytes() == held, "the file is unchanged"
    assert list(runrecord.read(path)) == [under]


@settings(max_examples=40, deadline=None)
@given(st.integers(min_value=runrecord.LINE - 48, max_value=runrecord.LINE + 48))
def test_whatever_the_writer_keeps_the_reader_returns(size: int) -> None:
    record = fault_of_length(size)
    with tempfile.TemporaryDirectory() as folder:
        root = Path(folder)
        path = root / runrecord.TIMING
        runrecord.append(path, FAULT, root=root)
        with contextlib.redirect_stdout(io.StringIO()):
            runrecord.append(path, record, root=root)
        runrecord.append(path, FAULT, root=root)
        got = list(runrecord.read(path))
    assert got == ([FAULT, record, FAULT] if size < runrecord.LINE else [FAULT, FAULT])


UNREADABLE_BY_ROOT = pytest.mark.skipif(os.geteuid() == 0, reason="root reads through modes")


@UNREADABLE_BY_ROOT
def test_a_record_its_owner_cannot_read_is_still_appended_on_a_line_of_its_own(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = tmp_path / runrecord.TIMING
    path.parent.mkdir()
    path.write_bytes(b'{"kind": "fau')
    path.chmod(0o200)
    try:
        runrecord.append(path, FAULT, root=tmp_path)
        runrecord.append(path, {"kind": "tier"}, root=tmp_path)
    finally:
        path.chmod(0o600)
    assert capsys.readouterr().out == ""
    assert list(runrecord.read(path)) == [FAULT, {"kind": "tier"}]


def outputs(capsys: pytest.CaptureFixture[str]) -> list[str]:
    said = re.sub(r"; slowest: .*", "", capsys.readouterr().out)
    return [re.sub(r"\d+\.\d+s", "Ns", line) for line in said.splitlines()]


def stuck(path: Path) -> str:
    path.write_text("", encoding="utf-8")
    path.chmod(0o444)
    return f"ratchet: not recorded: {path.name}: {NOT_WRITABLE}"


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
    assert said == [SPECIAL.replace(".ratchet/", "")]


def test_facts_counts_producer_and_gate_records_per_real_run_and_tracks_latest(
    tmp_path: Path,
) -> None:
    path = tmp_path / runrecord.TIMING
    rows: list[dict[str, object]] = [
        {"kind": runrecord.PRODUCER, "report": "suite", "producer": "suite-report", "run": "r1"},
        {"key": "suite-report", "seconds": 3.0, "status": "green", "run": "r1"},
        {"kind": runrecord.PRODUCER, "report": "suite", "producer": "suite-report", "run": "r2"},
        {
            "kind": runrecord.PRODUCER,
            "report": "suite",
            "producer": "suite-report",
            "run": runrecord.NO_RUN,
        },
        {
            "kind": runrecord.PRODUCER,
            "report": "suite",
            "producer": "suite-report",
            "run": runrecord.NO_RUN,
        },
        {"kind": runrecord.PRODUCER, "report": "suite", "producer": "", "run": "r3"},
        {"kind": runrecord.TIER, "seconds": 1.0, "run": "r3"},
        {"kind": runrecord.CACHE_RESTORE, "bytes": -1, "paths": [".cache"], "run": "r3"},
        {"kind": runrecord.CACHE_RESTORE, "bytes": 100, "paths": [".cache"], "run": "r3"},
    ]
    for row in rows:
        runrecord.append(path, row, root=tmp_path)
    facts = runrecord.facts(path)
    assert facts.producer_counts[("r1", "suite-report")] == 2
    assert facts.producer_counts[("r2", "suite-report")] == 1
    assert facts.latest_producer_run["suite-report"] == "r2"
    assert facts.restored == {(".cache",): 100}
    assert runrecord.ci_cache_paths("## Speed\n\n- Cache: none: no cache\n") == []
    assert all(run_id != runrecord.NO_RUN for run_id, _ in facts.producer_counts)
