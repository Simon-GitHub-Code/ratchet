"""The committed manifest: which ref is the default branch, and what it carries."""

from collections.abc import Sequence
from pathlib import Path

from ratchet_guards import committed

ROOT = Path("/repo")


def fake_git(answers: dict[str, str | None]) -> committed.Git:
    def git(cwd: Path, args: Sequence[str]) -> str | None:
        assert cwd == ROOT
        return answers.get(" ".join(args))

    return git


def test_the_remote_head_names_the_default_branch() -> None:
    git = fake_git({"symbolic-ref --short refs/remotes/origin/HEAD": "origin/trunk"})
    assert committed.default_branch(ROOT, git) == "origin/trunk"


def test_fallback_refs_are_tried_in_order() -> None:
    git = fake_git({"rev-parse --verify --quiet master^{commit}": "abc"})
    assert committed.default_branch(ROOT, git) == "master"
    assert committed.default_branch(ROOT, fake_git({})) is None


def test_the_manifest_is_shown_from_the_default_branch() -> None:
    answers = {
        "rev-parse --verify --quiet origin/main^{commit}": "abc",
        "show origin/main:guards/manifest.toml": "[[group]]\n",
    }
    assert committed.committed_manifest(ROOT, fake_git(answers)) == "[[group]]\n"
    assert committed.committed_manifest(ROOT, fake_git({})) is None
    absent = {"rev-parse --verify --quiet main^{commit}": "abc"}
    assert committed.committed_manifest(ROOT, fake_git(absent)) is None


def test_run_git_answers_none_when_git_fails(tmp_path: Path) -> None:
    assert committed.run_git(tmp_path, ["rev-parse", "--verify", "--quiet", "nope"]) is None
    assert committed.run_git(tmp_path / "missing", ["status"]) is None
