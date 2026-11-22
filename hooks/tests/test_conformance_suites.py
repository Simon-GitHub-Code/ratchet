import shutil
from pathlib import Path

import pytest

import conformance
import suites
from tests.repos import git
from tests.test_conformance import (
    BINDING,
    pristine,
    red,
    remove,
    replace,
    write,
)

NONE_ROW = conformance.SELF_TEST_SUITES
UNIT = "| unit | `just test` | `tests/unit/**` | `just test-files` |"
WIDE = "| wide | `just test` | `tests/**` | `just test-files` |"
E2E = "| e2e | `just e2e` | `tests/e2e/**` | `just e2e` |"
CI = ".github/workflows/ci.yml"
BUDGET = (
    '      - run: python3 "$RATCHET_PLUGIN/scripts/budget.py" "${{ matrix.gate }}" --tier tier1'
)
HEADER = "| Suite | Gate | Files | Run files |"


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    shutil.copytree(pristine(), tmp_path / "repo", symlinks=True)
    return tmp_path / "repo"


def suites_row(root: Path, *rows: str) -> None:
    replace(BINDING, NONE_ROW, "\n".join(rows))(root)


def test_a_binding_with_one_suite_and_the_recipe_that_runs_its_files_is_green(repo: Path) -> None:
    suites_row(repo, UNIT)
    assert red(repo) == []


def test_a_tests_section_without_the_suites_table_gets_the_templates_to_start_from(
    repo: Path,
) -> None:
    replace(BINDING, HEADER, "| Name | Gate | Files | Run |")(repo)
    [found] = [f for f in conformance.findings(repo) if f.rule == "changed_tests"]
    assert found.problem == (
        "'## Tests' has no suites table (Suite | Gate | Files | Run files). Write it in one fill"
        " with two other edits, since a Gates row bound before the table exists turns tier 1 red"
        " (ADR 0073): the table, one row per suite naming the gate that runs it whole, the globs"
        " of its test files and the recipe that runs given files of it, or `none: <reason>`; the"
        " Gates row 'changed tests run', bound to `just tests-changed`; and `tests-changed` in the"
        " justfile's `tier1_gates`."
    )
    assert isinstance(found.fix, conformance.Agent)
    assert found.fix.start[0] == HEADER
    assert found.stops == "changed tests run"


def test_the_template_row_left_as_it_is_is_red(repo: Path) -> None:
    template = next(
        line
        for line in conformance.template_binding().splitlines()
        if line.startswith("| <suite name> |")
    )
    suites_row(repo, template)
    assert red(repo) == [f"{BINDING}: {suites.PLACEHOLDER}"]


def test_a_none_row_with_no_reason_is_red(repo: Path) -> None:
    suites_row(repo, "| none: x | none | none | none |")
    assert red(repo) == [f"{BINDING}: a suites row says none with no reason: say why"]


def test_a_row_with_neither_a_way_to_run_files_nor_a_reason_is_red(repo: Path) -> None:
    suites_row(repo, UNIT.replace("`just test-files`", ""))
    assert red(repo) == [
        (
            f"{BINDING}: the suites row 'unit' has neither a `just <recipe>` that takes files and a"
            " `none: <reason>`: say exactly one"
        )
    ]


def test_a_row_naming_a_gate_the_gates_table_does_not_hold_is_red(repo: Path) -> None:
    suites_row(repo, UNIT.replace("`just test`", "`just nowhere`"))
    assert red(repo) == [
        (
            f"{BINDING}: the suites row 'unit' names `just nowhere`, which no row of the Gates"
            " table holds as its entry point"
        )
    ]


def test_a_row_that_says_why_it_cannot_run_by_file_is_green(repo: Path) -> None:
    suites_row(repo, UNIT.replace("`just test-files`", "none: it boots the whole service"))
    assert red(repo) == []


def test_a_recipe_the_justfile_does_not_define_is_red(repo: Path) -> None:
    suites_row(repo, UNIT.replace("test-files", "no-such-recipe"))
    assert red(repo) == [
        (
            "justfile: the suites row 'unit' runs given files with the recipe 'no-such-recipe',"
            " which the justfile does not define"
        )
    ]


def test_a_recipe_that_takes_no_files_is_red_with_its_definition_to_start_from(
    repo: Path,
) -> None:
    suites_row(repo, UNIT)
    replace("justfile", "test-files *files:", "test-files:")(repo)
    [found] = [f for f in conformance.findings(repo) if f.rule == "changed_tests"]
    assert found.problem == (
        "the recipe 'test-files' runs given files of the suite 'unit' and takes none; give it a"
        " variadic parameter, such as `*files`, and run only the files it is given, in the"
        " environment and with the build the whole suite has (ADR 0073)"
    )
    assert found.path == "justfile"
    assert isinstance(found.fix, conformance.Agent)
    assert "test-files:" in found.fix.start


def test_a_repository_with_no_justfile_has_no_recipe_to_hold_a_suite_to(repo: Path) -> None:
    suites_row(repo, UNIT)
    remove("justfile")(repo)
    assert not any(line.startswith("justfile: the suites") for line in red(repo))


def test_a_repository_with_no_binding_or_no_tests_section_has_no_suites_to_hold(
    repo: Path,
) -> None:
    replace(BINDING, "## Tests", "## Elsewhere")(repo)
    assert not any("suites" in line for line in red(repo))
    remove(BINDING)(repo)
    assert not any("suites" in line for line in red(repo))


def test_a_file_that_two_rows_match_is_red_naming_both_rows(repo: Path) -> None:
    suites_row(repo, UNIT, WIDE)
    write("tests/unit/a_test.txt", "a\n")(repo)
    write("tests/unit/b_test.txt", "b\n")(repo)
    write("tests/unit/c_test.txt", "c\n")(repo)
    write("tests/unit/d_test.txt", "d\n")(repo)
    git(repo, "init", "-q")
    git(repo, "add", "-A")
    [found] = [f for f in conformance.findings(repo) if f.rule == "changed_tests"]
    assert found.problem == (
        "tests/unit/a_test.txt, tests/unit/b_test.txt, tests/unit/c_test.txt and 1 more match the"
        " suites 'unit', 'wide'; a test file belongs to one suite, so narrow the globs of one row"
    )
    assert found.path == BINDING
    write("tests/unit/d_test.txt", "d\n")(repo)
    replace(BINDING, "`tests/**`", "`tests/other/**`")(repo)
    assert red(repo) == []


def test_two_files_in_two_rows_are_listed_without_a_count(repo: Path) -> None:
    suites_row(repo, UNIT, WIDE)
    write("tests/unit/a_test.txt", "a\n")(repo)
    git(repo, "init", "-q")
    git(repo, "add", "-A")
    [found] = [f for f in conformance.findings(repo) if f.rule == "changed_tests"]
    assert found.problem.startswith("tests/unit/a_test.txt match the suites")


def test_a_justfile_without_the_recipe_gets_the_templates(repo: Path) -> None:
    block = "\n".join(conformance.template_definition("tests-changed"))
    replace("justfile", block, "")(repo)
    found = [f for f in conformance.findings(repo) if "`tests-changed`" in f.problem]
    problem = (
        "has no `tests-changed` recipe, which the method's 'changed tests run' gate runs"
        " (ADR 0073); copy it from the template justfile"
    )
    assert [f.problem for f in found if f.rule == "changed_tests"] == [problem]
    assert all(isinstance(f.fix, conformance.Auto) for f in found if f.rule == "changed_tests")


def test_a_bound_changed_tests_gate_no_tier_runs_is_carried_into_tier_one(repo: Path) -> None:
    replace(
        "justfile",
        "test-counts tests-changed test-stability-changed",
        "test-counts test-stability-changed",
    )(repo)
    found = [f for f in conformance.findings(repo) if "tests-changed" in f.problem]
    assert [f.problem for f in found] == [
        (
            "no tier runs `tests-changed`, which the binding binds to 'changed tests run' at tier"
            " 1, so that gate never runs; run it in `tier1`"
        )
    ]
    assert isinstance(found[0].fix, conformance.Auto)
    written = found[0].fix.want((repo / "justfile").read_text(encoding="utf-8"))
    listed = next(line for line in written.splitlines() if line.startswith("tier1_gates :="))
    assert "tests-changed" in listed.split('"')[1].split()
    assert found[0].stops == "changed tests run"


def test_a_changed_tests_row_that_waits_on_its_trigger_is_not_carried(repo: Path) -> None:
    replace(
        "justfile",
        "test-counts tests-changed test-stability-changed",
        "test-counts test-stability-changed",
    )(repo)
    replace(
        BINDING,
        "| changed tests run | the suites' own runners, through `ratchet` plugin"
        " `scripts/changed_tests.py` |",
        "| changed tests run | later: code:source |",
    )(repo)
    assert not any("tests-changed" in line for line in red(repo))


def test_a_later_tier_suite_with_nothing_set_up_in_the_job_that_runs_it_is_red(
    repo: Path,
) -> None:
    suites_row(repo, UNIT, E2E)
    assert red(repo) == [
        (
            f"{CI}: a suite of the suites table has its gate in a later tier, and no step of the"
            " job that runs `tests-changed` sets up what it needs; add the steps under"
            " `if: matrix.gate == 'tests-changed'`, as `deep.yml` sets up the whole suite, and"
            " give `tests-changed` a job of its own in the justfile's `tier1_jobs`, since a job of"
            " several gates skips a step set up for one"
        )
    ]
    replace(
        CI,
        BUDGET,
        "      - if: matrix.gate == 'tests-changed'\n        run: ./scripts/install-browsers\n"
        + BUDGET,
    )(repo)
    [grouped] = red(repo)
    assert grouped.startswith("justfile: `tests-changed` runs in the group `rest`, but the")
    replace("justfile", ' bite rest"', ' bite tests-changed rest"')(repo)
    assert red(repo) == []


def test_a_repository_with_no_ci_workflow_has_no_job_to_set_up(repo: Path) -> None:
    suites_row(repo, UNIT, E2E)
    remove(CI)(repo)
    assert not any("tests-changed" in line for line in red(repo))


def test_the_ci_template_s_later_tier_marker_is_red_until_the_project_answers_it() -> None:
    template = (conformance.ADOPT_TEMPLATES / CI).read_text(encoding="utf-8")
    assert conformance.LATER_SETUP_PLACEHOLDER.search(template)
    scratch = pristine().parent / "unanswered"
    shutil.rmtree(scratch, ignore_errors=True)
    shutil.copytree(pristine(), scratch, symlinks=True)
    path = scratch / CI
    marker = (
        '      - { name: "bind: the setup each later-tier suite needs, pinned, in steps under'
        " if: matrix.gate == 'tests-changed', as deep.yml sets up the whole suite\","
        " run: exit 1 }\n"
    )
    assert marker in template
    text = path.read_text(encoding="utf-8")
    anchor = (
        '      - run: python3 "$RATCHET_PLUGIN/scripts/budget.py" "${{ matrix.gate }}" --tier tier1'
    )
    path.write_text(text.replace(anchor, marker + anchor, 1), encoding="utf-8")
    found = [line for line in red(scratch) if "toolchain" in line]
    assert len(found) == 1
    assert "in job `tier1` still says 'bind: the setup each later-tier suite needs'" in found[0]
    shutil.rmtree(scratch)
