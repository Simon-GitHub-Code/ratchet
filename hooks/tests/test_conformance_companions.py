"""The companion lines of AGENTS.md: each is the plugin's name alone, and the edit migrates them."""

import json
import shutil
from pathlib import Path

import pytest

import conformance
from tests.support import PLUGIN_ROOT
from tests.test_conformance import pristine

OLD = (
    "# P\n\n"
    "Four plugins build software here. `CLAUDE.md` is a symlink to this file.\n\n"
    "- **How is the work done?** `mattpocock-skills`: `tdd` for the loop, `pr` for the body.\n"
    "- **In what style?** `pstack`: `poteto-mode` routes a task.\n"
    "- **How is anything a person sees designed?** `impeccable`: every surface goes through it.\n"
    "- **What must be true before a merge?** `ratchet`: load the `ratchet:method` skill.\n\n"
    "## What this is\n\n"
    "- `pstack` is the only builder.\n"
    "- **Local rule** `impeccable` reviews the surfaces.\n"
)
NEW = (
    OLD.replace(OLD.splitlines()[4], "- `mattpocock-skills`")
    .replace(OLD.splitlines()[5], "- `pstack`")
    .replace(OLD.splitlines()[6], "- `impeccable`")
)


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    shutil.copytree(pristine(), tmp_path / "repo", symlinks=True)
    return tmp_path / "repo"


def agents(root: Path, text: str) -> None:
    (root / "AGENTS.md").write_text(text, encoding="utf-8")


def shape(root: Path) -> list[conformance.Finding]:
    return list(conformance.check_companion_lines(root))


def test_the_companions_are_the_marketplace_entrys_dependencies_but_ratchet() -> None:
    data = json.loads((PLUGIN_ROOT / ".claude-plugin/marketplace.json").read_text())
    [entry] = [plugin for plugin in data["plugins"] if plugin["name"] == "ratchet"]
    declared = tuple(dependency.split("@")[0] for dependency in entry["dependencies"])
    assert conformance.companions() == declared
    assert "ratchet" not in declared
    assert declared


def test_the_plain_form_is_green(repo: Path) -> None:
    agents(repo, NEW)
    assert shape(repo) == []


def test_an_old_form_line_is_red_and_the_edit_makes_it_green_changing_nothing_else(
    repo: Path,
) -> None:
    agents(repo, OLD)
    [found] = shape(repo)
    assert found.path == "AGENTS.md"
    assert found.problem == (
        "the lines for mattpocock-skills, pstack, impeccable are not exactly `- `<plugin>``"
    )
    assert isinstance(found.fix, conformance.Edit)
    assert found.fix.want(OLD) == NEW
    assert found.fix.want(NEW) == NEW


def test_the_edit_clears_the_finding_so_the_upgrade_makes_it_itself(repo: Path) -> None:
    agents(repo, OLD)
    [found] = [f for f in conformance.findings(repo) if "<plugin>" in f.problem]
    assert isinstance(found.fix, conformance.Auto)
    assert found.fix.want(OLD) == NEW


def test_the_ratchet_line_keeps_its_text_and_a_later_bullet_is_never_touched(repo: Path) -> None:
    agents(repo, OLD)
    [found] = shape(repo)
    assert isinstance(found.fix, conformance.Edit)
    edited = found.fix.want(OLD)
    assert "- **What must be true before a merge?** `ratchet`: load the `ratchet:method`" in edited
    assert edited.endswith(OLD.split("## What this is")[1])
    assert edited.count("\n") == OLD.count("\n")


def test_a_bold_name_is_recognised_and_rewritten_from_the_name_it_holds(repo: Path) -> None:
    old = "# P\n\n- **pstack**: styles.\r\n- **ratchet**: gates.\n\n## Next\n"
    agents(repo, old)
    [found] = shape(repo)
    assert isinstance(found.fix, conformance.Edit)
    assert found.fix.want(old) == "# P\n\n- `pstack`\r\n- **ratchet**: gates.\n\n## Next\n"


def test_a_project_bullet_naming_a_plugin_after_the_first_heading_is_not_a_finding(
    repo: Path,
) -> None:
    agents(repo, "# P\n\n- `ratchet:method` first.\n\n## Rules\n\n- `pstack`: kept as written.\n")
    assert shape(repo) == []
