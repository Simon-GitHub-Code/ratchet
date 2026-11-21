import contextlib
import functools
import io
import json
import os
import re
import shutil
import stat
import sys
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import NamedTuple

import pytest

import drain
import feature_preflight as preflight
import gatelib
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
    other = queue_notes.feature_start_path(root, BRANCH_C)
    assert other is not None
    assert queue_notes.loaded(gatelib.read_text(other)).get("passed") is None
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


def started(root: Path) -> Path:
    found = queue_notes.feature_start_path(root, BRANCH_A)
    assert found is not None
    return found


def start_passed(root: Path) -> bool:
    path = started(root)
    return path.is_file() and queue_notes.loaded(gatelib.read_text(path)).get("passed") is True


def by_hand(root: Path, edit: str) -> None:
    path = queue_notes.note_path(root, BRANCH_A)
    note = queue_notes.read_note(root, BRANCH_A)
    if edit == "delete":
        path.unlink()
    elif edit == "passed":
        queue_notes.write_note(path, {**note, "passed": True})
    else:
        queue_notes.write_note(path, {k: v for k, v in note.items() if k != "start"})


def unreadable_start(*_args: object, **_kwargs: object) -> object:
    message = "the forge did not list the open pull requests"
    raise publish_check.UnreadableError(message)


def updated(root: Path, *, readable: bool = True, change: int = 0) -> int:
    moved(root, f"src/m{change}.py", f"M = {change}\n")
    with pytest.MonkeyPatch.context() as patch:
        if not readable:
            patch.setattr(publish_check, "start_of", unreadable_start)
        return run(root, "update", ALPHA)


@pytest.mark.parametrize("edit", ["passed", "start"])
def test_a_final_check_an_update_rebased_away_lets_no_commit_through_a_note_written_by_hand(
    tmp_path: Path, capsys: Out, monkeypatch: pytest.MonkeyPatch, *, edit: str
) -> None:
    root = project(tmp_path)
    assert run(root, "open", ALPHA) == 0
    tree = tree_of(root)
    pipeline_preflight(tree, ALPHA, BRANCH_A, capsys=capsys, monkeypatch=monkeypatch)
    assert run(root, "check", ALPHA, "--final") == 0
    assert updated(root) == 0
    by_hand(root, edit)
    capsys.readouterr()
    assert not start_passed(root), "the update closed the start the final check passed"
    assert hooked(tree) == refused(BRANCH_A)


def test_an_update_that_cannot_read_its_new_start_lets_no_commit_through(
    tmp_path: Path, capsys: Out, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = project(tmp_path)
    assert run(root, "open", ALPHA) == 0
    tree = tree_of(root)
    pipeline_preflight(tree, ALPHA, BRANCH_A, capsys=capsys, monkeypatch=monkeypatch)
    assert run(root, "check", ALPHA, "--final") == 0
    assert updated(root, readable=False) == 0
    assert "start" not in queue_notes.read_note(root, BRANCH_A)
    capsys.readouterr()
    assert not start_passed(root)
    assert hooked(tree) == refused(BRANCH_A)


def test_the_check_an_update_runs_before_its_rebase_passes_no_start_of_the_pipeline(
    tmp_path: Path, capsys: Out, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = project(tmp_path)
    assert run(root, "open", ALPHA) == 0
    tree = tree_of(root)
    pipeline_preflight(tree, ALPHA, BRANCH_A, capsys=capsys, monkeypatch=monkeypatch)
    note = queue_notes.read_note(root, BRANCH_A)
    queue_notes.write_note(queue_notes.note_path(root, BRANCH_A), {**note, "passed": True})
    write(tree, "src/built.py", "B = 1\n")
    commit(tree, "feat(core): built")
    assert updated(root, change=1) == 0
    assert not start_passed(root)
    assert updated(root, readable=False, change=2) == 0
    assert "start" not in queue_notes.read_note(root, BRANCH_A)
    capsys.readouterr()
    assert not start_passed(root), "the check before the rebase is not the final check"
    assert hooked(tree) == refused(BRANCH_A)


def test_an_update_that_records_a_new_start_closes_the_pipelines_start_whatever_the_note_held(
    tmp_path: Path, capsys: Out, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = project(tmp_path)
    assert run(root, "open", ALPHA) == 0
    tree = tree_of(root)
    pipeline_preflight(tree, ALPHA, BRANCH_A, capsys=capsys, monkeypatch=monkeypatch)
    assert run(root, "check", ALPHA, "--final") == 0
    path = queue_notes.note_path(root, BRANCH_A)
    queue_notes.write_note(path, {**queue_notes.read_note(root, BRANCH_A), "rebasing": "0" * 40})
    assert run(root, "update", ALPHA) == 0
    by_hand(root, "passed")
    capsys.readouterr()
    assert not start_passed(root), "the new start closed the start the final check passed"
    assert hooked(tree) == refused(BRANCH_A)


def test_an_update_cut_off_in_its_rebase_has_closed_the_pipelines_start(
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
    note = queue_notes.read_note(root, BRANCH_A)
    queue_notes.write_note(
        queue_notes.note_path(root, BRANCH_A), {k: v for k, v in note.items() if k != "rebasing"}
    )
    capsys.readouterr()
    assert not start_passed(root), "the rebase's mark closed the start the final check passed"
    assert hooked(tree) == refused(BRANCH_A)


def test_an_update_refused_after_its_first_check_passes_no_start_of_the_pipeline(
    tmp_path: Path, capsys: Out, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = project(tmp_path)
    assert run(root, "open", ALPHA) == 0
    tree = tree_of(root)
    pipeline_preflight(tree, ALPHA, BRANCH_A, capsys=capsys, monkeypatch=monkeypatch)
    assert run(root, "check", ALPHA, "--final") == 0
    write(tree, "src/built.py", "B = 1\n")
    commit(tree, "feat(core): built")
    assert updated(root, change=1) == 0
    git(root, "update-ref", f"refs/remotes/origin/{BRANCH_A}", BRANCH_A)
    capsys.readouterr()
    moved(root, "src/m2.py", "M = 2\n")
    assert run(root, "update", ALPHA) == 1
    assert "the branch is pushed" in capsys.readouterr().out
    assert queue_notes.read_note(root, BRANCH_A).get("passed") is True
    assert not start_passed(root), "the check before the rebase is not the final check"
    assert hooked(tree) == refused(BRANCH_A)


def test_a_ticket_opened_again_without_a_preflight_lets_no_commit_through_a_note_written_by_hand(
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
    by_hand(root, "passed")
    capsys.readouterr()
    assert not start_passed(root), "the open closed the start the old run's check passed"
    assert hooked(tree) == refused(BRANCH_A)


@pytest.mark.parametrize("variant", ["check", "red", "halted", "unread"])
def test_only_a_green_final_check_passes_the_pipelines_start(
    tmp_path: Path, capsys: Out, monkeypatch: pytest.MonkeyPatch, *, variant: str
) -> None:
    root = project(tmp_path)
    assert run(root, "open", ALPHA) == 0
    tree = tree_of(root)
    pipeline_preflight(tree, ALPHA, BRANCH_A, capsys=capsys, monkeypatch=monkeypatch)
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(drain, "discard", lambda *_args: "kept for the gate")
        present = PRESENT.get(variant)
        if present is not None:
            patch.setattr(drain, "read_present", lambda *_args: present)
        assert run(root, "check", ALPHA, *([] if variant == "check" else ["--final"])) in {0, 1, 3}
    note = queue_notes.read_note(root, BRANCH_A)
    capsys.readouterr()
    assert note.get("passed") is (True if variant == "halted" else None)
    assert not start_passed(root)
    assert hooked(tree) == refused(BRANCH_A)


PRESENT: dict[str, tuple[list[str], bool, str | None]] = {
    "red": (["a role committed on the branch"], False, None),
    "halted": ([], True, None),
    "unread": ([], False, "the forge cannot be read"),
}


@pytest.mark.parametrize("form", ["link", "dots"])
def test_the_final_check_passes_a_start_that_names_its_tree_another_way(
    tmp_path: Path, capsys: Out, monkeypatch: pytest.MonkeyPatch, *, form: str
) -> None:
    root = project(tmp_path)
    assert run(root, "open", ALPHA) == 0
    tree = tree_of(root)
    named = tmp_path / "link" if form == "link" else tree / ".." / tree.name
    if form == "link":
        named.symlink_to(tree)
    pipeline_preflight(tree, ALPHA, BRANCH_A, capsys=capsys, monkeypatch=monkeypatch)
    start = json.loads(started(root).read_text(encoding="utf-8"))["start"]
    queue_notes.record_feature_start(named, BRANCH_A, start)
    assert hooked(tree) == refused(BRANCH_A)
    assert run(root, "check", ALPHA, "--final") == 0
    capsys.readouterr()
    assert start_passed(root)
    assert hooked(tree) == "allowed"


@pytest.mark.parametrize("kind", ["directory", "pipe", "unreadable", "missing"])
def test_the_final_check_says_the_same_whatever_the_pipelines_start_is(
    tmp_path: Path, capsys: Out, monkeypatch: pytest.MonkeyPatch, *, kind: str
) -> None:
    root = project(tmp_path)
    assert run(root, "open", ALPHA) == 0
    tree = tree_of(root)
    pipeline_preflight(tree, ALPHA, BRANCH_A, capsys=capsys, monkeypatch=monkeypatch)
    path = started(root)
    if kind in {"directory", "pipe", "missing"}:
        path.unlink()
    if kind == "directory":
        path.mkdir()
    elif kind == "pipe":
        os.mkfifo(path)
    elif kind == "unreadable":
        path.chmod(0)
    assert run(root, "check", ALPHA, "--final") == 0
    said = capsys.readouterr()
    assert said.out.splitlines()[-1] == publish_check.GREEN
    assert said.err == ""
    assert queue_notes.read_note(root, BRANCH_A).get("passed") is True
    if path.is_file():
        path.chmod(stat.S_IRUSR | stat.S_IWUSR)
    assert not start_passed(root)


@contextlib.contextmanager
def locked(root: Path) -> Iterator[None]:
    folder = started(root).parent
    folder.mkdir(exist_ok=True)
    folder.chmod(stat.S_IRUSR | stat.S_IXUSR)
    try:
        yield
    finally:
        folder.chmod(stat.S_IRWXU)


def snapshot(root: Path) -> tuple[bytes | None, ...]:
    tree = tree_of(root)
    head = gatelib.git(tree, "rev-parse", "HEAD") if tree.is_dir() else None
    files = (queue_notes.note_path(root, BRANCH_A), started(root))
    return (
        *(path.read_bytes() if path.is_file() else None for path in files),
        (gatelib.git(root, "for-each-ref") or "").encode(),
        (head or "").encode(),
    )


def told_to_unlock(said: str, tmp_path: Path) -> None:
    line = next(line for line in said.splitlines() if "cannot be written" in line)
    assert "`ratchet-feature-starts/feature-1-alpha.json` in the git directory" in line, line
    assert line.endswith("make that folder writable, then run this again"), line
    assert str(tmp_path) not in line, line


def amended(root: Path) -> None:
    tree = tree_of(root)
    write(tree, "src/role.py", "R = 1\n")
    git(tree, "add", "-A")
    git(tree, "commit", "-q", "--no-verify", "--amend", "-m", "feat(core): amended by a role")


def answered(root: Path) -> None:
    assert run(root, "close", ALPHA, "--outcome", "parked", "--reason", "which case?") == 0
    write(root, ALPHA, ticket(1, "Alpha", "Answered: lower.\n\n"))
    commit(root, "docs: answer the question")


def cut_off_mark(root: Path) -> None:
    path = queue_notes.note_path(root, BRANCH_A)
    queue_notes.write_note(path, {**queue_notes.read_note(root, BRANCH_A), "rebasing": "0" * 40})


def blocked_command(root: Path, case: str) -> int:
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(drain, "discard", lambda *_args: "kept for the gate")
        if case == "update unread":
            patch.setattr(publish_check, "start_of", unreadable_start)
        if case == "final red":
            patch.setattr(drain, "read_present", lambda *_args: PRESENT["red"])
        if case == "reopen":
            return run(root, "open", ALPHA)
        if case.startswith("update"):
            return run(root, "update", ALPHA)
        return run(root, "check", ALPHA, "--final")


SET_UP: dict[str, Callable[[Path], None]] = {
    "update": moved,
    "update unread": moved,
    "update resumed": cut_off_mark,
    "reopen": answered,
    "final red": lambda _root: None,
    "role amend": amended,
}


@pytest.mark.parametrize("case", SET_UP)
def test_a_command_that_cannot_close_the_pipelines_start_changes_nothing_and_fails(
    tmp_path: Path, capsys: Out, monkeypatch: pytest.MonkeyPatch, *, case: str
) -> None:
    root = project(tmp_path)
    assert run(root, "open", ALPHA) == 0
    tree = tree_of(root)
    pipeline_preflight(tree, ALPHA, BRANCH_A, capsys=capsys, monkeypatch=monkeypatch)
    assert run(root, "check", ALPHA, "--final") == 0
    with locked(root):
        SET_UP[case](root)
        capsys.readouterr()
        gate = hooked(tree) if tree.is_dir() else None
        before = snapshot(root)
        code = blocked_command(root, case)
    said = capsys.readouterr().out
    assert code == (1 if case == "reopen" else 2), said
    told_to_unlock(said, tmp_path)
    assert snapshot(root) == before, "the command changed the note, the start or a ref"
    assert (hooked(tree) if tree.is_dir() else None) == gate


def test_a_final_check_that_cannot_pass_the_pipelines_start_fails_and_the_commit_stays_refused(
    tmp_path: Path, capsys: Out, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = project(tmp_path)
    assert run(root, "open", ALPHA) == 0
    tree = tree_of(root)
    pipeline_preflight(tree, ALPHA, BRANCH_A, capsys=capsys, monkeypatch=monkeypatch)
    before = snapshot(root)
    with locked(root):
        code = run(root, "check", ALPHA, "--final")
    said = capsys.readouterr().out
    assert code == 2, said
    assert publish_check.GREEN not in said
    told_to_unlock(said, tmp_path)
    assert snapshot(root) == before, "the check passed the note while the start stays unpassed"
    assert hooked(tree) == refused(BRANCH_A)


def test_a_start_already_closed_is_not_written_again(
    tmp_path: Path, capsys: Out, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = project(tmp_path)
    assert run(root, "open", ALPHA) == 0
    tree = tree_of(root)
    pipeline_preflight(tree, ALPHA, BRANCH_A, capsys=capsys, monkeypatch=monkeypatch)
    with locked(root):
        moved(root)
        assert run(root, "update", ALPHA) == 0, capsys.readouterr().out
    capsys.readouterr()
    assert hooked(tree) == refused(BRANCH_A)


@pytest.mark.parametrize("where", ["no folder", "no start", "another tree"])
def test_a_drain_with_no_start_of_its_tree_to_close_runs_as_before(
    tmp_path: Path, capsys: Out, monkeypatch: pytest.MonkeyPatch, *, where: str
) -> None:
    root = project(tmp_path)
    if where == "another tree":
        git(root, "switch", "-q", "-c", BRANCH_A)
        pipeline_preflight(root, ALPHA, BRANCH_A, capsys=capsys, monkeypatch=monkeypatch)
        git(root, "switch", "-q", "main")
    folder = started(root).parent
    with contextlib.nullcontext() if where == "no folder" else locked(root):
        assert run(root, "open", ALPHA) == 0
        assert run(root, "check", ALPHA, "--final") == 0
        moved(root)
        assert run(root, "update", ALPHA) == 0
        assert run(root, "check", ALPHA, "--final") == 0
        said = capsys.readouterr()
        assert hooked(tree_of(root)) == "allowed"
    assert "cannot be written" not in said.out + said.err
    assert folder.is_dir() is (where != "no folder")


def test_an_update_that_would_make_its_work_tree_again_fails_first_when_its_start_is_locked(
    tmp_path: Path, capsys: Out, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = project(tmp_path)
    assert run(root, "open", ALPHA) == 0
    tree = tree_of(root)
    pipeline_preflight(tree, ALPHA, BRANCH_A, capsys=capsys, monkeypatch=monkeypatch)
    assert run(root, "check", ALPHA, "--final") == 0
    write(tree, "src/built.py", "B = 1\n")
    commit(tree, "feat(core): built")
    assert run(root, "close", ALPHA, "--outcome", "waits", "--reason", "behind: later") == 0
    moved(root)
    capsys.readouterr()
    walk = Walk(tmp_path, root, tree, capsys)
    saved = save(tmp_path, tmp_path / "saved" / "waiting")
    every_command_with_its_start_unwritable(walk, saved, ("final", "commit", "close", "moved"))
    as_passed(walk, saved)
    with locked(root):
        before = held(walk)
        code = run(root, "update", ALPHA)
        after = held(walk)
    said = capsys.readouterr().out
    assert code == 2, said
    told_to_unlock(said, tmp_path)
    assert [k for k in before.keys() | after.keys() if before.get(k) != after.get(k)] == []
    assert run(root, "update", ALPHA) == 0
    capsys.readouterr()
    assert hooked(tree) == refused(BRANCH_A)


def test_an_update_with_nothing_to_move_keeps_the_commit_its_final_check_let_through(
    tmp_path: Path, capsys: Out, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = project(tmp_path)
    assert run(root, "open", ALPHA) == 0
    tree = tree_of(root)
    pipeline_preflight(tree, ALPHA, BRANCH_A, capsys=capsys, monkeypatch=monkeypatch)
    assert run(root, "check", ALPHA, "--final") == 0
    assert run(root, "update", ALPHA) == 0
    capsys.readouterr()
    assert hooked(tree) == "allowed"
    real = drain.update

    def locking_after(*args: object) -> int:
        code = real(*args)  # type: ignore[arg-type]
        started(root).parent.chmod(stat.S_IRUSR | stat.S_IXUSR)
        return code

    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(drain, "update", locking_after)
        try:
            code = run(root, "update", ALPHA)
        finally:
            started(root).parent.chmod(stat.S_IRWXU)
    said = capsys.readouterr().out
    assert code == 0, said
    assert "stays unpassed, so a commit in its work tree is refused, and `update` ran;" in said
    assert hooked(tree) == refused(BRANCH_A)


class View(NamedTuple):
    tree: bool
    noted: bool
    started: bool
    passed: bool
    rebasing: bool
    parked: bool
    recorded: bool
    start_passed: bool
    mains: bool
    green: bool


@dataclass(frozen=True)
class Node:
    saved: Path
    steps: tuple[str, ...]
    view: View
    mains: bytes | None


@dataclass
class Walk:
    home: Path
    root: Path
    tree: Path
    capsys: Out
    changes: int = 0
    probed: dict[tuple[bool, ...], tuple[str, ...]] = field(default_factory=dict)


def final_as(walk: Walk, present: tuple[list[str], bool, str | None]) -> int:
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(drain, "discard", lambda *_args: "kept by the walk")
        patch.setattr(drain, "read_present", lambda *_args: present)
        return run(walk.root, "check", ALPHA, "--final")


def preflight_in(walk: Walk) -> int:
    argv = ["feature_preflight.py", ALPHA, "--root", str(walk.tree), "--base", "main"]
    return preflight.main([*argv, "--branch", BRANCH_A])


def update_in(walk: Walk, *, readable: bool) -> int:
    walk.changes += 1
    return updated(walk.root, readable=readable, change=walk.changes)


def reopen(walk: Walk) -> int:
    walk.changes += 1
    write(walk.root, ALPHA, ticket(1, "Alpha", f"Answered: case {walk.changes}.\n\n"))
    commit(walk.root, "docs: answer the question")
    return run(walk.root, "open", ALPHA)


Step = tuple[Callable[[View], bool], Callable[[Walk], int]]
STEPS: dict[str, Step] = {
    "open": (
        lambda seen: not seen.tree and not seen.parked,
        lambda walk: run(walk.root, "open", ALPHA),
    ),
    "reopen": (lambda seen: not seen.tree and seen.parked, reopen),
    "preflight": (lambda seen: seen.tree, preflight_in),
    "check": (lambda seen: seen.started, lambda walk: run(walk.root, "check", ALPHA)),
    "final": (
        lambda seen: seen.started,
        lambda walk: run(walk.root, "check", ALPHA, "--final"),
    ),
    "final red": (lambda seen: seen.started, lambda walk: final_as(walk, PRESENT["red"])),
    "final halted": (lambda seen: seen.started, lambda walk: final_as(walk, PRESENT["halted"])),
    "update": (lambda seen: seen.noted, lambda walk: update_in(walk, readable=True)),
    "update unread": (lambda seen: seen.noted, lambda walk: update_in(walk, readable=False)),
    "park": (
        lambda seen: seen.noted,
        lambda walk: run(walk.root, "close", ALPHA, "--outcome", "parked", "--reason", "q?"),
    ),
    "close": (
        lambda seen: seen.noted,
        lambda walk: run(walk.root, "close", ALPHA, "--outcome", "waits", "--reason", "behind: x"),
    ),
}
FINAL_STEPS = {"final", "final red", "final halted"}
OPENERS = {"open", "reopen", "preflight"}
UPDATES = {"update", "update unread"}
HAND = ("passed", "start", "delete")


def save(tmp_path: Path, into: Path) -> Path:
    for name in ("repo", "tmp"):
        shutil.copytree(tmp_path / name, into / name, symlinks=True)
    return into


def load(tmp_path: Path, saved: Path) -> None:
    for name in ("repo", "tmp"):
        shutil.rmtree(tmp_path / name)
        shutil.copytree(saved / name, tmp_path / name, symlinks=True)


def gated(tree: Path) -> bool:
    return GitRepoState().awaiting_final(tree) is not None


def swapped(path: Path, text: bytes | None) -> bytes | None:
    now = path.read_bytes() if path.is_file() else None
    path.unlink(missing_ok=True)
    if text is not None:
        path.write_bytes(text)
    return now


def gated_on_main(walk: Walk, mains: bytes | None) -> bool:
    path = started(walk.root)
    now = swapped(path, mains)
    refused_there = gated(walk.tree)
    swapped(path, now)
    return refused_there


def still_green(name: str, code: int, *, green: bool) -> bool:
    if name in FINAL_STEPS:
        return code == 0
    if name in OPENERS:
        return green and code != 0
    if name in UPDATES:
        return green and code == 1
    return green


def view_of(walk: Walk, mains: bytes | None, *, green: bool) -> View:
    note = queue_notes.read_note(walk.root, BRANCH_A)
    start = queue_notes.loaded(gatelib.read_text(started(walk.root)))
    return View(
        tree=walk.tree.is_dir(),
        noted=queue_notes.note_path(walk.root, BRANCH_A).is_file(),
        started="start" in note,
        passed=note.get("passed") is True,
        rebasing=bool(note.get("rebasing")),
        parked=note.get("outcome") == drain.PARKED,
        recorded=bool(start),
        start_passed=start.get("passed") is True,
        mains=mains is not None,
        green=green,
    )


def as_main_would(walk: Walk, steps: tuple[str, ...], *, green: bool, mains: bytes | None) -> None:
    through = not gated(walk.tree)
    assert not through or green or not gated_on_main(walk, mains), (
        f"{steps}: a commit main refuses goes through"
    )
    assert not start_passed(walk.root) or green, f"{steps}: the one-ticket start is passed"


def by_every_hand(walk: Walk, node: Node) -> None:
    path = queue_notes.note_path(walk.root, BRANCH_A)
    for edit in HAND:
        now = path.read_bytes()
        by_hand(walk.root, edit)
        as_main_would(walk, (*node.steps, f"hand {edit}"), green=node.view.green, mains=node.mains)
        swapped(path, now)


def taken(walk: Walk, node: Node, step: tuple[str, int]) -> Node:
    name, code = step
    steps = (*node.steps, name)
    green = still_green(name, code, green=node.view.green)
    mains = started(walk.root).read_bytes() if name == "preflight" and code == 0 else node.mains
    if walk.tree.is_dir():
        as_main_would(walk, steps, green=green, mains=mains)
        if name == "final" and code == 0:
            assert not gated(walk.tree), f"{steps}: a green final check leaves the commit refused"
    return Node(node.saved, steps, view_of(walk, mains, green=green), mains)


WALK_DEPTH = 4


def level(walk: Walk, frontier: list[Node], seen: set[View]) -> list[Node]:
    reached: list[Node] = []
    for node in frontier:
        for name, (applies, act) in STEPS.items():
            if not applies(node.view):
                continue
            load(walk.home, node.saved)
            code = act(walk)
            walk.capsys.readouterr()
            after = taken(walk, node, (name, code))
            if after.view in seen:
                continue
            seen.add(after.view)
            if after.view.tree and after.view.noted:
                by_every_hand(walk, after)
            saved = save(walk.home, walk.home / "saved" / str(len(seen)))
            walk.probed.setdefault(after.view[:6], after.steps)
            reached.append(Node(saved, after.steps, after.view, after.mains))
    return reached


UNCOMPARED = frozenset({"objects", "index"})
WRITING = os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_APPEND | os.O_TRUNC
MOVING = frozenset({"os.rename", "os.remove", "os.rmdir", "os.mkdir", "os.chmod"})
TRIED: list[list[str]] = []


def note_a_write(event: str, args: tuple[object, ...]) -> None:
    if not TRIED:
        return
    opened = event == "open" and isinstance(args[2], int) and args[2] & WRITING
    if opened or event in MOVING:
        TRIED[-1].append(os.fspath(args[0]))  # type: ignore[arg-type]


@functools.cache
def watching() -> None:
    sys.addaudithook(note_a_write)


@contextlib.contextmanager
def writes_watched() -> Iterator[list[str]]:
    watching()
    TRIED.append([])
    try:
        yield TRIED[-1]
    finally:
        TRIED.pop()


def held(walk: Walk) -> dict[str, object]:
    found: dict[str, object] = {}
    for base in (walk.root, walk.home / "tmp"):
        for top, folders, files in os.walk(base):
            folders[:] = [name for name in folders if name not in UNCOMPARED]
            for name in files:
                if name not in UNCOMPARED:
                    found[str(Path(top, name))] = Path(top, name).read_bytes()
    listing = gatelib.git(walk.root, "worktree", "list", "--porcelain") or ""
    found["work trees"] = listing
    for line in listing.splitlines():
        if line.startswith("worktree "):
            tree = Path(line.removeprefix("worktree "))
            found[f"{tree} index"] = gatelib.git(tree, "ls-files", "--stage")
    return found


@functools.cache
def subcommands() -> tuple[str, ...]:
    shown = io.StringIO()
    with contextlib.redirect_stdout(shown), pytest.raises(SystemExit):
        drain.main(["drain", "--help"])
    (listed,) = re.findall(r"positional arguments:\n\s+\{([^}]*)\}", shown.getvalue())
    return tuple(listed.split(","))


def commit_answer(walk: Walk) -> str | None:
    return hooked(walk.tree) if walk.tree.is_dir() else None


def as_passed(walk: Walk, saved: Path) -> None:
    load(walk.home, saved)
    path = started(walk.root)
    queue_notes.write_note(path, {**queue_notes.loaded(gatelib.read_text(path)), "passed": True})


def ran(walk: Walk, name: str) -> int:
    code = run(walk.root, name, ALPHA, "--final")
    walk.capsys.readouterr()
    return code


def every_command_with_its_start_unwritable(
    walk: Walk, saved: Path, steps: tuple[str, ...]
) -> None:
    as_passed(walk, saved)
    folder = str(started(walk.root).parent)
    for name in subcommands():
        with locked(walk.root), writes_watched() as tried:
            before = held(walk)
            ran(walk, name)
            after = held(walk)
        if after == before or not any(path.startswith(folder) for path in tried):
            continue
        answer = commit_answer(walk)
        as_passed(walk, saved)
        basis = checked_basis(walk)
        ran(walk, name)
        assert start_passed(walk.root), f"{steps} {name}: it changed things before it closed S"
        assert checked_basis(walk) == basis or not walk.tree.is_dir() or passed_by_note(walk), (
            f"{steps} {name}: S stays passed for a tree the drain's note does not pass"
        )
        assert answer == commit_answer(walk), f"{steps} {name}: the commit's answer turned on S"
        as_passed(walk, saved)


def checked_basis(walk: Walk) -> tuple[str | None, object]:
    head = gatelib.git(walk.tree, "rev-parse", "HEAD") if walk.tree.is_dir() else None
    return head, queue_notes.read_note(walk.root, BRANCH_A).get("start")


def passed_by_note(walk: Walk) -> bool:
    return queue_notes.read_note(walk.root, BRANCH_A).get("passed") is True


DRAIN_STATES: list[tuple[str, ...]] = [
    ("close",),
    ("final",),
    ("final", "close"),
    ("final", "final red"),
    ("final", "final red", "update unread"),
    ("final", "final red", "update unread", "close"),
    ("final", "final red", "update unread", "park"),
    ("final", "park"),
    ("final red",),
    ("park",),
    ("update unread",),
]


def replayed(
    tmp_path: Path, capsys: Out, monkeypatch: pytest.MonkeyPatch, *, steps: tuple[str, ...]
) -> Walk:
    root = project(tmp_path)
    monkeypatch.setattr(preflight, "run_gh", lambda _args: "[]")
    walk = Walk(tmp_path, root, tree_of(root), capsys)
    assert run(root, "open", ALPHA) == 0
    assert preflight_in(walk) == 0
    for name in steps:
        STEPS[name][1](walk)
    capsys.readouterr()
    return walk


@pytest.mark.parametrize(
    "steps",
    [
        pytest.param(steps, marks=pytest.mark.shard_slot(20 + n), id=", ".join(steps))
        for n, steps in enumerate(DRAIN_STATES)
    ],
)
def test_every_drain_command_on_a_locked_start_fails_changing_nothing_or_closes_nothing(
    tmp_path: Path, capsys: Out, monkeypatch: pytest.MonkeyPatch, *, steps: tuple[str, ...]
) -> None:
    walk = replayed(tmp_path, capsys, monkeypatch, steps=steps)
    saved = save(tmp_path, tmp_path / "saved" / "probe")
    every_command_with_its_start_unwritable(walk, saved, steps)


@pytest.mark.shard_slot(10)
def test_no_run_of_the_drain_lets_through_a_commit_main_refuses_unless_its_final_check_is_green(
    tmp_path: Path, capsys: Out, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = project(tmp_path)
    monkeypatch.setattr(preflight, "run_gh", lambda _args: "[]")
    walk = Walk(tmp_path, root, tree_of(root), capsys)
    assert run(root, "open", ALPHA) == 0
    assert preflight_in(walk) == 0
    capsys.readouterr()
    mains = started(root).read_bytes()
    saved = save(tmp_path, tmp_path / "saved" / "0")
    first = Node(saved, (), view_of(walk, mains, green=False), mains)
    seen = {first.view}
    frontier = [node for node in level(walk, [first], seen) if node.steps == ("final",)]
    assert len(frontier) == 1
    for _ in range(WALK_DEPTH):
        frontier = level(walk, frontier, seen)
    assert frontier == [], f"the walk reached a new state in {WALK_DEPTH + 1} steps"
    assert sorted(walk.probed.values()) == sorted(DRAIN_STATES), "add the new path to DRAIN_STATES"
