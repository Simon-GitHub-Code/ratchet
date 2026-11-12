"""``merge.py``: the merge with no forge lands what the judge and ``check`` both pass, only that."""

import json
from pathlib import Path

import pytest

import merge
import merge_ready
from merge_ready import Reason
from tests.repos import branch, commit, git, repository, write
from tests.test_merge_ready import BASE, VERDICT

FEATURE = "feature/4-thing"
BOTH = "python3 -c \"import os, sys; sys.exit(not all(map(os.path.exists, 'mb')))\""


def binding(check: str | None = "true", deep: str | None = "just check-deep") -> str:
    rows = [("check", check), ("check-deep", deep)]
    table = "".join(f"| {name} | `{command}` |\n" for name, command in rows if command)
    return f"# Binding\n\n## Entry points\n\n| Entry point | Command |\n| --- | --- |\n{table}"


def project(root: Path, files: dict[str, str] | None = None, **bound: str | None) -> Path:
    """A repository on ``main`` with the binding, and ``feature/4-thing`` checked out."""
    repository(root, {**BASE, "docs/process/binding.md": binding(**bound)})
    branch(root, FEATURE)
    for path, text in (files or {"b": "the branch's\n"}).items():
        write(root, path, text)
    commit(root, "feat(thing): the change")
    assert merge_ready.write_record(root, json.dumps(VERDICT)) == 0
    commit(root, "docs(thing): the verdict record")
    return root


def run(root: Path, name: str = FEATURE) -> int:
    return merge.main(["merge", name, "--root", str(root)])


def tips(root: Path) -> tuple[str, str]:
    return git(root, "rev-parse", "main"), git(root, "rev-parse", FEATURE)


def test_a_clean_change_is_merged_and_tier_2_is_named_next(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = project(tmp_path)
    was, head = tips(root)
    capsys.readouterr()
    assert run(root) == 0
    merged = git(root, "rev-parse", "main")
    assert git(root, "rev-list", "--parents", "-1", "main").split() == [merged, was, head]
    assert git(root, "log", "-1", "--format=%s", "main") == f"Merge branch '{FEATURE}'"
    assert capsys.readouterr().out == (
        f"merge: agent\nmerged: `{FEATURE}` into `main` at {merged[:12]}\n"
        "next: just check-deep on `main`: tier 2 runs after every merge, and a red one comes"
        " first\n"
    )
    assert git(root, "worktree", "list", "--porcelain").count("worktree ") == 1
    assert git(root, "rev-parse", "--abbrev-ref", "HEAD") == FEATURE


def test_a_checked_out_default_branch_moves_with_its_work_tree(tmp_path: Path) -> None:
    root = project(tmp_path)
    git(root, "checkout", "-q", "main")
    assert not (root / "b").exists()
    assert run(root) == 0
    assert (root / "b").read_text(encoding="utf-8") == "the branch's\n"
    assert git(root, "status", "--porcelain") == ""


def test_check_runs_on_the_merge_result_and_not_on_the_branch(tmp_path: Path) -> None:
    root = project(tmp_path, check=BOTH)
    git(root, "checkout", "-q", "main")
    write(root, "m", "main's\n")
    commit(root, "feat(core): merged meanwhile")
    git(root, "checkout", "-q", FEATURE)
    assert not (root / "m").exists(), "the branch alone would fail the check"
    assert run(root) == 0


def test_a_change_the_judge_leaves_to_the_owner_is_not_merged(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = project(tmp_path, {"justfile": "check:\n    false\n"})
    before = tips(root)
    capsys.readouterr()
    assert run(root) == 1
    assert capsys.readouterr().out == (
        "merge: owner\nguard: justfile (gate-commands)\n"
        f"next: the owner types `git switch main && git merge --no-ff {FEATURE}`, then"
        " `just check-deep` on `main`: no gate ran on this merge, and tier 2 runs every gate\n"
    )
    assert tips(root) == before
    assert git(root, "worktree", "list", "--porcelain").count("worktree ") == 1


@pytest.mark.parametrize(
    ("check", "reason"),
    [
        ("false", "gates: `false` is red on the merge result (exit 1)"),
        (None, "gates: the binding names no `check` entry point, so no gate ran"),
    ],
)
def test_a_red_or_missing_check_merges_nothing(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], *, check: str | None, reason: str
) -> None:
    root = project(tmp_path, check=check)
    before = tips(root)
    capsys.readouterr()
    assert run(root) == 1
    assert capsys.readouterr().out == f"merge: owner\n{reason}\n"
    assert tips(root) == before


def test_a_check_that_cannot_start_merges_nothing(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = project(tmp_path, check="no-such-runner-here check")
    capsys.readouterr()
    assert run(root) == 1
    out = capsys.readouterr().out
    assert out.startswith("merge: owner\ngates: `no-such-runner-here check` could not run on the")


def test_a_branch_that_conflicts_is_not_merged(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = project(tmp_path, {"src/a.py": "x = 2\n"})
    git(root, "checkout", "-q", "main")
    write(root, "src/a.py", "x = 3\n")
    commit(root, "feat(core): the other way")
    before = tips(root)
    capsys.readouterr()
    assert run(root) == 1
    assert capsys.readouterr().out == (
        f"merge: owner\nconflict: `{FEATURE}` does not merge into `main` cleanly\n"
    )
    assert tips(root) == before


def test_a_default_branch_that_cannot_move_forward_is_left_alone(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    root = project(tmp_path)

    def moves_main(tree: Path) -> Reason | None:
        del tree
        git(root, "update-ref", "refs/heads/main", git(root, "rev-parse", FEATURE))
        return None

    monkeypatch.setattr(merge, "run_check", moves_main)
    capsys.readouterr()
    assert run(root) == 1
    assert capsys.readouterr().out.startswith("merge: owner\nmoved: `main` could not be moved")


def test_a_dirty_default_work_tree_is_not_overwritten(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = project(tmp_path)
    git(root, "checkout", "-q", "main")
    write(root, "b", "the owner's, uncommitted\n")
    before = tips(root)
    assert run(root) == 1
    assert "moved: `main` could not be moved forward" in capsys.readouterr().out
    assert tips(root) == before
    assert (root / "b").read_text(encoding="utf-8") == "the owner's, uncommitted\n"


def test_a_work_tree_that_cannot_be_made_merges_nothing(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "repo").mkdir()
    root = project(tmp_path / "repo")
    taken = tmp_path / "taken"
    (taken / "tree").mkdir(parents=True)
    (taken / "tree/file").write_text("in the way\n", encoding="utf-8")
    monkeypatch.setattr(merge.tempfile, "mkdtemp", lambda **_: str(taken))
    capsys.readouterr()
    assert run(root) == 1
    assert capsys.readouterr().out.startswith("merge: owner\nrefs: no work tree of `main` could")


def test_with_a_forge_the_merge_job_merges(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = project(tmp_path)
    git(root, "remote", "add", "origin", "https://example.invalid/x.git")
    capsys.readouterr()
    assert run(root) == 2
    assert capsys.readouterr().out == (
        "merge: this repository has a forge (`origin`): the pull request's merge job merges\n"
    )


@pytest.mark.parametrize("name", ["feature/9-missing", "main"])
def test_only_a_local_branch_other_than_the_default_is_merged(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], name: str
) -> None:
    root = project(tmp_path)
    capsys.readouterr()
    assert run(root, name) == 2
    assert capsys.readouterr().out == (
        f"merge: `{name}` is not a local branch to merge into a local default branch\n"
    )


def test_a_repository_with_no_default_branch_has_nothing_to_merge_into(tmp_path: Path) -> None:
    git(tmp_path, "init", "-q", "-b", "trunk")
    git(tmp_path, "config", "user.email", "t@example.invalid")
    git(tmp_path, "config", "user.name", "t")
    commit(tmp_path, "feat(core): first")
    assert run(tmp_path, "trunk") == 2


def test_outside_a_repository_there_is_nothing_to_merge(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert run(tmp_path) == 2
    assert capsys.readouterr().out == f"merge: {tmp_path} is not a git repository\n"


def test_a_binding_without_tier_2_still_says_what_comes_next(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = project(tmp_path, deep=None)
    capsys.readouterr()
    assert run(root) == 0
    assert "next: the binding's `check-deep` entry point on `main`" in capsys.readouterr().out
