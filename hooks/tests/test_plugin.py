"""The plugin's own behaviour: the optional manifest, the locked plugin group, the shim's root."""

import importlib.util
import json
import subprocess
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


def git(root: Path, *args: str) -> str:
    argv = ["git", "-C", str(root), "-c", "user.name=t", "-c", "user.email=t@x", *args]
    completed = subprocess.run(argv, capture_output=True, text=True, check=True)  # noqa: S603 # -- a fixed argv, no shell
    return completed.stdout.strip()


def checkout(tmp_path: Path, *, manifest: bool, state: str = "default") -> Path:
    """A real repository on ``main``; the manifest ``untracked``, on a ``branch`` or ``default``."""
    root = tmp_path / "repo"
    root.mkdir(parents=True)
    git(root, "init", "-q", "-b", "main")
    git(root, "commit", "-q", "--allow-empty", "-m", "root")
    if not manifest:
        return root
    if state == "branch":
        git(root, "switch", "-q", "-c", "feat/adopt")
    (root / "guards").mkdir()
    (root / MANIFEST_PATH).write_text(MANIFEST_TEXT, encoding="utf-8")
    if state != "untracked":
        git(root, "add", "-A")
        git(root, "commit", "-q", "-m", "adopt")
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
        ("grok plugin uninstall ratchet", "plugin-removal"),
        ("codex plugin remove ratchet@ratchet", "plugin-removal"),
        ("pi remove git:github.com/o/ratchet", "plugin-removal"),
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


@pytest.mark.parametrize("state", ["untracked", "branch"])
def test_a_manifest_not_on_the_default_branch_guards_nothing_yet(
    tmp_path: Path, state: str
) -> None:
    root = checkout(tmp_path, manifest=True, state=state)
    guard = hook.local_guard(root, plugin_root=tmp_path / "plugin")
    assert hook.decide(edit(root / "CLAUDE.md", root), guard) is None
    assert hook.decide(edit(root / MANIFEST_PATH, root), guard) is None
    assert hook.decide(bash("echo x > .claude/settings.json", root), guard) is None
    verdict = hook.decide(bash("git push --force", root), guard)
    assert verdict is not None
    assert verdict.rule == "force-push"


def test_the_committed_manifest_wins_over_the_work_tree(tmp_path: Path) -> None:
    root = checkout(tmp_path, manifest=True)
    (root / MANIFEST_PATH).write_text("", encoding="utf-8")
    guard = hook.local_guard(root, plugin_root=tmp_path / "plugin")
    verdict = hook.decide(edit(root / "CLAUDE.md", root), guard)
    assert verdict is not None
    assert verdict.rule == "guard-path-edit"
    assert hook.reason(verdict).endswith(
        "Guard paths come from guards/manifest.toml as committed on the default branch. "
        "Approve only if you, the owner, intend this. An agent that thinks a gate is wrong "
        "writes a gate dispute (docs/disputes/NNNN-<slug>.md) instead."
    )


def test_the_remote_default_branch_is_preferred(tmp_path: Path) -> None:
    root = checkout(tmp_path, manifest=True)
    upstream = tmp_path / "upstream.git"
    git(root, "init", "-q", "--bare", str(upstream))
    git(root, "remote", "add", "origin", str(upstream))
    git(root, "push", "-q", "origin", "main")
    git(root, "remote", "set-head", "origin", "main")
    git(root, "rm", "-q", "-r", "guards")
    git(root, "commit", "-q", "-m", "drop locally")
    guard = hook.local_guard(root, plugin_root=tmp_path / "plugin")
    assert hook.decide(edit(root / "CLAUDE.md", root), guard) is not None


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
            "~/.grok/installed-plugins",
            "~/.grok/installed-plugins/**",
            "~/.grok/hooks",
            "~/.grok/hooks/**",
            "~/.grok/config.toml",
            "~/.codex/plugins",
            "~/.codex/plugins/**",
            "~/.codex/hooks.json",
            "~/.codex/config.toml",
            "~/.pi/agent/extensions",
            "~/.pi/agent/extensions/**",
            "~/.pi/agent/settings.json",
            "~/.config/opencode/plugins",
            "~/.config/opencode/plugins/**",
            "~/.config/opencode/opencode.json",
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
        HOME / ".grok/installed-plugins/ratchet/hooks/guard.py",
        HOME / ".grok/hooks/herdr.json",
        HOME / ".grok/config.toml",
        HOME / ".codex/plugins/cache/ratchet/hooks/hooks.json",
        HOME / ".codex/hooks.json",
        HOME / ".codex/config.toml",
        HOME / ".pi/agent/extensions/ratchet-guard.ts",
        HOME / ".pi/agent/settings.json",
        HOME / ".config/opencode/plugins/ratchet-guard.ts",
        HOME / ".config/opencode/opencode.json",
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


@pytest.mark.parametrize("event", ["SessionStart", "SubagentStart"])
def test_hooks_json_registers_the_session_shim_for_sessions_and_subagents(event: str) -> None:
    registered = json.loads((PLUGIN_ROOT / "hooks/hooks.json").read_text(encoding="utf-8"))
    [entry] = registered["hooks"][event]
    assert "matcher" not in entry
    [command] = entry["hooks"]
    assert command == {
        "type": "command",
        "command": 'python3 "${CLAUDE_PLUGIN_ROOT}/hooks/session.py"',
        "timeout": 10,
    }


def test_hooks_json_registers_the_shim_for_edits_and_bash() -> None:
    registered = json.loads((PLUGIN_ROOT / "hooks/hooks.json").read_text(encoding="utf-8"))
    [entry] = registered["hooks"]["PreToolUse"]
    assert entry["matcher"] == (
        "Edit|Write|MultiEdit|NotebookEdit|Bash|apply_patch"
        "|run_terminal_command|search_replace|hashline_edit|write"
    )
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
    assert shim.MINIMUM_PYTHON == (3, 11)
    assert shim.HOOKS == PLUGIN_ROOT / "hooks"


PATCH = """*** Begin Patch
*** Update File: {path}
@@
-a
+b
*** End Patch
"""


def patch(text: str, cwd: Path) -> dict[str, object]:
    return {"tool_name": "apply_patch", "tool_input": {"input": text}, "cwd": str(cwd)}


def test_apply_patch_is_judged_by_the_paths_in_its_headers(tmp_path: Path) -> None:
    root = checkout(tmp_path, manifest=True)
    guard = hook.local_guard(root, plugin_root=tmp_path / "plugin")
    verdict = hook.decide(patch(PATCH.format(path="CLAUDE.md"), root), guard)
    assert verdict is not None
    assert verdict.rule == "guard-path-edit"
    assert "CLAUDE.md is in guard group 'decisions'" in verdict.detail
    assert hook.decide(patch(PATCH.format(path="app/main.py"), root), guard) is None
    deleted = "*** Begin Patch\n*** Delete File: quality/thresholds.toml\n*** End Patch\n"
    verdict = hook.decide(patch(deleted, root), guard)
    assert verdict is not None
    assert verdict.rule == "guard-path-edit"
    moved = "*** Begin Patch\n*** Update File: notes.md\n*** Move to: .claude/settings.json\n"
    verdict = hook.decide(patch(moved, root), guard)
    assert verdict is not None
    assert verdict.rule == "guard-path-locked"
    locked = hook.decide(patch(PATCH.format(path=".claude/settings.json"), root), guard)
    assert locked is not None
    assert locked.decision is Decision.DENY
    assert hook.decide(patch("no headers here", root), guard) is None
    assert (
        hook.decide({"tool_name": "apply_patch", "tool_input": {}, "cwd": str(root)}, guard) is None
    )


# Grok reports its own tool names (captured from Grok 1.0.77's PreToolUse input); the Cursor
# preset's tools name the file in `path`.
@pytest.mark.parametrize(
    ("tool", "tool_input", "rule"),
    [
        ("run_terminal_command", {"command": "git push --force"}, "force-push"),
        (
            "search_replace",
            {"file_path": "CLAUDE.md", "old_string": "a", "new_string": "b"},
            "guard-path-edit",
        ),
        ("hashline_edit", {"file_path": "CLAUDE.md"}, "guard-path-edit"),
        ("write", {"file_path": ".claude/settings.json", "content": "{}"}, "guard-path-locked"),
        ("Shell", {"command": "git push -f origin feat/work"}, "force-push"),
        ("StrReplace", {"path": "CLAUDE.md"}, "guard-path-edit"),
        ("Write", {"path": ".claude/settings.json", "contents": "{}"}, "guard-path-locked"),
    ],
)
def test_other_harness_tool_names_are_judged_as_claude_tools(
    tmp_path: Path, *, tool: str, tool_input: dict[str, object], rule: str
) -> None:
    root = checkout(tmp_path, manifest=True)
    guard = hook.local_guard(root, plugin_root=tmp_path / "plugin")
    verdict = hook.decide({"tool_name": tool, "tool_input": tool_input, "cwd": str(root)}, guard)
    assert verdict is not None
    assert verdict.rule == rule


@pytest.mark.parametrize(
    ("tool", "tool_input"),
    [
        ("run_terminal_command", {"command": "git status"}),
        ("search_replace", {"file_path": "app/main.py", "old_string": "a", "new_string": "b"}),
        ("write", {"file_path": "notes.md", "content": "x"}),
        ("read_file", {"target_file": "CLAUDE.md"}),
    ],
)
def test_other_harness_tool_names_pass_when_no_rule_fires(
    tmp_path: Path, tool: str, tool_input: dict[str, object]
) -> None:
    root = checkout(tmp_path, manifest=True)
    guard = hook.local_guard(root, plugin_root=tmp_path / "plugin")
    assert (
        hook.decide({"tool_name": tool, "tool_input": tool_input, "cwd": str(root)}, guard) is None
    )


def test_an_edit_without_any_path_key_is_unreadable(tmp_path: Path) -> None:
    root = checkout(tmp_path, manifest=True)
    guard = hook.local_guard(root, plugin_root=tmp_path / "plugin")
    verdict = hook.decide(
        {"tool_name": "write", "tool_input": {"content": "x"}, "cwd": str(root)}, guard
    )
    assert verdict is not None
    assert verdict.rule == "unreadable"
