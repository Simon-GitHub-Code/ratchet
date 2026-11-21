import contextlib
import fcntl
import io
import json
import os
import select
import signal
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

import pytest

import drain
import merge
import merge_ready
import unproven
from merge_ready import Reason
from ratchet_guards import hook
from tests.repos import branch, commit, git, hook_script, repository, write
from tests.test_hook import checkout, switch_to_a_branch
from tests.test_merge_ready import BASE, FUZZ, GATES, VERDICT

FEATURE = "feature/4-thing"
BOTH = "python3 -c \"import os, sys; sys.exit(not all(map(os.path.exists, 'mb')))\""


def binding(check: str | None = "true", deep: str | None = "just check-deep") -> str:
    rows = [("check", check), ("check-deep", deep), ("merge", "just merge")]
    table = "".join(f"| {name} | `{command}` |\n" for name, command in rows if command)
    return f"# Binding\n\n## Entry points\n\n| Entry point | Command |\n| --- | --- |\n{table}"


def project(root: Path, files: dict[str, str] | None = None, **bound: str | None) -> Path:
    repository(root, {**BASE, "docs/process/binding.md": binding(**bound)})
    return feature(root, files)


def feature(root: Path, files: dict[str, str] | None = None) -> Path:
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
        f"merge: agent\nmerged: `{FEATURE}` into `main` at {merged[:12]}\n{PROVE_NEXT}\n"
    )
    assert git(root, "worktree", "list", "--porcelain").count("worktree ") == 1
    assert git(root, "rev-parse", "--abbrev-ref", "HEAD") == "main"
    assert git(root, "rev-parse", "HEAD") == merged
    assert git(root, "rev-parse", "--verify", f"refs/heads/{FEATURE}") == head


PROVE_NEXT = (
    "next: `just merge --deep` on `main`: tier 2 runs after every merge, and a red one comes first"
)
SWITCH_FIRST = (
    "next: `git switch main`, then `just merge --deep` on `main`: tier 2 runs after every merge,"
    " and a red one comes first"
)


def test_a_merge_made_from_another_checkout_leaves_it_alone(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = project(tmp_path)
    git(root, "switch", "--quiet", "--detach")
    capsys.readouterr()
    assert run(root) == 0
    assert capsys.readouterr().out.splitlines()[-1] == PROVE_NEXT
    assert git(root, "rev-parse", "--abbrev-ref", "HEAD") == "HEAD"


def test_a_merge_made_while_another_branch_is_checked_out_leaves_that_branch(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = project(tmp_path)
    git(root, "switch", "--quiet", "-c", "other")
    capsys.readouterr()
    assert run(root) == 0
    assert capsys.readouterr().out.splitlines()[-1] == PROVE_NEXT
    assert git(root, "rev-parse", "--abbrev-ref", "HEAD") == "other"


def test_a_dirty_checkout_stays_on_the_merged_branch_and_the_next_line_says_to_switch(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = project(tmp_path)
    write(root, "scratch.txt", "mine\n")
    capsys.readouterr()
    assert run(root) == 0
    assert capsys.readouterr().out.splitlines()[-1] == SWITCH_FIRST
    assert git(root, "rev-parse", "--abbrev-ref", "HEAD") == FEATURE


def test_a_default_branch_held_by_another_work_tree_leaves_the_checkout_and_says_to_switch(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = project(tmp_path / "repo")
    held = tmp_path / "held"
    git(root, "worktree", "add", "--quiet", str(held), "main")
    capsys.readouterr()
    assert run(root) == 0
    assert capsys.readouterr().out.splitlines()[-1] == SWITCH_FIRST
    assert git(root, "rev-parse", "--abbrev-ref", "HEAD") == FEATURE


def test_a_switch_that_fails_leaves_the_checkout_and_says_to_switch(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    root = project(tmp_path)
    real = merge.gatelib.git

    def refuse(tree: Path, *args: str, **options: str) -> str | None:
        return None if args[:1] == ("switch",) else real(tree, *args, **options)

    monkeypatch.setattr(merge.gatelib, "git", refuse)
    capsys.readouterr()
    assert run(root) == 0
    assert capsys.readouterr().out.splitlines()[-1] == SWITCH_FIRST
    assert git(root, "rev-parse", "--abbrev-ref", "HEAD") == FEATURE


def test_a_checked_out_default_branch_moves_with_its_work_tree(tmp_path: Path) -> None:
    root = project(tmp_path)
    git(root, "checkout", "-q", "main")
    assert not (root / "b").exists()
    assert run(root) == 0
    assert (root / "b").read_text(encoding="utf-8") == "the branch's\n"
    assert git(root, "status", "--porcelain") == ""


def test_a_branch_behind_the_default_branch_is_the_owners_and_nothing_lands(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = project(tmp_path, check=BOTH)
    git(root, "checkout", "-q", "main")
    write(root, "m", "main's\n")
    commit(root, "feat(core): merged meanwhile")
    git(root, "checkout", "-q", FEATURE)
    before = tips(root)
    capsys.readouterr()
    assert run(root) == 1
    assert "behind: the branch does not hold the tip of `main`" in capsys.readouterr().out
    assert tips(root) == before, "its verdict was given on another tree: update, judge, then land"


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

    def moves_main(tree: Path, entry: str) -> Reason | None:
        del tree, entry
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
    assert capsys.readouterr().out.splitlines()[-1] == PROVE_NEXT


@pytest.mark.parametrize(("deep", "code"), [("true", 0), ("false", 1)])
def test_the_adoption_lands_on_its_own_tier_2_and_not_on_check(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], *, deep: str, code: int
) -> None:
    repository(tmp_path, {"README.md": "# A\n"})
    branch(tmp_path, "adopt-ratchet")
    write(tmp_path, "guards/manifest.toml", merge_ready.TEMPLATE.read_text(encoding="utf-8"))
    write(tmp_path, "docs/process/binding.md", binding(check="false", deep=deep))
    commit(tmp_path, "chore(method): adopt")
    capsys.readouterr()
    assert run(tmp_path, "adopt-ratchet") == code
    out = capsys.readouterr().out
    assert ("merged: `adopt-ratchet` into `main`" in out) is (code == 0)
    assert ("gates: `false` is red on the merge result" in out) is (code == 1)


SPEC = ".scratch/thing/spec.md"


def on_main(root: Path, files: dict[str, str]) -> Path:
    repository(root, {**BASE, "docs/process/binding.md": binding()})
    for path, text in files.items():
        write(root, path, text)
    return root


def records(root: Path) -> int:
    return merge.main(["merge", "--records", "--root", str(root)])


def record_branches(root: Path) -> list[str]:
    return git(root, "branch", "--list", "records/*", "--format=%(refname:short)").split()


def test_records_alone_land_with_nothing_committed_on_main(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = on_main(tmp_path, {SPEC: "# Thing\n", ".scratch/changes/x.md": "## Summary\n"})
    was = git(root, "rev-parse", "main")
    capsys.readouterr()
    assert records(root) == 0
    out = capsys.readouterr().out
    assert out.startswith("records: 2 path(s) committed on `records/")
    assert "merge: agent\nmerged: `records/" in out
    first, second = git(root, "rev-list", "--parents", "-1", "main").split()[1:]
    assert first == was
    assert git(root, "log", "-1", "--format=%s", second) == merge.SUBJECT
    assert git(root, "rev-list", "--count", f"{was}..main") == "2"
    assert git(root, "status", "--porcelain") == ""
    assert git(root, "rev-parse", "--abbrev-ref", "HEAD") == "main"
    assert (root / SPEC).read_text(encoding="utf-8") == "# Thing\n"
    assert record_branches(root) == []


def test_records_the_judge_leaves_to_the_owner_wait_on_their_branch(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = on_main(tmp_path, {".scratch/thing/issues/01-a.md": "Status: ready-for-agent\n"})
    was = git(root, "rev-parse", "main")
    capsys.readouterr()
    assert records(root) == 1
    out = capsys.readouterr().out
    (waiting,) = record_branches(root)
    assert "gives a ticket the agent-ready label `ready-for-agent`" in out
    assert f"the owner types `git switch main && git merge --no-ff {waiting}`" in out
    assert git(root, "rev-parse", "main") == was
    assert git(root, "status", "--porcelain") == ""


@pytest.mark.parametrize(
    ("files", "said"),
    [
        ({SPEC: "# Thing\n", "src/a.py": "x = 2\n"}, "the work tree holds src/a.py;"),
        ({}, "the work tree holds nothing uncommitted;"),
    ],
)
def test_more_than_records_is_not_landed_as_records(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], *, files: dict[str, str], said: str
) -> None:
    root = on_main(tmp_path, files)
    status = git(root, "status", "--porcelain")
    capsys.readouterr()
    assert records(root) == 2
    assert said in capsys.readouterr().out
    assert git(root, "status", "--porcelain") == status
    assert record_branches(root) == []


def test_records_land_from_the_default_branch_and_without_a_forge(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = on_main(tmp_path, {SPEC: "# Thing\n"})
    branch(root, "feature/4-thing")
    assert records(root) == 2
    assert "records land from the default branch" in capsys.readouterr().out
    git(root, "checkout", "-q", "main")
    git(root, "remote", "add", "origin", "https://example.invalid/x.git")
    assert records(root) == 2
    assert "its merge job merges records alone" in capsys.readouterr().out
    assert record_branches(root) == []


@pytest.mark.parametrize(
    ("hook", "said"),
    [
        ("echo prose: red on Thing; exit 1", "the commit hooks refused them: prose: red on Thing"),
        (f"echo formatted >> {SPEC}", f"the commit hooks changed {SPEC} as they ran"),
    ],
    ids=["a hook that refuses", "a hook that rewrites a record"],
)
def test_records_the_commit_hooks_do_not_take_stay_as_the_owner_left_them(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], *, hook: str, said: str
) -> None:
    root = on_main(tmp_path, {SPEC: "# Staged\n"})
    git(root, "add", SPEC)
    write(root, SPEC, "# Edited\n")
    staged = git(root, "ls-files", "--stage", SPEC)
    hook_script(root / ".git/hooks", "pre-commit", hook)
    was = git(root, "rev-parse", "main")
    capsys.readouterr()
    assert records(root) == 1
    assert capsys.readouterr().out == (
        f"records: gates: the records stay uncommitted on the default branch, since {said}\n"
    )
    assert git(root, "rev-parse", "main") == was
    assert git(root, "ls-files", "--stage", SPEC) == staged
    assert (root / SPEC).read_text(encoding="utf-8").startswith("# Edited\n")
    assert git(root, "rev-parse", "--abbrev-ref", "HEAD") == "main"
    assert record_branches(root) == []


def test_a_failing_checkout_hook_does_not_stop_records_landing(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = on_main(tmp_path, {SPEC: "# Thing\n"})
    on_records = 'case "$(git symbolic-ref --quiet --short HEAD)" in records/*) exit 1;; esac'
    hook_script(root / ".git/hooks", "post-checkout", on_records)
    assert records(root) == 0
    assert "merge: agent\nmerged: `records/" in capsys.readouterr().out
    assert git(root, "rev-parse", "--abbrev-ref", "HEAD") == "main"
    assert git(root, "status", "--porcelain") == ""


STOPPED = "records/20261113-064102"


def stopped_on_its_branch(root: Path, *, committed: bool) -> None:
    git(root, "switch", "-q", "-c", STOPPED)
    git(root, "add", "--all")
    if committed:
        git(root, "commit", "-q", "-m", merge.SUBJECT)


@pytest.mark.parametrize(
    "committed", [False, True], ids=["in the commit hooks", "after its commit"]
)
def test_a_run_stopped_on_its_records_branch_is_finished_by_the_next(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], *, committed: bool
) -> None:
    root = on_main(tmp_path, {SPEC: "# Thing\n"})
    stopped_on_its_branch(root, committed=committed)
    capsys.readouterr()
    assert records(root) == 0
    out = capsys.readouterr().out
    assert out.startswith(f"records: a run that stopped left them on `{STOPPED}`; back on `main`\n")
    assert "merge: agent\nmerged: `records/" in out
    assert git(root, "rev-parse", "--abbrev-ref", "HEAD") == "main"
    assert git(root, "status", "--porcelain") == ""
    assert git(root, "show", f"main:{SPEC}") == "# Thing"
    assert record_branches(root) == []


def test_a_stopped_run_git_cannot_leave_says_what_to_type(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = on_main(tmp_path, {SPEC: "# Thing\n"})
    stopped_on_its_branch(root, committed=False)
    lock = root / ".git" / "index.lock"
    lock.touch()
    capsys.readouterr()
    assert records(root) == 2
    out = capsys.readouterr().out
    assert out.startswith(
        f"merge: a run that stopped left the records on `{STOPPED}`, and git cannot switch back to"
        " `main`: fatal: Unable to create"
    )
    assert out.endswith(
        f"next: once no git command runs in this repository, type `rm {lock}`, then"
        " `just merge --records`\n"
    )
    assert git(root, "rev-parse", "--abbrev-ref", "HEAD") == STOPPED
    lock.unlink()
    assert records(root) == 0
    assert record_branches(root) == []


def test_a_records_branch_a_run_stopped_in_check_left_is_deleted_once_merged(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = on_main(tmp_path, {SPEC: "# Thing\n"})
    stopped_on_its_branch(root, committed=True)
    git(root, "switch", "-q", "main")
    capsys.readouterr()
    assert run(root, STOPPED) == 0
    assert f"merge: agent\nmerged: `{STOPPED}` into `main`" in capsys.readouterr().out
    assert git(root, "show", f"main:{SPEC}") == "# Thing"
    assert record_branches(root) == []


def test_a_branch_of_any_name_holding_records_alone_is_deleted_once_merged(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = on_main(tmp_path, {})
    branch(root, "docs/tickets-1")
    write(root, SPEC, "# Thing\n")
    commit(root, "docs(thing): the spec")
    git(root, "switch", "-q", "main")
    capsys.readouterr()
    assert run(root, "docs/tickets-1") == 0
    assert "merge: agent\nmerged: `docs/tickets-1` into `main`" in capsys.readouterr().out
    assert git(root, "show", f"main:{SPEC}") == "# Thing"
    assert git(root, "branch", "--list", "docs/tickets-1") == ""


def test_a_branch_holding_code_is_kept_once_merged(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = project(tmp_path)
    git(root, "switch", "-q", "main")
    capsys.readouterr()
    assert run(root) == 0
    assert git(root, "branch", "--list", FEATURE).strip() == FEATURE


def test_a_stopped_run_whose_branch_was_edited_since_is_left_as_it_is(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = on_main(tmp_path, {SPEC: "# Thing\n"})
    stopped_on_its_branch(root, committed=True)
    write(root, SPEC, "# Edited since\n")
    capsys.readouterr()
    assert records(root) == 2
    out = capsys.readouterr().out
    assert "git cannot switch back to `main`: error: Your local changes" in out
    assert out.endswith(
        f"next: commit or set aside what git names on `{STOPPED}`, then `just merge --records`\n"
    )
    assert git(root, "rev-parse", "--abbrev-ref", "HEAD") == STOPPED
    assert (root / SPEC).read_text(encoding="utf-8") == "# Edited since\n"


@pytest.mark.parametrize(
    ("ref", "said"),
    [
        ("records/20261113-031415", "refs: `records/20261113-031415` exists already"),
        ("records", "refs: `records/20261113-031415` could not be made and checked out"),
    ],
    ids=["the same name", "a branch its name would sit under"],
)
def test_a_records_branch_that_cannot_be_made_changes_nothing(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
    *,
    ref: str,
    said: str,
) -> None:
    root = on_main(tmp_path, {SPEC: "# Thing\n"})
    stopped = datetime(2026, 11, 13, 3, 14, 15, tzinfo=UTC)
    monkeypatch.setattr(
        merge, "datetime", type("Clock", (), {"now": staticmethod(lambda _: stopped)})
    )
    git(root, "branch", ref)
    capsys.readouterr()
    assert records(root) == 1
    assert said in capsys.readouterr().out
    assert git(root, "status", "--porcelain", "--untracked-files=all") == f"?? {SPEC}"
    assert git(root, "rev-parse", "--abbrev-ref", "HEAD") == "main"


@pytest.mark.parametrize("argv", [["merge"], ["merge", "feature/4-thing", "--records"]])
def test_a_branch_or_the_records_and_never_both(argv: list[str]) -> None:
    with pytest.raises(SystemExit):
        merge.main(argv)


def test_a_merge_holds_its_lock_and_a_second_merge_of_the_branch_merges_nothing(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    root = project(tmp_path)
    was, head = tips(root)
    monkeypatch.setattr(merge, "LOCK_TRIES", 1)
    with merge.merge_lock_path(root, FEATURE).open("a+b") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        capsys.readouterr()
        assert run(root) == 2
    assert capsys.readouterr().out == (
        f"merge: a merge of `{FEATURE}` is already running; nothing was merged\n"
    )
    assert tips(root) == (was, head)
    assert run(root) == 0, "the lock was freed with its holder"


LAND_GONE = (
    "merge: the land that started this merge has stopped; nothing was merged; run land again\n"
)
REFUSED = "merge: RATCHET_LANDING names no landing of this repository; nothing was merged\n"


def private_path(root: Path, name: str = "x") -> Path:
    return merge.private_lock_path(root, name)


@pytest.mark.parametrize("state", ["missing", "free"])
def test_a_merge_whose_land_is_gone_merges_nothing_and_leaves_the_private_lock_to_clean(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
    *,
    state: str,
) -> None:
    root = project(tmp_path)
    was, head = tips(root)
    private = private_path(root)
    if state == "free":
        private.write_text("kept")
    monkeypatch.setenv(merge.LANDING_VAR, str(private))
    capsys.readouterr()
    assert run(root) == 2
    assert capsys.readouterr().out == LAND_GONE
    assert tips(root) == (was, head)
    assert private.exists() == (state == "free")
    if state == "free":
        assert private.read_text() == "kept"
        monkeypatch.delenv(merge.LANDING_VAR, raising=False)
        assert drain.main(["drain.py", "clean", "--root", str(root)]) == 0
        assert not private.exists(), "the sweep of free private locks is the housekeeping"


def test_a_merge_whose_land_is_alive_goes_on_and_leaves_the_private_lock(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = project(tmp_path)
    private = private_path(root)
    monkeypatch.setenv(merge.LANDING_VAR, str(private))
    with private.open("a+b") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        assert run(root) == 0
    assert private.exists()
    assert merge.LANDING_VAR not in os.environ, "what the merge runs does not inherit it"


def refused_untouched(
    root: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch, *, named: Path
) -> None:
    was, head = tips(root)
    monkeypatch.setenv(merge.LANDING_VAR, str(named))
    capsys.readouterr()
    assert run(root) == 2
    assert capsys.readouterr().out == REFUSED
    assert tips(root) == (was, head)


def test_a_variable_naming_a_file_outside_the_state_dir_removes_nothing(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    root = project(tmp_path)
    outside = tmp_path / "precious"
    outside.write_text("keep me")
    refused_untouched(root, capsys, monkeypatch, named=outside)
    assert outside.read_text() == "keep me"
    prefixed = tmp_path / f"{merge.PRIVATE}x"
    prefixed.write_text("keep me too")
    refused_untouched(root, capsys, monkeypatch, named=prefixed)
    assert prefixed.read_text() == "keep me too"


def test_a_variable_naming_a_state_file_without_the_prefix_removes_nothing(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    root = project(tmp_path)
    other = merge.state_dir(root) / "ratchet-other"
    other.write_text("keep me")
    refused_untouched(root, capsys, monkeypatch, named=other)
    assert other.read_text() == "keep me"


def test_a_variable_naming_a_symlink_in_the_state_dir_follows_and_removes_nothing(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    root = project(tmp_path)
    target = tmp_path / "target"
    target.write_text("keep me")
    link = private_path(root)
    link.symlink_to(target)
    refused_untouched(root, capsys, monkeypatch, named=link)
    assert link.is_symlink()
    assert target.read_text() == "keep me"


def test_a_variable_naming_a_directory_in_the_state_dir_is_refused(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    root = project(tmp_path)
    folder = private_path(root)
    folder.mkdir()
    refused_untouched(root, capsys, monkeypatch, named=folder)
    assert folder.is_dir()


def test_a_lock_probe_creates_nothing_and_follows_no_symlink(tmp_path: Path) -> None:
    gone = tmp_path / "gone"
    assert not merge.lock_held(gone)
    assert not gone.exists()
    target = tmp_path / "target"
    target.write_text("keep")
    link = tmp_path / "link"
    link.symlink_to(target)
    with target.open("a+b") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        assert not merge.lock_held(link), "a symlink is not followed to the lock behind it"
    assert target.read_text() == "keep"


def test_records_take_no_merge_lock(tmp_path: Path) -> None:
    root = on_main(tmp_path, {SPEC: "# Thing\n"})
    assert records(root) == 0
    assert not list(merge.state_dir(root).glob("ratchet-merge-lock-*"))
    assert merge.merge_lock_path(root, FEATURE).name.startswith("ratchet-merge-lock-")


MERGE_PY = Path(merge.__file__).resolve()
DEEP_ENTRY = "python3 deep.py"
DEEP_PY = """import os
import subprocess
import sys
import time
from pathlib import Path

if os.environ.get("DEEP_LOG"):
    detached = subprocess.run(["git", "symbolic-ref", "-q", "HEAD"], check=False).returncode
    with Path(os.environ["DEEP_LOG"]).open("a") as log:
        log.write(f"{Path.cwd()} {detached}\\n")
hold = os.environ.get("DEEP_HOLD")
if hold:
    Path(hold).touch()
    deadline = time.monotonic() + 60
    while not Path(f"{hold}.go").exists() and time.monotonic() < deadline:
        time.sleep(0.05)
sys.exit(0 if Path(".cache-x/f").is_file() and Path("made").is_file() else 3)
"""
SETUP_PY = """import os
import sys
import time
from pathlib import Path

hold = os.environ.get("SETUP_HOLD")
if hold:
    Path(hold).touch()
    time.sleep(60)
if os.environ.get("SETUP_FAILS"):
    sys.exit(4)
Path("made").touch()
"""
REGISTRY = 'setup = ["python3 setup.py"]\ninclude = [".cache-x"]\n'
BOUND_ZERO = "\n[ceilings.deep]\nunproven = 0\n"
LINE_ONE = "line 1 of ratchet-deep.jsonl"
PROVES_AGAIN = "then `just merge --deep` proves the tip of `main` again"
WAIT_NEXT = (
    f"next: run `just merge --deep` in the background and wait on its exit, then merge `{FEATURE}`"
    " again: it proves the tip of `main`"
)


def proving(
    root: Path,
    *,
    deep: str | None = DEEP_ENTRY,
    bound: str = "",
    merges: int = 0,
    registry: str = REGISTRY,
) -> Path:
    repository(
        root,
        {
            **BASE,
            "docs/process/binding.md": binding(deep=deep) + GATES + FUZZ,
            "quality/thresholds.toml": BASE["quality/thresholds.toml"] + bound,
            "quality/bites.toml": registry,
            ".gitignore": ".cache-x/\n",
            "deep.py": DEEP_PY,
            "setup.py": SETUP_PY,
        },
    )
    for number in range(1, merges + 1):
        write(root, f"src/m{number}.py", f"m = {number}\n")
        commit(root, f"feat(core): merge {number}")
    write(root, ".cache-x/f", "kept out of git\n")
    return feature(root)


def prove(root: Path) -> int:
    return merge.main(["merge", "--deep", "--root", str(root)])


def recorded(root: Path) -> list[dict[str, object]]:
    path = merge.record_path(root)
    lines = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
    return [json.loads(line) for line in lines]


def proof(commit: str, status: str, at: str = "2026-11-20T09:00:00Z") -> str:
    fields = {"schema": 1, "commit": commit, "tree": "", "status": status, "exit": 0}
    made = {**fields, "seconds": 1.0, "at": at, "entry": DEEP_ENTRY, "speaks_for": [], "why": ""}
    return json.dumps(made) + "\n"


def deep_lock(root: Path) -> Path:
    return merge.state_dir(root) / unproven.LOCK


def test_merge_deep_proves_the_tip_in_a_detached_tree_after_its_includes_and_setup(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    root = proving(tmp_path / "repo")
    log = tmp_path / "deep.log"
    monkeypatch.setenv("DEEP_LOG", str(log))
    tip = git(root, "rev-parse", "main")
    capsys.readouterr()
    assert prove(root) == 0
    assert capsys.readouterr().out.startswith(f"deep: green on `main` at {tip[:12]} in ")
    [line] = recorded(root)
    assert unproven.instant(line.pop("at")) is not None
    assert isinstance(line.pop("seconds"), float)
    assert line == {
        "schema": 1,
        "commit": tip,
        "tree": git(root, "rev-parse", "main^{tree}"),
        "status": "green",
        "exit": 0,
        "entry": DEEP_ENTRY,
        "speaks_for": [tip],
        "why": "",
    }
    where, detached = log.read_text(encoding="utf-8").split()
    assert Path(where) != root
    assert detached == "1", "the tree checks out the commit, on no branch"
    assert git(root, "worktree", "list", "--porcelain").count("worktree ") == 1
    assert not merge.lock_held(deep_lock(root))


def test_a_red_check_deep_appends_a_red_line(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = proving(tmp_path / "repo")
    (root / ".cache-x/f").unlink()
    capsys.readouterr()
    assert prove(root) == 1
    assert capsys.readouterr().out.endswith(": `python3 deep.py` exited 3\n")
    [line] = recorded(root)
    assert (line["status"], line["exit"]) == ("red", 3)


def test_a_failing_setup_appends_an_unrun_line_a_merge_does_not_read_as_red(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    root = proving(tmp_path / "repo")
    monkeypatch.setenv("SETUP_FAILS", "1")
    assert prove(root) == 2
    [line] = recorded(root)
    assert (line["status"], line["exit"], line["speaks_for"]) == ("unrun", None, [])
    assert line["why"] == "setup `python3 setup.py` exited 4"
    capsys.readouterr()
    assert run(root) == 0
    assert capsys.readouterr().out.startswith("merge: agent\nmerged: ")


def taken_scratch(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    taken = tmp_path / "taken"
    (taken / "tree").mkdir(parents=True)
    (taken / "tree/file").write_text("in the way\n", encoding="utf-8")
    monkeypatch.setattr(merge.tempfile, "mkdtemp", lambda **_: str(taken))


UNRUN: dict[str, tuple[str | None, str, str]] = {
    "no check-deep": (None, REGISTRY, "the binding names no `check-deep` entry point"),
    "a registry that does not read": (
        DEEP_ENTRY,
        "setup = [",
        "quality/bites.toml does not read: ",
    ),
    "a setup that cannot start": (
        DEEP_ENTRY,
        'setup = ["no-such-runner-here x"]\n',
        "`no-such-runner-here x` could not run: ",
    ),
    "a check-deep that cannot start": (
        "no-such-runner-here deep",
        REGISTRY,
        "`no-such-runner-here deep` could not run: ",
    ),
    "a check-deep a signal stops": (
        'python3 -c "import os, signal; os.kill(os.getpid(), signal.SIGTERM)"',
        REGISTRY,
        "was stopped by signal 15",
    ),
}


@pytest.mark.parametrize("case", UNRUN)
def test_whatever_stops_check_deep_judging_the_tree_is_unrun(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], case: str
) -> None:
    deep, registry, why = UNRUN[case]
    root = proving(tmp_path / "repo", deep=deep, registry=registry)
    capsys.readouterr()
    assert prove(root) == 2
    [line] = recorded(root)
    assert line["status"] == "unrun"
    assert why in str(line["why"])
    assert "deep: unrun on `main`" in capsys.readouterr().out


def test_an_include_that_leaves_the_repository_or_a_tree_that_cannot_be_made_is_unrun(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = proving(tmp_path / "repo")
    outside = tmp_path / "outside"
    outside.mkdir()
    (root / ".cache-x/f").unlink()
    (root / ".cache-x").rmdir()
    (root / ".cache-x").symlink_to(outside)
    assert prove(root) == 2
    assert "leaves the repository through a symbolic link" in str(recorded(root)[-1]["why"])
    taken_scratch(tmp_path, monkeypatch)
    assert prove(root) == 2
    assert "could be made at" in str(recorded(root)[-1]["why"])


def test_a_setup_that_outlives_its_limit_is_unrun(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = proving(tmp_path / "repo")
    monkeypatch.setattr(merge, "SETUP_LIMIT", 0.5)
    monkeypatch.setenv("SETUP_HOLD", str(tmp_path / "held"))
    assert prove(root) == 2
    assert recorded(root)[-1]["why"] == "`python3 setup.py` timed out after 0.5s"


def test_a_recorded_red_refuses_with_tier2_and_the_green_after_it_lets_the_merge_through(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = proving(tmp_path / "repo")
    tip = git(root, "rev-parse", "main")
    (root / ".cache-x/f").unlink()
    assert prove(root) == 1
    before = tips(root)
    capsys.readouterr()
    assert run(root) == 1
    assert capsys.readouterr().out == (
        f"merge: owner\ntier2: tier 2 is red on the default branch: {LINE_ONE} on `{tip[:12]}`"
        f" speaks for `{tip[:12]}` feat(core): the clean state, and the owner merges what fixes"
        f" it\nnext: the owner merges what fixes tier 2, {PROVES_AGAIN}\n"
    )
    assert tips(root) == before
    write(root, ".cache-x/f", "back\n")
    assert prove(root) == 0
    assert run(root) == 0, "the later green on the same commit decides it"


def test_a_red_after_a_green_names_each_merge_it_speaks_for(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = proving(tmp_path / "repo", merges=3)
    third, second, first = git(root, "rev-list", "--first-parent", "-3", "main").split()
    record = proof(first, "green") + proof(third, "red", "2026-11-20T09:30:00Z")
    merge.record_path(root).write_text(record, encoding="utf-8")
    capsys.readouterr()
    assert run(root) == 1
    assert capsys.readouterr().out.splitlines()[1] == (
        "tier2: tier 2 is red on the default branch: line 2 of ratchet-deep.jsonl on"
        f" `{third[:12]}` speaks for `{third[:12]}` feat(core): merge 3; `{second[:12]}`"
        " feat(core): merge 2, and the owner merges what fixes it"
    )


def test_a_declared_bound_exceeded_waits_for_merge_deep_and_an_unrun_proof_does_not_end_it(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    root = proving(tmp_path / "repo", bound=BOUND_ZERO)
    waits = (
        "merge: wait\nunproven: no finished deep run covers the newest 1 of the default branch's"
        " merges, and `ceilings.deep.unproven` allows 0: run `just merge --deep`, which proves the"
        f" tip of `main`\n{WAIT_NEXT}\n"
    )
    capsys.readouterr()
    assert run(root) == 1
    assert capsys.readouterr().out == waits
    monkeypatch.setenv("SETUP_FAILS", "1")
    assert prove(root) == 2
    capsys.readouterr()
    assert run(root) == 1
    assert capsys.readouterr().out == waits, "an unrun proof covers nothing"
    monkeypatch.delenv("SETUP_FAILS")
    assert prove(root) == 0
    assert run(root) == 0


def test_a_held_deep_lock_reads_as_a_run_on_the_commit_it_names(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = proving(tmp_path / "repo", bound=BOUND_ZERO)
    tip = git(root, "rev-parse", "main")
    with deep_lock(root).open("a+b") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        lock.write(tip.encode())
        lock.flush()
        capsys.readouterr()
        assert run(root) == 1
        second = capsys.readouterr().out.splitlines()[1]
        assert second.endswith(
            f"allows 0: wait for the `merge --deep` holding ratchet-deep.lock on `{tip[:12]}`"
        )
        for named in (b"not a commit", b"\xff"):
            lock.truncate(0)
            lock.write(named)
            lock.flush()
            assert run(root) == 1
            said = capsys.readouterr().out.splitlines()[1]
            assert said.endswith("which proves the tip of `main`"), named


SHA = "0" * 40
UNPARSABLE: dict[str, bytes] = {
    "not JSON": b"not json\n",
    "not text": b"\xff\n",
    "not an object": b"[1]\n",
    "another schema": proof(SHA, "green").replace('"schema": 1', '"schema": 2').encode(),
    "an unknown status": proof(SHA, "maybe").encode(),
    "a commit that is no commit": proof(SHA, "green").replace(SHA, "abc").encode(),
    "a time that is no instant": proof(SHA, "green", "2026-11-20T09:00:00").encode(),
}


@pytest.mark.parametrize("case", [*UNPARSABLE, "a directory"])
def test_a_record_that_does_not_parse_refuses_and_names_the_file_and_the_escape(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], case: str
) -> None:
    root = proving(tmp_path / "repo")
    path = merge.record_path(root)
    if case in UNPARSABLE:
        path.write_bytes(proof(git(root, "rev-parse", "main"), "green").encode() + UNPARSABLE[case])
    else:
        path.mkdir()
    before = tips(root)
    capsys.readouterr()
    assert run(root) == 1
    assert capsys.readouterr().out == (
        f"merge: owner\ntier2: {unproven.UNKNOWN}\nnext: the owner removes {path}, which does not"
        f" parse, {PROVES_AGAIN}\n"
    )
    assert tips(root) == before
    assert prove(root) == 2
    assert f"merge: {path} does not parse; the owner removes it, {PROVES_AGAIN}" in (
        capsys.readouterr().out
    )


def test_once_the_owner_removes_a_record_that_does_not_parse_the_next_proof_ends_the_hold(
    tmp_path: Path,
) -> None:
    root = proving(tmp_path / "repo")
    merge.record_path(root).write_text("not json\n", encoding="utf-8")
    assert run(root) == 1
    merge.record_path(root).unlink()
    assert prove(root) == 0
    assert run(root) == 0


@pytest.mark.parametrize(
    ("status", "bound", "code"), [("red", "", 0), ("green", BOUND_ZERO, 1)], ids=["red", "green"]
)
def test_a_line_whose_commit_is_not_on_the_default_branch_covers_nothing(
    tmp_path: Path, *, status: str, bound: str, code: int
) -> None:
    root = proving(tmp_path / "repo", bound=bound)
    elsewhere = git(root, "rev-parse", FEATURE)
    merge.record_path(root).write_text(proof(elsewhere, status), encoding="utf-8")
    assert run(root) == code


def test_a_record_of_ten_thousand_lines_is_read_and_the_merge_lands(tmp_path: Path) -> None:
    root = proving(tmp_path / "repo")
    tip = git(root, "rev-parse", "main")
    merge.record_path(root).write_text(proof(tip, "green") * 10_000, encoding="utf-8")
    assert run(root) == 0


def test_a_merge_runs_no_check_deep_outside_an_adoption(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = proving(tmp_path / "repo")
    log = tmp_path / "deep.log"
    monkeypatch.setenv("DEEP_LOG", str(log))
    assert run(root) == 0
    assert not log.exists()
    assert not merge.record_path(root).exists()


@pytest.mark.parametrize("status", ["green", "red"])
def test_a_caller_that_waited_on_the_lock_proves_nothing_once_the_tip_was_proved(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
    *,
    status: str,
) -> None:
    root = proving(tmp_path / "repo")
    tip = git(root, "rev-parse", "main")
    log = tmp_path / "deep.log"
    monkeypatch.setenv("DEEP_LOG", str(log))
    held = deep_lock(root).open("a+b")
    fcntl.flock(held, fcntl.LOCK_EX | fcntl.LOCK_NB)
    real = merge.locked

    def the_other_proof_ends(
        path: Path, *, create: bool = True, wait: bool = False
    ) -> contextlib.AbstractContextManager[int | None]:
        if wait:
            merge.record_path(root).write_text(proof(tip, status), encoding="utf-8")
            held.close()
        return real(path, create=create, wait=wait)

    monkeypatch.setattr(merge, "locked", the_other_proof_ends)
    capsys.readouterr()
    assert prove(root) == {"green": 0, "red": 1}[status]
    assert capsys.readouterr().out == (
        "merge: another `merge --deep` proves `main` now; this one waits for it\n"
        f"merge: `main` at {tip[:12]} was proved {status} while this waited\n"
    )
    assert not log.exists(), "no second proof"
    assert len(recorded(root)) == 1


def test_a_caller_that_waited_proves_the_tip_when_the_other_proof_was_unrun(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = proving(tmp_path / "repo")
    tip = git(root, "rev-parse", "main")
    log = tmp_path / "deep.log"
    monkeypatch.setenv("DEEP_LOG", str(log))
    held = deep_lock(root).open("a+b")
    fcntl.flock(held, fcntl.LOCK_EX | fcntl.LOCK_NB)
    real = merge.locked

    def the_other_proof_is_unrun(
        path: Path, *, create: bool = True, wait: bool = False
    ) -> contextlib.AbstractContextManager[int | None]:
        if wait:
            merge.record_path(root).write_text(proof(tip, "unrun"), encoding="utf-8")
            held.close()
        return real(path, create=create, wait=wait)

    monkeypatch.setattr(merge, "locked", the_other_proof_is_unrun)
    assert prove(root) == 0
    assert len(log.read_text(encoding="utf-8").splitlines()) == 1, "this caller proved the tip"
    assert [line["status"] for line in recorded(root)] == ["unrun", "green"]


def started(root: Path, *, piped: bool = False, **env: str) -> subprocess.Popen[str]:
    return subprocess.Popen(  # noqa: S603 # -- this interpreter on the script under test
        [sys.executable, str(MERGE_PY), "--deep", "--root", str(root)],
        env={**os.environ, **env},
        stdout=subprocess.PIPE if piped else subprocess.DEVNULL,
        text=True,
        start_new_session=True,
    )


def appeared(path: Path) -> None:
    deadline = time.monotonic() + 30
    while not path.exists():
        assert time.monotonic() < deadline, f"{path} never appeared"
        time.sleep(0.02)


def test_two_callers_on_one_tip_prove_it_once_and_append_one_line(tmp_path: Path) -> None:
    root = proving(tmp_path / "repo")
    hold, log = tmp_path / "hold", tmp_path / "deep.log"
    first = started(root, DEEP_HOLD=str(hold), DEEP_LOG=str(log))
    appeared(hold)
    second = started(root, piped=True, DEEP_HOLD=str(hold), DEEP_LOG=str(log))
    assert second.stdout is not None
    waiting = select.select([second.stdout], [], [], 20)[0]
    said = second.stdout.readline() if waiting else ""
    Path(f"{hold}.go").touch()
    assert first.wait(timeout=60) == 0
    rest, _ = second.communicate(timeout=60)
    assert said == "merge: another `merge --deep` proves `main` now; this one waits for it\n"
    assert second.returncode == 0
    assert "was proved green while this waited" in rest
    assert len(log.read_text(encoding="utf-8").splitlines()) == 1, "check-deep ran once"
    assert [line["status"] for line in recorded(root)] == ["green"]


@pytest.mark.parametrize("inside", ["DEEP_HOLD", "SETUP_HOLD"], ids=["check-deep", "setup"])
def test_a_proof_killed_part_way_frees_the_lock_records_no_green_and_the_next_proves_the_tip(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], inside: str
) -> None:
    root = proving(tmp_path / "repo")
    hold = tmp_path / "hold"
    killed = started(root, **{inside: str(hold)})
    appeared(hold)
    os.killpg(killed.pid, signal.SIGKILL)
    killed.wait(timeout=30)
    after_a_kill(root, capsys, swept=True)


KILL_BEFORE_THE_LINE = """import os
import signal
import sys

sys.path.insert(0, sys.argv[1])
import merge

merge.unproven.proof_line = lambda proof: os.kill(os.getpid(), signal.SIGKILL)
merge.main(["merge", "--deep", "--root", sys.argv[2]])
"""


def test_a_proof_killed_between_check_deep_and_its_line_records_nothing(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = proving(tmp_path / "repo")
    driver = tmp_path / "driver.py"
    driver.write_text(KILL_BEFORE_THE_LINE, encoding="utf-8")
    done = subprocess.run(  # noqa: S603 # -- this interpreter on a driver the test wrote
        [sys.executable, str(driver), str(MERGE_PY.parent), str(root)],
        capture_output=True,
        check=False,
    )
    assert done.returncode == -signal.SIGKILL
    after_a_kill(root, capsys, swept=False)


def after_a_kill(root: Path, capsys: pytest.CaptureFixture[str], *, swept: bool) -> None:
    assert not merge.lock_held(deep_lock(root)), "the kernel let the lock go with its holder"
    assert recorded(root) == []
    capsys.readouterr()
    assert prove(root) == 0
    assert ("merge: removed the work tree" in capsys.readouterr().out) is swept
    assert [line["status"] for line in recorded(root)] == ["green"]
    assert git(root, "worktree", "list", "--porcelain").count("worktree ") == 1


def test_merge_deep_proves_only_a_local_default_branch_with_no_forge(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = proving(tmp_path / "repo")
    deep_lock(root).mkdir()
    assert prove(root) == 2
    said = capsys.readouterr().out
    assert said.endswith("ratchet-deep.lock cannot be opened; nothing was proved\n")
    git(root, "remote", "add", "origin", "https://example.invalid/x.git")
    assert prove(root) == 2
    assert "its deep workflow proves the tip" in capsys.readouterr().out
    lonely = tmp_path / "lonely"
    git(tmp_path, "init", "-q", "-b", "trunk", str(lonely))
    git(lonely, "config", "user.email", "t@example.invalid")
    git(lonely, "config", "user.name", "t")
    commit(lonely, "feat(core): first")
    assert prove(lonely) == 2
    assert capsys.readouterr().out == "merge: there is no local default branch to prove\n"
    with pytest.raises(SystemExit):
        merge.main(["merge", "--deep", FEATURE])


TEMPLATE_MANIFEST = (
    Path(merge.__file__).resolve().parents[1] / "skills/adopt/templates/guards/manifest.toml"
).read_text(encoding="utf-8")


def asked(cwd: Path, call: dict[str, object]) -> dict[str, str]:
    stdout = io.StringIO()
    assert hook.main(cwd, io.StringIO(json.dumps({**call, "cwd": str(cwd)})), stdout) == 0
    return json.loads(stdout.getvalue())["hookSpecificOutput"] if stdout.getvalue() else {}


def test_the_hook_lets_merge_deep_write_its_record_and_refuses_an_agent_s_write_of_it(
    tmp_path: Path,
) -> None:
    root = checkout(tmp_path, manifest=TEMPLATE_MANIFEST)
    switch_to_a_branch(root)
    assert asked(root, {"tool_name": "Bash", "tool_input": {"command": "just merge --deep"}}) == {}
    for call in (
        {"tool_name": "Bash", "tool_input": {"command": "echo '{}' >> .git/ratchet-deep.jsonl"}},
        {"tool_name": "Write", "tool_input": {"file_path": str(root / ".git/ratchet-deep.jsonl")}},
    ):
        said = asked(root, call)
        assert said["permissionDecision"] == "deny"
        reason = said["permissionDecisionReason"]
        assert ".git/ratchet-deep.jsonl" in reason
        assert "local-git" in reason
