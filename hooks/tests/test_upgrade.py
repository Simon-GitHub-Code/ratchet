"""``upgrade``: what this plugin version needs that an adopted repository lacks, file by file."""

import shutil
from pathlib import Path

import pytest

import plugin_pin
import upgrade
from tests.test_conformance import BINDING, pristine, replace

PIN = f"- Ratchet plugin: 0.0.0 at {'0' * 40}"


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    shutil.copytree(pristine(), tmp_path / "repo", symlinks=True)
    return tmp_path / "repo"


@pytest.fixture
def plugin(tmp_path: Path) -> Path:
    """A plugin copy with no history at version 0.0.0: the pristine repository's pin."""
    (tmp_path / "plugin/.claude-plugin").mkdir(parents=True)
    (tmp_path / "plugin/.claude-plugin/plugin.json").write_text('{"version": "0.0.0"}')
    return tmp_path / "plugin"


def report(repo: Path, plugin: Path, capsys: pytest.CaptureFixture[str]) -> tuple[int, str]:
    code = upgrade.main(["upgrade.py", str(repo), "--plugin", str(plugin)])
    return code, capsys.readouterr().out


def test_a_repository_with_nothing_missing_says_so(
    repo: Path, plugin: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    code, out = report(repo, plugin, capsys)
    assert (code, out) == (0, "upgrade: nothing is missing for the Ratchet plugin 0.0.0\n")


def test_each_missing_item_is_printed_under_its_file_with_who_edits_it(
    repo: Path, plugin: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    replace(BINDING, "| tier0-tree | `just tier0-tree` |\n", "")(repo)
    replace("justfile", "hooks-install:\n", "hooks-installs:\n")(repo)
    replace("AGENTS.md", "## Asking the owner", "## Asking")(repo)
    code, out = report(repo, plugin, capsys)
    assert code == 1
    blocks = out.split("\n\n")
    assert blocks[0] == f"upgrade: {repo} lacks what the Ratchet plugin 0.0.0 needs"
    assert blocks[2].splitlines()[:3] == [
        "AGENTS.md: a guard path (group `decisions`): an agent's edit asks the owner",
        "  - has no '## Asking the owner' section; copy it from the template AGENTS.md",
        "      ## Asking the owner",
    ]
    assert blocks[1].splitlines() == [
        f"{BINDING}: a guard path (group `decisions`): an agent's edit asks the owner",
        (
            "  - the Entry points table has no row for 'tier0-tree'; add the template's row"
            " | tier0-tree | `just tier0-tree` |, with your runner's command"
        ),
        "      | tier0-tree | `just tier0-tree` |",
    ]
    assert blocks[-2].splitlines()[0] == (
        "justfile: a guard path (group `gate-commands`): an agent's edit asks the owner"
    )
    assert "      hooks-install:" in blocks[-2].splitlines()
    assert blocks[-1].startswith(
        "upgrade: 3 items in 3 files, 3 of them guard paths. This script changed nothing:"
    )


def test_the_pin_comes_first_when_the_plugin_in_use_is_another(
    repo: Path, plugin: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    (plugin / ".claude-plugin/plugin.json").write_text('{"version": "0.1.0"}')
    replace("AGENTS.md", "## Asking the owner", "## Asking")(repo)
    code, out = report(repo, plugin, capsys)
    assert code == 1
    first = out.split("\n\n")[1].splitlines()
    assert first[0].startswith(f"{BINDING}: ")
    assert first[1] == (
        f"  - pins 0.0.0 at {'0' * 40}, and the plugin in use is 0.1.0; replace the pin line"
        " with the one below, and every workflow `ref` that checks the plugin out with its commit"
    )
    assert first[2] == "      - Ratchet plugin: 0.1.0 at <the full commit of 0.1.0>"


def test_who_edits_a_file_follows_the_manifest(repo: Path) -> None:
    assert upgrade.editor(repo, ".claude/settings.json") == (
        "the owner edits it by hand: its guard group `harness` denies agents"
    )
    assert upgrade.editor(repo, "docs/adr") == (
        "a record (group `records`): an addition passes, a change asks the owner"
    )
    assert upgrade.editor(repo, "docs/features") == "not a guard path: an agent edits it"
    assert upgrade.editor(repo, "AGENTS.md (or CLAUDE.md)").startswith("a guard path")
    (repo / "guards/manifest.toml").write_text("not = [toml")
    assert upgrade.editor(repo, "justfile") == "guards/manifest.toml does not say who edits it"
    (repo / "guards/manifest.toml").unlink()
    assert upgrade.editor(repo, "justfile") == "guards/manifest.toml does not say who edits it"


def test_a_repository_with_no_pin_line_is_told_by_conformance_alone(
    repo: Path, plugin: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    replace(BINDING, PIN, "")(repo)
    code, out = report(repo, plugin, capsys)
    assert code == 1
    assert out.count("Ratchet plugin:") == 3, out
    assert plugin_pin.line_for(plugin_pin.PLUGIN) in out


def test_without_arguments_it_reads_the_working_directory(
    repo: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.chdir(repo)
    assert upgrade.main(["upgrade.py"]) in (0, 1)
    assert capsys.readouterr().out.startswith("upgrade: ")
