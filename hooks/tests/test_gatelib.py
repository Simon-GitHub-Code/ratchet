"""The gate library: one merge base for every gate, the changed paths, the thresholds file."""

from pathlib import Path

import pytest

import gatelib
from tests.repos import branch, commit, git, repository, write


def test_git_answers_none_outside_a_repository(tmp_path: Path) -> None:
    assert gatelib.git(tmp_path, "rev-parse", "HEAD") is None
    assert gatelib.git(repository(tmp_path), "rev-parse", "--abbrev-ref", "HEAD") == "main"


def test_default_branches_prefer_the_remote_head_then_the_binding(tmp_path: Path) -> None:
    root = repository(tmp_path)
    assert gatelib.default_branches(root) == ["origin/main", "main", "origin/master", "master"]
    write(root, "docs/process/binding.md", "# Binding\n\nNo default branch line.\n")
    assert gatelib.default_branches(root)[0] == "origin/main"
    write(root, "docs/process/binding.md", "# Binding\n\n- Default branch: `trunk`\n")
    assert gatelib.default_branches(root)[:2] == ["origin/trunk", "trunk"]
    upstream = tmp_path.parent / f"{tmp_path.name}-upstream.git"
    git(root, "init", "-q", "--bare", str(upstream))
    git(root, "remote", "add", "origin", str(upstream))
    git(root, "push", "-q", "origin", "main")
    git(root, "remote", "set-head", "origin", "main")
    assert gatelib.default_branches(root)[0] == "origin/main"


def test_base_commit_is_the_merge_base_or_the_parent_on_the_default_branch(
    tmp_path: Path,
) -> None:
    root = repository(tmp_path)
    assert gatelib.base_commit(root) is None, "one commit on main has no before"
    first = git(root, "rev-parse", "HEAD")
    second = commit(root, "feat(core): second")
    assert gatelib.base_commit(root) == first, "on main, before is HEAD~1"
    branch(root)
    assert gatelib.base_commit(root) == second, "a fresh branch compares with its tip"
    write(root, "x.txt", "x\n")
    third = commit(root, "feat(core): third")
    assert gatelib.base_commit(root) == second
    assert gatelib.base_commit(root, "main") == second
    assert gatelib.base_commit(root, "nope") == second, "an unknown base falls back to HEAD~1"
    assert third != second


def test_changed_and_tracked_paths_see_the_work_tree(tmp_path: Path) -> None:
    root = repository(tmp_path, {"a.txt": "a\n", "b/c.txt": "c\n"})
    base = git(root, "rev-parse", "HEAD")
    branch(root)
    write(root, "a.txt", "changed\n")
    write(root, "new.txt", "new\n")
    (root / "b/c.txt").unlink()
    assert gatelib.changed_paths(root, base) == ["a.txt", "b/c.txt", "new.txt"]
    assert gatelib.tracked_paths(root) == ["a.txt", "new.txt"]
    assert gatelib.tracked_paths(root, base) == ["a.txt", "b/c.txt"]
    assert gatelib.show(root, base, "a.txt") == "a"
    assert gatelib.show(root, base, "missing") is None
    assert gatelib.changed_paths(tmp_path / "nowhere", base) == []
    assert gatelib.tracked_paths(tmp_path / "nowhere") == []
    assert gatelib.tracked_paths(tmp_path / "nowhere", "HEAD") == []


def test_subjects_skip_merges(tmp_path: Path) -> None:
    root = repository(tmp_path)
    base = git(root, "rev-parse", "HEAD")
    branch(root)
    commit(root, "feat(core): one")
    git(root, "checkout", "-q", "main")
    commit(root, "feat(core): two")
    git(root, "merge", "-q", "--no-ff", "--no-edit", "change")
    assert gatelib.subjects(root, base) == ["feat(core): two", "feat(core): one"]
    assert gatelib.subjects(tmp_path / "nowhere", base) == []


def test_read_text_handles_missing_and_binary_files(tmp_path: Path) -> None:
    assert gatelib.read_text(tmp_path / "missing") is None
    (tmp_path / "bin").write_bytes(b"\x00\x01")
    assert gatelib.read_text(tmp_path / "bin") is None
    (tmp_path / "text").write_bytes(b"caf\xc3\xa9 \xff")
    assert gatelib.read_text(tmp_path / "text") == "café �"


def test_thresholds_and_tables(tmp_path: Path) -> None:
    assert gatelib.thresholds(tmp_path) == {}
    write(tmp_path, "quality/thresholds.toml", "[ceilings.change]\nfiles = 3\n")
    data = gatelib.thresholds(tmp_path)
    assert gatelib.table(data, "ceilings", "change") == {"files": 3}
    assert gatelib.table(data, "ceilings", "change", "files") == {}
    assert gatelib.table(data, "nope") == {}


def test_matches_spans_segments_with_a_double_star() -> None:
    assert gatelib.matches("src/core/a.py", ["src/core/**"])
    assert not gatelib.matches("src/core/a.py", ["src/*.py"])
    assert not gatelib.matches("src/core/a.py", [])


def test_report_prints_each_problem_and_the_verdict(capsys: pytest.CaptureFixture[str]) -> None:
    assert gatelib.report("x", []) == 0
    assert capsys.readouterr().out == "x: green\n"
    assert gatelib.report("x", ["one", "two"], "abcdef0123456789") == 1
    assert capsys.readouterr().out == "one\ntwo\nx: red against abcdef012345\n"
