import shutil
from collections.abc import Callable
from pathlib import Path

import pytest

import conformance
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
