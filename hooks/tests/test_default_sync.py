import io
import json
from pathlib import Path

import pytest

from justfile_text import definition
from ratchet_guards import hook
from ratchet_guards.manifest import MANIFEST_PATH
from tests.repos import commit, git, repository, write
from tests.support import JUSTFILE, MANIFEST_TEXT, MERGE_JOB, MERGE_WORKFLOW
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


def without(*names: str) -> str:
    text = JUSTFILE
    for name in names:
        text = text.replace("\n".join(definition(text, name)) + "\n", "")
    return text


def cloned(
    tmp_path: Path, *, adopted: bool = False, justfile: str | None = JUSTFILE, job: bool = False
) -> Path:
    source = tmp_path / "source"
    source.mkdir(parents=True)
    recipes = {} if justfile is None else {"justfile": justfile}
    recipes |= {MERGE_JOB: MERGE_WORKFLOW} if job else {}
    repository(source, {MANIFEST_PATH: MANIFEST_TEXT, **recipes} if adopted else None)
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


def answer(root: Path, command: str) -> object:
    payload = {"tool_name": "Bash", "tool_input": {"command": command}, "cwd": str(root)}
    stdout = io.StringIO()
    assert hook.main(root, io.StringIO(json.dumps(payload)), stdout) == 0
    return json.loads(stdout.getvalue())["hookSpecificOutput"]


MERGES = ["gh pr merge 5", "git merge --no-ff feature/x", "git cherry-pick feature/x"]


@pytest.mark.parametrize("command", MERGES)
@pytest.mark.parametrize(
    "justfile",
    [None, without("merge-ready"), without("merge"), without("merge-ready", "merge")],
    ids=["no justfile", "no merge-ready", "no merge", "neither"],
)
def test_a_merge_where_the_default_branch_lacks_the_way_out_is_asked_as_before_adoption(
    tmp_path: Path, command: str, justfile: str | None
) -> None:
    unadopted = answer(cloned(tmp_path / "unadopted"), command)
    lacking = answer(cloned(tmp_path / "lacking", adopted=True, justfile=justfile), command)
    assert lacking == unadopted
    assert answer(cloned(tmp_path / "whole", adopted=True), command) != unadopted


@pytest.mark.parametrize("command", MERGES)
def test_a_refusal_names_the_merge_job_only_where_the_default_branch_holds_one(
    tmp_path: Path, command: str
) -> None:
    jobbed = answer(cloned(tmp_path / "jobbed", adopted=True, job=True), command)
    root = cloned(tmp_path / "jobless", adopted=True)
    write(root, MERGE_JOB, MERGE_WORKFLOW)
    jobless = answer(root, command)
    assert jobbed["permissionDecision"] == jobless["permissionDecision"] == "deny"
    assert "Leave it to" in jobbed["permissionDecisionReason"]
    assert "Leave it to" not in jobless["permissionDecisionReason"]
    assert "merge job merges" not in jobless["permissionDecisionReason"]
    assert "holds no merge job" in jobless["permissionDecisionReason"]


@pytest.mark.parametrize("command", MERGES)
def test_a_way_out_removed_outside_the_default_branch_still_refuses_the_merge(
    tmp_path: Path, command: str
) -> None:
    root = cloned(tmp_path, adopted=True)
    write(root, "justfile", without("merge-ready", "merge"))
    assert decision(root, "Bash", {"command": command}) == "deny"
    git(root, "config", "user.email", "t@example.invalid")
    git(root, "config", "user.name", "t")
    git(root, "switch", "-q", "-c", "strip")
    commit(root, "chore(core): drop the merge recipes on a branch")
    git(root, "switch", "-q", "main")
    assert decision(root, "Bash", {"command": command}) == "deny"
    git(root, "switch", "-q", "strip")
    assert decision(root, "Bash", {"command": "gh pr merge 5"}) == "deny"


@pytest.mark.parametrize("adopted", [False, True])
@pytest.mark.parametrize(
    "command",
    ["git -c core.abbrev=12 pull origin main", "git -c core.abbrev=12 pull --ff-only origin main"],
)
def test_a_setting_on_a_pull_that_names_the_remote_and_branch_makes_it_a_merge(
    tmp_path: Path, *, command: str, adopted: bool
) -> None:
    root = cloned(tmp_path, adopted=adopted)
    assert decision(root, "Bash", {"command": command.replace("-c core.abbrev=12 ", "")}) == ""
    assert decision(root, "Bash", {"command": command}) == ANSWER[adopted]


UNREAD_PULLS = [
    "GIT_DIR=.git git pull -q origin main",
    "git --git-dir=.git pull -q --ff-only origin main",
    "export GIT_WORK_TREE=.; git pull -q origin main",
    "echo hi && git pull -q origin main",
]


@pytest.mark.parametrize("adopted", [False, True])
@pytest.mark.parametrize("command", UNREAD_PULLS)
def test_a_pull_that_names_the_remote_and_branch_on_a_line_the_guard_cannot_read_is_a_merge(
    tmp_path: Path, *, command: str, adopted: bool
) -> None:
    root = cloned(tmp_path, adopted=adopted)
    assert decision(root, "Bash", {"command": "git pull -q origin main"}) == ""
    assert decision(root, "Bash", {"command": command}) == ANSWER[adopted]


INERT = [
    "git -c color.ui=always pull -q",
    "git -c color.ui=false -c advice.detachedHead=false pull -q --ff-only origin main",
    "git -c color.ui merge -q --ff-only origin/main",
    "X=never git --config-env=color.ui=X merge -q @{u}",
]


@pytest.mark.parametrize("adopted", [False, True])
@pytest.mark.parametrize("command", INERT)
def test_a_setting_that_only_changes_what_git_prints_leaves_a_sync_a_sync(
    tmp_path: Path, *, command: str, adopted: bool
) -> None:
    root = cloned(tmp_path, adopted=adopted)
    assert decision(root, "Bash", {"command": command}) == ""
    source = tmp_path / "source"
    write(source, "forged.txt", "merged by the forge\n")
    commit(source, "feat(core): merged by the forge")
    git(root, "fetch", "-q")
    run(root, command)
    assert git(root, "rev-parse", "main") == git(source, "rev-parse", "main")
    assert not moved(root)


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
