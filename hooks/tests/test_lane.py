"""``lane.py``: only a change of records and pages takes the light lane; any doubt runs it all."""

import subprocess
from pathlib import Path

import pytest

import lane
from tests.repos import branch, commit, git, repository, write

BASE = {
    "README.md": "# A\n",
    "docs/adr/0001-x.md": "# 1\n",
    "docs/model/page.md": "# page\n",
    "scripts/lane.py": "x = 1\n",
    "justfile": "default:\n",
    ".github/workflows/ci.yml": "name: ci\n",
    "skills/feature/SKILL.md": "# skill\n",
    "src/app.py": "y = 1\n",
}
CLASSIFIER_PATHS = ["scripts/lane.py", "justfile", ".github/workflows/ci.yml"]


def change(root: Path, edits: dict[str, str | None]) -> None:
    for path, text in edits.items():
        if text is None:
            (root / path).unlink()
        else:
            write(root, path, text)
    commit(root, "docs: the change")


def lane_of_change(
    root: Path, edits: dict[str, str | None], monkeypatch: pytest.MonkeyPatch
) -> str:
    repository(root, dict(BASE))
    branch(root)
    change(root, edits)
    monkeypatch.chdir(root)
    return lane.lane_for("main")


@pytest.mark.parametrize(
    "edits",
    [
        {"docs/adr/0002-new.md": "# 2\n"},
        {"docs/adr/0001-x.md": "# 1 again\n"},
        {"docs/model/page.md": "# page again\n"},
        {"docs/deep/er/page.md": "# deeper\n"},
        {"README.md": "# B\n"},
        {"CHANGELOG.md": "# log\n"},
        {"docs/adr/0001-x.md": None},
        {"docs/adr/0002-new.md": "# 2\n", "README.md": "# B\n", "CHANGELOG.md": "# log\n"},
    ],
)
def test_a_change_of_records_and_pages_alone_takes_the_light_lane(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, edits: dict[str, str | None]
) -> None:
    assert lane_of_change(tmp_path, edits, monkeypatch) == "pages"


@pytest.mark.parametrize(
    "edits",
    [
        {"src/app.py": "y = 2\n"},
        {"skills/feature/SKILL.md": "# skill again\n"},
        {"skills/adopt/templates/docs/adr/0000-x.md": "# t\n"},
        {"docs/notes.txt": "plain\n"},
        {"docs/image.png": "png\n"},
        {"src/README.md": "# nested\n"},
        {"readme.md": "# lower\n"},
        {".github/PULL_REQUEST_TEMPLATE.md": "# t\n"},
        {"evals/x/docs/page.md": "# eval\n"},
        {"docs.md": "# not under docs\n"},
        {"docs/adr/0002-new.md": "# 2\n", "src/app.py": "y = 2\n"},
        {"README.md": "# B\n", "skills/feature/SKILL.md": "# skill again\n"},
        {"src/app.py": None},
    ],
)
def test_a_change_that_touches_anything_else_runs_the_full_suite(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, edits: dict[str, str | None]
) -> None:
    assert lane_of_change(tmp_path, edits, monkeypatch) == "full"


@pytest.mark.parametrize("path", CLASSIFIER_PATHS)
def test_a_change_to_the_classifier_the_justfile_or_the_workflows_is_never_light(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, path: str
) -> None:
    alone = lane_of_change(tmp_path / "alone", {path: "changed\n"}, monkeypatch)
    mixed = lane_of_change(
        tmp_path / "mixed", {path: "changed\n", "docs/adr/0002-new.md": "# 2\n"}, monkeypatch
    )
    assert (alone, mixed) == ("full", "full")


@pytest.mark.parametrize("path", CLASSIFIER_PATHS)
def test_no_glob_of_the_list_matches_the_classifier_the_justfile_or_the_workflows(
    path: str,
) -> None:
    assert lane.lane_of([path]) == "full"
    assert not any(rule.fullmatch(path) for rule in lane.ALLOWED)


def test_a_rename_counts_by_both_paths(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    moves = {
        "out": ("docs/model/page.md", "scripts/page.py"),
        "in": ("src/app.py", "docs/model/app.md"),
        "within": ("docs/model/page.md", "docs/adr/0002-page.md"),
    }
    found = []
    for name, (old, new) in moves.items():
        root = repository(tmp_path / name, dict(BASE))
        branch(root)
        git(root, "mv", old, new)
        commit(root, "docs: move")
        monkeypatch.chdir(root)
        found.append(lane.lane_for("main"))
    assert found == ["full", "full", "pages"]


def test_the_diff_is_against_the_merge_base_not_the_tip_of_the_base(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repository(tmp_path, dict(BASE))
    branch(tmp_path)
    change(tmp_path, {"docs/adr/0002-new.md": "# 2\n"})
    git(tmp_path, "checkout", "-q", "main")
    change(tmp_path, {"src/app.py": "y = 2\n"})
    git(tmp_path, "checkout", "-q", "change")
    monkeypatch.chdir(tmp_path)
    assert lane.lane_for("main") == "pages"


def test_an_empty_diff_runs_the_full_suite(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    repository(tmp_path, dict(BASE))
    branch(tmp_path)
    monkeypatch.chdir(tmp_path)
    assert lane.lane_for("main") == "full"


def test_no_base_runs_the_full_suite(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    repository(tmp_path, dict(BASE))
    branch(tmp_path)
    change(tmp_path, {"README.md": "# B\n"})
    monkeypatch.chdir(tmp_path)
    assert lane.lane_for("") == "full"


def test_a_base_git_cannot_find_runs_the_full_suite(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repository(tmp_path, dict(BASE))
    branch(tmp_path)
    change(tmp_path, {"README.md": "# B\n"})
    monkeypatch.chdir(tmp_path)
    assert lane.lane_for("no-such-ref") == "full"


def test_a_git_that_cannot_start_runs_the_full_suite(monkeypatch: pytest.MonkeyPatch) -> None:
    def missing(*_args: object, **_kwargs: object) -> None:
        raise FileNotFoundError

    monkeypatch.setattr(subprocess, "run", missing)
    assert lane.lane_for("main") == "full"


def test_a_name_that_is_not_text_runs_the_full_suite(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repository(tmp_path, dict(BASE))
    branch(tmp_path)
    odd = tmp_path / b"docs/\xff.md".decode("utf-8", "surrogateescape")
    odd.write_text("# odd\n", encoding="utf-8")
    commit(tmp_path, "docs: odd")
    monkeypatch.chdir(tmp_path)
    assert lane.lane_for("main") == "full"


def test_a_listing_that_does_not_end_in_a_separator_runs_the_full_suite(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    answers = iter([b"abc\n", b"docs/a.md"])
    monkeypatch.setattr(lane, "git", lambda *_args: next(answers))
    assert lane.changed("main") is None


def test_the_lane_is_printed_for_the_workflow(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    repository(tmp_path, dict(BASE))
    branch(tmp_path)
    change(tmp_path, {"README.md": "# B\n"})
    monkeypatch.chdir(tmp_path)
    assert lane.main(["lane.py", "main"]) == 0
    assert lane.main(["lane.py"]) == 0
    assert capsys.readouterr().out == "pages\nfull\n"
