from pathlib import Path

import pytest

from ratchet_guards.manifest import MANIFEST_PATH
from tests.repos import commit, git, repository, write
from tests.support import MANIFEST_TEXT
from tests.test_default_ref import decision, run

UPSTREAM = [
    "git merge --ff-only @{u}",
    "git merge --ff-only",
    "git merge @{u}",
    "git merge --no-edit @{upstream}",
    "git merge",
    "git pull",
    "git pull origin",
]
NAMED = [
    "git merge --ff-only origin/main",
    "git merge origin/main",
    "git merge --no-edit origin/main",
]
TAMPERS: dict[str, list[str]] = {
    "git branch -u feature/x": UPSTREAM[:-1],
    "git branch --set-upstream-to=feature/x main": UPSTREAM[:-1],
    "git branch -u origin/feature/x": UPSTREAM,
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
OTHER = (
    "git init -q --bare ../other.git && git push -q ../other.git origin/feature/x:refs/heads/main"
    " && git remote add other ../other.git && git fetch -q other"
)
NEVER_SYNCS: dict[str, list[str]] = {
    "git fetch -q origin feature/x": ["git merge FETCH_HEAD", "git merge --ff-only FETCH_HEAD"],
    OTHER: ["git merge other/main", "git merge --ff-only other/main", "git pull other main"],
    "true": [
        "git merge origin/main origin/feature/x",
        "git merge --ff-only origin/main feature/x",
        "git -c branch.main.merge=refs/heads/feature/x pull --no-rebase",
        "git -c branch.main.merge=refs/heads/feature/x merge --ff-only",
    ],
}
ATTACKS = [
    pytest.param(setup, command, id=f"{setup}; {command}")
    for setup, commands in NEVER_SYNCS.items()
    for command in commands
]
ANSWER = {False: "ask", True: "deny"}


def cloned(tmp_path: Path, *, adopted: bool = False) -> Path:
    source = tmp_path / "source"
    source.mkdir()
    repository(source, {MANIFEST_PATH: MANIFEST_TEXT} if adopted else None)
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


@pytest.mark.parametrize("adopted", [False, True])
@pytest.mark.parametrize(("tamper", "sync"), CASES)
def test_a_sync_that_git_reads_as_another_branch_is_the_owners(
    tmp_path: Path, *, tamper: str, sync: str, adopted: bool
) -> None:
    root = cloned(tmp_path, adopted=adopted)
    assert decision(root, "Bash", {"command": sync}) == ""
    run(root, tamper)
    assert decision(root, "Bash", {"command": sync}) == ANSWER[adopted]


@pytest.mark.parametrize(("setup", "command"), ATTACKS)
def test_a_merge_that_only_looks_like_a_sync_brings_another_branch_onto_main(
    tmp_path: Path, setup: str, command: str
) -> None:
    root = cloned(tmp_path)
    run(root, setup)
    run(root, command)
    assert git(root, "rev-parse", "main^{tree}") == git(root, "rev-parse", "feature/x^{tree}")


@pytest.mark.parametrize("adopted", [False, True])
@pytest.mark.parametrize(("setup", "command"), ATTACKS)
def test_a_merge_that_only_looks_like_a_sync_is_the_owners(
    tmp_path: Path, *, setup: str, command: str, adopted: bool
) -> None:
    root = cloned(tmp_path, adopted=adopted)
    run(root, setup)
    assert decision(root, "Bash", {"command": command}) == ANSWER[adopted]


@pytest.mark.parametrize(
    "command",
    [
        "git merge --no-ff feature/x",
        "git merge --ff-only origin/feature/x",
        "git cherry-pick feature/x",
        "git read-tree -m -u feature/x",
    ],
)
def test_a_merge_tried_on_a_detached_head_after_its_own_switch_passes(
    tmp_path: Path, command: str
) -> None:
    root = cloned(tmp_path, adopted=True)
    assert decision(root, "Bash", {"command": command}) == "deny"
    assert decision(root, "Bash", {"command": "git switch --detach"}) == ""
    run(root, "git switch -q --detach")
    assert decision(root, "Bash", {"command": command}) == ""


@pytest.mark.parametrize(("tamper", "sync"), CASES)
def test_a_sync_on_the_line_that_changed_what_it_names_asks_the_owner(
    tmp_path: Path, tamper: str, sync: str
) -> None:
    root = cloned(tmp_path)
    assert decision(root, "Bash", {"command": f"{tamper} && {sync}"}) in {"ask", "deny"}
    assert decision(root, "Bash", {"command": f"{tamper}; {sync}"}) in {"ask", "deny"}


@pytest.mark.parametrize("adopted", [False, True])
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
        "git merge origin/main",
        "git merge --no-edit origin/main",
        "git merge --ff-only origin/main",
        "git fetch origin && git merge --no-edit origin/main",
        "git merge --abort",
    ],
)
def test_the_everyday_sync_passes(tmp_path: Path, command: str, adopted: bool) -> None:  # noqa: FBT001 # -- a parametrized test argument
    assert decision(cloned(tmp_path, adopted=adopted), "Bash", {"command": command}) == ""


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
