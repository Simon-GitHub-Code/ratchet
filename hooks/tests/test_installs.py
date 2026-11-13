"""A recipe that runs a package manager's tool installs it first (ADR 0039).

The fixture is shaped like the app of a first-hour eval run: a Python project whose page is checked
by Biome and TypeScript through npm, with the three tier 0 recipes that run them.
"""

import os
import shutil
import stat
import subprocess
from pathlib import Path

import pytest

import conformance
import installs
import justfile_text
import owed
import upgrade
from tests.test_conformance import pristine

NPX_BODIES = {
    "fmt-check": "uv run --frozen ruff format --check .\n    npx --no-install biome format .",
    "lint": "uv run --frozen ruff check .\n    npx --no-install biome lint --error-on-warnings .",
    "types": '@if find src -name "*.js" | grep -q .; then npx --no-install tsc -p .; fi',
}
BIND = {
    "fmt-check": '@echo "bind: formatter --check"; exit 1',
    "lint": '@echo "bind: linter with near-all rules"; exit 1',
    "types": '@echo "bind: strict type checker"; exit 1',
}
RUNNER = (
    'test_runner := "bind: the command that runs the test files named after it,'
    ' or every test with none"\n'
)


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    shutil.copytree(pristine(), tmp_path / "repo", symlinks=True)
    return tmp_path / "repo"


def justfile(root: Path) -> str:
    return (root / "justfile").read_text(encoding="utf-8")


def eval_app(root: Path, lockfile: str = "package-lock.json") -> None:
    """The adopted app: its lockfile and the gate recipes that run its npm tools."""
    (root / lockfile).write_text("{}\n", encoding="utf-8")
    text = justfile(root)
    for gate, body in NPX_BODIES.items():
        text = text.replace(BIND[gate], body)
    (root / "justfile").write_text(text, encoding="utf-8")


def reasons(root: Path) -> str:
    """What the finding says is missing, before its edit is tried: the agent's list."""
    [finding] = [f for _, f in conformance.raised(root) if f.rule == "installs"]
    assert isinstance(finding.fix, conformance.Edit)
    assert isinstance(finding.fix.otherwise, conformance.Agent)
    return "\n".join(finding.fix.otherwise.then)


def found(root: Path) -> list[conformance.Finding]:
    return [f for f in conformance.findings(root) if f.rule == "installs"]


def test_the_template_asks_nothing_of_a_project_with_no_package_lockfile(repo: Path) -> None:
    assert found(repo) == []


def test_a_lockfile_with_no_recipe_that_runs_its_tools_is_not_asked_for_an_install(
    repo: Path,
) -> None:
    (repo / "package-lock.json").write_text("{}\n", encoding="utf-8")
    assert found(repo) == []


def test_the_eval_apps_recipes_are_red_without_an_install_and_name_each_recipe(repo: Path) -> None:
    eval_app(repo)
    [finding] = found(repo)
    assert "`fmt-check`, `lint`, `types`" in finding.problem
    assert "runs `npm ci` from package-lock.json" in finding.problem
    assert "no `install` recipe that runs `npm ci`" in reasons(repo)
    assert "`tier0`" in reasons(repo)
    assert any(
        line.startswith("justfile: the recipes `fmt-check`, `lint`, `types` run npm")
        for line in conformance.failures(repo)
    )


def test_the_edit_gives_every_such_recipe_the_install_and_the_conformance_gate_goes_green(
    repo: Path,
) -> None:
    eval_app(repo)
    [finding] = found(repo)
    assert isinstance(finding.fix, conformance.Auto)
    (repo / "justfile").write_text(finding.fix.want(justfile(repo)), encoding="utf-8")
    text = justfile(repo)
    assert "fmt-check: install\n" in text
    assert "lint: install\n" in text
    assert "types: install\n" in text
    assert "npm ci --no-audit --no-fund" in text
    assert conformance.failures(repo) == []
    assert finding.fix.want(text) == text


@pytest.mark.parametrize(
    ("lock", "command"),
    [
        (("package-lock.json", "{}"), "npm ci"),
        (("npm-shrinkwrap.json", "{}"), "npm ci"),
        (("pnpm-lock.yaml", "lockfileVersion: 9"), "pnpm install --frozen-lockfile"),
        (("yarn.lock", "# yarn lockfile v1"), "yarn install --frozen-lockfile"),
        (("yarn.lock", "__metadata:\n  version: 8"), "yarn install --immutable"),
        (("bun.lock", "{}"), "bun install --frozen-lockfile"),
        (("bun.lockb", "binary"), "bun install --frozen-lockfile"),
    ],
)
def test_the_install_is_the_frozen_one_of_the_lockfiles_package_manager(
    repo: Path, lock: tuple[str, str], command: str
) -> None:
    lockfile, contents = lock
    eval_app(repo, lockfile)
    (repo / lockfile).write_text(contents, encoding="utf-8")
    [finding] = found(repo)
    assert isinstance(finding.fix, conformance.Auto)
    made = finding.fix.want(justfile(repo))
    assert command in made
    assert f"cp {lockfile} node_modules/.ratchet-install" in made


@pytest.mark.parametrize(
    "line",
    [
        "pnpm exec eslint .",
        "pnpm run lint",
        "yarn eslint .",
        "yarn run lint",
        "bunx tsc",
        "bun run lint",
        "npm run lint",
        "npm exec -- tsc",
        "node_modules/.bin/tsc",
        "npx biome check",
        "{{test_runner}} {{files}}",
    ],
)
def test_every_way_a_recipe_runs_an_installed_tool_needs_the_install(repo: Path, line: str) -> None:
    (repo / "pnpm-lock.yaml").write_text("lockfileVersion: 9\n", encoding="utf-8")
    text = justfile(repo).replace(RUNNER, 'test_runner := "npx --no-install vitest run"\n')
    (repo / "justfile").write_text(text.replace(BIND["lint"], line), encoding="utf-8")
    [finding] = found(repo)
    assert "`lint`" in finding.problem or "`test-files`" in finding.problem


@pytest.mark.parametrize(
    "line",
    [
        "npm ci --dry-run --ignore-scripts",
        "npm audit --audit-level=low",
        "npm outdated || true",
        "yarn install --immutable",
        "yarn audit",
        "pnpm install --frozen-lockfile",
        "pnpm dlx cowsay hi",
        "bun install --frozen-lockfile",
        "uv run --frozen ruff check .",
        "# npx --no-install biome lint",
    ],
)
def test_a_line_that_runs_no_installed_tool_needs_no_install(repo: Path, line: str) -> None:
    (repo / "package-lock.json").write_text("{}\n", encoding="utf-8")
    (repo / "justfile").write_text(justfile(repo).replace(BIND["lint"], line), encoding="utf-8")
    assert found(repo) == []


def test_an_install_that_is_not_the_frozen_one_is_replaced_and_the_rest_kept(repo: Path) -> None:
    eval_app(repo)
    (repo / "justfile").write_text(
        justfile(repo).replace("install:\n    @true\n", "install:\n    npm install\n", 1),
        encoding="utf-8",
    )
    [finding] = found(repo)
    assert "no `install` recipe that runs `npm ci`" in reasons(repo)
    assert finding.problem.startswith("the recipes `fmt-check`")
    assert isinstance(finding.fix, conformance.Auto)
    made = finding.fix.want(justfile(repo))
    assert "npm install\n" not in made
    assert made.count("\ninstall:\n") == 1
    assert "tier0: plugin-pin install" in made


def test_a_justfile_with_no_tier_gets_the_install_at_its_end() -> None:
    want = installs.with_install(installs.NPM)
    assert want("lint:\n    npx tsc\n\n").rstrip().endswith("node_modules/.ratchet-install; }")
    assert want("") == "\n".join(installs.recipe_lines(installs.NPM)) + "\n"


def test_an_install_is_written_above_the_first_tier_when_the_justfile_has_none() -> None:
    made = installs.with_install(installs.NPM)("lint:\n    npx tsc\n\ntier0: plugin-pin\n    x\n")
    assert made.index("\ninstall:\n") < made.index("\ntier0:")
    assert made.index("lint:") < made.index("\ninstall:\n")


def test_a_tier_that_starts_its_gates_before_any_install_is_named(repo: Path) -> None:
    eval_app(repo)
    (repo / "justfile").write_text(
        justfile(repo).replace("plugin-pin install\n", "plugin-pin\n"), encoding="utf-8"
    )
    [finding] = found(repo)
    assert "`tier0`" in reasons(repo)
    assert "before their gates" in reasons(repo)
    assert isinstance(finding.fix, conformance.Auto)
    made = finding.fix.want(justfile(repo))
    assert "tier0: plugin-pin install\n" in made
    assert "check-deep: plugin-pin install\n" in made


def test_a_recipe_in_an_imported_file_is_left_to_an_agent_the_edit_cannot_reach(
    repo: Path,
) -> None:
    eval_app(repo)
    text = justfile(repo)
    moved = text[text.index("\nfmt-check:\n") + 1 : text.index("\nlint:\n") + 1]
    rest = text.replace(moved, "")
    (repo / "justfile").write_text(f'import "gates.just"\n\n{rest}', encoding="utf-8")
    (repo / "gates.just").write_text(moved, encoding="utf-8")
    [finding] = found(repo)
    assert isinstance(finding.fix, conformance.Agent)
    assert any("`fmt-check`" in reason for reason in finding.fix.then)


def test_no_ledger_entry_excuses_it_and_the_upgrade_writes_the_fix_instead(repo: Path) -> None:
    eval_app(repo)
    [finding] = found(repo)
    assert owed.never(finding.key())
    upgrade.converge(repo, {})
    assert "lint: install\n" in justfile(repo)
    assert found(repo) == []
    assert not owed.read(repo).entries


def test_a_recipe_installs_once_and_again_when_node_modules_or_the_lockfile_changes(
    tmp_path: Path,
) -> None:
    """The recipe the check demands, run by ``just``, with an ``npm`` that counts its calls."""
    just = shutil.which("just")
    if just is None:
        pytest.skip("just is not installed")
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    npm = bin_dir / "npm"
    npm.write_text(
        '#!/bin/sh\necho "$@" >> "$CALLS"\nrm -rf node_modules\nmkdir -p node_modules\n',
        encoding="utf-8",
    )
    npm.chmod(npm.stat().st_mode | stat.S_IEXEC)
    project = tmp_path / "app"
    project.mkdir()
    (project / "package-lock.json").write_text("{}\n", encoding="utf-8")
    (project / "justfile").write_text(
        "\n".join(installs.recipe_lines(installs.NPM)) + "\n\nlint: install\n    @echo linted\n",
        encoding="utf-8",
    )
    calls = tmp_path / "calls"
    env = {**os.environ, "PATH": f"{bin_dir}:{os.environ['PATH']}", "CALLS": str(calls)}

    def run() -> int:
        subprocess.run(  # noqa: S603 # -- a fixed argv in a project this test built
            [just, "lint"], cwd=project, env=env, capture_output=True, check=True
        )
        return len(calls.read_text(encoding="utf-8").splitlines())

    assert run() == 1
    assert run() == 1
    shutil.rmtree(project / "node_modules")
    assert run() == 2
    (project / "package-lock.json").write_text('{"changed": 1}\n', encoding="utf-8")
    assert run() == 3


@pytest.mark.parametrize(
    ("after", "line", "made"),
    [
        ("plugin-pin", "tier0: plugin-pin\n    x", "tier0: plugin-pin install\n    x"),
        ("plugin-pin", "tier0: plugin-pin g\n    x", "tier0: plugin-pin install g\n    x"),
        ("plugin-pin", "tier0: g\n    x", "tier0: install g\n    x"),
        ("plugin-pin", "tier0:\n    x", "tier0: install\n    x"),
        (None, "tier0: plugin-pin\n    x", "tier0: install plugin-pin\n    x"),
        ("plugin-pin", "tier0: plugin-pin install\n    x", "tier0: plugin-pin install\n    x"),
        ("pin", "tier0: plugin-pin\n    x", "tier0: install plugin-pin\n    x"),
    ],
)
def test_a_dependency_goes_right_after_the_one_it_follows(
    after: str | None, line: str, made: str
) -> None:
    assert justfile_text.with_dependency_after("tier0", "install", after)(line) == made
