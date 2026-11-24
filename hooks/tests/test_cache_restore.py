import json
import os
import re
from pathlib import Path

import pytest

import cache_restore
import conformance
import gatelib
import report
import runrecord
from tests import repos

AT = re.compile(r"\d{4}-\d{2}-\d{2}T\d\d:\d\d:\d\dZ")
CACHE_BINDING = (
    "# Binding\n\n"
    "## Speed\n\n"
    "- Parallel: all\n"
    "- Cache: `.cache`, `.mypy_cache` keyed on `uv.lock`\n"
    "- Shards: 1\n"
)


def read_records(path: Path) -> list[dict[str, object]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def test_ci_cache_paths_reads_cache_bullet_spans_and_skips_placeholders_and_none() -> None:
    assert runrecord.ci_cache_paths(CACHE_BINDING) == [".cache", ".mypy_cache", "uv.lock"]
    assert runrecord.ci_cache_paths("## Speed\n\n- Cache: <the lockfiles>\n") == []
    assert runrecord.ci_cache_paths("## Speed\n\n- Cache: none: no CI cache\n") == []
    assert runrecord.ci_cache_paths("## Speed\n\n- Cache: stated for this stack\n") == []
    assert runrecord.ci_cache_paths("") == []


def test_empty_cache_restore_is_printed_and_recorded(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    root = repos.repository(tmp_path, {str(gatelib.BINDING): CACHE_BINDING})
    monkeypatch.setenv(report.RUN, "run-10")
    record = ".ratchet/timing.jsonl"
    code = cache_restore.main(
        [
            "cache_restore.py",
            "--root",
            str(root),
            "--report",
            record,
            "--hit",
            "false",
            "--",
            ".cache",
        ]
    )
    assert code == 0
    out = capsys.readouterr().out
    assert out == "ratchet-cache: restored 0 bytes (.cache: nothing restored)\n"
    [entry] = read_records(root / record)
    assert entry["kind"] == runrecord.CACHE_RESTORE
    assert entry["bytes"] == 0
    assert entry["paths"] == [".cache"]
    assert entry["hit"] is False
    assert entry["run"] == "run-10"
    assert entry["tree"] == report.content_id(root)
    assert AT.fullmatch(str(entry["at"]))


def test_empty_directory_reports_zero_bytes_restored(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = repos.repository(tmp_path, {str(gatelib.BINDING): CACHE_BINDING})
    (root / ".cache").mkdir()
    assert cache_restore.path_bytes(root / "absent") == 0
    assert (
        cache_restore.main(
            ["cache_restore.py", "--root", str(root), "--report", ".ratchet/no-hit.jsonl", ".cache"]
        )
        == 0
    )
    assert capsys.readouterr().out == "ratchet-cache: restored 0 bytes (.cache: nothing restored)\n"
    [entry] = read_records(root / ".ratchet/no-hit.jsonl")
    assert "hit" not in entry
    assert cache_restore.main(["cache_restore.py", "--root", str(root), ".cache"]) == 0
    assert capsys.readouterr().out == "ratchet-cache: restored 0 bytes (.cache: nothing restored)\n"
    assert not (root / runrecord.TIMING).exists()


def test_non_empty_cache_restore_sums_nested_files_and_skips_symlinks(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = repos.repository(tmp_path, {str(gatelib.BINDING): CACHE_BINDING})
    (root / ".cache/sub").mkdir(parents=True)
    (root / ".cache/a.bin").write_bytes(b"abc")
    (root / ".cache/sub/b.bin").write_bytes(b"defgh")
    outside = tmp_path / "outside.txt"
    outside.write_bytes(b"0" * 100)
    (root / ".cache/link").symlink_to(outside)
    (root / ".cache/top-link").symlink_to(outside)
    (root / ".mypy_cache").write_bytes(b"12")
    if hasattr(os, "mkfifo"):
        os.mkfifo(root / ".cache/fifo")
    record = ".ratchet/timing.jsonl"
    code = cache_restore.main(
        [
            "cache_restore.py",
            "--root",
            str(root),
            "--report",
            record,
            "--hit",
            "true",
            "--",
            ".cache",
            ".cache",
            ".mypy_cache",
            ".cache/top-link",
        ]
    )
    assert code == 0
    assert capsys.readouterr().out == (
        "ratchet-cache: restored 10 bytes (.cache, .cache, .mypy_cache, .cache/top-link)\n"
    )
    [entry] = read_records(root / record)
    assert entry["bytes"] == 10
    assert entry["hit"] is True


def test_missing_paths_argument_reads_binding_and_skips_when_none(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = repos.repository(tmp_path, {str(gatelib.BINDING): "## Speed\n\n- Cache: none: off\n"})
    argv = ["cache_restore.py", "--root", str(root), "--report", ".ratchet/t.jsonl"]
    assert cache_restore.main(argv) == 0
    assert capsys.readouterr().out == "ratchet-cache: no cache paths configured\n"
    assert not (root / ".ratchet/t.jsonl").exists()
    (root / gatelib.BINDING).write_text(CACHE_BINDING, encoding="utf-8")
    (root / ".cache").mkdir()
    (root / ".cache/data").write_bytes(b"12345")
    assert cache_restore.main(["cache_restore.py", "--root", str(root)]) == 0
    assert (
        capsys.readouterr().out
        == "ratchet-cache: restored 5 bytes (.cache, .mypy_cache, uv.lock)\n"
    )


def test_conformance_flags_empty_ci_cache_restore_and_clears_on_non_empty(
    tmp_path: Path,
) -> None:
    root = repos.repository(tmp_path, {str(gatelib.BINDING): CACHE_BINDING})
    assert list(conformance.check_cache_restored(root)) == []
    timing = root / runrecord.TIMING
    timing.parent.mkdir(exist_ok=True)
    bad = {"kind": runrecord.CACHE_RESTORE, "paths": [".cache"], "bytes": 0}
    ignored = [
        {"kind": runrecord.CACHE_RESTORE, "paths": [".cache"], "bytes": -1},
        {"kind": runrecord.CACHE_RESTORE, "paths": [".cache"], "bytes": True},
        {"kind": runrecord.CACHE_RESTORE, "paths": [1], "bytes": 0},
    ]
    timing.write_text("\n".join(json.dumps(r) for r in [*ignored, bad]) + "\n", encoding="utf-8")
    [found] = list(conformance.check_cache_restored(root))
    assert found.path == conformance.BINDING_PATH
    assert ".cache" in found.problem
    assert "0 bytes" in found.problem
    assert isinstance(found.fix, conformance.Agent)
    good = {"kind": runrecord.CACHE_RESTORE, "paths": [".cache"], "bytes": 64}
    timing.write_text("\n".join([json.dumps(bad), json.dumps(good)]) + "\n", encoding="utf-8")
    assert list(conformance.check_cache_restored(root)) == []


def test_conformance_falls_back_to_binding_cache_paths_and_ignores_prose_only_bindings(
    tmp_path: Path,
) -> None:
    root = repos.repository(tmp_path, {str(gatelib.BINDING): CACHE_BINDING})
    timing = root / runrecord.TIMING
    timing.parent.mkdir(exist_ok=True)
    timing.write_text(
        json.dumps({"kind": runrecord.CACHE_RESTORE, "bytes": 0}) + "\n", encoding="utf-8"
    )
    [found] = list(conformance.check_cache_restored(root))
    assert ".cache" in found.problem
    assert ".mypy_cache" in found.problem
    prose = repos.repository(
        tmp_path / "prose", {str(gatelib.BINDING): "## Speed\n\n- Cache: stated for this stack\n"}
    )
    (prose / runrecord.TIMING).parent.mkdir(exist_ok=True)
    (prose / runrecord.TIMING).write_text(
        json.dumps({"kind": runrecord.CACHE_RESTORE, "paths": [".cache"], "bytes": 0}) + "\n",
        encoding="utf-8",
    )
    assert list(conformance.check_cache_restored(prose)) == []


def test_cold_cache_miss_records_miss_and_does_not_fail_conformance(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = repos.repository(tmp_path, {str(gatelib.BINDING): CACHE_BINDING})
    record = str(runrecord.TIMING)
    code = cache_restore.main(
        [
            "cache_restore.py",
            "--root",
            str(root),
            "--report",
            record,
            "--hit",
            "",
            "--",
            ".cache",
        ]
    )
    assert code == 0
    assert capsys.readouterr().out == "ratchet-cache: restored 0 bytes (.cache: cold miss)\n"
    [entry] = read_records(root / record)
    assert entry["bytes"] == 0
    assert entry["miss"] is True
    assert "hit" not in entry
    assert list(conformance.check_cache_restored(root)) == []
    restore_empty = cache_restore.main(
        [
            "cache_restore.py",
            "--root",
            str(root),
            "--report",
            record,
            "--hit",
            "false",
            "--",
            ".cache",
        ]
    )
    assert restore_empty == 0
    assert capsys.readouterr().out == "ratchet-cache: restored 0 bytes (.cache: nothing restored)\n"
    [found] = list(conformance.check_cache_restored(root))
    assert ".cache" in found.problem
    assert "0 bytes" in found.problem
    (root / ".cache").mkdir()
    (root / ".cache/a.bin").write_bytes(b"xy")
    assert (
        cache_restore.main(
            [
                "cache_restore.py",
                "--root",
                str(root),
                "--report",
                record,
                "--hit",
                "",
                "--",
                ".cache",
            ]
        )
        == 0
    )
    assert capsys.readouterr().out == "ratchet-cache: restored 2 bytes (.cache)\n"
    assert list(conformance.check_cache_restored(root)) == []
