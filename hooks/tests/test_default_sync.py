from pathlib import Path

import pytest

from tests.repos import commit, git, repository, write
from tests.test_default_ref import decision, run

UPSTREAM = ["git merge --ff-only @{u}", "git merge --ff-only", "git pull", "git pull origin"]
NAMED = ["git merge --ff-only origin/main"]
TAMPERS: dict[str, list[str]] = {
    "git branch -u feature/x": UPSTREAM[:3],
    "git branch --set-upstream-to=feature/x main": UPSTREAM[:3],
    "git config branch.main.merge refs/heads/feature/x": UPSTREAM,
    "git fetch -q --set-upstream origin feature/x": UPSTREAM,
    "git branch origin/main feature/x": NAMED,
    "git tag origin/main feature/x": NAMED,
    "git update-ref refs/origin/main feature/x": NAMED,
    "mkdir .git/origin && cp .git/refs/heads/feature/x .git/origin/main": NAMED,
}
CASES = [
    pytest.param(tamper, sync, id=f"{tamper}; {sync}")
    for tamper, syncs in TAMPERS.items()
    for sync in syncs
]


def cloned(tmp_path: Path) -> Path:
    source = tmp_path / "source"
    source.mkdir()
    repository(source)
    git(source, "switch", "-q", "-c", "feature/x")
    write(source, "work.txt", "work\n")
    commit(source, "feat(core): work nobody judged")
    git(source, "switch", "-q", "main")
    clone = tmp_path / "clone"
    git(tmp_path, "clone", "-q", str(source), str(clone))
    git(clone, "branch", "-q", "--no-track", "feature/x", "origin/feature/x")
    return clone


def moved(root: Path) -> bool:
    return git(root, "rev-parse", "main") == git(root, "rev-parse", "feature/x")


@pytest.mark.parametrize(("tamper", "sync"), CASES)
def test_the_sync_brings_another_branch_onto_main_when_nothing_stops_it(
    tmp_path: Path, tamper: str, sync: str
) -> None:
    root = cloned(tmp_path)
    run(root, tamper)
    run(root, sync)
    assert moved(root)


@pytest.mark.parametrize(("tamper", "sync"), CASES)
def test_a_sync_that_git_reads_as_another_branch_asks_the_owner(
    tmp_path: Path, tamper: str, sync: str
) -> None:
    root = cloned(tmp_path)
    assert decision(root, "Bash", {"command": sync}) == ""
    run(root, tamper)
    assert decision(root, "Bash", {"command": sync}) == "ask"


@pytest.mark.parametrize(("tamper", "sync"), CASES)
def test_a_sync_on_the_line_that_changed_what_it_names_asks_the_owner(
    tmp_path: Path, tamper: str, sync: str
) -> None:
    root = cloned(tmp_path)
    assert decision(root, "Bash", {"command": f"{tamper} && {sync}"}) in {"ask", "deny"}
    assert decision(root, "Bash", {"command": f"{tamper}; {sync}"}) in {"ask", "deny"}


@pytest.mark.parametrize(
    "command",
    [
        "git pull",
        "git pull --ff-only",
        "git pull origin main",
        "git fetch origin && git merge --ff-only origin/main",
        "git switch main && git pull",
        "git checkout main && git merge --ff-only @{upstream}",
        "git status && git stash && git pull",
    ],
)
def test_the_everyday_sync_passes(tmp_path: Path, command: str) -> None:
    assert decision(cloned(tmp_path), "Bash", {"command": command}) == ""


def tracking_main(tmp_path: Path) -> Path:
    root = cloned(tmp_path)
    git(root, "config", "user.email", "t@example.invalid")
    git(root, "config", "user.name", "t")
    git(root, "switch", "-q", "-c", "feat/y", "origin/main")
    write(root, "y.txt", "y\n")
    commit(root, "feat(core): y")
    return root


@pytest.mark.parametrize("command", ["git push", "git push origin feat/y"])
def test_a_push_that_git_sends_to_the_upstream_is_a_push_to_main(
    tmp_path: Path, command: str
) -> None:
    root = tracking_main(tmp_path)
    assert decision(root, "Bash", {"command": "git push origin feat/y"}) == ""
    assert decision(root, "Bash", {"command": f"git -c push.default=upstream {command[4:]}"}) == (
        "deny"
    )
    git(root, "config", "push.default", "upstream")
    assert decision(root, "Bash", {"command": command}) == "deny"
    git(root.parent / "source", "switch", "-q", "--detach")
    run(root, command)
    assert git(root.parent / "source", "rev-parse", "main") == git(root, "rev-parse", "feat/y")
