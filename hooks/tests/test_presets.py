"""The stack presets: each carries the same fragments, and each fragment fits its template."""

import json
import re
import shutil
import subprocess
import tomllib
from pathlib import Path

import pytest

import pins
import suppressions

ADOPT = Path(__file__).resolve().parents[2] / "skills/adopt"
PRESETS = sorted(p for p in (ADOPT / "presets").iterdir() if p.is_dir())
TEMPLATE_JUSTFILE = (ADOPT / "templates/justfile").read_text(encoding="utf-8")
TEMPLATE_BINDING = (ADOPT / "templates/docs/process/binding.md").read_text(encoding="utf-8")
TEMPLATE_MANIFEST = tomllib.loads((ADOPT / "templates/guards/manifest.toml").read_text())
RECIPE = re.compile(r"^([a-z][a-z0-9-]*)(?:\s+[^:\n]*)?:\s*(?:#.*)?$", re.MULTILINE)
QUESTION = re.compile(r"^\| ([^|]+?) \| ", re.MULTILINE)


def recipes(justfile: str) -> set[str]:
    return set(RECIPE.findall(justfile))


def test_there_are_presets_for_both_stacks() -> None:
    assert [p.name for p in PRESETS] == ["python-uv", "typescript-pnpm"]


@pytest.mark.parametrize("preset", PRESETS, ids=lambda p: p.name)
def test_every_preset_carries_every_fragment(preset: Path) -> None:
    for name in ("justfile", "binding.md", "manifest.toml", "thresholds.toml", "ci.yml"):
        assert (preset / name).is_file(), name
    assert any((preset / "tools").iterdir())
    assert any((preset / "fitness").iterdir())


@pytest.mark.parametrize("preset", PRESETS, ids=lambda p: p.name)
def test_preset_recipes_are_template_recipes(preset: Path) -> None:
    # Why: a preset recipe replaces the template stub of the same name; a name the template
    # lacks would be an entry point no binding table names.
    mine = recipes((preset / "justfile").read_text(encoding="utf-8"))
    assert mine, "a preset binds at least one recipe"
    assert mine <= recipes(TEMPLATE_JUSTFILE), sorted(mine - recipes(TEMPLATE_JUSTFILE))
    shipped = {"markers", "suppressions", "prose", "snapshots", "snapshots-check", "commits"}
    assert not (mine & shipped), "the shipped gates are bound in the template already"


@pytest.mark.parametrize("preset", PRESETS, ids=lambda p: p.name)
def test_preset_binding_rows_answer_template_questions(preset: Path) -> None:
    text = (preset / "binding.md").read_text(encoding="utf-8")
    questions = set(QUESTION.findall(text)) - {"Question", "---"}
    template_questions = set(QUESTION.findall(TEMPLATE_BINDING)) - {"Question", "---"}
    assert questions, "the preset fills Tool cells"
    assert questions <= template_questions, sorted(questions - template_questions)
    for heading in ("## Runner", "## Absolute paths", "## Limits", "## Tool versions"):
        assert heading in text, heading


@pytest.mark.parametrize("preset", PRESETS, ids=lambda p: p.name)
def test_preset_manifest_groups_exist_in_the_template(preset: Path) -> None:
    groups = tomllib.loads((preset / "manifest.toml").read_text())["group"]
    template_groups = {g["name"] for g in TEMPLATE_MANIFEST["group"]}
    for group in groups:
        assert group["name"] in template_groups, group["name"]
        assert group["paths"], group["name"]
        assert all(not p.startswith(("/", "~")) for p in group["paths"])


@pytest.mark.parametrize("preset", PRESETS, ids=lambda p: p.name)
def test_preset_thresholds_declare_an_absolute_core_group(preset: Path) -> None:
    data = tomllib.loads((preset / "thresholds.toml").read_text())
    language = next(k for k in data if k != "ceilings")
    core = data[language]["coverage"]["core"]
    assert core["absolute"] is True
    assert core["paths"]
    assert all("paths" in g for g in data[language]["coverage"].values())
    assert data["ceilings"]["change"] == {"files": 40, "lines": 1500}


@pytest.mark.parametrize("preset", PRESETS, ids=lambda p: p.name)
def test_preset_ci_steps_are_green_on_the_pins_gate(preset: Path) -> None:
    # Why: item 6 of the owner-free brief: `pins` must be green on the template, presets included.
    fragment = (preset / "ci.yml").read_text(encoding="utf-8")
    assert "steps:" in fragment
    assert "cache:" in fragment
    workflow = "name: x\npermissions:\n  contents: read\njobs:\n  a:\n" + "\n".join(
        f"    {line}" for line in fragment.splitlines() if not line.startswith("#")
    )
    assert pins.check_workflow(".github/workflows/ci.yml", workflow) == []
    assert "uses:" in workflow


def test_the_typescript_package_fragment_is_json_and_pins_pnpm() -> None:
    data = json.loads((ADOPT / "presets/typescript-pnpm/tools/package.json").read_text())
    assert data["packageManager"].startswith("pnpm@")
    assert "vitest" in data["devDependencies"]


def test_the_python_pyproject_fragment_parses_and_forbids_coverage_exclusions() -> None:
    data = tomllib.loads((ADOPT / "presets/python-uv/tools/pyproject.toml").read_text())
    assert data["tool"]["ruff"]["lint"]["mccabe"]["max-complexity"] == 10
    assert "exclude_lines" not in data["tool"]["coverage"]["report"]
    assert "omit" not in data["tool"]["coverage"]["run"]


def test_the_stryker_fragment_feeds_the_register_gate_and_keeps_the_score_a_trend() -> None:
    # Why: issue #2. `break: null` alone made stryker green with survivors; the json report is
    # what `mutation_survivors.py` judges, and the `mutate` globs are what `--changed` applies.
    data = json.loads((ADOPT / "presets/typescript-pnpm/tools/stryker.config.json").read_text())
    assert "json" in data["reporters"]
    assert data["jsonReporter"]["fileName"] == "reports/mutation/mutation.json"
    assert data["thresholds"]["break"] is None
    assert any(not g.startswith("!") for g in data["mutate"])
    assert all(".test." in g for g in data["mutate"] if g.startswith("!")), "only tests excluded"


@pytest.mark.parametrize("preset", PRESETS, ids=lambda p: p.name)
def test_preset_mutation_recipes_go_through_the_register_gate(preset: Path) -> None:
    text = (preset / "justfile").read_text(encoding="utf-8")
    assert text.count("scripts/mutation_survivors.py") >= 3, (
        "changed scope, changed gate, full gate"
    )
    assert "--changed" in text
    if preset.name == "python-uv":
        assert "rm -rf mutants" in text, "mutmut keeps verdicts a new test cannot change"
        assert "mutmut results --all true |" in text, "mutmut 3.8 takes `--all BOOLEAN`"
        assert "--paths-to-mutate" not in text, "not a mutmut 3 option"
        tools = tomllib.loads((preset / "tools/pyproject.toml").read_text())
        assert tools["tool"]["mutmut"]["source_paths"] == ["src/"]
        assert any(d.startswith("mutmut>=3.8") for d in tools["dependency-groups"]["dev"])
    else:
        assert 'stryker run --mutate "$files"' in text
        assert "--report reports/mutation/mutation.json" in text


def test_the_template_justfile_never_relies_on_tilde_expansion() -> None:
    # Why: `just` never expands `~`, so a quoted `~/...` fallback names a directory that does not
    # exist and every shipped gate fails unless RATCHET_PLUGIN is set.
    assert '"~/' not in TEMPLATE_JUSTFILE
    assert "home_directory()" in TEMPLATE_JUSTFILE


@pytest.mark.parametrize("preset", PRESETS, ids=lambda p: p.name)
def test_preset_diff_gates_work_without_a_remote(preset: Path) -> None:
    # Why: `/ratchet:init` supports a local-only repository, which has no `origin/main`.
    assert "origin/" not in (preset / "justfile").read_text(encoding="utf-8")
    assert "merge_base :=" in TEMPLATE_JUSTFILE


@pytest.mark.parametrize("preset", PRESETS, ids=lambda p: p.name)
def test_preset_text_trips_no_suppression_gate(preset: Path) -> None:
    # Why: adopt copies the preset's prose into the repository, where the suppressions gate
    # counts every match; a sentence that forbids a pragma by naming it counts as one.
    for path in sorted(p for p in preset.rglob("*") if p.is_file()):
        text = path.read_text(encoding="utf-8")
        for kind, pattern in suppressions.KINDS.items():
            assert not re.search(pattern, text), f"{path.relative_to(preset)}: {kind}"


def test_python_test_counts_list_node_ids_despite_quiet_addopts() -> None:
    # Why: a second `-q` turns `--collect-only` into per-file counts with no `::` to count,
    # which made test-counts compare 0 with 0 and pass on any suite.
    tools = tomllib.loads((ADOPT / "presets/python-uv/tools/pyproject.toml").read_text())
    assert "-q" in tools["tool"]["pytest"]["ini_options"]["addopts"].split()
    text = (ADOPT / "presets/python-uv/justfile").read_text(encoding="utf-8")
    collects = re.findall(r"pytest --collect-only[^|]*", text)
    assert collects
    assert all("-o addopts=" in c for c in collects), collects


def test_the_python_fitness_scaffold_is_clean_under_the_preset_s_own_ruff(tmp_path: Path) -> None:
    ruff = shutil.which("ruff")
    assert ruff, "ruff is a dev dependency of the plugin"
    preset = ADOPT / "presets/python-uv"
    shutil.copy(preset / "tools/pyproject.toml", tmp_path / "pyproject.toml")
    (tmp_path / "tests/fitness").mkdir(parents=True)
    shutil.copy(preset / "fitness/test_fitness.py", tmp_path / "tests/fitness/test_fitness.py")
    for argv in (["check", "--no-cache", "."], ["format", "--check", "--no-cache", "."]):
        run = subprocess.run(  # noqa: S603 # -- a fixed argv, no shell
            [ruff, *argv], cwd=tmp_path, capture_output=True, text=True, check=False
        )
        assert run.returncode == 0, run.stdout + run.stderr


@pytest.mark.parametrize("preset", PRESETS, ids=lambda p: p.name)
def test_uvx_runs_only_python_packages(preset: Path) -> None:
    # Why: gitleaks and trivy are not on PyPI, and actionlint and cyclonedx-py are executables
    # of differently named packages, so a bare `uvx <tool>` failed to resolve in two adoptions.
    text = (preset / "justfile").read_text(encoding="utf-8")
    assert not re.search(r"uvx (actionlint|gitleaks|trivy|shellcheck|cyclonedx-py)\b", text)


def test_the_typescript_fragments_work_under_pnpm_and_vitest() -> None:
    preset = ADOPT / "presets/typescript-pnpm"
    text = (preset / "justfile").read_text(encoding="utf-8")
    assert "reportsDirectory=." not in text, "vitest refuses to clean the project root"
    stryker = json.loads((preset / "tools/stryker.config.json").read_text())
    assert "@stryker-mutator/vitest-runner" in stryker["plugins"], "pnpm hides unnamed plugins"
    knip = json.loads((preset / "tools/knip.json").read_text())
    dev = json.loads((preset / "tools/package.json").read_text())["devDependencies"]
    assert set(knip["ignoreDependencies"]) <= set(dev)
    assert "just" in knip["ignoreBinaries"]


def body(justfile: str, recipe: str) -> str:
    match = re.search(rf"^{recipe}:.*\n((?:    .*\n)+)", justfile, re.MULTILINE)
    assert match, recipe
    return match.group(1)


@pytest.mark.parametrize("preset", PRESETS, ids=lambda p: p.name)
def test_coverage_measures_every_root_a_coverage_group_names(preset: Path) -> None:
    # Why: coverage_groups.py skips a group no report measures, so a root left out of the run
    # silently drops its group, `tests/fitness/**` in the absolute core included.
    justfile = (preset / "justfile").read_text(encoding="utf-8")
    thresholds = tomllib.loads((preset / "thresholds.toml").read_text(encoding="utf-8"))
    (language,) = (k for k in thresholds if k != "ceilings")
    named = {
        path.split("/")[0]
        for group in thresholds[language]["coverage"].values()
        for path in group["paths"]
    } - {"tools"}
    run = body(justfile, "coverage").splitlines()[0]
    measured = set(re.findall(r"--cov=([\w.-]+)|--coverage\.include='([\w.-]+)/", run))
    assert named <= {a or b for a, b in measured}, run
    assert body(justfile, "ratchet").splitlines()[0] == run, "the ratchet measures as coverage does"


# Why: in the UI trial `eslint .` and `prettier --check .` went red on the agent worktrees
# `/pstack:interrogate` leaves under .claude/worktrees/ and on impeccable's .impeccable/ files.
COMPANION_DIRS = (".claude/", ".impeccable/")


def test_whole_tree_tools_skip_the_companions_working_files() -> None:
    justfile = (ADOPT / "presets/typescript-pnpm/justfile").read_text()
    for line in (line for line in justfile.splitlines() if "eslint ." in line):
        for directory in COMPANION_DIRS:
            assert f"--ignore-pattern '{directory}'" in line, line
    ignored = (ADOPT / "presets/typescript-pnpm/tools/.prettierignore").read_text().splitlines()
    assert set(COMPANION_DIRS) <= set(ignored)
    gitignore = (ADOPT.parent / "init/templates/gitignore").read_text().splitlines()
    assert ".claude/worktrees/" in gitignore
    assert ".impeccable/hook.cache.json" in gitignore


def test_the_python_audit_exports_hashes_since_pip_audit_runs_without_pip() -> None:
    """pip-audit refuses ``--disable-pip`` on a requirements file without hashes.

    The recipe exits early while nothing ships, so the first runtime dependency is the
    first time pip-audit reads the file; a trial on 2026-11-09 went red there.
    """
    text = (ADOPT / "presets/python-uv/justfile").read_text(encoding="utf-8")
    recipe = text.split("\naudit:\n", 1)[1].split("\n\n", 1)[0]
    exports = [line for line in recipe.splitlines() if "uv export" in line]
    assert exports
    assert "--disable-pip" in recipe
    assert not any("--no-hashes" in line for line in exports), exports


FAKE_RUNNERS = {
    # The stand-in prints one test, collects none where NONE is set, and fails where FAIL is set;
    # "base" means inside the merge-base worktree.
    "python-uv": (
        "uv",
        (
            'case "$PWD" in *.ratchet/base) where=base;; *) where=head;; esac\n'
            '[ "${FAIL:-}" = "$where" ] && exit 2\n'
            '[ "${NONE:-}" = "$where" ] && exit 5\n'
            'echo "tests/test_a.py::test_a"\n'
        ),
    ),
    "typescript-pnpm": (
        "pnpm",
        (
            'case "$PWD" in *.ratchet/base) where=base;; *) where=head;; esac\n'
            '[ "$1" = install ] && exit 0\n'
            '[ "${FAIL:-}" = "$where" ] && exit 1\n'
            '[ "${NONE:-}" = "$where" ] && exit 0\n'
            'echo "src/a.test.ts > a"\n'
        ),
    ),
}


def _run_test_counts(preset: Path, tmp_path: Path, **env: str) -> subprocess.CompletedProcess[str]:
    tool, body = FAKE_RUNNERS[preset.name]
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    (bin_dir / tool).write_text("#!/usr/bin/env bash\n" + body)
    (bin_dir / tool).chmod(0o755)
    repo = tmp_path / "repo"
    repo.mkdir()
    git = shutil.which("git")
    assert git
    for argv in (["init", "-q"], ["commit", "-q", "--allow-empty", "-m", "base"]):
        subprocess.run(  # noqa: S603 # -- a fixed argv, no shell
            [git, "-c", "user.name=t", "-c", "user.email=t@t", *argv], cwd=repo, check=True
        )
    text = (preset / "justfile").read_text(encoding="utf-8")
    recipe = text.split("\ntest-counts:\n", 1)[1].split("\n\n", 1)[0]
    script = "\n".join(line.removeprefix("    ") for line in recipe.splitlines())
    (repo / "test-counts.sh").write_text(script.replace("{{merge_base}}", "HEAD"))
    bash = shutil.which("bash")
    assert bash
    return subprocess.run(  # noqa: S603 # -- a fixed argv, no shell
        [bash, "test-counts.sh"],
        cwd=repo,
        env={"PATH": f"{bin_dir}:/usr/bin:/bin", "HOME": str(tmp_path), **env},
        capture_output=True,
        text=True,
        check=False,
    )


@pytest.mark.parametrize("preset", PRESETS, ids=lambda p: p.name)
def test_test_counts_is_green_when_both_sides_collect_the_same(
    preset: Path, tmp_path: Path
) -> None:
    run = _run_test_counts(preset, tmp_path)
    assert run.returncode == 0, run.stdout + run.stderr
    assert "tests: 1 at base, 1 at head" in run.stdout


@pytest.mark.parametrize("preset", PRESETS, ids=lambda p: p.name)
@pytest.mark.parametrize("side", ["base", "head"])
def test_test_counts_is_red_when_a_side_fails_to_collect(
    preset: Path, side: str, tmp_path: Path
) -> None:
    """A trial on 2026-11-09 saw a base that could not install read as zero tests and pass."""
    run = _run_test_counts(preset, tmp_path, FAIL=side)
    assert run.returncode != 0, run.stdout
    assert "test-counts:" in run.stderr
    assert not (tmp_path / "repo/.ratchet/base").exists()


@pytest.mark.parametrize("preset", PRESETS, ids=lambda p: p.name)
def test_test_counts_reads_a_base_with_no_tests_as_zero(preset: Path, tmp_path: Path) -> None:
    run = _run_test_counts(preset, tmp_path, NONE="base")
    assert run.returncode == 0, run.stdout + run.stderr
    assert "tests: 0 at base, 1 at head" in run.stdout


def test_python_deptry_scans_src_so_the_own_package_is_first_party() -> None:
    """From the root, deptry read a src-layout package's own import as DEP003 (2026-11-09)."""
    text = (ADOPT / "presets/python-uv/justfile").read_text(encoding="utf-8")
    recipe = text.split("\ndeadcode:\n", 1)[1].split("\n\n", 1)[0]
    assert "uv run deptry src" in recipe
