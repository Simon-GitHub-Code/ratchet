"""Dependency-only changes to a guard path are told apart from gate changes."""

from pathlib import Path

import pytest

from ratchet_guards import dependencies, diff
from tests.repos import branch, commit, repository, write

PYPROJECT = """[project]
name = "app"
version = "0.1.0"
dependencies = ["httpx>=0.27"]

[dependency-groups]
dev = ["pytest>=8"]

[tool.ruff.lint]
select = ["ALL"]
"""

CARGO = """[package]
name = "splitbill"
version = "0.1.0"

[dependencies]
serde = "1"

[target.'cfg(unix)'.dependencies]
libc = "0.2"

[workspace.dependencies]
anyhow = "1"

[lints.clippy]
pedantic = "deny"
"""

PACKAGE = (
    '{"name": "shelf", "version": "1.0.0", "dependencies": {"lit": "^3"},'
    ' "scripts": {"test": "vitest"}}'
)

UV_LOCK = (
    '[[package]]\nname = "ruff"\nversion = "0.9.1"\n\n'
    '[[package]]\nname = "httpx"\nversion = "0.27.0"\n'
)


def test_a_new_python_dependency_and_a_version_bump_are_dependency_only() -> None:
    after = PYPROJECT.replace('"httpx>=0.27"', '"httpx>=0.27", "attrs>=24"')
    after = after.replace('dev = ["pytest>=8"]', 'dev = ["pytest>=8", "hypothesis>=6"]')
    after = after.replace('version = "0.1.0"', 'version = "0.2.0"')
    assert dependencies.changes("pyproject.toml", PYPROJECT, after) == [
        "attrs: (none) -> attrs>=24",
        "dev:hypothesis: (none) -> hypothesis>=6",
    ]


def test_a_gate_config_change_beside_a_dependency_is_a_guard_change() -> None:
    after = PYPROJECT.replace('"httpx>=0.27"', '"httpx>=0.27", "attrs>=24"')
    loosened = after.replace('select = ["ALL"]', 'select = ["E"]')
    assert dependencies.changes("pyproject.toml", PYPROJECT, loosened) is None
    sourced = after + '\n[tool.uv.sources]\nruff = { path = "../ruff" }\n'
    assert dependencies.changes("pyproject.toml", PYPROJECT, sourced) is None


def test_cargo_tables_target_and_workspace_dependencies() -> None:
    after = CARGO.replace('serde = "1"', 'serde = "1"\nproptest = "1"')
    after = after.replace('libc = "0.2"', 'libc = "0.3"').replace('anyhow = "1"', "")
    assert dependencies.changes("Cargo.toml", CARGO, after) == [
        'cfg(unix):dependencies:libc: "0.2" -> "0.3"',
        'dependencies:proptest: (none) -> "1"',
        'workspace:anyhow: "1" -> (none)',
    ]
    assert dependencies.changes("Cargo.toml", CARGO, CARGO.replace('"deny"', '"allow"')) is None


def test_package_json_dependencies_and_scripts() -> None:
    after = PACKAGE.replace('"lit": "^3"', '"lit": "^3", "idb": "^8"').replace("1.0.0", "1.1.0")
    assert dependencies.changes("web/package.json", PACKAGE, after) == [
        "dependencies:idb: (none) -> ^8"
    ]
    assert dependencies.changes("package.json", PACKAGE, PACKAGE.replace("vitest", "true")) is None


def test_lockfiles_report_versions_or_that_they_changed() -> None:
    downgraded = UV_LOCK.replace("0.9.1", "0.5.0")
    assert dependencies.changes("uv.lock", UV_LOCK, downgraded) == ["ruff: 0.9.1 -> 0.5.0"]
    assert dependencies.changes("pnpm-lock.yaml", "a: 1\n", "a: 2\n") == ["pnpm-lock.yaml: changed"]


def test_a_snapshot_is_judged_as_the_file_it_copies() -> None:
    path = "quality/snapshots/web__package.json"
    after = PACKAGE.replace('"lit": "^3"', '"lit": "^4"')
    assert dependencies.recognised(path)
    assert dependencies.changes(path, PACKAGE, after) == ["dependencies:lit: ^3 -> ^4"]
    assert not dependencies.recognised("quality/snapshots/ruff.toml")


@pytest.mark.parametrize(
    ("path", "before", "after"),
    [
        ("pyproject.toml", None, PYPROJECT),
        ("pyproject.toml", PYPROJECT, None),
        ("pyproject.toml", PYPROJECT, "broken ["),
        ("package.json", PACKAGE, "[1, 2]"),
        ("ruff.toml", "a = 1\n", "a = 2\n"),
    ],
)
def test_created_deleted_broken_or_unknown_files_stay_guard_changes(
    path: str, before: str | None, after: str | None
) -> None:
    assert dependencies.changes(path, before, after) is None


MANIFEST = """[[group]]
name = "tool-config"
reason = "r"
paths = ["pyproject.toml", "uv.lock"]

[[group]]
name = "ratchets"
reason = "r"
paths = ["quality/**"]
"""


def test_guard_diff_lists_dependency_changes_apart(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = repository(
        tmp_path,
        {
            "guards/manifest.toml": MANIFEST,
            "pyproject.toml": PYPROJECT,
            "uv.lock": UV_LOCK,
            "quality/snapshots/pyproject.toml": PYPROJECT,
        },
    )
    branch(root, "feature/2-attrs")
    added = PYPROJECT.replace('"httpx>=0.27"', '"httpx>=0.27", "attrs>=24"')
    write(root, "pyproject.toml", added)
    write(root, "quality/snapshots/pyproject.toml", added)
    write(root, "uv.lock", UV_LOCK + '\n[[package]]\nname = "attrs"\nversion = "24.2.0"\n')
    assert diff.main(["--root", str(root), "--base", "main", "--fail-if-present"]) == 0
    out = capsys.readouterr().out
    assert out.startswith("no guard path changed")
    assert "  pyproject.toml: attrs: (none) -> attrs>=24" in out
    assert "  uv.lock: attrs: (none) -> 24.2.0" in out
    write(root, "pyproject.toml", added.replace('["ALL"]', '["E"]'))
    commit(root, "feat(core): attrs")
    assert diff.main(["--root", str(root), "--base", "main", "--fail-if-present"]) == 1
    assert "tool-config\tpyproject.toml" in capsys.readouterr().out
