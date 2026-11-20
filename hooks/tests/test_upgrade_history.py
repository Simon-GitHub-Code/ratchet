import shutil
import subprocess
from collections.abc import Callable
from pathlib import Path

import pytest

import conformance
import gatelib
import owed
import upgrade
from tests import adopted, repos
from tests.support import PLUGIN_ROOT
from tests.test_conformance import pristine, replace

BINDING = "docs/process/binding.md"
MANIFEST = "guards/manifest.toml"
LINES = "- Model: `{model}`\n- Mirrors: `src/**`\n- Synced: {synced}\n"
SECTION = "\n## Formal verification\n\n" + LINES
GROUP = '\n[[group]]\nname = "formal"\nreason = "The model is the design."\npaths = ["formal/**"]\n'
PLACEHOLDERS = (
    "- Model: <globs of the models, their harness and the project-local skill, each in backticks>\n"
    "- Mirrors: <globs of the code the models describe, each in backticks>\n"
    "- Synced: <the default-branch commit the last round checked the model against>\n"
)
SELF_TEST_NONE = "| none: the self-test shares no report | none | none | none | none |"
SHARED = (
    "| suite | `just build` | `suite.xml` | `just coverage`, `just diff-coverage` |"
    " `just coverage` |"
)
NOT_FOUND = "which is no longer found"
ABSENT = "f" * 40


def lines(found: list[conformance.Finding]) -> list[str]:
    return sorted(str(finding) for finding in found)


def formal_entries(root: Path) -> list[owed.Entry]:
    return [entry for entry in owed.read(root).entries if entry.rule == "formal_verification"]


def residual(root: Path) -> tuple[list[str], list[str]]:
    red, owing = conformance.owed_split(root, conformance.findings(root))
    return lines(red), lines(owing)


def old_release(tmp_path: Path, synced: str | None = None) -> Path:
    root = adopted.copy(adopted.adopted("0.9.2"), tmp_path / "repo")
    repos.write(root, "src/a.txt", "a\n")
    repos.write(root, "formal/m.txt", "m\n")
    repos.write(root, MANIFEST, (root / MANIFEST).read_text(encoding="utf-8") + GROUP)
    held = repos.commit(root, "feat(core): the code and its model")
    section = SECTION.format(model="formal/**", synced=synced or held)
    repos.write(root, BINDING, (root / BINDING).read_text(encoding="utf-8") + section)
    repos.commit(root, "chore(method): bind the formal model")
    repos.branch(root, "upgrade")
    return root


def test_a_synced_commit_the_history_holds_is_never_owed_and_the_upgrade_leaves_it_green(
    tmp_path: Path,
) -> None:
    root = old_release(tmp_path)
    assert upgrade.main(["upgrade.py", "--root", str(root), "--apply"]) == 0
    assert formal_entries(root) == []
    red, owing = residual(root)
    assert red == []
    assert not any(NOT_FOUND in line for line in owing)


def test_a_synced_commit_the_clone_lacks_is_still_found_and_owed(tmp_path: Path) -> None:
    root = old_release(tmp_path, ABSENT)
    assert upgrade.main(["upgrade.py", "--root", str(root), "--apply"]) == 0
    [entry] = formal_entries(root)
    assert entry.problem.startswith(f"the '- Synced:' commit {ABSENT} is not in this clone")
    red, owing = residual(root)
    assert red == []
    assert any(f"the '- Synced:' commit {ABSENT} is not in this clone" in line for line in owing)


def conforming(tmp_path: Path) -> Path:
    root = repos.repository(tmp_path / "repo")
    shutil.copytree(pristine(), root, symlinks=True, dirs_exist_ok=True)
    repos.write(root, "src/a.txt", "a\n")
    repos.write(root, "formal/m.txt", "m\n")
    repos.write(root, "model/m.txt", "m\n")
    repos.commit(root, "chore(method): adopt the method")
    return root


def bound(root: Path, synced: str = "", model: str = "formal/**") -> None:
    held = synced or repos.git(root, "rev-parse", "HEAD")
    replace(BINDING, PLACEHOLDERS, LINES.format(model=model, synced=held))(root)


def synced_in_history(root: Path) -> None:
    bound(root)


def synced_absent(root: Path) -> None:
    bound(root, ABSENT)


def model_outside_the_default_branchs_group(root: Path) -> None:
    repos.git(root, "update-ref", "refs/remotes/origin/main", "HEAD")
    repos.git(root, "symbolic-ref", "refs/remotes/origin/HEAD", "refs/remotes/origin/main")
    bound(root, model="model/**")


def default_branch_only_in_the_reflog(root: Path) -> None:
    repos.git(root, "branch", "-m", "main", "trunk")


def report_ignored_only_by_the_clone(root: Path) -> None:
    replace(BINDING, SELF_TEST_NONE, SHARED)(root)
    exclude = Path(repos.git(root, "rev-parse", "--git-path", "info/exclude"))
    (root / exclude).write_text("suite.xml\n", encoding="utf-8")


DISAGREEING: dict[str, Callable[[Path], None]] = {
    "formal_synced": synced_in_history,
    "formal_synced, absent": synced_absent,
    "formal_group": model_outside_the_default_branchs_group,
    "default_branch": default_branch_only_in_the_reflog,
    "shared_reports": report_ignored_only_by_the_clone,
}


@pytest.mark.parametrize("make", DISAGREEING.values(), ids=DISAGREEING.keys())
def test_the_upgrade_owes_and_leaves_red_only_what_the_projects_own_conformance_finds(
    tmp_path: Path, make: Callable[[Path], None]
) -> None:
    root = conforming(tmp_path)
    make(root)
    repos.commit(root, "chore(method): the project as it stands")
    repos.branch(root, "upgrade")
    result = upgrade.plan(root, PLUGIN_ROOT)
    assert isinstance(result, upgrade.Plan), result
    upgrade.apply(root, result.changes)
    found = lines(conformance.findings(root))
    assert found == lines(result.agent + result.owner)
    assert not any(NOT_FOUND in line for line in found)
    repos.commit(root, "chore(method): upgrade")
    again = upgrade.plan(root, PLUGIN_ROOT)
    assert isinstance(again, upgrade.Plan), again
    assert again.changes == {}


def test_an_entry_an_earlier_upgrade_owed_that_the_project_does_not_find_is_dropped(
    tmp_path: Path,
) -> None:
    root = conforming(tmp_path)
    bound(root)
    synced = repos.git(root, "rev-parse", "HEAD")
    problem = (
        f"{BINDING}: the '- Synced:' commit {synced} is not in this clone; fetch it with"
        f" `git fetch origin {synced}`, or the full history with `git fetch --unshallow`"
    )
    wrong = owed.Entry(BINDING, "formal_verification", problem, "0.13.0")
    repos.write(root, str(owed.LEDGER), owed.render([wrong]))
    repos.commit(root, "chore(method): an upgrade that owed what is not found")
    assert any(NOT_FOUND in line for line in residual(root)[0])
    repos.branch(root, "upgrade")
    assert upgrade.main(["upgrade.py", "--root", str(root), "--apply"]) == 0
    assert formal_entries(root) == []
    assert residual(root) == ([], [])
    repos.commit(root, "chore(method): upgrade")
    again = upgrade.plan(root, PLUGIN_ROOT)
    assert isinstance(again, upgrade.Plan), again
    assert again.changes == {}


FORMAT = "--format=%(refname) %(objectname) %(symref)"


def git_run(root: Path, *args: str, feed: str | None = None) -> subprocess.CompletedProcess[str]:
    git = shutil.which("git")
    assert git
    return subprocess.run(  # noqa: S603 # -- a fixed argv against a test repository
        [git, "-C", str(root), *args], capture_output=True, text=True, input=feed, check=False
    )


def project(tmp_path: Path, backend: str) -> Path:
    root = conforming(tmp_path)
    bound(root)
    repos.commit(root, "chore(method): the project as it stands")
    repos.branch(root, "upgrade")
    if backend == "reftable":
        moved = git_run(root, "refs", "migrate", "--ref-format=reftable")
        if moved.returncode != 0:
            pytest.skip(f"this git cannot make a reftable repository: {moved.stderr!r}")
    return root


def hold(root: Path, backend: str, refs: dict[str, str]) -> None:
    if backend == "files":
        with (root / ".git/packed-refs").open("a", encoding="utf-8") as packed:
            packed.writelines(f"{oid} {ref}\n" for ref, oid in refs.items())
        return
    feed = "".join(f"create {ref} {oid}\n" for ref, oid in refs.items())
    assert git_run(root, "update-ref", "--stdin", feed=feed).returncode == 0


def head_of(root: Path) -> str:
    return repos.git(root, "rev-parse", "HEAD")


def a_long_name(root: Path, backend: str) -> None:
    hold(root, backend, {"refs/heads/" + "y" * 300: head_of(root)})


def many_refs(root: Path, backend: str, count: int = 2000) -> None:
    head = head_of(root)
    hold(root, backend, {f"refs/remotes/origin/r{n:05d}": head for n in range(count)})


def names_that_differ_in_case(root: Path, backend: str) -> None:
    head = head_of(root)
    hold(root, backend, {"refs/remotes/origin/Topic": head, "refs/remotes/origin/topic": head})


def hostile(root: Path, backend: str) -> None:
    head = head_of(root)
    repos.git(root, "tag", "-a", "-m", "t", "annotated", head)
    repos.git(root, "update-ref", "refs/tags/tree", repos.git(root, "rev-parse", "HEAD^{tree}"))
    repos.git(root, "update-ref", "refs/tags/blob", repos.git(root, "rev-parse", f"HEAD:{BINDING}"))
    repos.git(root, "update-ref", "refs/remotes/x/main", head)
    repos.git(root, "symbolic-ref", "refs/remotes/x/HEAD", "refs/remotes/x/main")
    repos.git(root, "symbolic-ref", "refs/remotes/y/HEAD", "refs/remotes/y/gone")
    if backend == "files":
        (root / ".git/refs/heads/broken").write_text("not a hash\n", encoding="utf-8")


REF_SETS: dict[str, Callable[[Path, str], None]] = {
    "a 300-byte name": a_long_name,
    "2,000 refs": many_refs,
    "names that differ only in case": names_that_differ_in_case,
    "symbolic, dangling, tag, tree, blob and broken refs": hostile,
}
BACKENDS = ["files", "reftable"]


def refs(root: Path) -> str:
    return git_run(root, "for-each-ref", FORMAT).stdout


def held(root: Path) -> dict[str, bytes]:
    store = root / ".git"
    return {str(path.relative_to(store)): path.read_bytes() for path in store.rglob("*") if path.is_file()}


def base_copy(root: Path, target: Path) -> None:
    gatelib.export(root, "HEAD", target)


COPIES: dict[str, Callable[[Path, Path], object]] = {
    "trial": conformance.copied,
    "base": base_copy,
}


@pytest.mark.parametrize("make", REF_SETS.values(), ids=REF_SETS.keys())
@pytest.mark.parametrize("backend", BACKENDS)
def test_the_plans_copy_holds_exactly_the_projects_refs_and_leaves_its_store_alone(
    tmp_path: Path, backend: str, make: Callable[[Path, str], None]
) -> None:
    root = project(tmp_path, backend)
    make(root, backend)
    before = held(root)
    for name, copy in COPIES.items():
        target = tmp_path / name / "tree"
        copy(root, target)
        assert refs(target) == refs(root), name
    assert upgrade.main(["upgrade.py", "--root", str(root), "--apply"]) == 0
    assert held(root) == before


def files_in_copies(tmp_path: Path, backend: str, count: int) -> dict[str, int]:
    root = project(tmp_path / str(count), backend)
    many_refs(root, backend, count)
    counted = {}
    for name, copy in COPIES.items():
        target = tmp_path / str(count) / name / "tree"
        copy(root, target)
        counted[name] = sum(1 for path in (target / ".git").rglob("*") if path.is_file())
    return counted


@pytest.mark.parametrize("backend", BACKENDS)
def test_the_files_a_copy_writes_do_not_grow_with_the_projects_refs(
    tmp_path: Path, backend: str
) -> None:
    few = files_in_copies(tmp_path, backend, 10)
    many = files_in_copies(tmp_path, backend, 2000)
    assert many == few


@pytest.mark.parametrize("ref", ["HEAD~1", "HEAD^{tree}"])
def test_export_takes_any_commit_or_tree_it_took_before(tmp_path: Path, ref: str) -> None:
    root = project(tmp_path, "files")
    repos.write(root, "src/a.txt", "changed\n")
    repos.commit(root, "feat(core): a later commit")
    target = tmp_path / "base" / "tree"
    gatelib.export(root, ref, target)
    assert (target / "src/a.txt").read_text(encoding="utf-8") == "a\n"
    commit = ["rev-parse", "--verify", "-q", f"{ref}^{{commit}}"]
    assert git_run(target, *commit[:-1], "HEAD^{commit}").stdout == git_run(root, *commit).stdout
