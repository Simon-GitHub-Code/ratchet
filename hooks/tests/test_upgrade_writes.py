"""What an upgrade writes by itself, so that no safeguard it binds is off and none it leaves is hid.

Each edit here is one the upgrade makes without judgement: the template's own text, a gate the
plugin ships, or the day-one value adoption writes. What needs judgement stays for an agent, and
the checks that do not run until it is done are named.
"""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

import conformance
import gatelib
import justfile_text
import markdown_text
import owed
import plugin_pin
import project_bite
import triggers
import upgrade
from tests import adopted
from tests.repos import branch, commit, git, repository
from tests.test_conformance import BINDING, pristine, replace
from tests.test_owed import merged_upgrade

JUSTFILE = "justfile"
CI = ".github/workflows/ci.yml"
DEEP = ".github/workflows/deep.yml"
OVER = ' --over "$OVER"'
OVER_LINES = (
    "  # Why: runners vary, so a ceiling reports on a pull request and blocks on the default"
    " branch.\n  OVER: ${{ github.event_name == 'pull_request' && 'report' || 'block' }}\n"
)
FLAG_ROW = "| flag | `flags.toml` | `^\\[\\[flag\\]\\]` |"
FLAG = (
    '[[flag]]\nkey = "{}"\nowner = "the owner"\ndefault = false\nremove-by = 2099-01-01\n'
    'reason = "a behaviour that ships off until it is ready"\n'
)


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    shutil.copytree(pristine(), tmp_path / "repo", symlinks=True)
    return tmp_path / "repo"


def settle(root: Path) -> list[conformance.Finding]:
    """Make every edit the upgrade would make of ``root``, in place; what is left after."""
    return upgrade.converge(root, {}).left


def text(root: Path, path: str) -> str:
    return (root / path).read_text(encoding="utf-8")


def test_a_budget_call_an_older_template_wrote_gains_the_flags_the_template_passes(
    repo: Path,
) -> None:
    before = {path: text(repo, path) for path in (CI, DEEP)}
    replace(CI, OVER_LINES, "")(repo)
    replace(CI, f" --tier tier1{OVER} --report", " --report")(repo)
    replace(CI, f"{OVER} --report", " --report")(repo)
    replace(DEEP, " --tier tier2 --report", " --report")(repo)
    found = conformance.findings(repo)
    assert {(f.path, type(f.fix)) for f in found} == {
        (CI, conformance.Auto),
        (DEEP, conformance.Auto),
    }
    assert len(found) == 3, "ci.yml lacks --over and --tier, deep.yml --tier"
    assert settle(repo) == []
    assert {path: text(repo, path) for path in (CI, DEEP)} == before


def test_a_budget_call_the_project_wrote_itself_is_left_for_an_agent(repo: Path) -> None:
    replace(CI, f"tier0{OVER} --report", "tier0 --quiet --report")(repo)
    replace(CI, f"{OVER} --report", " --report")(repo)
    written = text(repo, CI)
    [found] = settle(repo)
    assert found.path == CI
    assert "('--over' absent)" in found.problem
    assert isinstance(found.fix, conformance.Agent)
    assert text(repo, CI) == written


def test_a_missing_shard_count_is_the_day_one_count_and_a_bad_one_is_an_agent_s(
    repo: Path,
) -> None:
    replace(BINDING, "- Shards: 1\n", "")(repo)
    assert settle(repo) == []
    assert "\n- Shards: 1\n" in markdown_text.section(text(repo, BINDING), "## Speed")
    replace(BINDING, "- Shards: 1\n", "- Shards: two\n")(repo)
    [found] = settle(repo)
    assert found.problem.startswith("the Speed section needs '- Shards: N'")
    assert isinstance(found.fix, conformance.Agent)


def test_a_speed_section_that_lists_nothing_gains_the_shard_count_below_its_text(
    repo: Path,
) -> None:
    speed = markdown_text.section(text(repo, BINDING), "## Speed")
    listed = "".join(line + "\n" for line in speed.splitlines() if line.startswith("- "))
    replace(BINDING, listed, "")(repo)
    shards = next(f for f in conformance.findings(repo) if "'- Shards: N'" in f.problem)
    assert isinstance(shards.fix, conformance.Auto)
    after = shards.fix.want(text(repo, BINDING))
    assert "\n\n- Shards: 1\n" in markdown_text.section(after, "## Speed")
    assert conformance.with_shards(None) == ""
    assert conformance.with_shards(after) == after


THRESHOLDS = "quality/thresholds.toml"
LIVE_CEILING = "[ceilings.change]\nproduct_files = 25\nproduct_lines = 1000\ntest_lines = 3000\n"
COMMENTED_CEILING = (
    "# [ceilings.change]\n# product_files = 25\n# product_lines = 1000\n# test_lines = 3000\n"
)
OLDER_CEILING = "[ceilings.change]\nfiles = 40\nlines = 1500\n"


def test_a_binding_without_a_tests_section_gains_the_templates_beside_its_neighbours(
    repo: Path,
) -> None:
    replace(BINDING, "\n## Tests\n", "\n## Trials\n")(repo)
    assert any("has no '## Tests' section" in f.problem for f in conformance.findings(repo))
    assert settle(repo) == []
    after = text(repo, BINDING)
    assert markdown_text.has_section(after, "## Tests")
    assert after.index("## Absolute paths") < after.index("## Tests") < after.index("## Trials")


def test_a_binding_that_has_a_tests_section_is_not_asked_for_one(repo: Path) -> None:
    assert not any("## Tests" in f.problem for f in conformance.findings(repo))


def test_a_change_ceiling_that_is_only_a_comment_becomes_the_method_s_live_one(repo: Path) -> None:
    replace(THRESHOLDS, LIVE_CEILING, COMMENTED_CEILING)(repo)
    [found] = [f for f in conformance.findings(repo) if "ceilings.change" in f.problem]
    assert isinstance(found.fix, conformance.Auto)
    assert settle(repo) == []
    assert LIVE_CEILING in text(repo, THRESHOLDS)
    assert COMMENTED_CEILING not in text(repo, THRESHOLDS)


def test_a_thresholds_file_with_no_change_ceiling_gains_the_method_s_below_its_text(
    repo: Path,
) -> None:
    replace(THRESHOLDS, LIVE_CEILING, "")(repo)
    before = text(repo, THRESHOLDS).rstrip()
    assert settle(repo) == []
    assert text(repo, THRESHOLDS) == f"{before}\n\n{LIVE_CEILING}"


def test_a_change_ceiling_the_project_set_is_left_as_it_is(repo: Path) -> None:
    own = "[ceilings.change]\nproduct_files = 10\nproduct_lines = 300\ntest_lines = 900\n"
    replace(THRESHOLDS, LIVE_CEILING, own)(repo)
    assert not any("ceilings.change" in f.problem for f in conformance.findings(repo))


def test_the_older_change_ceiling_moves_to_product_keys_at_the_same_values(repo: Path) -> None:
    # Why: ADR 0045. The values carry over unchanged, so the move loosens nothing and the lowered
    # gate reads it as the same ceiling; only the tests get a ceiling of their own.
    replace(THRESHOLDS, LIVE_CEILING, "[ceilings.change]\nfiles = 12\nlines = 700\n")(repo)
    [found] = [f for f in conformance.findings(repo) if "ceilings.change" in f.problem]
    assert isinstance(found.fix, conformance.Auto)
    assert settle(repo) == []
    assert "[ceilings.change]\nproduct_files = 12\nproduct_lines = 700\ntest_lines = 3000\n" in (
        text(repo, THRESHOLDS)
    )


def test_a_binding_without_test_paths_gains_the_templates_section(repo: Path) -> None:
    replace(BINDING, "\n## Test paths\n", "\n## Tested at\n")(repo)
    assert any("has no '## Test paths' section" in f.problem for f in conformance.findings(repo))
    left = settle(repo)
    assert any("'- Tests:' is still the template's" in f.problem for f in left), (
        "which paths are tests is the project's to say, so the upgrade leaves it to an agent"
    )


def test_a_test_paths_section_that_says_none_with_no_reason_is_red(repo: Path) -> None:
    replace(BINDING, "- Tests: `tests/**`", "- Tests: none: no")(repo)
    assert any("needs the reason" in f.problem for f in conformance.findings(repo))


def test_a_thresholds_file_the_ceiling_cannot_be_added_to_is_not_rewritten() -> None:
    assert conformance.with_change_ceiling(None) == LIVE_CEILING
    taken = "ceilings = 3\n"
    assert conformance.with_change_ceiling(taken) == taken


def test_a_missing_triggers_section_is_written_with_the_row_every_stack_writes_alike(
    repo: Path,
) -> None:
    replace(BINDING, "## Triggers\n", "## Hunches\n")(repo)
    left = settle(repo)
    kinds, problems = triggers.parse(text(repo, BINDING))
    assert problems == []
    assert set(kinds) == {"flag"}
    assert FLAG_ROW in text(repo, BINDING)
    written = markdown_text.table(text(repo, BINDING), "## Triggers")
    assert written is not None
    assert written.padded, "a Markdown formatter pads a table this narrow"
    assert left
    assert all("the Triggers table has no row for kind" in f.problem for f in left), left
    assert all(isinstance(f.fix, conformance.Agent) for f in left)


def test_a_row_whose_kind_the_triggers_table_cannot_count_is_never_written_later(
    repo: Path,
) -> None:
    replace(BINDING, "| flag | none: the self-test has none | |\n", "")(repo)
    row = next(
        line for line in text(repo, BINDING).splitlines() if line.startswith("| changed flags")
    )
    replace(BINDING, row + "\n", "")(repo)
    left = settle(repo)
    dropped = next(f for f in left if "no 'changed flags off and on' row" in f.problem)
    assert isinstance(dropped.fix, conformance.Agent)
    assert "the Triggers table has no row for 'flag'" in dropped.fix.then[0]
    assert "changed flags off and on" in conformance.not_running(repo, left)


def test_a_later_row_of_a_kind_the_table_lacks_is_named_as_not_running(repo: Path) -> None:
    replace(BINDING, "| flag | none: the self-test has none | |\n", "")(repo)
    row = next(
        line for line in text(repo, BINDING).splitlines() if line.startswith("| changed flags")
    )
    cells = row.split("|")
    cells[2] = " later: code:flag "
    replace(BINDING, row, "|".join(cells))(repo)
    assert conformance.not_running(repo, []) == ["changed flags off and on"]


def test_an_operability_row_of_the_plugin_s_own_gate_is_bound_and_another_is_an_agent_s(
    repo: Path,
) -> None:
    for question in ("feature flags declared", "secrets"):
        row = next(
            line for line in text(repo, BINDING).splitlines() if line.startswith(f"| {question} |")
        )
        replace(BINDING, row + "\n", "")(repo)
    left = next(f for f in settle(repo) if f.rule == "operability")
    assert left.problem == "the Gates table has no 'secrets' row"
    assert left.stops == "secrets"
    assert "| feature flags declared | `ratchet` plugin `scripts/flags.py` | 0 |" in text(
        repo, BINDING
    )


def test_a_gate_the_plugin_ships_that_no_tier_runs_is_named_and_left_when_the_tier_has_a_body(
    repo: Path,
) -> None:
    replace(JUSTFILE, " conformance triggers incidents ", " conformance triggers ")(repo)
    [found] = settle(repo)
    assert found.problem.startswith(
        "no tier runs `incidents`, which the binding binds to 'incidents tighten a gate' at tier 1"
    )
    assert found.stops == "incidents tighten a gate"
    assert isinstance(found.fix, conformance.Agent)
    assert found.fix.start == conformance.template_definition("incidents")


def tiers_by_name(root: Path) -> str:
    """The template justfile with tier 0 naming its gates, as older templates did; the names."""
    justfile = text(root, JUSTFILE)
    gates = justfile_text.STRING.search(
        next(line for line in justfile.splitlines() if line.startswith("tier0_tree_gates :="))
    )
    assert gates is not None
    names = gates.group(1)
    for tier, listed in (("tier0", "tier0-tree commits"), ("tier0-tree", names)):
        variable = tier.replace("-", "_") + "_gates"
        old = f"{tier}: plugin-pin\n    {{{{parallel}}}} {{{{serial0}}}} -- {{{{{variable}}}}}\n"
        assert old in justfile, tier
        justfile = justfile.replace(old, f"{tier}: plugin-pin {listed}\n")
    (root / JUSTFILE).write_text(justfile, encoding="utf-8")
    return names


def test_a_tier_that_names_its_gates_runs_the_one_it_lacks_from_the_template_s_recipe(
    repo: Path,
) -> None:
    names = tiers_by_name(repo)
    without = names.replace("flags ", "")
    replace(
        JUSTFILE, f"tier0-tree: plugin-pin {names}", f"tier0-tree: plugin-pin {without} # fast"
    )(repo)
    template = conformance.template_file(JUSTFILE)
    flags = "\n".join(justfile_text.definition(template, "flags"))
    replace(JUSTFILE, flags + "\n", "")(repo)
    assert settle(repo) == []
    after = text(repo, JUSTFILE)
    assert f"tier0-tree: plugin-pin {without} flags # fast\n" in after
    assert flags in after
    assert "flags" in justfile_text.reaches(after, "tier0")


def test_a_template_recipe_that_cannot_be_copied_leaves_the_gate_to_an_agent(
    repo: Path,
) -> None:
    names = tiers_by_name(repo)
    replace(JUSTFILE, f"plugin-pin {names}", f"plugin-pin {names.replace('flags ', '')}")(repo)
    template = conformance.template_file(JUSTFILE)
    flags = "\n".join(justfile_text.definition(template, "flags"))
    replace(JUSTFILE, flags + "\n", "")(repo)
    replace(JUSTFILE, "ratchet_plugin := ", "the_plugin := ")(repo)
    found = conformance.findings(repo)
    unrun = next(f for f in found if f.problem.startswith("no tier runs `flags`"))
    assert isinstance(unrun.fix, conformance.Agent)
    assert unrun.fix.then == ("it needs `ratchet_plugin`, which the justfile does not define",)
    assert unrun.problem.endswith("run it in `tier0-tree`")


def test_a_tier_list_is_written_from_a_tier_that_names_its_gates_and_holds_each_gate_added(
    repo: Path,
) -> None:
    replace(
        JUSTFILE,
        "tier1: plugin-pin\n    {{parallel}} {{serial1}} -- {{tier1_gates}}\n",
        "tier1: plugin-pin test conformance\n",
    )(repo)
    justfile = text(repo, JUSTFILE)
    gates = next(line for line in justfile.splitlines() if line.startswith("tier1_gates := "))
    (repo / JUSTFILE).write_text(justfile.replace(gates + "\n", ""), encoding="utf-8")
    left = settle(repo)
    after = text(repo, JUSTFILE)
    listed = justfile_text.listed(after, "tier1_gates")
    assert {
        "test",
        "conformance",
        "triggers",
        "incidents",
        "guard-diff",
        "settings-check",
    } <= listed
    assert listed == set(justfile_text.dependencies(after, "tier1") or ()) - {"plugin-pin"}
    assert not [f for f in left if f.problem.startswith("no tier runs")]


def test_a_missing_list_of_gates_to_shard_starts_empty(repo: Path) -> None:
    justfile = text(repo, JUSTFILE)
    sharded = next(line for line in justfile.splitlines() if line.startswith("tier1_sharded := "))
    (repo / JUSTFILE).write_text(justfile.replace(sharded + "\n", ""), encoding="utf-8")
    [found] = conformance.findings(repo)
    assert isinstance(found.fix, conformance.Auto)
    assert settle(repo) == []
    assert 'tier1_sharded := ""' in text(repo, JUSTFILE)


MINIMAL = (
    "\n## Gates\n\n| Question | Tool | Tier | Threshold source | Entry point |\n"
    "| --- | --- | --- | --- | --- |\n"
    + "".join(
        conformance.template_gate_row(question) + "\n"
        for question in ("method conformance", "incidents tighten a gate", "load smoke")
    )
)
MATRIX = "jobs:\n  tier1:\n    strategy:\n      matrix:\n        "
ONE_LINE = "jobs:\n  tier1:\n    strategy:\n      matrix:\n        gate: [test, conformance]\n"
LAID_OUT = (
    "jobs:\n  tier0:\n    x: 1\n  tier1:\n    strategy:\n      matrix:\n        gate:\n"
    "          [\n            test,\n            conformance\n          ]\n    steps: []\n"
)


@pytest.mark.parametrize(
    ("workflow", "after"),
    [
        (ONE_LINE, ONE_LINE.replace("conformance]", "conformance, incidents]")),
        (LAID_OUT, LAID_OUT.replace("conformance\n", "conformance,\n            incidents,\n")),
        (
            ONE_LINE.replace("[test, conformance]", "[]"),
            ONE_LINE.replace("[test, conformance]", "[conformance, incidents]"),
        ),
        (
            LAID_OUT.replace("            test,\n            conformance\n", ""),
            LAID_OUT.replace("            test,\n", "").replace(
                "conformance\n", "conformance,\n            incidents,\n"
            ),
        ),
    ],
)
def test_a_tier1_job_that_lists_its_gates_gains_a_gate_the_plugin_ships(
    tmp_path: Path, workflow: str, after: str
) -> None:
    (tmp_path / ".github/workflows").mkdir(parents=True)
    (tmp_path / CI).write_text(workflow, encoding="utf-8")
    found = list(conformance.ci_unrun(tmp_path, MINIMAL))
    assert found[-1].problem.startswith(
        "the tier1 job lists the gates it runs and not `incidents`, which the binding binds to"
        " 'incidents tighten a gate' at tier 1, so CI never runs that gate"
    )
    assert [f.stops for f in found][-1] == "incidents tighten a gate"
    done = workflow
    for finding in found:
        assert isinstance(finding.fix, conformance.Edit)
        done = finding.fix.want(done)
    assert done == after
    assert all(
        isinstance(finding.fix, conformance.Edit) and finding.fix.want(after) == after
        for finding in found
    )


@pytest.mark.parametrize(
    "workflow",
    [
        "jobs:\n  tier0:\n    x: 1\n",
        "jobs:\n  tier1:\n    runs-on: ubuntu-latest\n",
        "jobs:\n  tier1:\n    strategy:\n      matrix:\n        gate: ${{ fromJSON(x) }}\n",
        "jobs:\n  tier1:\n    strategy:\n      matrix:\n        gate: [test, 'a b']\n",
        "jobs:\n  tier1:\n    strategy:\n      matrix:\n        gate:\n          - test\n",
        "jobs:\n  tier1:\n    strategy:\n      matrix:\n        gate:\n",
        f"{MATRIX}gate:\n          [\n            test,\n",
        f"{MATRIX}gate:\n          [\n  'a b',\n  ]\n",
    ],
)
def test_a_tier1_job_whose_gates_are_not_a_plain_list_is_not_judged(
    tmp_path: Path, workflow: str
) -> None:
    (tmp_path / ".github/workflows").mkdir(parents=True)
    (tmp_path / CI).write_text(workflow, encoding="utf-8")
    assert list(conformance.ci_unrun(tmp_path, MINIMAL)) == []
    assert conformance.with_matrix_gate("tier1", "incidents")(workflow) == workflow


def test_the_justfile_reading_follows_lists_calls_and_the_names_a_recipe_lacks() -> None:
    justfile = (
        'base := "a b"\nmore := base + " c" + loop\nloop := more\n\n'
        "tier: x\n    {{runner}} -- {{more}}\n    just y\n\n"
        "x:\n    echo x\n\ny: z\n\nz:\n    echo z {{param}}\n\n"
        "bare: x z # quick\n\nlate: && x\n"
    )
    assert justfile_text.listed(justfile, "more") == {"a", "b", "c"}
    assert justfile_text.listed(justfile, "absent") == set()
    assert {"x", "y", "z", "a", "b", "c"} <= justfile_text.reaches(justfile, "tier")
    assert justfile_text.undefined(justfile, "tier") == ["runner"]
    assert justfile_text.undefined(justfile, "z") == ["param"]
    assert justfile_text.undefined(justfile, "absent") == []
    assert justfile_text.dependencies(justfile, "bare") == ("x", "z")
    assert justfile_text.dependencies(justfile, "tier") is None
    assert justfile_text.dependencies(justfile, "late") is None
    assert justfile_text.dependencies(justfile, "absent") is None
    added = justfile_text.with_dependency("bare", "y")(justfile)
    assert "bare: x z y # quick\n" in added
    assert justfile_text.with_dependency("bare", "y")(added) == added
    assert justfile_text.with_dependency("tier", "y")(justfile) == justfile
    assert justfile_text.with_dependency("bare", "y")(None) == ""
    named = justfile_text.with_variable("extra", lambda now: f'"{len(now)}"')(justfile)
    assert named.startswith(
        f'base := "a b"\nmore := base + " c" + loop\nloop := more\nextra := "{len(justfile)}"\n'
    )
    assert justfile_text.with_variable("base", lambda _: '""')(justfile) == justfile
    assert justfile_text.with_listed("base", "d")(justfile).startswith('base := "a b d"\n')
    assert justfile_text.with_listed("base", "a")(justfile) == justfile
    assert justfile_text.with_listed("more", "d")(justfile) == justfile
    assert justfile_text.with_listed("absent", "d")(justfile) == justfile
    assert justfile_text.with_listed("empty", "d")('empty := ""\n') == 'empty := "d"\n'
    assert justfile_text.with_listed("single", "d")("single := 'a'\n") == "single := 'a'\n"
    assert justfile_text.with_variable("first", lambda _: '""')("x:\n    echo\n") == (
        'first := ""\nx:\n    echo\n'
    )
    assert justfile_text.with_variable("first", lambda _: '""')(None).strip() == 'first := ""'


def test_an_upgraded_older_adoption_runs_every_gate_of_the_plugin_s_own_it_binds(
    tmp_path: Path,
) -> None:
    root = adopted.copy(adopted.adopted("0.8.2"), tmp_path / "repo")
    upgrade.apply(root, upgrade.plan(root, plugin_pin.PLUGIN).changes)
    binding = text(root, BINDING)
    justfile = text(root, JUSTFILE)
    ci = text(root, CI)
    tiers = conformance.tier_recipes(binding, justfile)
    for question in (
        "feature flags declared",
        "incidents tighten a gate",
        "safeguards bound before more instances",
        "architecture reviewed when its shape changes",
    ):
        row = next(r for r in project_bite.gate_rows(binding) if r.question == question)
        assert project_bite.SHIPPED.fullmatch(row.tool), row
        assert row.gate in justfile_text.reaches(justfile, tiers[f"tier{row.tier}"]), row
        if row.tier == "1":
            assert f" {row.gate}," in ci or f" {row.gate}]" in ci, row
    assert "| changed flags off and on | later: code:flag |" in binding
    assert FLAG_ROW in binding
    if shutil.which("just"):
        listed = subprocess.run(  # noqa: S603 # -- a fixed argv
            ["just", "--justfile", str(root / JUSTFILE), "--evaluate", "tier1_gates"],  # noqa: S607
            capture_output=True,
            text=True,
            check=True,
        ).stdout.split()
        assert {"incidents", "triggers", "conformance"} <= set(listed)


def test_a_row_the_upgrade_wrote_later_holds_once_its_kind_arrives(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = adopted.copy(merged_upgrade(), tmp_path / "repo")
    assert "| changed flags off and on | later: code:flag |" in text(root, BINDING)
    branch(root, "first")
    (root / "flags.toml").write_text(text(root, "flags.toml") + FLAG.format("one"))
    commit(root, "feat: the first flag")
    capsys.readouterr()
    assert triggers.main(["triggers.py", "--root", str(root), "--base", "main"]) == 0
    assert "'flag' has arrived" in capsys.readouterr().out
    git(root, "checkout", "-q", "main")
    git(root, "merge", "-q", "--ff-only", "first")
    branch(root, "second")
    (root / "flags.toml").write_text(text(root, "flags.toml") + "\n" + FLAG.format("two"))
    commit(root, "feat: the second flag")
    assert triggers.main(["triggers.py", "--root", str(root), "--base", "main"]) == 1
    assert "changed flags off and on" in capsys.readouterr().out


def test_the_owed_line_names_the_checks_that_do_not_run_until_it_is_paid(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = adopted.copy(merged_upgrade(), tmp_path / "repo")
    capsys.readouterr()
    assert owed.main(["owed.py", "--root", str(root)]) == 0
    line = capsys.readouterr().out
    assert "None of them stops a merge, and between upgrades the list only gets shorter." in line
    assert "Nothing fixes them by itself: ask an agent to." in line
    waiting = line.split("Checks that do not run until then: ")[1]
    assert "the weekly checks" in waiting
    assert "configuration hardening" in waiting
    assert json.dumps(line).count("\\n") == 1, "one line"


def test_an_always_asked_row_whose_tool_the_plugin_does_not_ship_is_an_agent_s() -> None:
    failure = "the Gates table has no 'strict types' row; a row is bound or none, never dropped"
    found = conformance.dropped_row(
        failure, "strict types", trigger="always", fired=conformance.Fired({}, frozenset())
    )
    assert isinstance(found.fix, conformance.Agent)
    assert found.stops == "strict types"


def test_a_workflow_without_the_plugin_variable_gains_the_flags_and_no_variable() -> None:
    template = conformance.template_file(CI)
    old = template.replace(f" --tier tier1{OVER} --report", " --report").replace(
        f"{OVER} --report", " --report"
    )
    without = old.replace(OVER_LINES, "").replace("  RATCHET_PLUGIN: ", "  PLUGIN: ")
    after = conformance.budget_flags("ci.yml")(without)
    assert f"tier0{OVER} --report" in after
    assert "  OVER:" not in after


def test_a_marker_above_every_job_belongs_to_no_job() -> None:
    assert conformance.job_of(["jobs:", "  # bind: toolchain setup"], 2) == "?"
    assert conformance.job_of(["name: x", "  # bind: toolchain setup"], 2) == "?"


def test_the_owner_is_told_an_agent_adds_a_door_on_a_branch_and_he_decides_at_the_merge() -> None:
    assert "agents may not" not in conformance.NO_DOOR
    assert "on a branch, and you decide at that merge" in conformance.NO_DOOR


def test_the_owner_hears_that_one_change_carries_the_fixes_and_he_merges_once() -> None:
    assert "guard path" not in upgrade.owing_words(3, approved=0)
    said = upgrade.owing_words(3, approved=2)
    assert " 2 are in guard paths: an agent can make all the fixes in one change" in said
    assert "you decide at that one merge" in said
    assert "The project owes 1 thing since" in upgrade.owing_words(1, approved=1)


def test_an_exported_tree_keeps_a_link_as_a_link(tmp_path: Path) -> None:
    root = repository(tmp_path / "repo", {"real.txt": "real\n"})
    (root / "link.txt").symlink_to("real.txt")
    commit(root, "chore: a link")
    target = tmp_path / "out"
    gatelib.export(root, "HEAD", target)
    assert (target / "link.txt").is_symlink()
    assert (target / "link.txt").readlink() == Path("real.txt")
