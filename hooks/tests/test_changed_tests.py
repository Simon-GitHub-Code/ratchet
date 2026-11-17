import os
import shutil
import stat
from pathlib import Path

import pytest

import changed_tests
import suites
from tests.repos import branch, commit, git, repository, write

Out = pytest.CaptureFixture[str]
GATES = (
    "| Question | Tool | Tier | Threshold source | Entry point |\n|---|---|---|---|---|\n"
    "| unit | a tool | 1 | | `just test` |\n"
    "| end to end | a tool | 2 | | `just e2e` |\n"
)
UNIT = "| unit | `just test` | `tests/unit/**` | `just test-files` |\n"
E2E = "| e2e | `just e2e` | `tests/e2e/**` | `just e2e` |\n"
NEITHER = (
    "the suites row 'a' has neither a `just <recipe>` that takes files and a `none:"
    " <reason>`: say exactly one"
)
BOTH = NEITHER.replace("neither", "both")
HEAD = "| Suite | Gate | Files | Run files |\n| --- | --- | --- | --- |\n"


def binding(*rows: str) -> str:
    return f"# Binding\n\n## Gates\n\n{GATES}\n## Tests\n\n{HEAD}{''.join(rows)}\n## Speed\n"


@pytest.fixture
def just(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    log = tmp_path / "just.log"
    stub = bin_dir / "just"
    stub.write_text(
        f'#!/bin/sh\necho "$@" >> "{log}"\n[ -n "$SIGNAL" ] && kill -"$SIGNAL" $$\n'
        'exit "${STATUS:-0}"\n',
        encoding="utf-8",
    )
    stub.chmod(stub.stat().st_mode | stat.S_IXUSR)
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")
    monkeypatch.delenv("STATUS", raising=False)
    monkeypatch.delenv("SIGNAL", raising=False)
    return log


def calls(log: Path) -> list[str]:
    return log.read_text(encoding="utf-8").splitlines() if log.exists() else []


def project(tmp_path: Path, *rows: str) -> Path:
    root = tmp_path / "repo"
    root.mkdir()
    repository(
        root,
        {
            "docs/process/binding.md": binding(*rows),
            "tests/unit/a_test.txt": "a\n",
            "tests/e2e/b_test.txt": "b\n",
            "src/code.txt": "c\n",
        },
    )
    branch(root)
    return root


def run(root: Path, *args: str) -> int:
    return changed_tests.main(["changed_tests.py", "--root", str(root), *args])


def test_a_change_that_touches_no_test_file_is_green_and_says_so(
    tmp_path: Path, just: Path, capsys: Out
) -> None:
    root = project(tmp_path, UNIT, E2E)
    write(root, "src/code.txt", "more\n")
    commit(root, "feat(core): code")
    assert run(root) == 0
    out = capsys.readouterr().out
    assert "green: no test file of the suites in scope (e2e) changed" in out
    assert "changed_tests: green: 0 of 1 suites had changed test files" in out
    assert calls(just) == []


def test_a_change_to_one_suite_runs_its_files_through_its_recipe(
    tmp_path: Path, just: Path, capsys: Out
) -> None:
    root = project(tmp_path, UNIT)
    write(root, "tests/unit/a_test.txt", "edited\n")
    write(root, "tests/unit/new_test.txt", "new\n")
    commit(root, "feat(core): tests")
    assert run(root) == 0
    assert calls(just) == ["test-files tests/unit/a_test.txt tests/unit/new_test.txt"]
    out = capsys.readouterr().out
    assert "unit: running 2 changed files through `just test-files`" in out
    assert out.endswith("changed_tests: green: 1 of 1 suites had changed test files\n")


def test_two_suites_run_their_own_files_and_only_the_later_tier_one_by_default(
    tmp_path: Path, just: Path
) -> None:
    root = project(tmp_path, UNIT, E2E)
    write(root, "tests/unit/a_test.txt", "edited\n")
    write(root, "tests/e2e/b_test.txt", "edited\n")
    commit(root, "feat(core): tests")
    assert run(root) == 0
    assert calls(just) == ["e2e tests/e2e/b_test.txt"]


def test_all_runs_every_suite_s_changed_files(tmp_path: Path, just: Path) -> None:
    root = project(tmp_path, UNIT, E2E)
    write(root, "tests/unit/a_test.txt", "edited\n")
    write(root, "tests/e2e/b_test.txt", "edited\n")
    commit(root, "feat(core): tests")
    assert run(root, "all") == 0
    assert calls(just) == ["test-files tests/unit/a_test.txt", "e2e tests/e2e/b_test.txt"]


def test_a_deleted_file_is_not_run(tmp_path: Path, just: Path, capsys: Out) -> None:
    root = project(tmp_path, UNIT)
    git(root, "rm", "-q", "tests/unit/a_test.txt")
    commit(root, "feat(core): drop a test")
    write(root, "tests/unit/gone_test.txt", "x\n")
    (root / "tests/unit/gone_test.txt").unlink()
    assert run(root) == 0
    assert calls(just) == []
    assert "green: no test file of the suites in scope (unit) changed" in capsys.readouterr().out


def test_a_tracked_file_deleted_in_the_work_tree_is_not_run(tmp_path: Path, just: Path) -> None:
    root = project(tmp_path, UNIT)
    (root / "tests/unit/a_test.txt").unlink()
    assert run(root) == 0
    assert calls(just) == []


def test_an_untracked_new_file_and_an_unstaged_edit_are_run(tmp_path: Path, just: Path) -> None:
    root = project(tmp_path, UNIT)
    write(root, "tests/unit/fresh_test.txt", "fresh\n")
    write(root, "tests/unit/a_test.txt", "edited\n")
    assert run(root) == 0
    assert calls(just) == ["test-files tests/unit/a_test.txt tests/unit/fresh_test.txt"]


def test_a_renamed_file_runs_under_its_new_name(tmp_path: Path, just: Path) -> None:
    root = project(tmp_path, UNIT)
    git(root, "mv", "tests/unit/a_test.txt", "tests/unit/renamed_test.txt")
    assert run(root) == 0
    assert calls(just) == ["test-files tests/unit/renamed_test.txt"]


def test_an_ignored_file_is_not_a_change(tmp_path: Path, just: Path) -> None:
    root = project(tmp_path, UNIT)
    write(root, ".gitignore", "tests/unit/ignored_test.txt\n")
    commit(root, "feat(core): ignore")
    write(root, "tests/unit/ignored_test.txt", "x\n")
    assert run(root) == 0
    assert calls(just) == []


def test_a_file_that_two_rows_match_is_red_and_nothing_runs(
    tmp_path: Path, just: Path, capsys: Out
) -> None:
    both = "| wide | `just e2e` | `tests/**` | `just e2e` |\n"
    root = project(tmp_path, UNIT, both)
    write(root, "tests/unit/a_test.txt", "edited\n")
    write(root, "tests/other_test.txt", "other\n")
    assert run(root) == 1
    out = capsys.readouterr().out
    assert (
        "changed_tests: red: docs/process/binding.md: tests/unit/a_test.txt matches the suites"
        " 'unit', 'wide'; a test file belongs to one suite"
    ) in out
    assert "other_test" not in out
    assert calls(just) == []


@pytest.mark.usefixtures("just")
def test_a_failing_suite_run_is_red(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: Out
) -> None:
    root = project(tmp_path, UNIT)
    write(root, "tests/unit/a_test.txt", "edited\n")
    monkeypatch.setenv("STATUS", "3")
    assert run(root) == 3
    assert "changed_tests: red: 1 of 1 suites had changed test files" in capsys.readouterr().out


@pytest.mark.usefixtures("just")
def test_a_suite_run_killed_by_a_signal_is_red_and_names_the_signal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: Out
) -> None:
    root = project(tmp_path, UNIT, E2E)
    write(root, "tests/e2e/c_test.txt", "new\n")
    monkeypatch.setenv("SIGNAL", "9")
    assert run(root, "all") == 137
    out = capsys.readouterr().out
    assert "changed_tests: e2e: killed by SIGKILL" in out
    assert "changed_tests: red: 1 of 2 suites had changed test files" in out


def test_a_later_suite_that_passes_keeps_the_first_failure(
    tmp_path: Path, just: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = project(tmp_path, UNIT, E2E)
    write(root, "tests/unit/a_test.txt", "edited\n")
    write(root, "tests/e2e/c_test.txt", "new\n")
    monkeypatch.setenv("STATUS", "3")
    assert run(root, "all") == 3
    assert len(calls(just)) == 2


def test_a_suite_that_cannot_run_by_file_says_why_and_is_not_run(
    tmp_path: Path, just: Path, capsys: Out
) -> None:
    unrunnable = "| unit | `just test` | `tests/unit/**` | `none: it boots the whole service` |\n"
    root = project(tmp_path, unrunnable)
    write(root, "tests/unit/a_test.txt", "edited\n")
    assert run(root) == 0
    assert calls(just) == []
    out = capsys.readouterr().out
    assert (
        "changed_tests: unit: 1 changed file not run, since the suite cannot run by file: it"
        " boots the whole service; `just test` runs it whole"
    ) in out


def test_a_recipe_that_cannot_start_is_127(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: Out
) -> None:
    root = project(tmp_path, UNIT)
    write(root, "tests/unit/a_test.txt", "edited\n")
    only_git = tmp_path / "only-git"
    only_git.mkdir()
    (only_git / "git").symlink_to(shutil.which("git") or "git")
    monkeypatch.setenv("PATH", str(only_git))
    assert run(root) == 127
    assert "changed_tests: unit: cannot run just:" in capsys.readouterr().out


@pytest.mark.usefixtures("just")
def test_a_binding_with_no_suites_table_is_red(tmp_path: Path, capsys: Out) -> None:
    root = project(tmp_path, UNIT)
    write(root, "docs/process/binding.md", "# Binding\n\n## Tests\n\nText.\n")
    assert run(root) == 1
    assert "changed_tests: red: docs/process/binding.md: '## Tests' lacks its suites table" in (
        capsys.readouterr().out
    )


@pytest.mark.usefixtures("just")
def test_a_repository_with_no_binding_is_red(tmp_path: Path, capsys: Out) -> None:
    root = tmp_path / "bare"
    root.mkdir()
    repository(root)
    assert run(root) == 1
    assert "has no '## Tests' section" in capsys.readouterr().out


def test_a_tree_with_no_commit_to_compare_with_is_green(
    tmp_path: Path, just: Path, capsys: Out
) -> None:
    root = tmp_path / "single"
    root.mkdir()
    repository(root, {"docs/process/binding.md": binding(UNIT), "tests/unit/a_test.txt": "a\n"})
    assert run(root) == 0
    assert "there is no commit to compare the tree with" in capsys.readouterr().out
    assert calls(just) == []


def test_the_base_can_be_named(tmp_path: Path, just: Path) -> None:
    root = project(tmp_path, UNIT)
    write(root, "tests/unit/a_test.txt", "edited\n")
    commit(root, "feat(core): edit")
    first = git(root, "rev-parse", "HEAD")
    write(root, "tests/unit/other_test.txt", "other\n")
    commit(root, "feat(core): another")
    assert run(root, "--base", first) == 0
    assert calls(just) == ["test-files tests/unit/other_test.txt"]


@pytest.mark.usefixtures("just")
def test_a_suites_table_only_of_none_rows_runs_nothing(tmp_path: Path, capsys: Out) -> None:
    root = project(tmp_path, "| none: the project has no tests yet | none | none | none |\n")
    write(root, "tests/unit/a_test.txt", "edited\n")
    assert run(root) == 0
    assert "no test file of the suites in scope (none) changed" in capsys.readouterr().out


def parse(*rows: str) -> tuple[list[suites.Suite] | None, list[str]]:
    return suites.parse(binding(*rows))


def test_a_table_of_suites_reads_each_row() -> None:
    found, problems = parse(
        UNIT, "| e2e | `just e2e` | `a/**`, `b/*.x` | `none: needs a server` |\n"
    )
    assert problems == []
    assert found == [
        suites.Suite("unit", "just test", ("tests/unit/**",), "just test-files", ""),
        suites.Suite("e2e", "just e2e", ("a/**", "b/*.x"), "", "needs a server"),
    ]


@pytest.mark.parametrize(
    ("row", "problem"),
    [
        ("| <name> | <`just x`> | <`globs`> | <`just y`> |\n", suites.PLACEHOLDER),
        ("| none: x | none | none | none |\n", "a suites row says none with no reason: say why"),
        (
            "| 9 | `just a` | `x/**` | `just b` |\n",
            "the suites row '9' needs a name, one `just <recipe>` gate and backticked globs",
        ),
        (
            "| a | `just a` `just c` | `x/**` | `just b` |\n",
            "the suites row 'a' needs a name, one `just <recipe>` gate and backticked globs",
        ),
        (
            "| a | `make a` | `x/**` | `just b` |\n",
            "the suites row 'a' needs a name, one `just <recipe>` gate and backticked globs",
        ),
        (
            "| a | `just a` | x/** | `just b` |\n",
            "the suites row 'a' needs a name, one `just <recipe>` gate and backticked globs",
        ),
        (
            "| a | `just a` | `x/**` | |\n",
            NEITHER,
        ),
        (
            "| a | `just a` | `x/**` | none: run `just b` whole |\n",
            BOTH,
        ),
        (
            "| a | `just a` | `x/**` | none: no |\n",
            "the suites row 'a' says none with no reason: say why",
        ),
        (
            "| a | `just a` | `x/**` | `make b` |\n",
            NEITHER,
        ),
    ],
)
def test_a_row_the_table_cannot_use_is_a_problem(row: str, problem: str) -> None:
    found, problems = parse(row)
    assert found is None
    assert problems == [problem]


def test_two_rows_with_one_name_are_a_problem() -> None:
    found, problems = parse(UNIT, UNIT)
    assert found is None
    assert problems == ["two suites rows have one name"]


def test_a_binding_with_no_tests_section_or_no_table_says_which() -> None:
    assert suites.parse(None) == (None, ["has no '## Tests' section"])
    assert suites.parse("## Tests\n\nText\n") == (
        None,
        ["'## Tests' lacks its suites table with the columns Suite | Gate | Files | Run files"],
    )


def test_the_table_s_rows_are_split_from_the_rest_of_the_binding() -> None:
    rows, rest = suites.split(binding(UNIT, E2E))
    assert rows == {
        "unit": "unit|`just test`|`tests/unit/**`|`just test-files`",
        "e2e": "e2e|`just e2e`|`tests/e2e/**`|`just e2e`",
    }
    assert "tests/unit" not in rest
    assert "## Speed" in rest
    assert suites.split("## Tests\n\nText\n") == ({}, "## Tests\n\nText\n")


def test_an_owner_is_each_suite_whose_globs_match() -> None:
    found, _ = parse(UNIT, E2E)
    assert found is not None
    assert [s.name for s in suites.owners(found, "tests/e2e/x.py")] == ["e2e"]
    assert suites.owners(found, "src/x.py") == []
