import subprocess
from pathlib import Path

import pytest

from first_hour import merges
from first_hour.merges import How, Landing, Window


def git(repo: Path, *args: str) -> str:
    done = subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True,
        text=True,
        check=True,
        env={
            "GIT_CONFIG_GLOBAL": "/dev/null",
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_AUTHOR_NAME": "o",
            "GIT_AUTHOR_EMAIL": "o@x.invalid",
            "GIT_COMMITTER_NAME": "o",
            "GIT_COMMITTER_EMAIL": "o@x.invalid",
            "PATH": "/usr/bin:/bin",
        },
    )
    return done.stdout


def branch_with_a_commit(repo: Path, name: str) -> None:
    git(repo, "switch", "-q", "-c", name, "main")
    (repo / f"{name.replace('/', '-')}.txt").write_text(name)
    git(repo, "add", ".")
    git(repo, "commit", "-q", "-m", f"work on {name}")
    git(repo, "switch", "-q", "main")


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    git(tmp_path, "init", "-q", "-b", "main")
    git(tmp_path, "commit", "-q", "--allow-empty", "-m", "start")
    return tmp_path


def reflog(repo: Path) -> str:
    return git(repo, "log", "-g", f"--format={merges.REFLOG_FORMAT}", "--date=unix", "main")


def test_every_way_onto_the_default_branch_is_a_landing_fast_forward_or_not(repo: Path) -> None:
    branch_with_a_commit(repo, "feature/by-hand")
    git(repo, "merge", "-q", "--no-ff", "feature/by-hand")
    branch_with_a_commit(repo, "feature/fast")
    git(repo, "merge", "-q", "--ff-only", "feature/fast")
    branch_with_a_commit(repo, "feature/entry")
    tree = repo / "tree"
    git(repo, "worktree", "add", "-q", "--detach", str(tree), "main")
    git(tree, "merge", "-q", "--no-ff", "-m", "Merge branch 'feature/entry'", "feature/entry")
    landed = git(tree, "rev-parse", "HEAD").strip()
    git(repo, "merge", "-q", "--ff-only", landed)
    branch_with_a_commit(repo, "feature/detached")
    git(tree, "merge", "-q", "--no-ff", "-m", "Merge branch 'feature/detached'", "feature/detached")
    old = git(repo, "rev-parse", "main").strip()
    new = git(tree, "rev-parse", "HEAD").strip()
    git(repo, "update-ref", "-m", "merge.py", "refs/heads/main", new, old)
    git(repo, "commit", "-q", "--allow-empty", "-m", "not a merge")

    found = merges.landings(reflog(repo))

    assert [(landing.branch, landing.message.split(":")[0]) for landing in found] == [
        ("feature/by-hand", "merge feature/by-hand"),
        ("feature/fast", "merge feature/fast"),
        ("feature/entry", f"merge {landed}"),
        ("feature/detached", "merge.py"),
    ]
    assert all(landing.at > 1_700_000_000 for landing in found)


def test_a_line_that_is_not_a_reflog_entry_is_skipped() -> None:
    assert merges.landings("garbage\nabc\tmain\tmerge x: y\n") == []
    assert merges.landings("abc\tmain@{}\tmerge x: Fast-forward\tsubject\n") == []


def landing(branch: str, at: float) -> Landing:
    return Landing(sha="a" * 40, at=at, branch=branch, message=f"merge {branch}: Fast-forward")


WINDOWS = [
    Window("git switch main && git merge --no-ff feature/1-save", 100.0, 110.0),
    Window("just merge feature/2-list", 200.0, 260.0),
]


@pytest.mark.parametrize(
    ("merged", "expected"),
    [
        (landing("feature/1-save", 104), How.HAND),
        (landing("feature/2-list", 230), How.ENTRY_POINT),
        (landing("feature/2-list", 261), How.ENTRY_POINT),
        (landing("feature/3-mark", 105), How.AGENT),
        (landing("feature/3-mark", 230), How.AGENT),
        (landing("feature/1-save", 300), How.AGENT),
        (landing("feature/1", 104), How.AGENT),
        (landing("", 104), How.AGENT),
    ],
)
def test_a_merge_is_the_owners_only_if_their_paste_named_it_while_it_landed(
    merged: Landing, expected: How
) -> None:
    assert merges.how(merged, WINDOWS) is expected


def test_only_landings_not_seen_before_are_new() -> None:
    first, second = landing("a", 1), landing("b", 2)
    assert merges.new([first], [first, second]) == [second]
