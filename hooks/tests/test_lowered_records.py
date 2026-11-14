"""A ``Relaxes:`` record excuses a loosening only in the change that adds it (ADR 0047)."""

from pathlib import Path

import pytest

import lowered
from tests.repos import branch, commit, git, repository, write

THRESHOLDS = "quality/thresholds.toml"
KEY = "gate.floor"
Adr = tuple[str, str]


def table(value: str | None) -> str:
    return "[gate]\nother = 1\n" + ("" if value is None else f"floor = {value}\n")


def record(old: str, new: str, name: str = "0900-a-step.md") -> Adr:
    return f"docs/adr/{name}", f"# A step\n\nRelaxes: {KEY} from {old} to {new}\n"


@pytest.fixture
def root(tmp_path: Path) -> Path:
    return repository(tmp_path / "repo", {THRESHOLDS: table("2"), "docs/adr/0001-x.md": "# X\n"})


def change(root: Path, name: str, value: str | None, *adrs: Adr, same: bool = False) -> int:
    """One commit on a new branch from ``main``: the value (unless ``same``), the records."""
    git(root, "checkout", "-q", "main")
    branch(root, name)
    if not same:
        write(root, THRESHOLDS, table(value))
    for path, text in adrs:
        write(root, path, text)
    commit(root, f"feat: {name}")
    return judge(root)


def land(root: Path, name: str) -> None:
    git(root, "checkout", "-q", "main")
    git(root, "merge", "-q", "--no-ff", "-m", f"merge {name}", name)


def judge(root: Path) -> int:
    return lowered.main(["lowered.py", "--root", str(root)])


def merge_result(root: Path, name: str) -> int:
    """What the gate says of ``main`` merged into ``name``, which is what merge.py judges."""
    git(root, "checkout", "-q", name)
    git(root, "merge", "-q", "--no-ff", "-m", "merge main", "main")
    return judge(root)


def test_a_record_does_not_outlive_the_change_that_added_it(root: Path) -> None:
    assert change(root, "a", "1", record("2", "1")) == 0
    land(root, "a")
    assert change(root, "b", "2") == 0
    land(root, "b")
    assert change(root, "c", "1") == 1, "A's record was spent on A's change"


def test_a_record_landed_alone_does_not_let_another_branchs_loosening_land(root: Path) -> None:
    assert change(root, "y", "1") == 1, "red on its own branch"
    assert change(root, "x", None, record("2", "1"), same=True) == 0, "nothing to excuse"
    land(root, "x")
    assert merge_result(root, "y") == 1, "and red on the merge result"


def test_a_record_for_a_step_another_branch_makes_is_red_when_that_branch_lands_later(
    root: Path,
) -> None:
    assert change(root, "x", None, record("2", "1"), same=True) == 0
    land(root, "x")
    assert change(root, "y", "1") == 1, "x's record is in the base"


def test_a_record_must_name_the_base_and_the_head_value(root: Path) -> None:
    wrong = (record("3", "1"), record("2", "0", "0901-b.md"))
    assert change(root, "z", "1", *wrong) == 1


def test_the_change_that_adds_the_record_may_loosen(root: Path) -> None:
    assert change(root, "z", "1", record("2", "1")) == 0


def test_a_removed_key_written_back_looser_needs_its_own_record(root: Path) -> None:
    assert change(root, "drop", None, record("2", "removed")) == 0
    land(root, "drop")
    assert change(root, "readd", "1") == 1, "the key last held 2"


def test_a_removed_key_written_back_at_or_above_its_last_value_is_green(root: Path) -> None:
    assert change(root, "drop", None, record("2", "removed")) == 0
    land(root, "drop")
    assert change(root, "readd", "3") == 0


def test_a_removed_key_written_back_looser_with_a_record_is_green(root: Path) -> None:
    assert change(root, "drop", None, record("2", "removed")) == 0
    land(root, "drop")
    assert change(root, "readd", "1", record("2", "1", "0902-c.md")) == 0


def test_a_key_the_file_never_held_is_new(root: Path) -> None:
    branch(root, "new")
    write(root, THRESHOLDS, table("2") + "brand = 0\n")
    commit(root, "feat: new")
    assert judge(root) == 0


def test_a_key_is_forgotten_only_through_named_steps(root: Path) -> None:
    assert change(root, "flag", "true", record("2", "true", "0903-d.md")) == 0
    land(root, "flag")
    assert change(root, "number", "1", record("true", "1", "0904-e.md")) == 0


def test_the_retype_round_trip_without_the_first_record_is_red(root: Path) -> None:
    assert change(root, "flag", "true") == 1


def test_the_retype_round_trip_without_the_second_record_is_red(root: Path) -> None:
    assert change(root, "flag", "true", record("2", "true", "0903-d.md")) == 0
    land(root, "flag")
    assert change(root, "number", "1") == 1


def test_both_steps_in_one_change_need_the_record_for_the_whole_move(root: Path) -> None:
    both = (record("2", "true", "0903-d.md"), record("true", "1", "0904-e.md"))
    assert change(root, "both", "1", *both) == 1, "the change moves 2 to 1, which neither names"


def test_a_version_of_the_file_that_does_not_parse_is_skipped_in_the_history(root: Path) -> None:
    assert change(root, "drop", None, record("2", "removed")) == 0
    land(root, "drop")
    git(root, "checkout", "-q", "main")
    write(root, THRESHOLDS, "[gate\n")
    commit(root, "chore: broken")
    write(root, THRESHOLDS, table(None))
    commit(root, "chore: mended")
    assert change(root, "readd", "1") == 1, "the broken version is passed over"
