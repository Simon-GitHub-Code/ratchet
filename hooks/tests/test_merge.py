"""``merge.py``: the merge with no forge lands what the judge and ``check`` both pass, only that."""

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

import merge
import merge_ready
from merge_ready import Reason
from tests.repos import branch, commit, git, hook_script, repository, write
from tests.test_merge_ready import BASE, VERDICT

FEATURE = "feature/4-thing"
BOTH = "python3 -c \"import os, sys; sys.exit(not all(map(os.path.exists, 'mb')))\""


def binding(check: str | None = "true", deep: str | None = "just check-deep") -> str:
    rows = [("check", check), ("check-deep", deep), ("merge", "just merge")]
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
    assert "next: the binding's `check-deep` entry point on `main`" in capsys.readouterr().out


# --- the adoption (ADR 0031) ------------------------------------------------------------------


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


# --- records alone (ADR 0031) -----------------------------------------------------------------

SPEC = ".scratch/thing/spec.md"


def on_main(root: Path, files: dict[str, str]) -> Path:
    """The adopted repository on ``main``, with ``files`` written and left uncommitted."""
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
    # Why: the owner may have staged one version of a record and kept editing another; neither
    # is theirs to lose because a hook said no.
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
    """What a ``--records`` run killed in the commit hooks, or after its commit, leaves."""
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
    # Why: a run killed while the commit hooks ran leaves HEAD on its records branch, and the
    # same command is the one the route names again; it must pick up where the last one stopped.
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
    # Why: git killed partway leaves its index lock behind, and only one who knows no git runs
    # may delete it; the run says so and names the command after it.
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
