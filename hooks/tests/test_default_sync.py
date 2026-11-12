"""On a default branch only the remote's own default branch comes in without the owner.

``git merge --ff-only @{u}``, ``git merge --ff-only origin/main`` and a bare ``git pull`` are how
a checkout catches up with the remote, and the hook lets them through. Each names what it merges
through something an agent can set: the branch's upstream, or a short name that a local branch,
a tag or a stray ref of the same spelling is read before. The first test shows that each
tampering does bring another branch onto ``main`` when nothing stands in the way. The others ask
the hook as a tool call would, after the tampering and on the same command line (ADR 0024).
"""

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
    """A clone on ``main``, level with the remote, beside a ``feature/x`` that is ahead of it."""
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
    """Whether ``main`` now holds the work of ``feature/x``."""
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
    """A branch made from ``origin/main``, as ``git switch -c`` leaves it: its upstream is main."""
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
