"""Dependency-only changes to a guard path are told apart from gate changes."""

import json
import re
from pathlib import Path

import pytest

from ratchet_guards import dependencies, diff
from tests.repos import branch, repository, write

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


def _shown(path: str, before: str, after: str) -> list[str]:
    return [str(change) for change in dependencies.changes(path, before, after)]


def _reason(path: str, before: str | None, after: str | None) -> str:
    with pytest.raises(dependencies.NotDependencyOnlyError) as raised:
        dependencies.changes(path, before, after)
    return str(raised.value)


def test_new_python_dependencies_and_a_version_bump_are_dependency_only() -> None:
    after = PYPROJECT.replace('"httpx>=0.27"', '"httpx>=0.27", "attrs>=24"')
    after = after.replace('dev = ["pytest>=8"]', 'dev = ["pytest>=8", "Hypothesis[cli]>=6"]')
    after = after.replace('version = "0.1.0"', 'version = "0.2.0"')
    assert _shown("pyproject.toml", PYPROJECT, after) == [
        "hypothesis (dependency-groups.dev) (none) -> Hypothesis[cli]>=6",
        "attrs (project.dependencies) (none) -> attrs>=24",
    ]


def test_a_gate_config_change_beside_a_dependency_is_not_dependency_only() -> None:
    after = PYPROJECT.replace('"httpx>=0.27"', '"httpx>=0.27", "attrs>=24"')
    loosened = after.replace('select = ["ALL"]', 'select = ["E"]')
    assert "more than the dependency tables" in _reason("pyproject.toml", PYPROJECT, loosened)
    sourced = after + '\n[tool.uv.sources]\nruff = { path = "../ruff" }\n'
    assert "more than the dependency tables" in _reason("pyproject.toml", PYPROJECT, sourced)


def test_cargo_additions_in_every_table() -> None:
    after = CARGO.replace(
        'serde = "1"', 'serde = "1"\nproptest = { version = "1", features = ["std"] }'
    )
    after = after.replace('libc = "0.2"', 'libc = "0.2"\nnix = "0.29"')
    assert _shown("Cargo.toml", CARGO, after) == [
        'proptest (dependencies) (none) -> {"features": ["std"], "version": "1"}',
        "nix (target.cfg(unix).dependencies) (none) -> 0.29",
    ]
    assert "more than" in _reason("Cargo.toml", CARGO, CARGO.replace('"deny"', '"allow"'))


def test_package_json_additions_and_scripts() -> None:
    after = PACKAGE.replace('"lit": "^3"', '"lit": "^3", "idb": "^8"').replace("1.0.0", "1.1.0")
    assert _shown("web/package.json", PACKAGE, after) == ["idb (dependencies) (none) -> ^8"]
    assert "more than" in _reason("package.json", PACKAGE, PACKAGE.replace("vitest", "true"))


@pytest.mark.parametrize(
    "case",
    [
        (
            "pyproject.toml",
            PYPROJECT,
            PYPROJECT.replace("httpx>=0.27", "httpx>=0.20"),
            "changes httpx",
        ),
        ("pyproject.toml", PYPROJECT, PYPROJECT.replace('"pytest>=8"', ""), "removes pytest"),
        (
            "pyproject.toml",
            PYPROJECT,
            PYPROJECT.replace('"httpx>=0.27"', '"httpx>=0.27", "HTTPX<0.1"'),
            "twice",
        ),
        (
            "pyproject.toml",
            PYPROJECT,
            PYPROJECT.replace('"pytest>=8"', '"pytest>=8", "x @ git+https://x/x"'),
            "not from the registry",
        ),
        (
            "pyproject.toml",
            PYPROJECT,
            PYPROJECT.replace('"pytest>=8"', '"pytest>=8", {include-group = "a"}'),
            "not a requirement",
        ),
        (
            "Cargo.toml",
            CARGO,
            CARGO.replace('serde = "1"', 'serde = "1"\nx = { git = "https://x/x" }'),
            "<git>",
        ),
        (
            "Cargo.toml",
            CARGO,
            CARGO.replace('serde = "1"', 'serde = "1"\nx = { path = "../x" }'),
            "<path>",
        ),
        (
            "Cargo.toml",
            CARGO,
            CARGO.replace('serde = "1"', 'serde = "1"\nx = { package = "y", version = "1" }'),
            "<package, version>",
        ),
        (
            "Cargo.toml",
            CARGO,
            CARGO.replace('serde = "1"', 'serde = "1"\nx = { registry = "r", version = "1" }'),
            "<registry, version>",
        ),
        (
            "Cargo.toml",
            CARGO,
            CARGO.replace('serde = "1"', 'serde = "1"\nserde_json = "1"\nserde-json = "1"'),
            "twice",
        ),
        ("Cargo.toml", CARGO, CARGO.replace('libc = "0.2"', 'libc = "0.1"'), "changes libc"),
        (
            "package.json",
            PACKAGE,
            PACKAGE.replace('"lit": "^3"', '"lit": "^3", "a": "npm:evil@1"'),
            "npm:evil@1",
        ),
        (
            "package.json",
            PACKAGE,
            PACKAGE.replace('"lit": "^3"', '"lit": "^3", "a": "me/a"'),
            "me/a",
        ),
        (
            "package.json",
            PACKAGE,
            PACKAGE.replace('"lit": "^3"', '"lit": "^3", "a": "git://x/a"'),
            "git://x/a",
        ),
        (
            "package.json",
            PACKAGE,
            PACKAGE.replace('"lit": "^3"', '"lit": "^3", "a": "../a"'),
            "../a",
        ),
        (
            "package.json",
            PACKAGE,
            PACKAGE.replace('"lit": "^3"', '"lit": "^3", "lit": "^1"'),
            "twice",
        ),
        ("pyproject.toml", None, PYPROJECT, "created or deleted"),
        ("pyproject.toml", PYPROJECT, None, "created or deleted"),
        ("pyproject.toml", PYPROJECT, "broken [", "does not parse"),
        ("package.json", PACKAGE, "[1, 2]", "does not parse"),
        ("ruff.toml", "a = 1\n", "a = 2\n", "ruff.toml is not read"),
        ("yarn.lock", "a: 1\n", "a: 2\n", "yarn.lock is not read"),
    ],
)
def test_manifest_changes_that_are_not_dependency_only_say_why(
    case: tuple[str, str | None, str | None, str],
) -> None:
    path, before, after, reason = case
    assert reason in _reason(path, before, after)


def _uv_lock(*packages: str) -> str:
    return "version = 1\n\n" + "\n".join(packages)


ROOT = (
    '[[package]]\nname = "app"\nversion = "0.1.0"\nsource = { editable = "." }\ndependencies = []\n'
)
PYPI = '{ registry = "https://pypi.org/simple" }'


def _pkg(name: str, version: str, *, source: str = PYPI, digest: str = "a") -> str:
    return (
        f'[[package]]\nname = "{name}"\nversion = "{version}"\nsource = {source}\n'
        f'sdist = {{ url = "https://x/{name}", hash = "sha256:{digest}" }}\n'
    )


UV_BEFORE = _uv_lock(ROOT, _pkg("ruff", "0.9.1"), _pkg("httpx", "0.27.0"))


def test_a_lockfile_reports_added_and_raised_versions() -> None:
    after = _uv_lock(
        ROOT.replace("[]", '[{ name = "attrs" }]'),
        _pkg("ruff", "0.10.0", digest="b"),
        _pkg("httpx", "0.27.0"),
        _pkg("attrs", "24.2.0"),
    )
    assert _shown("uv.lock", UV_BEFORE, after) == [
        "attrs (lockfile) (none) -> 24.2.0",
        "ruff (lockfile) 0.9.1 -> 0.10.0",
    ]


@pytest.mark.parametrize(
    ("after", "reason"),
    [
        (UV_BEFORE.replace('"0.9.1"', '"0.5.0"'), "ruff falls from 0.9.1 to 0.5.0"),
        (UV_BEFORE.replace('"0.9.1"', '"0.9.1rc1"'), "falls"),
        (
            _uv_lock(ROOT, _pkg("ruff", "0.9.1", digest="evil"), _pkg("httpx", "0.27.0")),
            "ruff 0.9.1 changed in place",
        ),
        (
            _uv_lock(
                ROOT,
                _pkg("ruff", "0.9.1", source='{ registry = "https://evil/simple" }'),
                _pkg("httpx", "0.27.0"),
            ),
            "changed in place",
        ),
        (
            _uv_lock(
                ROOT,
                _pkg("ruff", "0.9.2", source='{ registry = "https://evil/simple" }'),
                _pkg("httpx", "0.27.0"),
            ),
            "not a known registry",
        ),
        (
            _uv_lock(
                ROOT,
                _pkg("ruff", "0.9.2", source='{ git = "https://x/ruff" }'),
                _pkg("httpx", "0.27.0"),
            ),
            "not a known registry",
        ),
        (
            _uv_lock(
                ROOT,
                _pkg("ruff", "0.9.1", source='{ editable = "../ruff" }'),
                _pkg("httpx", "0.27.0"),
            ),
            "changed in place",
        ),
        (
            _uv_lock(ROOT, _pkg("Ruff", "0.5.0"), _pkg("ruff", "0.9.1"), _pkg("httpx", "0.27.0")),
            "ruff falls from 0.9.1 to 0.5.0",
        ),
        (
            _uv_lock(
                ROOT,
                _pkg("ruff", "0.9.1"),
                _pkg("ruff", "0.9.1", digest="b"),
                _pkg("httpx", "0.27.0"),
            ),
            "locked twice",
        ),
    ],
)
def test_a_lockfile_rollback_or_swap_is_not_dependency_only(after: str, reason: str) -> None:
    assert reason in _reason("uv.lock", UV_BEFORE, after)


def test_a_dropped_transitive_package_and_a_post_release_are_reported() -> None:
    after = _uv_lock(ROOT, _pkg("ruff", "0.9.1.post1"))
    assert _shown("uv.lock", UV_BEFORE, after) == [
        "httpx (lockfile) 0.27.0 -> (none)",
        "ruff (lockfile) 0.9.1 -> 0.9.1.post1",
    ]


CARGO_LOCK = (
    'version = 4\n\n[[package]]\nname = "splitbill"\nversion = "0.1.0"\n\n'
    '[[package]]\nname = "syn"\nversion = "2.0.1"\n'
    'source = "registry+https://github.com/rust-lang/crates.io-index"\nchecksum = "aa"\n'
)


def test_cargo_lock_keeps_two_major_versions_and_rejects_a_git_crate() -> None:
    older = CARGO_LOCK.replace('"2.0.1"', '"1.0.109"').replace('"aa"', '"bb"').split("\n\n", 2)[2]
    assert _shown("Cargo.lock", CARGO_LOCK, CARGO_LOCK + "\n" + older) == [
        "syn (lockfile) 2.0.1 -> 1.0.109, 2.0.1"
    ]
    git = CARGO_LOCK.replace(
        "registry+https://github.com/rust-lang/crates.io-index", "git+https://x/syn"
    )
    git = git.replace('"2.0.1"', '"2.0.2"')
    assert "not a known registry" in _reason("Cargo.lock", CARGO_LOCK, git)


NPM_LOCK = json.dumps(
    {
        "lockfileVersion": 3,
        "packages": {
            "": {"name": "shelf", "dependencies": {"lit": "^3"}},
            "node_modules/lit": {
                "version": "3.1.0",
                "resolved": "https://registry.npmjs.org/lit/-/lit-3.1.0.tgz",
                "integrity": "sha512-a",
            },
        },
    }
)


def test_package_lock_reads_the_packages_map() -> None:
    data = json.loads(NPM_LOCK)
    data["packages"][""]["dependencies"]["idb"] = "^8"
    data["packages"]["node_modules/idb"] = {
        "version": "8.0.0",
        "resolved": "https://registry.npmjs.org/idb/-/idb-8.0.0.tgz",
        "integrity": "sha512-b",
        "dev": True,
    }
    assert _shown("package-lock.json", NPM_LOCK, json.dumps(data)) == [
        "idb (lockfile) (none) -> 8.0.0"
    ]
    data["packages"]["node_modules/lit"]["resolved"] = "git+ssh://git@github.com/me/lit.git"
    assert "changed in place" in _reason("package-lock.json", NPM_LOCK, json.dumps(data))


def test_a_snapshot_is_judged_as_the_file_it_copies() -> None:
    path = "quality/snapshots/web__package.json"
    after = PACKAGE.replace('"lit": "^3"', '"lit": "^3", "idb": "^8"')
    assert dependencies.recognised(path)
    assert _shown(path, PACKAGE, after) == ["idb (dependencies) (none) -> ^8"]
    assert not dependencies.recognised("quality/snapshots/ruff.toml")


MANIFEST = """[[group]]
name = "tool-config"
reason = "r"
paths = ["pyproject.toml", "uv.lock"]

[[group]]
name = "ratchets"
reason = "r"
paths = ["quality/**"]
"""


def test_guard_diff_lists_and_counts_dependency_files_with_what_they_change(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = repository(
        tmp_path,
        {
            "guards/manifest.toml": MANIFEST,
            "pyproject.toml": PYPROJECT,
            "uv.lock": UV_BEFORE,
            "quality/snapshots/pyproject.toml": PYPROJECT,
            "quality/snapshots/yarn.lock": "a: 1\n",
        },
    )
    branch(root, "feature/2-attrs")
    added = PYPROJECT.replace('"httpx>=0.27"', '"httpx>=0.27", "attrs>=24"')
    write(root, "pyproject.toml", added)
    write(root, "quality/snapshots/pyproject.toml", added.replace('["ALL"]', '["E"]'))
    write(root, "quality/snapshots/yarn.lock", "a: 2\n")
    write(root, "uv.lock", UV_BEFORE + "\n" + _pkg("attrs", "24.2.0"))
    summary = tmp_path / "summary.md"
    argv = ["--root", str(root), "--base", "main", "--fail-if-present", "--summary", str(summary)]
    assert diff.main(argv) == 1
    assert capsys.readouterr().out.splitlines() == [
        "tool-config\tpyproject.toml",
        "  dependencies added: attrs (project.dependencies) (none) -> attrs>=24",
        "ratchets\tquality/snapshots/pyproject.toml",
        "  not dependency-only: it changes more than the dependency tables and the package version",
        "ratchets\tquality/snapshots/yarn.lock",
        "  not dependency-only: yarn.lock is not read",
        "tool-config\tuv.lock",
        "  lockfile: attrs (lockfile) (none) -> 24.2.0",
    ]
    assert "| `uv.lock` | tool-config | lockfile: attrs (lockfile) (none) -> 24.2.0 |" in (
        summary.read_text()
    )


def test_an_optional_dependency_group_is_read_like_the_others() -> None:
    before = PYPROJECT + '\n[project.optional-dependencies]\nweb = ["flask>=3"]\n'
    after = before.replace('"flask>=3"', '"flask>=3", "jinja2>=3"')
    assert _shown("pyproject.toml", before, after) == [
        "jinja2 (optional-dependencies.web) (none) -> jinja2>=3"
    ]
    assert "removes flask" in _reason("pyproject.toml", before, before.replace('"flask>=3"', ""))


def _poetry_pkg(name: str, version: str, source: str = "") -> str:
    return (
        f'[[package]]\nname = "{name}"\nversion = "{version}"\n'
        f'files = [{{file = "{name}-{version}.whl", hash = "sha256:a"}}]\n{source}'
    )


def test_poetry_lock_takes_the_default_index_and_a_legacy_index_but_not_a_vcs_source() -> None:
    before = _poetry_pkg("attrs", "24.1.0")
    legacy = '\n[package.source]\ntype = "legacy"\nurl = "https://pypi.org/simple"\n'
    after = "\n".join([before, _poetry_pkg("idna", "3.7", legacy)])
    assert _shown("poetry.lock", before, after) == ["idna (lockfile) (none) -> 3.7"]
    vcs = '\n[package.source]\ntype = "git"\nurl = "https://x/idna"\n'
    swapped = "\n".join([before, _poetry_pkg("idna", "3.7", vcs)])
    assert "idna 3.7 comes from" in _reason("poetry.lock", before, swapped)


def test_a_lock_entry_that_is_not_a_named_table_is_not_counted() -> None:
    odd = 'version = 1\npackage = [1, {version = "9"}]\n'
    assert _shown("uv.lock", odd, odd + "\n") == []


def test_package_lock_keeps_links_and_workspace_packages_as_local_paths() -> None:
    data = json.loads(NPM_LOCK)
    data["packages"]["node_modules/app"] = {"resolved": "packages/app", "link": True}
    data["packages"]["packages/app"] = {"version": "1.0.0"}
    before = json.dumps(data)
    data["packages"]["node_modules/lit"]["version"] = "3.2.0"
    data["packages"]["node_modules/lit"]["integrity"] = "sha512-b"
    assert _shown("package-lock.json", before, json.dumps(data)) == [
        "lit (lockfile) 3.1.0 -> 3.2.0"
    ]


def test_a_version_with_no_release_number_sorts_below_every_numbered_one() -> None:
    after = UV_BEFORE.replace('"0.9.1"', '"nightly"')
    assert "ruff falls from 0.9.1 to nightly" in _reason("uv.lock", UV_BEFORE, after)


def test_dropping_the_highest_of_two_side_by_side_versions_is_a_rollback() -> None:
    older = CARGO_LOCK.replace('"2.0.1"', '"1.0.109"').replace('"aa"', '"bb"').split("\n\n", 2)[2]
    both = CARGO_LOCK + "\n" + older
    assert "syn falls from 2.0.1 to 1.0.109" in _reason("Cargo.lock", both, older)


PNPM_LOCK = """lockfileVersion: '9.0'

settings:
  autoInstallPeers: true
  excludeLinksFromLockfile: false

importers:

  .:
    dependencies:
      lit:
        specifier: ^3.1.0
        version: 3.1.0
    devDependencies:
      '@types/node':
        specifier: ^20
        version: 20.11.0(typescript@5.4.0)

packages:

  '@lit-labs/ssr-dom-shim@1.2.0':
    resolution: {integrity: sha512-ssr}

  '@types/node@20.11.0':
    resolution: {integrity: sha512-node}

  lit@3.1.0:
    resolution: {integrity: sha512-lit}
    engines: {node: '>=18'}
    peerDependencies:
      react: '*'

snapshots:

  '@lit-labs/ssr-dom-shim@1.2.0': {}

  lit@3.1.0:
    dependencies:
      '@lit-labs/ssr-dom-shim': 1.2.0
"""


def test_pnpm_lock_reports_added_and_raised_versions() -> None:
    after = PNPM_LOCK.replace(
        "lit@3.1.0:\n    resolution: {integrity: sha512-lit}",
        "lit@3.2.0:\n    resolution: {integrity: sha512-new}",
    )
    after = after.replace("version: 3.1.0", "version: 3.2.0")
    after = after.replace(
        "packages:\n", "packages:\n\n  idb@8.0.0:\n    resolution: {integrity: sha512-idb}\n", 1
    )
    assert _shown("pnpm-lock.yaml", PNPM_LOCK, after) == [
        "idb (lockfile) (none) -> 8.0.0",
        "lit (lockfile) 3.1.0 -> 3.2.0",
    ]
    assert _shown("pnpm-lock.yaml", PNPM_LOCK, PNPM_LOCK) == []


@pytest.mark.parametrize(
    ("after", "reason"),
    [
        (PNPM_LOCK.replace("sha512-lit", "sha512-evil"), "lit 3.1.0 changed in place"),
        (
            PNPM_LOCK.replace(
                "lit@3.1.0:\n    resolution: {integrity: sha512-lit}",
                "lit@3.1.0:\n    resolution: {tarball: https://x.example/lit.tgz}",
            ),
            "changed in place",
        ),
        (
            PNPM_LOCK.replace("lit@3.1.0:\n    resolution", "lit@3.0.0:\n    resolution"),
            "lit falls from 3.1.0 to 3.0.0",
        ),
        (
            PNPM_LOCK.replace(
                "\nsnapshots:",
                "\n  evil@1.0.0:\n    resolution: {integrity: sha512-e, tarball: https://evil.example/e}\n"
                "\nsnapshots:",
            ),
            "evil 1.0.0 comes from https://evil.example",
        ),
        (
            PNPM_LOCK.replace(
                "lit@3.1.0:\n    resolution: {integrity: sha512-lit}",
                "lit@3.1.0:\n    resolution: {integrity: sha512-lit, tarball: file:../lit.tgz}",
            ),
            "changed in place",
        ),
        (
            PNPM_LOCK.replace(
                "lit@3.1.0:\n    resolution: {integrity: sha512-lit}",
                "'lit@https://x/lit.tgz':\n    resolution: {integrity: sha512-lit}",
            ),
            "not a name and version",
        ),
        (
            PNPM_LOCK.replace("version: 3.1.0", "version: github.com/x/lit/abc"),
            "pinned to github.com/x/lit/abc",
        ),
        (PNPM_LOCK.replace("        version: 3.1.0\n", ""), "pinned to nothing"),
        (PNPM_LOCK.replace("version: 3.1.0", "version: link:../lit"), "comes from"),
        (PNPM_LOCK + "\noverrides:\n  lit: 2.0.0\n", "only a pnpm v9"),
        (PNPM_LOCK.replace("'9.0'", "'6.0'"), "only a pnpm v9"),
        (PNPM_LOCK.replace("settings:\n", "settings:\n\tx: 1\n"), "not in the shape"),
        (PNPM_LOCK.replace("settings:\n", "settings:\n  a: 1\n  a: 2\n"), "key is given twice"),
        ("just text", "not in the shape"),
        ("- a\n", "not in the shape"),
        ("[]", "not in the shape"),
    ],
)
def test_a_pnpm_lockfile_that_swaps_or_cannot_be_read_is_not_dependency_only(
    after: str, reason: str
) -> None:
    assert reason in _reason("pnpm-lock.yaml", PNPM_LOCK, after)


def test_pnpm_lock_keeps_a_linked_workspace_package_and_a_registry_tarball() -> None:
    linked = PNPM_LOCK.replace("version: 3.1.0", "version: link:../lit", 1)
    assert _shown("pnpm-lock.yaml", linked, linked) == []
    private = PNPM_LOCK.replace("sha512-lit}", "sha512-lit, tarball: https://npm.example/lit.tgz}")
    assert _shown("pnpm-lock.yaml", private, private) == []
    more = private.replace(
        "\nsnapshots:",
        "\n  idb@8.0.0:\n    resolution: {integrity: sha512-i, tarball: https://npm.example/idb.tgz}\n"
        "\nsnapshots:",
    )
    assert _shown("pnpm-lock.yaml", private, more) == ["idb (lockfile) (none) -> 8.0.0"]


# pnpm 11 and later open the lockfile with a boot document, which locks the pnpm that runs and
# the config dependencies, then write the project's own after a second marker.
PNPM_BOOT = """---
lockfileVersion: '9.0'

importers:

  .:
    configDependencies: {}
    packageManagerDependencies:
      pnpm:
        specifier: 12.18.0
        version: 12.18.0

packages:

  pnpm@12.18.0:
    resolution: {integrity: sha512-pnpm}
    engines: {node: '>=18'}
    hasBin: true

snapshots:

  pnpm@12.18.0: {}

---
"""
PNPM_12_LOCK = PNPM_BOOT + PNPM_LOCK.replace(
    "      '@lit-labs/ssr-dom-shim': 1.2.0\n",
    "      '@lit-labs/ssr-dom-shim': 1.2.0\n    transitivePeerDependencies:\n      - react\n"
    "      - '@types/react'\n",
)
IDB = "packages:\n\n  idb@8.0.0:\n    resolution: {integrity: sha512-idb}\n"


def _boot(text: str, old: str, new: str) -> str:
    boot, marker, project = text.partition("\n---\n")
    return boot.replace(old, new) + marker + project


def test_a_pnpm_lockfile_with_a_boot_document_reads_both() -> None:
    assert _shown("pnpm-lock.yaml", PNPM_12_LOCK, PNPM_12_LOCK) == []
    added = PNPM_12_LOCK[::-1].replace("packages:\n"[::-1], IDB[::-1], 1)[::-1]
    assert _shown("pnpm-lock.yaml", PNPM_12_LOCK, added) == ["idb (lockfile) (none) -> 8.0.0"]
    raised = _boot(PNPM_12_LOCK, "12.18.0", "12.19.0")
    assert _shown("pnpm-lock.yaml", PNPM_12_LOCK, raised) == [
        "pnpm (boot document) 12.18.0 -> 12.19.0"
    ]
    assert _shown("pnpm-lock.yaml", PNPM_LOCK, PNPM_12_LOCK) == [
        "pnpm (boot document) (none) -> 12.18.0"
    ]


PNPM_IN_PROJECT = PNPM_12_LOCK.replace(
    "      lit:\n", "      pnpm:\n        specifier: 13.0.0\n        version: 13.0.0\n      lit:\n"
).replace(
    "\n  lit@3.1.0:\n",
    "\n  pnpm@13.0.0:\n    resolution: {integrity: sha512-p13}\n\n  lit@3.1.0:\n",
)
# Why: the boot document runs the one pnpm its pin names, so an older one locked beside the newest
# and pinned is a rollback, though the newest stays.
PNPM_BOOT_BESIDE = _boot(
    PNPM_12_LOCK,
    "        specifier: 12.18.0\n        version: 12.18.0\n",
    "        specifier: 12.17.0\n        version: 12.17.0\n",
).replace(
    "\nsnapshots:\n\n  pnpm@12.18.0: {}\n",
    "\n  pnpm@12.17.0:\n    resolution: {integrity: sha512-p17}\n"
    "\nsnapshots:\n\n  pnpm@12.17.0: {}\n\n  pnpm@12.18.0: {}\n",
    1,
)
# Why: YAML may write a mapping on one line; pnpm does not, and the reader would see no packages.
PNPM_BOOT_FLOW = (
    "---\nlockfileVersion: '9.0'\n\nimporters:\n\n  .:\n    configDependencies: {}\n"
    "    packageManagerDependencies: {pnpm: {specifier: 12.17.0, version: 12.17.0}}\n\n"
    "packages: {pnpm@12.17.0: {resolution: {integrity: sha512-p17}}}\n\n"
    "snapshots: {pnpm@12.17.0: {}}\n\n---\n" + PNPM_12_LOCK.partition("\n---\n")[2]
)
PNPM_PROJECT_FLOW = re.sub(
    r"\npackages:\n.*?\nsnapshots:",
    "\npackages: {lit@2.0.0: {resolution: {integrity: sha512-old}}}\n\nsnapshots:",
    PNPM_LOCK.replace("version: 3.1.0", "version: 2.0.0"),
    flags=re.DOTALL,
)


@pytest.mark.parametrize(
    ("before", "after", "reason"),
    [
        (PNPM_12_LOCK, _boot(PNPM_12_LOCK, "12.18.0", "12.17.0"), "pnpm falls from 12.18.0"),
        (PNPM_IN_PROJECT, _boot(PNPM_IN_PROJECT, "12.18.0", "12.17.0"), "pnpm falls"),
        (PNPM_12_LOCK, PNPM_BOOT_BESIDE, "pnpm falls from 12.18.0 to 12.17.0"),
        (PNPM_12_LOCK, PNPM_BOOT_FLOW, "not in the shape"),
        (
            PNPM_12_LOCK,
            PNPM_BOOT_FLOW.replace("  .:\n    configDependencies: {}\n", "  .: {}\n"),
            "not in the shape",
        ),
        (PNPM_LOCK, PNPM_PROJECT_FLOW, "not in the shape"),
        (PNPM_12_LOCK, _boot(PNPM_12_LOCK, "sha512-pnpm", "sha512-evil"), "changed in place"),
        (
            PNPM_12_LOCK,
            _boot(PNPM_12_LOCK, "sha512-pnpm}", "sha512-p, tarball: https://evil.example/p}"),
            "changed in place",
        ),
        (
            PNPM_12_LOCK,
            _boot(PNPM_12_LOCK, "    configDependencies: {}\n", "    dependencies: {}\n"),
            "its boot document locks dependencies",
        ),
        (
            PNPM_12_LOCK,
            _boot(PNPM_12_LOCK, "  .:\n    configDependencies: {}\n", "  .: x\n"),
            "not in the shape",
        ),
        (
            PNPM_12_LOCK,
            _boot(PNPM_12_LOCK, "\nsnapshots:", "\noverrides:\n  a: 1\nsnapshots:"),
            "v9",
        ),
        (PNPM_12_LOCK, PNPM_12_LOCK + "---\n" + PNPM_LOCK, "not in the shape"),
        (PNPM_12_LOCK, PNPM_LOCK + "---\n" + PNPM_LOCK, "not in the shape"),
        (PNPM_12_LOCK, "---\n" + PNPM_LOCK, "not in the shape"),
        (PNPM_12_LOCK, PNPM_12_LOCK.replace("\n---\n", "\n--- \n"), "not in the shape"),
        (
            PNPM_12_LOCK,
            PNPM_12_LOCK.replace(
                "      lit:\n        specifier", "      - lit\n      x:\n        s"
            ),
            "not in the shape",
        ),
        (
            PNPM_12_LOCK,
            PNPM_12_LOCK.replace("      - react\n", "      - react\n      evil: 1\n"),
            "not in the shape",
        ),
    ],
)
def test_a_pnpm_boot_document_is_held_to_the_same_rules(
    before: str, after: str, reason: str
) -> None:
    assert reason in _reason("pnpm-lock.yaml", before, after)
