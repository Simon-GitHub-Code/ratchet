"""The plugin's own behaviour: the optional manifest, the locked plugin group, the shim's root."""

import importlib.util
import json
from pathlib import Path
from types import ModuleType

import pytest

from ratchet_guards import hook
from ratchet_guards.manifest import (
    EMPTY,
    MANIFEST_PATH,
    Group,
    Scope,
    plugin_group,
    with_plugin_group,
)
from ratchet_guards.verdict import Decision, Verdict
from tests.support import HOME, MANIFEST, MANIFEST_TEXT, PLUGIN, PLUGIN_ROOT, make_guard, rules

W = "guard-path-write"


def checkout(tmp_path: Path, *, manifest: bool) -> Path:
    root = tmp_path / "repo"
    (root / ".git").mkdir(parents=True)
    if manifest:
        (root / "guards").mkdir()
        (root / MANIFEST_PATH).write_text(MANIFEST_TEXT, encoding="utf-8")
    return root


def edit(path: Path, cwd: Path) -> dict[str, object]:
    return {"tool_name": "Edit", "tool_input": {"file_path": str(path)}, "cwd": str(cwd)}


def bash(command: str, cwd: Path) -> dict[str, object]:
    return {"tool_name": "Bash", "tool_input": {"command": command}, "cwd": str(cwd)}


def test_without_a_manifest_only_the_command_rules_apply(tmp_path: Path) -> None:
    root = checkout(tmp_path, manifest=False)
    guard = hook.local_guard(root, plugin_root=tmp_path / "plugin")
    assert hook.decide(edit(root / "CLAUDE.md", root), guard) is None
    assert hook.decide(edit(root / MANIFEST_PATH, root), guard) is None
    assert hook.decide(bash("echo x > CLAUDE.md", root), guard) is None
    assert hook.decide(bash("rm -rf .github", root), guard) is None
    for command, rule in [
        ("git commit --no-verify -m x", "no-verify"),
        ("git push --force", "force-push"),
        ("git push origin main", "push-main"),
        ("git filter-repo --path x", "rewrite-history"),
        ("gh api -X DELETE repos/o/r/branches/main/protection", "branch-protection"),
        ("gh label create x", "labels"),
        ("gh repo edit --default-branch dev", "repo-settings"),
        ("claude --settings s.json -p hi", "nested-settings"),
        ("claude plugin disable ratchet", "plugin-removal"),
    ]:
        verdict = hook.decide(bash(command, root), guard)
        assert verdict is not None, command
        assert verdict.rule == rule
        assert verdict.decision is Decision.DENY
    merge = hook.decide(bash("gh pr merge 1", root), guard)
    assert merge is not None
    assert merge.decision is Decision.ASK


def test_with_a_manifest_the_path_rules_apply_too(tmp_path: Path) -> None:
    root = checkout(tmp_path, manifest=True)
    guard = hook.local_guard(root, plugin_root=tmp_path / "plugin")
    verdict = hook.decide(edit(root / "CLAUDE.md", root), guard)
    assert verdict is not None
    assert verdict.rule == "guard-path-edit"
    assert hook.decide(bash("echo x > CLAUDE.md", root), guard) == Verdict(
        W, "redirects into CLAUDE.md (decisions)"
    )


def test_the_manifest_is_read_from_the_nearest_checkout(tmp_path: Path) -> None:
    root = checkout(tmp_path, manifest=True)
    assert hook.repository_root(root / "app/src") == root
    assert hook.repository_root(tmp_path / "loose") == tmp_path / "loose"
    assert hook.read_manifest(root / "app").groups == MANIFEST.groups
    assert hook.read_manifest(tmp_path / "loose") is EMPTY
    guard = hook.local_guard(root / "app", plugin_root=tmp_path / "plugin")
    verdict = hook.decide(edit(root / "CLAUDE.md", root / "app"), guard)
    assert verdict is not None
    assert verdict.rule == "guard-path-edit"


def test_the_plugin_group_is_prepended_and_locked() -> None:
    group = plugin_group(PLUGIN)
    assert group == Group(
        "plugin",
        "The guard's own code, the installed plugins and the user settings that enable them: "
        "changing them can switch the guard off, so the owner changes them by hand.",
        (
            "/plugin",
            "/plugin/**",
            "~/.claude/plugins",
            "~/.claude/plugins/**",
            "~/.claude/settings.json",
            "~/.claude/settings.local.json",
        ),
        locked=True,
    )
    assert with_plugin_group(EMPTY, PLUGIN).groups == (group,)
    assert with_plugin_group(MANIFEST, PLUGIN).groups == (group, *MANIFEST.groups)
    assert group.patterns == tuple(
        pattern for pattern in group.patterns if Scope.REPOSITORY is not _scope(pattern)
    )


def _scope(pattern: str) -> Scope:
    return Scope.HOME if pattern.startswith("~/") else Scope.ABSOLUTE


@pytest.mark.parametrize(
    "path",
    [
        PLUGIN / "hooks/guard.py",
        PLUGIN / "hooks/ratchet_guards/hook.py",
        PLUGIN / "hooks/hooks.json",
        PLUGIN / ".claude-plugin/plugin.json",
        PLUGIN,
        HOME / ".claude/plugins/installed_plugins.json",
        HOME / ".claude/plugins/cache/ratchet/hooks/guard.py",
        HOME / ".claude/plugins",
        HOME / ".claude/settings.json",
        HOME / ".claude/settings.local.json",
    ],
)
def test_plugin_files_are_locked_with_or_without_a_manifest(path: Path) -> None:
    for manifest in (EMPTY, MANIFEST):
        guard = make_guard(manifest=manifest)
        verdict = hook.decide(edit(path, Path("/repo")), guard)
        assert verdict is not None, path
        assert verdict.rule == "guard-path-locked"
        assert "is in guard group 'plugin'" in verdict.detail


@pytest.mark.parametrize(
    "command",
    [
        "echo x > /plugin/hooks/guard.py",
        "sed -i s/deny/allow/ /plugin/hooks/ratchet_guards/verdict.py",
        "rm -rf /plugin",
        "rm -rf /plugin/hooks",
        "mv /plugin /tmp/elsewhere",
        "cp evil.py /plugin/hooks/guard.py",
        "rm -rf /",
        "echo x > ~/.claude/plugins/cache/ratchet/hooks/hooks.json",
        "rm -rf ~/.claude/plugins",
        "rm -rf $HOME/.claude",
        "cp x /home/agent/.claude/settings.json",
        "echo '{}' > /home/agent/.claude/settings.local.json",
        "cd /plugin && echo x > hooks/guard.py",
    ],
)
def test_shell_writes_to_plugin_files_are_denied(command: str) -> None:
    for manifest in (EMPTY, MANIFEST):
        assert rules(command, make_guard(manifest=manifest)) == [W], command


@pytest.mark.parametrize(
    "command",
    [
        "echo x > /plugin-sibling/hooks/guard.py",
        "echo x > /elsewhere/guard.py",
        "rm -rf /tmp/plugin",
        "cat /plugin/hooks/guard.py",
        "echo x > ~/.claude/notes.md",
        "echo x > CLAUDE.md",
    ],
)
def test_other_paths_are_free_without_a_manifest(command: str) -> None:
    assert rules(command, make_guard(manifest=EMPTY)) == []


def test_the_plugin_root_is_resolved_from_the_package() -> None:
    assert hook.PLUGIN_ROOT == PLUGIN_ROOT
    assert (hook.PLUGIN_ROOT / "hooks/ratchet_guards/__init__.py").is_file()
    assert (hook.PLUGIN_ROOT / "hooks/guard.py").is_file()
    assert (hook.PLUGIN_ROOT / "hooks/hooks.json").is_file()
    guard = hook.local_guard(PLUGIN_ROOT)
    verdict = hook.decide(edit(hook.PLUGIN_ROOT / "hooks/guard.py", PLUGIN_ROOT), guard)
    assert verdict is not None
    assert verdict.rule == "guard-path-locked"


def test_hooks_json_registers_the_shim_for_edits_and_bash() -> None:
    registered = json.loads((PLUGIN_ROOT / "hooks/hooks.json").read_text(encoding="utf-8"))
    [entry] = registered["hooks"]["PreToolUse"]
    assert entry["matcher"] == "Edit|Write|MultiEdit|NotebookEdit|Bash"
    [command] = entry["hooks"]
    assert command == {
        "type": "command",
        "command": 'python3 "${CLAUDE_PLUGIN_ROOT}/hooks/guard.py"',
        "timeout": 10,
    }


def _shim() -> ModuleType:
    spec = importlib.util.spec_from_file_location("guard_shim", PLUGIN_ROOT / "hooks/guard.py")
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_the_shim_resolves_the_root_from_cwd_then_the_environment() -> None:
    shim = _shim()
    project, default = {"CLAUDE_PROJECT_DIR": "/p"}, Path("/d")
    assert shim.root_of('{"cwd": "/x"}', project, default) == Path("/x")
    assert shim.root_of('{"cwd": ""}', project, default) == Path("/p")
    assert shim.root_of('{"cwd": 3}', project, default) == Path("/p")
    assert shim.root_of("{}", {}, default) == default
    assert shim.root_of("not json", {}, default) == default
    assert shim.root_of("[]", {}, default) == default
    assert shim.MINIMUM_PYTHON == (3, 13)
    assert shim.HOOKS == PLUGIN_ROOT / "hooks"
