"""``drain.py check``: the publish check of ADR 0060 in a drain, and the start it reads."""

import stat
from pathlib import Path
from unittest import mock

import pytest

import drain
import feature_preflight as preflight
import publish_check
import queue_notes
from tests.repos import commit, git, write
from tests.test_drain import (
    ALPHA,
    BRANCH_A,
    GAMMA,
    Out,
    build,
    moved,
    note,
    offline,
    project,
    run,
    scratch,  # noqa: F401 # -- an autouse fixture of the drain's tests
)

UNREADABLE = publish_check.UnreadableError("the forge cannot be reached")
NO_CHECK = "no publish check has passed its start"


def opened(tmp_path: Path) -> Path:
    root = project(tmp_path)
    assert run(root, "open", ALPHA) == 0
    return root


def test_open_records_the_start_in_the_note(tmp_path: Path, capsys: Out) -> None:
    root = project(tmp_path)
    assert run(root, "open", ALPHA) == 0
    printed = capsys.readouterr().out
    start = note(root, BRANCH_A)["start"]
    assert f'"head": "{git(root, "rev-parse", "main")}"' in printed
    assert start == {
        "branch": BRANCH_A,
        "head": git(root, "rev-parse", "main"),
        "forge": None,
        "rebase": None,
    }
    assert "passed" not in note(root, BRANCH_A)


def test_open_that_cannot_read_the_start_makes_no_tree(tmp_path: Path, capsys: Out) -> None:
    root = project(tmp_path)
    with mock.patch.object(publish_check, "start_of", side_effect=UNREADABLE):
        assert run(root, "open", ALPHA) == 1
    assert "the start of `feature/1-alpha` cannot be read: the forge cannot be reached" in (
        capsys.readouterr().out
    )
    assert not drain.tree_of(root, BRANCH_A).exists()


def test_a_check_without_final_judges_and_marks_nothing(tmp_path: Path, capsys: Out) -> None:
    root = opened(tmp_path)
    capsys.readouterr()
    assert run(root, "check", ALPHA) == 0
    assert capsys.readouterr().out.startswith(publish_check.GREEN)
    assert "passed" not in note(root, BRANCH_A)
    assert run(root, "land", ALPHA) == drain.UNREAD
    assert NO_CHECK in capsys.readouterr().out
    assert run(root, "check", ALPHA, "--final") == 0
    assert note(root, BRANCH_A)["passed"] is True


def test_a_check_without_final_parks_a_branch_that_moved_as_a_final_one_does(
    tmp_path: Path, capsys: Out
) -> None:
    root = opened(tmp_path)
    tree = drain.tree_of(root, BRANCH_A)
    write(tree, "src/role.py", "ROLE = 1\n")
    commit(tree, "feat(core): a role's own commit")
    capsys.readouterr()
    assert run(root, "check", ALPHA) == 1
    assert "a role committed" in capsys.readouterr().out
    held = note(root, BRANCH_A)
    assert (held["outcome"], held["hold"]) == ("parked", True)
    assert "passed" not in held


def test_land_refuses_a_note_with_no_start_or_no_mark(tmp_path: Path, capsys: Out) -> None:
    root = opened(tmp_path)
    path = queue_notes.note_path(root, BRANCH_A)
    held = note(root, BRANCH_A)
    queue_notes.write_note(path, {k: v for k, v in held.items() if k != "start"} | {"passed": True})
    capsys.readouterr()
    assert run(root, "land", ALPHA) == drain.UNREAD
    assert NO_CHECK in capsys.readouterr().out
    queue_notes.write_note(path, held)
    assert run(root, "land", ALPHA) == drain.UNREAD


def test_a_check_with_no_start_says_so_and_changes_nothing(tmp_path: Path, capsys: Out) -> None:
    root = project(tmp_path)
    assert run(root, "check", ALPHA) == 2
    assert "has no start to check against" in capsys.readouterr().out
    assert note(root, BRANCH_A) == {}


def test_a_check_of_a_held_ticket_checks_nothing(tmp_path: Path, capsys: Out) -> None:
    root = opened(tmp_path)
    assert run(root, "close", ALPHA, "--outcome", "parked", "--reason", "a question") == 0
    capsys.readouterr()
    assert run(root, "check", ALPHA, "--final") == drain.UNREAD
    assert "is held and is not checked" in capsys.readouterr().out
    assert "passed" not in note(root, BRANCH_A)


def test_a_check_of_a_merged_ticket_checks_nothing(tmp_path: Path, capsys: Out) -> None:
    root = opened(tmp_path)
    path = queue_notes.note_path(root, BRANCH_A)
    queue_notes.write_note(path, {**note(root, BRANCH_A), "outcome": "merged"})
    capsys.readouterr()
    assert run(root, "check", ALPHA, "--final") == 1
    assert "is merged and is not checked" in capsys.readouterr().out


def test_a_check_makes_no_tree_for_a_branch_with_no_commit_of_its_own(
    tmp_path: Path, capsys: Out
) -> None:
    root = opened(tmp_path)
    git(root, "worktree", "remove", "--force", str(drain.tree_of(root, BRANCH_A)))
    capsys.readouterr()
    assert run(root, "check", ALPHA, "--final") == drain.UNREAD
    assert "no commit of its own and no work tree" in capsys.readouterr().out
    assert not drain.tree_of(root, BRANCH_A).exists()
    assert "passed" not in note(root, BRANCH_A)


def test_a_check_makes_no_tree_past_the_cap(tmp_path: Path, capsys: Out) -> None:
    root = project(tmp_path)
    assert run(root, "open", ALPHA, "--concurrency", "1") == 0
    build(root, BRANCH_A, {"src/b.py": "B = 1\n"})
    git(root, "worktree", "remove", "--force", str(drain.tree_of(root, BRANCH_A)))
    assert run(root, "open", GAMMA, "--concurrency", "1") == 0
    capsys.readouterr()
    assert run(root, "check", ALPHA, "--final", "--concurrency", "1") == drain.UNREAD
    assert "1 tickets are building: the cap" in capsys.readouterr().out
    assert not drain.tree_of(root, BRANCH_A).exists()


def test_update_checks_nothing_when_the_note_holds_no_start(tmp_path: Path, capsys: Out) -> None:
    root = opened(tmp_path)
    path = queue_notes.note_path(root, BRANCH_A)
    queue_notes.write_note(path, {k: v for k, v in note(root, BRANCH_A).items() if k != "start"})
    tree = drain.tree_of(root, BRANCH_A)
    write(tree, "src/b.py", "B = 1\n")
    commit(tree, "feat(core): the change")
    moved(root)
    capsys.readouterr()
    assert run(root, "update", ALPHA) == 0
    assert '"moved": true' in capsys.readouterr().out
    assert note(root, BRANCH_A)["start"]["head"] == git(tree, "rev-parse", "HEAD")
    assert "passed" not in note(root, BRANCH_A)


def test_a_rebase_records_the_new_head_as_the_start_with_no_mark(
    tmp_path: Path, capsys: Out
) -> None:
    root = opened(tmp_path)
    tree = build(root, BRANCH_A, {"src/b.py": "B = 1\n"})
    assert note(root, BRANCH_A)["passed"] is True
    moved(root)
    capsys.readouterr()
    assert run(root, "update", ALPHA) == 0
    held = note(root, BRANCH_A)
    assert held["start"]["head"] == git(tree, "rev-parse", "HEAD")
    assert "passed" not in held
    assert run(root, "land", ALPHA) == drain.UNREAD


def test_a_conflict_records_the_start_of_a_rebase_a_reconcile_is_to_finish(
    tmp_path: Path, capsys: Out
) -> None:
    root = opened(tmp_path)
    tree = build(root, BRANCH_A, {"src/a.py": "A = 2\n"})
    moved(root, "src/a.py", "A = 3\n")
    capsys.readouterr()
    assert run(root, "update", ALPHA) == 2
    held = note(root, BRANCH_A)
    assert held["start"]["head"] == git(tree, "rev-parse", "HEAD")
    assert held["start"]["rebase"]["base"] == "main"
    assert len(held["start"]["rebase"]["commits"]) == 1
    assert "passed" not in held


def test_a_start_that_cannot_be_read_after_a_rebase_leaves_the_note_with_none(
    tmp_path: Path, capsys: Out
) -> None:
    root = opened(tmp_path)
    build(root, BRANCH_A, {"src/b.py": "B = 1\n"})
    moved(root)
    capsys.readouterr()
    with mock.patch.object(publish_check, "start_of", side_effect=UNREADABLE):
        assert run(root, "update", ALPHA) == 0
    seen = capsys.readouterr()
    assert '"moved": true' in seen.out
    assert "the start of `feature/1-alpha` cannot be read" in seen.err
    assert "start" not in note(root, BRANCH_A)


def executable(directory: Path, name: str, text: str) -> None:
    directory.mkdir(exist_ok=True)
    path = directory / name
    path.write_text(f"#!/bin/sh\n{text}\n", encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IXUSR)


def test_the_forge_is_asked_in_the_repository_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = project(tmp_path)
    where = tmp_path / "where"
    executable(tmp_path / "bin", "gh", f'pwd > "{where}"\necho "[]"')
    monkeypatch.setenv("PATH", f"{tmp_path / 'bin'}:/usr/bin:/bin")
    monkeypatch.chdir(tmp_path)
    asked = drain.forge_gh(drain.Drain(root, preflight.run_gh))
    assert asked(["pr", "list"]) == "[]\n"
    assert where.read_text(encoding="utf-8").strip() == str(root)


def test_the_forge_answers_nothing_when_gh_fails_or_is_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = project(tmp_path)
    asked = drain.forge_gh(drain.Drain(root, preflight.run_gh))
    executable(tmp_path / "bin", "gh", "exit 1")
    monkeypatch.setenv("PATH", str(tmp_path / "bin"))
    assert asked(["pr", "list"]) is None
    monkeypatch.setenv("PATH", str(tmp_path / "empty"))
    assert asked(["pr", "list"]) is None


def test_the_forge_is_the_drains_own_runner_unless_it_is_the_real_gh(tmp_path: Path) -> None:
    root = project(tmp_path)
    assert drain.forge_gh(offline(root))(["pr", "list"]) is None

    def fake(_args: object) -> str:
        return "[]"

    assert drain.forge_gh(drain.Drain(root, fake)) is fake
