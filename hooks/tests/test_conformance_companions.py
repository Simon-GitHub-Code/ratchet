"""The companion lines of AGENTS.md: each is the plugin's name alone, and the edit migrates them."""

import json
import shutil
from pathlib import Path

import pytest

import conformance
import upgrade
from tests.repos import skeleton, snapshot
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
SECTION = "## Moved by the Ratchet upgrade"
PLAIN = (
    OLD.replace(OLD.splitlines()[4], "- `mattpocock-skills`")
    .replace(OLD.splitlines()[5], "- `pstack`")
    .replace(OLD.splitlines()[6], "- `impeccable`")
)
MOVED = OLD.splitlines()[4:7]
NEW = PLAIN + "\n" + SECTION + "\n\n" + "\n".join(MOVED) + "\n"


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
    agents(repo, PLAIN)
    assert shape(repo) == []


def test_an_old_form_line_is_red_and_the_edit_makes_it_green_changing_nothing_else(
    repo: Path,
) -> None:
    agents(repo, OLD)
    [found] = shape(repo)
    assert found.path == "AGENTS.md"
    assert found.problem.startswith(
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
    assert OLD.split("## What this is")[1] in edited
    assert edited.count("\n") == OLD.count("\n") + 2 + len(MOVED) + 1


def test_a_bold_name_is_recognised_and_rewritten_from_the_name_it_holds(repo: Path) -> None:
    old = "# P\n\n- **pstack**: styles.\r\n- **ratchet**: gates.\n\n## Next\n"
    agents(repo, old)
    [found] = shape(repo)
    assert isinstance(found.fix, conformance.Edit)
    assert found.fix.want(old) == (
        "# P\n\n- `pstack`\r\n- **ratchet**: gates.\n\n## Next\n\n"
        f"{SECTION}\n\n- **pstack**: styles.\n"
    )


def test_a_project_bullet_naming_a_plugin_after_the_first_heading_is_not_a_finding(
    repo: Path,
) -> None:
    agents(repo, "# P\n\n- `ratchet:method` first.\n\n## Rules\n\n- `pstack`: kept as written.\n")
    assert shape(repo) == []


def fix_of(root: Path, text: str) -> conformance.Edit:
    agents(root, text)
    [found] = shape(root)
    assert isinstance(found.fix, conformance.Edit)
    return found.fix


def test_the_rules_on_the_lines_move_word_for_word_to_the_end_of_the_file(repo: Path) -> None:
    edited = fix_of(repo, OLD).want(OLD)
    assert edited == NEW
    assert edited.endswith("\n".join(MOVED) + "\n")


def test_the_finding_says_the_project_s_text_moves_and_is_not_deleted(repo: Path) -> None:
    agents(repo, OLD)
    [found] = shape(repo)
    assert "moves to '## Moved by the Ratchet upgrade' and is not deleted" in found.problem


def test_an_existing_section_is_appended_to_and_not_made_twice(repo: Path) -> None:
    old = f"# P\n\n- **pstack**: styles.\n\n{SECTION}\n\n- an earlier rule\n\n## Next\n\n- text\n"
    edited = fix_of(repo, old).want(old)
    assert edited.count(SECTION) == 1
    assert edited == (
        f"# P\n\n- `pstack`\n\n{SECTION}\n\n- an earlier rule\n- **pstack**: styles.\n\n"
        "## Next\n\n- text\n"
    )


def test_the_fix_changes_nothing_when_run_again(repo: Path) -> None:
    fix = fix_of(repo, OLD)
    once = fix.want(OLD)
    assert fix.want(once) == once
    agents(repo, once)
    assert shape(repo) == []


def test_a_file_with_plain_lines_is_left_as_it_is() -> None:
    assert conformance.with_plain_companions(conformance.companions())(PLAIN) == PLAIN


def test_the_upgrade_report_names_the_file_and_lists_the_lines_it_moved(repo: Path) -> None:
    agents(repo, OLD)
    shutil.copytree(skeleton(), repo / ".git", symlinks=True)
    snapshot(repo, "feat(core): the old companion lines")
    result = upgrade.plan(repo, PLUGIN_ROOT)
    assert isinstance(result, upgrade.Plan)
    report = upgrade.render(repo, result, applied=False)
    assert "  AGENTS.md\n" in report
    assert f"    - moved to '{SECTION}' for the owner to read:" in report
    for line in MOVED:
        assert f"      {line}\n" in report


WRAPPED = (
    "- **In what style?** `pstack`: `poteto-mode` routes a task,\n  and the owner reads its plan.\n"
)
NESTED = (
    "- **How is it designed?** `impeccable`: two rules.\n"
    "  - every surface goes through it\n"
    "  - no screen ships unreviewed\n"
)
AFTER = (
    "- **What must be true before a merge?** `ratchet`: load the `ratchet:method` skill.\n"
    "\n## Next\n"
)


@pytest.mark.parametrize(
    ("items", "newline"),
    [((WRAPPED,), "\n"), ((NESTED,), "\n"), ((WRAPPED, NESTED), "\r\n")],
    ids=["wrapped", "nested", "both-crlf"],
)
def test_an_item_that_wraps_or_nests_moves_whole_and_in_order(
    repo: Path, items: tuple[str, ...], newline: str
) -> None:
    old = ("# P\n\n" + "".join(items) + AFTER).replace("\n", newline)
    moved = [line for item in items for line in item.splitlines()]
    plain = [f"- `{'pstack' if item is WRAPPED else 'impeccable'}`" for item in items]
    want = newline.join(["# P", "", *plain, *AFTER.splitlines(), "", SECTION, "", *moved, ""])
    fix = fix_of(repo, old)
    edited = fix.want(old)
    assert edited == want
    assert fix.want(edited) == edited
    agents(repo, edited)
    assert shape(repo) == []


def test_a_plain_bullet_with_lines_beneath_it_is_red(repo: Path) -> None:
    agents(repo, "# P\n\n- `pstack`\n  routes a task.\n\n## Next\n")
    [found] = shape(repo)
    assert found.problem.startswith("the lines for pstack are not exactly")


def test_a_blank_line_then_text_ends_the_item(repo: Path) -> None:
    old = "# P\n\n- **x** `pstack`: styles.\n\nFour plugins build here.\n"
    edited = fix_of(repo, old).want(old)
    assert edited == (
        f"# P\n\n- `pstack`\n\nFour plugins build here.\n\n{SECTION}\n\n- **x** `pstack`: styles.\n"
    )


@pytest.mark.parametrize(
    "item", ["- **x** `pstack`: styles.", "- **x** `pstack`: styles,\n  wrapped."]
)
def test_an_item_that_ends_a_file_with_no_final_newline_moves_whole(repo: Path, item: str) -> None:
    old = f"# P\n\n{item}"
    edited = fix_of(repo, old).want(old)
    assert edited == f"# P\n\n- `pstack`\n\n{SECTION}\n\n{item}\n"
