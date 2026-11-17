"""The guard refuses a commit in a queue work tree whose start has not passed its final check."""

from pathlib import Path

import pytest

import drain
import feature_preflight as preflight
import publish_check
import queue_notes
from ratchet_guards import bash
from ratchet_guards.context import Context
from ratchet_guards.hook import GitRepoState
from ratchet_guards.verdict import RULES, Decision, Verdict
from tests.repos import git
from tests.support import make_guard
from tests.test_drain import (
    ALPHA,
    BRANCH_A,
    Out,
    killed,
    moved,
    project,
    run,
    scratch,  # noqa: F401 # -- an autouse fixture of the drain's tests
)

COMMIT = "git commit -m 'feat(core): the change'"
GATED = ["queue-final-check"]
FINAL = "Run `drain.py check <ticket> --final` first, and commit when it is green."


def judged(command: str, cwd: Path) -> list[Verdict]:
    return bash.check(command, Context(cwd), make_guard(GitRepoState()))


def test_a_commit_in_a_queue_work_tree_before_the_final_check_is_refused(
    tmp_path: Path, capsys: Out
) -> None:
    root = project(tmp_path)
    assert run(root, "open", ALPHA) == 0
    assert run(root, "check", ALPHA) == 0
    capsys.readouterr()
    tree = tree_of(root)
    found = judged(COMMIT, tree)
    assert [(v.rule, v.decision, v.detail) for v in found] == [
        ("queue-final-check", Decision.DENY, f"git commit on `{BRANCH_A}`")
    ]
    assert RULES["queue-final-check"].way_out == FINAL
    assert [v.rule for v in judged(f"git -C {tree} commit -m x", root)] == ["queue-final-check"]


def test_the_same_commit_after_the_final_check_goes_through(tmp_path: Path) -> None:
    root = project(tmp_path)
    assert run(root, "open", ALPHA) == 0
    assert [v.rule for v in judged(COMMIT, tree_of(root))] == GATED
    assert run(root, "check", ALPHA, "--final") == 0
    assert judged(COMMIT, tree_of(root)) == []


def test_a_commit_outside_any_queue_work_tree_is_unaffected(tmp_path: Path) -> None:
    root = project(tmp_path)
    assert run(root, "open", ALPHA) == 0
    assert [v.rule for v in judged(COMMIT, tree_of(root))] == GATED
    assert judged(COMMIT, root) == []
    git(root, "switch", "-q", "-c", "feat/other")
    assert judged(COMMIT, root) == []


def test_a_drain_stopped_between_the_final_check_and_the_commit_may_commit(
    tmp_path: Path,
) -> None:
    root = project(tmp_path)
    assert run(root, "open", ALPHA) == 0
    assert [v.rule for v in judged(COMMIT, tree_of(root))] == GATED
    assert run(root, "check", ALPHA, "--final") == 0
    assert run(root, "check", ALPHA) == 0
    assert judged(COMMIT, tree_of(root)) == []


def mid_rebase(tree: Path) -> None:
    git(tree, "checkout", "-q", "--detach")
    rebase = tree / git(tree, "rev-parse", "--git-path", "rebase-merge")
    rebase.mkdir()
    (rebase / "head-name").write_text(f"refs/heads/{BRANCH_A}\n", encoding="utf-8")


def test_a_tree_left_detached_mid_rebase_with_an_unpassed_start_is_refused(
    tmp_path: Path,
) -> None:
    root = project(tmp_path)
    assert run(root, "open", ALPHA) == 0
    tree = tree_of(root)
    mid_rebase(tree)
    assert [v.rule for v in judged(COMMIT, tree)] == GATED


def test_an_unfinished_rebase_whose_start_passed_with_final_may_commit(tmp_path: Path) -> None:
    root = project(tmp_path)
    assert run(root, "open", ALPHA) == 0
    tree = tree_of(root)
    assert run(root, "check", ALPHA, "--final") == 0
    mid_rebase(tree)
    assert judged(COMMIT, tree) == []


def test_a_commit_outside_any_repository_is_unaffected(tmp_path: Path) -> None:
    plain = tmp_path / "plain"
    plain.mkdir()
    assert judged(COMMIT, plain) == []


def test_a_note_that_is_not_json_is_skipped_and_another_still_refuses(tmp_path: Path) -> None:
    root = project(tmp_path)
    assert run(root, "open", ALPHA) == 0
    notes = root / ".ratchet" / "queue" / "tickets"
    (notes / "0-broken.json").write_text("{not json", encoding="utf-8")
    assert [v.rule for v in judged(COMMIT, tree_of(root))] == GATED


def tree_of(root: Path) -> Path:
    return drain.tree_of(root, BRANCH_A)


def test_a_check_before_a_resume_does_not_open_the_gate(
    tmp_path: Path, capsys: Out, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = project(tmp_path)
    assert run(root, "open", ALPHA) == 0
    capsys.readouterr()
    note = queue_notes.note_path(root, BRANCH_A)
    monkeypatch.setattr(preflight, "run_gh", lambda _args: "[]")
    argv = ["publish_check.py", str(note), "--root", str(tree_of(root)), "--before-resume"]
    assert publish_check.main(argv) == 0
    assert "authorises a resume and nothing else" in capsys.readouterr().out
    assert queue_notes.read_note(root, BRANCH_A).get("passed") is not True
    assert [v.rule for v in judged(COMMIT, tree_of(root))] == GATED


def test_a_commit_after_an_update_cut_off_past_its_rebase_is_refused(
    tmp_path: Path, capsys: Out
) -> None:
    root = project(tmp_path)
    assert run(root, "open", ALPHA) == 0
    assert run(root, "check", ALPHA, "--final") == 0
    assert judged(COMMIT, tree_of(root)) == []
    moved(root)
    with pytest.MonkeyPatch.context() as patch:
        killed(patch, "after_rebase")
        with pytest.raises(KeyboardInterrupt):
            run(root, "update", ALPHA)
    assert [v.rule for v in judged(COMMIT, tree_of(root))] == GATED
    assert run(root, "update", ALPHA) == 0
    assert [v.rule for v in judged(COMMIT, tree_of(root))] == GATED
    assert run(root, "check", ALPHA, "--final") == 0
    capsys.readouterr()
    assert judged(COMMIT, tree_of(root)) == []
