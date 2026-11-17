"""The guard refuses a commit on a one-ticket run's branch until its publish check has passed."""

import json
from dataclasses import asdict
from pathlib import Path

import pytest

import drain
import publish_check
import queue_notes
from ratchet_guards import bash, events
from ratchet_guards.context import Context
from ratchet_guards.hook import GitRepoState
from ratchet_guards.verdict import RULES, Decision, Verdict
from tests.repos import git, repository, write
from tests.support import make_guard
from tests.test_drain import ALPHA, BRANCH_A, Out, project, run, scratch  # noqa: F401
from tests.test_feature_preflight import RECORD, gh_returning, issue
from tests.test_feature_preflight import run as run_preflight

COMMIT = "git commit -m 'feat(core): the change'"
BRANCH = "feature/4-x"
GATED = ["queue-final-check"]
RESULT = {
    "criticFindingsByRound": [],
    "unresolvedBlockingFindings": [],
    "reviewIssues": [],
    "reviewIssuesByRound": [],
}


def judged(command: str, cwd: Path) -> list[Verdict]:
    return bash.check(command, Context(cwd), make_guard(GitRepoState()))


def started(tmp_path: Path) -> tuple[Path, Path]:
    root = repository(tmp_path / "repo", {"docs/agents/triage-labels.md": RECORD})
    git(root, "switch", "-q", "-c", BRANCH)
    report = run_preflight(root, "4", gh_returning(issue()), branch=BRANCH)
    assert report.ok
    saved = tmp_path / "start.json"
    saved.write_text(json.dumps(asdict(report)), encoding="utf-8")
    return root, saved


def publish(root: Path, saved: Path, *flags: str) -> int:
    return publish_check.main(["publish_check.py", str(saved), "--root", str(root), *flags])


def give_result(saved: Path) -> None:
    publish_check.result_path(saved).write_text(json.dumps(RESULT), encoding="utf-8")


def test_a_one_ticket_commit_is_refused_until_the_publish_check_has_passed(tmp_path: Path) -> None:
    root, saved = started(tmp_path)
    found = judged(COMMIT, root)
    assert [(v.rule, v.decision, v.detail) for v in found] == [
        ("queue-final-check", Decision.DENY, f"git commit on `{BRANCH}`")
    ]
    assert "publish_check.py" in str(RULES["queue-final-check"].way_out)
    give_result(saved)
    assert publish(root, saved) == 0
    assert judged(COMMIT, root) == []


def test_a_failed_publish_check_keeps_the_commit_refused(tmp_path: Path, capsys: Out) -> None:
    root, saved = started(tmp_path)
    assert publish(root, saved) == 2
    assert [v.rule for v in judged(COMMIT, root)] == GATED
    give_result(saved)
    write(root, "src/x.py", "x = 1\n")
    git(root, "add", "-A")
    git(root, "commit", "-q", "-m", "a role commits")
    capsys.readouterr()
    assert publish(root, saved) == 1
    assert [v.rule for v in judged(COMMIT, root)] == GATED


def test_a_check_before_a_resume_does_not_pass_the_start(tmp_path: Path) -> None:
    root, saved = started(tmp_path)
    assert publish(root, saved, "--before-resume") == 0
    assert [v.rule for v in judged(COMMIT, root)] == GATED


def test_another_branch_of_the_same_checkout_is_not_gated(tmp_path: Path) -> None:
    root, _ = started(tmp_path)
    assert [v.rule for v in judged(COMMIT, root)] == GATED
    git(root, "switch", "-q", "main")
    assert judged(COMMIT, root) == []


def test_each_preflight_on_the_branch_records_the_start_again(tmp_path: Path) -> None:
    root, saved = started(tmp_path)
    give_result(saved)
    assert publish(root, saved) == 0
    assert judged(COMMIT, root) == []
    assert run_preflight(root, "4", gh_returning(issue()), branch=BRANCH).ok
    assert [v.rule for v in judged(COMMIT, root)] == GATED


def test_a_drains_gate_is_as_it_was(tmp_path: Path) -> None:
    root = project(tmp_path)
    assert run(root, "open", ALPHA) == 0
    tree = drain.tree_of(root, BRANCH_A)
    assert [v.rule for v in judged(COMMIT, tree)] == GATED
    assert run(root, "check", ALPHA, "--final") == 0
    assert judged(COMMIT, tree) == []
    assert queue_notes.read_note(root, BRANCH_A).get("passed") is True
    common = Path(git(root, "rev-parse", "--path-format=absolute", "--git-common-dir"))
    assert not (common / events.FEATURE_STARTS).exists()


def test_the_start_is_a_locked_guard_path_in_every_checkout() -> None:
    hit = make_guard().locator.classify(
        Path("/elsewhere/.git") / events.FEATURE_STARTS / "feature-4-x.json", destructive=False
    )
    assert hit is not None
    assert hit.group.locked
    assert hit.group.name == "feature-starts"


def test_a_write_killed_midway_leaves_the_old_note_and_the_commit_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root, saved = started(tmp_path)
    give_result(saved)
    note = queue_notes.feature_start_path(root, BRANCH)
    assert note is not None
    before = note.read_text(encoding="utf-8")

    def killed(*_: object) -> Path:
        message = "killed"
        raise OSError(message)

    monkeypatch.setattr(Path, "replace", killed)
    with pytest.raises(OSError, match="killed"):
        publish(root, saved)
    assert note.read_text(encoding="utf-8") == before
    assert [v.rule for v in judged(COMMIT, root)] == GATED


def test_a_start_outside_a_repository_writes_no_note(tmp_path: Path) -> None:
    queue_notes.record_feature_start(tmp_path, BRANCH_A, {"head": "0" * 40})
    assert queue_notes.feature_start_path(tmp_path, BRANCH_A) is None
    assert list(tmp_path.iterdir()) == []


def test_a_pass_with_no_start_recorded_writes_no_note(tmp_path: Path) -> None:
    queue_notes.pass_feature_start(tmp_path, BRANCH_A)
    assert list(tmp_path.iterdir()) == []
    root = project(tmp_path / "repo")
    queue_notes.pass_feature_start(root, BRANCH_A)
    path = queue_notes.feature_start_path(root, BRANCH_A)
    assert path is not None
    assert not path.exists()
