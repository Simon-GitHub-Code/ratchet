from pathlib import Path

from merge_ready import Reason, report
from queue_notes import caused, causes_of, code_of, mechanical
from tests.repos import commit, git, repository, write


def rows(reasons: list[Reason]) -> list[str]:
    return [line for line in report(reasons, "a" * 40).splitlines() if line.startswith("- ")]


def test_the_judges_pull_request_rows_read_as_their_codes() -> None:
    behind = Reason("behind", "`main` moved: update the branch: run `just update`")
    gates = Reason("gates", "ci is red: see the run")
    owner = Reason("guard", "a guard path changed: `quality/thresholds.toml`")
    assert [code_of(line) for line in rows([behind, gates, owner])] == ["behind", "gates", "guard"]
    assert [code_of(str(reason)) for reason in (behind, gates, owner)] == [
        "behind",
        "gates",
        "guard",
    ]


def test_only_mechanical_rows_leave_a_ticket_to_the_drain() -> None:
    behind, gates = Reason("behind", "main moved: x"), Reason("gates", "ci: red")
    owner = Reason("guard", "a guard path changed")
    assert mechanical(rows([behind, gates]))
    assert mechanical([str(behind), str(gates)])
    assert not mechanical(rows([owner]))
    assert not mechanical(rows([behind, owner]))
    assert not mechanical([])


def test_a_blocked_run_is_released_only_when_a_path_it_named_changes_on_the_default_branch(
    tmp_path: Path,
) -> None:
    root = repository(tmp_path, {"guards/manifest.toml": "a\n", "other.txt": "a\n"})
    note: dict[str, object] = {
        "causes": ["guards/manifest.toml"],
        "base": git(root, "rev-parse", "HEAD"),
    }
    assert not caused(root, note)
    write(root, "other.txt", "b\n")
    commit(root, "chore(core): an unrelated change")
    assert not caused(root, note)
    write(root, "guards/manifest.toml", "b\n")
    commit(root, "chore(core): the guard edit lands")
    assert caused(root, note)


def test_a_note_with_no_default_branch_commit_is_released_by_no_landed_change(
    tmp_path: Path,
) -> None:
    root = repository(tmp_path, {"guards/manifest.toml": "a\n"})
    write(root, "guards/manifest.toml", "b\n")
    commit(root, "chore(core): the guard edit lands")
    assert not caused(root, {"causes": ["guards/manifest.toml"]})


def test_the_paths_a_blocked_run_names_as_its_cause_are_its_guard_edits_and_its_disputes() -> None:
    result: dict[str, object] = {
        "guardEdits": [{"path": "guards/manifest.toml"}, {"path": "justfile"}, {"note": "x"}],
        "disputes": [{"path": "justfile"}, {"path": "docs/disputes/a.md"}],
    }
    assert causes_of(result) == ["guards/manifest.toml", "justfile", "docs/disputes/a.md"]
    assert causes_of({}) == []
