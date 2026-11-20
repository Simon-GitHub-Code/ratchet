import io
import json
from pathlib import Path

import pytest

import drain
import feature_preflight as preflight
import publish_check
import queue_notes
from ratchet_guards import bash, hook
from ratchet_guards.context import Context
from ratchet_guards.hook import GitRepoState
from ratchet_guards.verdict import RULES, Decision, Verdict
from tests.repos import commit, git, write
from tests.support import make_guard
from tests.test_drain import (
    ALPHA,
    BRANCH_A,
    BRANCH_C,
    GAMMA,
    Out,
    killed,
    moved,
    project,
    run,
    scratch,  # noqa: F401 # -- an autouse fixture of the drain's tests
    ticket,
)

COMMIT = "git commit -m 'feat(core): the change'"
GATED = ["queue-final-check"]
FINAL = (
    "First run `drain.py check <ticket> --final` in a drain, or `publish_check.py` in a "
    "one-ticket run, and commit when it is green."
)


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


@pytest.mark.parametrize(
    "point", ["before_rebase", "after_rebase", "mid_note_write", "after_note_write"]
)
def test_an_update_cut_off_after_the_orchestrator_committed_parks_nothing(
    tmp_path: Path, capsys: Out, point: str
) -> None:
    root = project(tmp_path)
    assert run(root, "open", ALPHA) == 0
    assert run(root, "check", ALPHA, "--final") == 0
    tree = tree_of(root)
    assert judged(COMMIT, tree) == []
    write(tree, "src/b.py", "B = 1\n")
    commit(tree, "feat(core): the orchestrator's change")
    moved(root)
    with pytest.MonkeyPatch.context() as patch:
        killed(patch, point)
        with pytest.raises(KeyboardInterrupt):
            run(root, "update", ALPHA)
    capsys.readouterr()
    assert run(root, "update", ALPHA) == 0
    said = capsys.readouterr().out
    assert "a role published" not in said
    assert queue_notes.read_note(root, BRANCH_A).get("outcome") != drain.PARKED


def hooked(cwd: Path) -> str:
    case = {"tool_name": "Bash", "tool_input": {"command": COMMIT}, "cwd": str(cwd)}
    answer = io.StringIO()
    assert hook.main(cwd, io.StringIO(json.dumps(case)), answer) == 0
    if not answer.getvalue():
        return "allowed"
    said = json.loads(answer.getvalue())["hookSpecificOutput"]
    return f"{said['permissionDecision']}: {said['permissionDecisionReason']}"


def refused(branch: str) -> str:
    return f"deny: {hook.reason(Verdict('queue-final-check', f'git commit on `{branch}`'))}"


def pipeline_preflight(
    tree: Path, ref: str, branch: str, *, capsys: Out, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(preflight, "run_gh", lambda _args: "[]")
    argv = ["feature_preflight.py", ref, "--root", str(tree), "--base", "main", "--branch", branch]
    assert preflight.main(argv) == 0, capsys.readouterr().err
    capsys.readouterr()


def test_a_drains_final_check_lets_through_the_commit_its_pipelines_preflight_started(
    tmp_path: Path, capsys: Out, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = project(tmp_path)
    assert run(root, "plan") == 0
    assert run(root, "open", ALPHA) == 0
    tree = tree_of(root)
    pipeline_preflight(tree, ALPHA, BRANCH_A, capsys=capsys, monkeypatch=monkeypatch)
    assert hooked(tree) == refused(BRANCH_A)
    assert run(root, "check", ALPHA) == 0
    assert hooked(tree) == refused(BRANCH_A), "a check without --final passes nothing"
    assert run(root, "check", ALPHA, "--final") == 0
    capsys.readouterr()
    assert hooked(tree) == "allowed"


def test_a_final_check_that_refuses_keeps_the_commit_refused(
    tmp_path: Path, capsys: Out, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = project(tmp_path)
    assert run(root, "open", ALPHA) == 0
    tree = tree_of(root)
    pipeline_preflight(tree, ALPHA, BRANCH_A, capsys=capsys, monkeypatch=monkeypatch)
    assert run(root, "check", ALPHA, "--final") == 0
    moved(root)
    with pytest.MonkeyPatch.context() as patch:
        killed(patch, "after_rebase")
        with pytest.raises(KeyboardInterrupt):
            run(root, "update", ALPHA)
    assert run(root, "check", ALPHA, "--final") == drain.UNREAD
    capsys.readouterr()
    assert hooked(tree) == refused(BRANCH_A)


def test_a_ticket_parked_and_opened_again_waits_for_its_new_final_check(
    tmp_path: Path, capsys: Out, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = project(tmp_path)
    assert run(root, "open", ALPHA) == 0
    tree = tree_of(root)
    pipeline_preflight(tree, ALPHA, BRANCH_A, capsys=capsys, monkeypatch=monkeypatch)
    assert run(root, "check", ALPHA, "--final") == 0
    assert run(root, "close", ALPHA, "--outcome", "parked", "--reason", "which case?") == 0
    write(root, ALPHA, ticket(1, "Alpha", "Answered: lower.\n\n"))
    commit(root, "docs: answer the question")
    assert run(root, "open", ALPHA) == 0
    assert hooked(tree) == refused(BRANCH_A)
    pipeline_preflight(tree, ALPHA, BRANCH_A, capsys=capsys, monkeypatch=monkeypatch)
    assert hooked(tree) == refused(BRANCH_A)
    assert run(root, "check", ALPHA, "--final") == 0
    capsys.readouterr()
    assert hooked(tree) == "allowed"


def test_two_tickets_open_at_once_each_wait_for_their_own_final_check(
    tmp_path: Path, capsys: Out, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = project(tmp_path)
    assert run(root, "open", ALPHA) == 0
    assert run(root, "open", GAMMA) == 0
    alpha, gamma = tree_of(root), drain.tree_of(root, BRANCH_C)
    pipeline_preflight(alpha, ALPHA, BRANCH_A, capsys=capsys, monkeypatch=monkeypatch)
    pipeline_preflight(gamma, GAMMA, BRANCH_C, capsys=capsys, monkeypatch=monkeypatch)
    assert run(root, "check", ALPHA, "--final") == 0
    capsys.readouterr()
    assert hooked(alpha) == "allowed"
    assert hooked(gamma) == refused(BRANCH_C)
    assert run(root, "check", GAMMA, "--final") == 0
    capsys.readouterr()
    assert hooked(gamma) == "allowed"


def test_a_drains_final_check_leaves_a_one_ticket_start_of_another_tree_unpassed(
    tmp_path: Path, capsys: Out, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = project(tmp_path)
    git(root, "switch", "-q", "-c", BRANCH_A)
    pipeline_preflight(root, ALPHA, BRANCH_A, capsys=capsys, monkeypatch=monkeypatch)
    assert hooked(root) == refused(BRANCH_A)
    git(root, "switch", "-q", "main")
    assert run(root, "open", ALPHA) == 0
    assert run(root, "check", ALPHA, "--final") == 0
    assert run(root, "close", ALPHA, "--outcome", "waits", "--reason", "behind: later") == 0
    capsys.readouterr()
    git(root, "switch", "-q", BRANCH_A)
    assert hooked(root) == refused(BRANCH_A)
