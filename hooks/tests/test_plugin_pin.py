"""``plugin_pin``: the plugin a tier runs from is the commit and version the binding pins."""

from pathlib import Path

import pytest

import plugin_pin
from tests import repos

MANIFEST = ".claude-plugin/plugin.json"
ELSEWHERE = "f" * 40


def plugin(root: Path, version: str = "1.2.3") -> tuple[Path, str]:
    """A plugin checkout at ``root`` and the commit it is at."""
    repos.repository(root, {MANIFEST: f'{{"version": "{version}"}}'})
    return root, repos.git(root, "rev-parse", "HEAD")


def adopted(root: Path, line: str) -> Path:
    repos.write(root, "docs/process/binding.md", f"# Binding\n\n## Runner\n\njust\n\n{line}\n")
    return root


def run(root: Path, used: Path, capsys: pytest.CaptureFixture[str]) -> tuple[int, str]:
    code = plugin_pin.main(["plugin_pin.py", "--root", str(root), "--plugin", str(used)])
    return code, capsys.readouterr().out


def test_a_checkout_at_the_pinned_commit_and_version_is_green(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    used, commit = plugin(tmp_path / "plugin")
    root = adopted(tmp_path / "repo", f"- Ratchet plugin: 1.2.3 at {commit}")
    assert run(root, used, capsys) == (
        0,
        f"plugin-pin: 1.2.3 at {commit[:12]}, as docs/process/binding.md pins\nplugin-pin: green\n",
    )


def test_a_checkout_that_moved_on_is_red_and_says_both_ways_out(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    used, pinned = plugin(tmp_path / "plugin")
    root = adopted(tmp_path / "repo", f"- Ratchet plugin: 1.2.3 at {pinned}")
    moved = repos.commit(used, "feat(gate): a stricter gate")
    code, out = run(root, used, capsys)
    assert code == 1
    assert f"the plugin at {used} is 1.2.3 at {moved}" in out
    assert f"docs/process/binding.md pins 1.2.3 at {pinned}" in out
    assert "set RATCHET_PLUGIN to a checkout of the pinned commit" in out
    assert "every workflow `ref`" in out
    assert "the owner sees" in out
    assert out.endswith("plugin-pin: red\n")


def test_a_pin_whose_version_is_not_the_commits_is_red(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    used, commit = plugin(tmp_path / "plugin")
    root = adopted(tmp_path / "repo", f"- Ratchet plugin: 1.2.4 at {commit}")
    code, out = run(root, used, capsys)
    assert code == 1
    assert f"is 1.2.3 at {commit}; docs/process/binding.md pins 1.2.4 at {commit}" in out


def test_a_copy_with_no_history_is_judged_on_its_version_and_says_so(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    used = tmp_path / "cache/ratchet/1.2.3"
    repos.write(used, MANIFEST, '{"version": "1.2.3"}')
    root = adopted(tmp_path / "repo", f"- Ratchet plugin: 1.2.3 at {ELSEWHERE}")
    said = "this copy carries no commit, so only the version was compared"
    green = f"plugin-pin: 1.2.3, as docs/process/binding.md pins; {said}\nplugin-pin: green\n"
    assert run(root, used, capsys) == (0, green)
    adopted(root, f"- Ratchet plugin: 1.3.0 at {ELSEWHERE}")
    code, out = run(root, used, capsys)
    assert code == 1
    assert f"the plugin at {used} is 1.2.3; docs/process/binding.md pins 1.3.0 at" in out


@pytest.mark.parametrize("line", ["- Ratchet plugin: <X.Y.Z> at <ratchet-sha>", "", None])
def test_a_binding_that_pins_nothing_is_red_and_names_the_plugin_in_use(
    line: str | None, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    used, commit = plugin(tmp_path / "plugin")
    root = tmp_path / "repo" if line is None else adopted(tmp_path / "repo", line)
    code, out = run(root, used, capsys)
    assert code == 1
    assert "docs/process/binding.md: no `- Ratchet plugin: <version> at <commit>` line" in out
    assert f"this run uses 1.2.3 at {commit}" in out


def test_the_commit_is_the_plugins_own_inside_a_git_hook(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    # Why: git exports GIT_DIR to a hook, and `git -C` obeys it over the directory it is given.
    used, commit = plugin(tmp_path / "plugin")
    root = repos.repository(tmp_path / "repo")
    adopted(root, f"- Ratchet plugin: 1.2.3 at {commit}")
    monkeypatch.setenv("GIT_DIR", str(root / ".git"))
    assert run(root, used, capsys)[0] == 0


@pytest.mark.parametrize("manifest", [None, "not json", "[]", '{"version": 3}'])
def test_a_plugin_whose_version_cannot_be_read_is_red(
    manifest: str | None, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    used = tmp_path / "plugin"
    used.mkdir()
    if manifest is not None:
        repos.write(used, MANIFEST, manifest)
    root = adopted(tmp_path / "repo", f"- Ratchet plugin: 1.2.3 at {ELSEWHERE}")
    code, out = run(root, used, capsys)
    assert code == 1
    assert f"the plugin at {used} is an unknown version; " in out


def test_a_checkout_git_cannot_read_is_red_not_judged_on_its_version(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    used = tmp_path / "plugin"
    repos.write(used, MANIFEST, '{"version": "1.2.3"}')
    (used / ".git").mkdir()
    root = adopted(tmp_path / "repo", f"- Ratchet plugin: 1.2.3 at {ELSEWHERE}")
    code, out = run(root, used, capsys)
    assert code == 1
    assert "is 1.2.3 at a commit git cannot read; " in out


def test_the_plugin_judged_is_the_one_the_script_runs_from(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    version = plugin_pin.version_of(plugin_pin.PLUGIN)
    commit = plugin_pin.commit_of(plugin_pin.PLUGIN) or ELSEWHERE
    adopted(tmp_path, f"- Ratchet plugin: {version} at {commit}")
    monkeypatch.chdir(tmp_path)
    assert plugin_pin.main(["plugin_pin.py"]) == 0
    assert capsys.readouterr().out.endswith("plugin-pin: green\n")
