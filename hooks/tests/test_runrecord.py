"""``runrecord``: the lines a run appends to its record carry when, which run and which tree."""

import json
import re
import shlex
import sys
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

AT = re.compile(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ")
PASS = shlex.join([sys.executable, "-c", "pass"])
BINDING = (
    "## Speed\n\n- Parallel: all\n- Cache: the lockfile `uv.lock`, the tool caches `.cache/tool`,"
    " `.cache/*.bin`, `~/.ratchet-nothing-here/x` and `a b`\n- Shards: 1\n"
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
        "uv.lock",
        ".cache/tool",
        ".cache/*.bin",
        "~/.ratchet-nothing-here/x",
    ]
    assert runrecord.cache_paths("## Speed\n\n- Shards: 1\n") == []
    assert runrecord.cache_paths("") == []


def test_a_cache_path_that_matches_nothing_is_missing(work: Path) -> None:
    (work / ".cache").mkdir()
    (work / ".cache/tool").mkdir()
    assert runrecord.missing_cache(work) == [
        "uv.lock",
        ".cache/*.bin",
        "~/.ratchet-nothing-here/x",
    ]
    (work / "uv.lock").write_text("", encoding="utf-8")
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
    assert (tier["status"], tier["exit"], tier["gates"]) == ("green", 0, 1)
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
    named = [line for line in out if "Cache line names" in line]
    assert code == 0
    assert named[0] == (
        "parallel: the binding's Cache line names uv.lock, which does not exist after this run"
    )
    assert [line.split()[6] for line in named] == [
        "uv.lock,",
        ".cache/tool,",
        ".cache/*.bin,",
        "~/.ratchet-nothing-here/x,",
    ]
    assert out[-1].startswith("parallel: green"), "the verdict stays the last line"
    found = lines(record)[2:]
    assert [(each["kind"], each["path"]) for each in found] == [
        (runrecord.CACHE_MISSING, "uv.lock"),
        (runrecord.CACHE_MISSING, ".cache/tool"),
        (runrecord.CACHE_MISSING, ".cache/*.bin"),
        (runrecord.CACHE_MISSING, "~/.ratchet-nothing-here/x"),
    ]
    assert all(AT.fullmatch(str(each["at"])) and "run" in each and "tree" in each for each in found)


def test_without_a_report_the_cache_line_is_said_and_nothing_is_written(
    work: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert parallel.main(["parallel", "--root", str(work), "--", PASS]) == 0
    assert "Cache line names uv.lock" in capsys.readouterr().out
    assert not (work / ".ratchet/timing.jsonl").exists()


def test_a_cache_path_that_exists_is_not_said(
    work: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    for name in ("uv.lock", ".cache/tool/x", ".cache/x.bin"):
        repos.write(work, name, "")
    record = work / ".ratchet/timing.jsonl"
    argv = ["parallel", "--report", str(record), "--root", str(work), "--", PASS]
    assert parallel.main(argv) == 0
    named = [line for line in capsys.readouterr().out.splitlines() if "Cache line" in line]
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
    here = {"kind": runrecord.CACHE_MISSING, "path": "uv.lock"}
    rows = [json.dumps(each) for each in (gone, tier, {"key": "lint"}, here)]
    record.write_text("\n".join([*rows[:1], "not json", "[1]", *rows[1:]]) + "\n", encoding="utf-8")
    assert held() == ["the Cache line names uv.lock, which did not exist after the last tier run"]
    record.write_text("\n".join([*rows, json.dumps(tier)]) + "\n", encoding="utf-8")
    assert held() == [], "a later tier run that left nothing missing clears them"
    record.write_text(rows[0] + "\n", encoding="utf-8")
    assert held() == [], "a line with no tier run before it belongs to no run"


def test_the_finding_is_raised_by_the_gate_from_the_record(work: Path) -> None:
    record = work / ".ratchet/timing.jsonl"
    record.parent.mkdir(exist_ok=True)
    rows = [{"kind": runrecord.TIER}, {"kind": runrecord.CACHE_MISSING, "path": "uv.lock"}]
    record.write_text("\n".join(json.dumps(each) for each in rows) + "\n", encoding="utf-8")
    [found] = [f for f in conformance.findings(work) if f.rule == "cache_written"]
    assert found.path == conformance.BINDING_PATH
    assert isinstance(found.fix, conformance.Agent)
