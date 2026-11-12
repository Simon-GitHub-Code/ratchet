"""The committed manifest: which ref is the default branch, and what it carries."""

import subprocess
from collections.abc import Sequence
from pathlib import Path

import pytest

from ratchet_guards import committed
from tests.repos import commit, git, repository, write

ROOT = Path("/repo")


def fake_git(answers: dict[str, str | None]) -> committed.Git:
    def run(cwd: Path, args: Sequence[str]) -> str | None:
        assert cwd == ROOT
        return answers.get(" ".join(args))

    return run


def exists(ref: str) -> str:
    return f"show-ref --verify --hash {ref}"


def test_the_remote_head_names_the_default_branch() -> None:
    answers = {
        "symbolic-ref refs/remotes/origin/HEAD": "refs/remotes/origin/trunk",
        exists("refs/remotes/origin/trunk"): "abc",
    }
    assert committed.default_branch(ROOT, fake_git(answers)) == "refs/remotes/origin/trunk"


def test_a_remote_head_that_names_nothing_is_skipped() -> None:
    answers = {
        "symbolic-ref refs/remotes/origin/HEAD": "refs/remotes/origin/trunk",
        exists("refs/heads/main"): "abc",
    }
    assert committed.default_branch(ROOT, fake_git(answers)) == "refs/heads/main"


def test_fallback_refs_are_tried_in_order() -> None:
    assert committed.default_branch(ROOT, fake_git({exists("refs/heads/master"): "abc"})) == (
        "refs/heads/master"
    )
    both = {exists("refs/heads/master"): "abc", exists("refs/remotes/origin/master"): "abc"}
    assert committed.default_branch(ROOT, fake_git(both)) == "refs/remotes/origin/master"
    assert committed.default_branch(ROOT, fake_git({})) is None


def test_the_manifest_is_shown_from_the_default_branch() -> None:
    answers = {
        exists("refs/remotes/origin/main"): "abc",
        "show refs/remotes/origin/main:guards/manifest.toml": "[[group]]\n",
    }
    assert committed.committed_manifest(ROOT, fake_git(answers)) == "[[group]]\n"
    assert committed.committed_manifest(ROOT, fake_git({})) is None
    absent = {exists("refs/heads/main"): "abc"}
    assert committed.committed_manifest(ROOT, fake_git(absent)) is None


def test_run_git_answers_none_when_git_fails(tmp_path: Path) -> None:
    assert committed.run_git(tmp_path, ["rev-parse", "--verify", "--quiet", "nope"]) is None
    assert committed.run_git(tmp_path / "missing", ["status"]) is None


def test_run_git_answers_none_when_git_is_missing_or_hangs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("PATH", str(tmp_path))
    assert committed.run_git(tmp_path, ["status"]) is None
    monkeypatch.undo()

    def hang(*_args: object, **_kwargs: object) -> None:
        raise subprocess.TimeoutExpired(["git"], committed.GIT_TIMEOUT_SECONDS)

    monkeypatch.setattr(subprocess, "run", hang)
    assert committed.run_git(tmp_path, ["status"]) is None


@pytest.mark.parametrize("shadow", ["tags/main", "heads/refs/heads/main", "refs/heads/main"])
def test_a_ref_under_the_short_name_does_not_stand_in_for_the_branch(
    tmp_path: Path, shadow: str
) -> None:
    root = repository(tmp_path, {"README.md": "# A\n"})
    first = git(root, "rev-parse", "HEAD")
    write(root, "guards/manifest.toml", "[[group]]\n")
    commit(root, "chore(guards): the manifest")
    git(root, "update-ref", f"refs/{shadow}", first)
    assert committed.default_branch(root) == "refs/heads/main"
    assert committed.committed_manifest(root) == "[[group]]"
