"""The hook: payload decisions, the JSON it prints, its real adapters and the shim script."""

import io
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from ratchet_guards import hook
from ratchet_guards.context import Guard
from ratchet_guards.manifest import MANIFEST_PATH, parse
from ratchet_guards.paths import Checkout
from ratchet_guards.verdict import Decision, Verdict
from tests.support import MANIFEST_TEXT, PLUGIN_ROOT, ROOT, FakeRepo, make_guard

SHIM = PLUGIN_ROOT / "hooks/guard.py"


def payload(tool: str, **tool_input: str) -> dict[str, object]:
    return {"tool_name": tool, "tool_input": tool_input, "cwd": str(ROOT), "session_id": "s"}


@pytest.mark.parametrize(
    ("tool", "key", "path"),
    [
        ("Edit", "file_path", "/repo/CLAUDE.md"),
        ("Write", "file_path", "config/lint.toml"),
        ("MultiEdit", "file_path", "/repo/.github/workflows/ci.yml"),
        ("NotebookEdit", "notebook_path", "/repo/formal/x.ipynb"),
    ],
)
def test_edits_to_guard_paths_ask(tool: str, key: str, path: str) -> None:
    verdict = hook.decide(payload(tool, **{key: path}), make_guard())
    assert verdict is not None
    assert verdict.rule == "guard-path-edit"


@pytest.mark.parametrize(
    "path",
    [
        "/repo/.claude/settings.json",
        "/repo/.claude/workflows/pipeline.js",
        "/repo/.claude/worktrees/agent-1/.claude/settings.json",
        "/repo/.claude/worktrees/agent-1/guards/manifest.toml",
        "/repo/guards/manifest.toml",
        "/repo/tools/guards/hook.py",
        "/repo/.git/hooks/pre-commit",
        "/home/agent/.claude/settings.json",
    ],
)
def test_edits_to_locked_guard_paths_are_denied(path: str) -> None:
    verdict = hook.decide(payload("Write", file_path=path), make_guard())
    assert verdict is not None
    assert verdict.rule == "guard-path-locked"
    assert verdict.decision is Decision.DENY


def test_the_edit_detail_names_group_and_reason() -> None:
    verdict = hook.decide(payload("Edit", file_path="/repo/justfile"), make_guard())
    assert verdict is not None
    assert verdict.detail == (
        "justfile is in guard group 'gate-commands'. The command contract and the scripts "
        "that run or score the gates: changing them changes what passing means."
    )


@pytest.mark.parametrize(
    "case",
    [
        payload("Edit", file_path="/repo/app/core/money.py"),
        payload("Write", file_path="/repo/docs/disputes/0001-x.md"),
        payload("Write", file_path="/repo/.claude/worktrees/agent-1/app/src/a.py"),
        payload("Edit", file_path="/repo/.claude/worktrees/agent-1/web/src/a.ts"),
        payload("Write", file_path="/repo/.claude/worktrees/stale/notes.md"),
        payload("Read", file_path="/repo/CLAUDE.md"),
        payload("Edit", file_path="/repo/tools"),
        payload("Bash", command="just check"),
    ],
)
def test_ordinary_calls_get_no_verdict(case: dict[str, object]) -> None:
    assert hook.decide(case, make_guard()) is None


def test_bash_paths_resolve_against_the_payload_cwd() -> None:
    verdict = hook.decide(payload("Bash", command="echo x > CLAUDE.md"), make_guard())
    assert verdict == Verdict("guard-path-write", "redirects into CLAUDE.md (decisions)")


def test_the_local_guard_reads_git(tmp_path: Path) -> None:
    assert isinstance(hook.local_guard(tmp_path).repo, hook.GitRepoState)


def test_bash_takes_the_strictest_verdict() -> None:
    verdict = hook.decide(payload("Bash", command="gh pr merge 1 && git push -f"), make_guard())
    assert verdict == Verdict("force-push", "git push -f")


@pytest.mark.parametrize(
    ("case", "detail"),
    [
        ([], "the hook input is not a JSON object"),
        (
            {"tool_name": "Bash", "tool_input": {}},
            "the hook input lacks tool_name, tool_input or cwd",
        ),
        (
            {"tool_name": 1, "tool_input": {}, "cwd": "/"},
            "the hook input lacks tool_name, tool_input or cwd",
        ),
        (
            {"tool_name": "Bash", "tool_input": [], "cwd": "/"},
            "the hook input lacks tool_name, tool_input or cwd",
        ),
        ({"tool_name": "Bash", "tool_input": {}, "cwd": "/"}, "Bash input has no command"),
        (
            {"tool_name": "Edit", "tool_input": {"file_path": 3}, "cwd": "/"},
            "Edit input has no file_path",
        ),
    ],
)
def test_malformed_payloads_ask(case: object, detail: str) -> None:
    assert hook.decide(case, make_guard()) == Verdict("unreadable", detail)


def test_reasons_and_output_shape() -> None:
    deny = json.loads(hook.respond(Verdict("labels", "gh label create x")))
    assert deny == {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": (
                "Ratchet guard denied this tool call; no part of it ran. It manages labels: "
                "gh label create x. Do not work around it. If you believe the gate is wrong, "
                "stop, write a gate dispute (docs/disputes/NNNN-<slug>.md) and report it to "
                "whoever started you. If the call also did unrelated work, redo that in a tool "
                "call of its own."
            ),
        }
    }
    ask = json.loads(hook.respond(Verdict("merge-pr", "gh pr merge")))["hookSpecificOutput"]
    assert ask["permissionDecision"] == "ask"
    assert ask["permissionDecisionReason"] == (
        "Ratchet guard: this tool call merges a pull request; the merge job or the owner "
        "merges one: gh pr merge. Approve only if you, the owner, intend this. An agent that "
        "thinks a gate is wrong writes a gate dispute (docs/disputes/NNNN-<slug>.md) instead."
    )
    assert hook.respond(None) == ""
    edit = hook.reason(Verdict("guard-path-edit", "CLAUDE.md is in guard group 'g'. Because."))
    assert edit.startswith(
        "Ratchet guard: this tool call edits a guard path; only the owner approves a "
        "gate change: CLAUDE.md is in guard group 'g'. Because. Guard paths come from "
        "guards/manifest.toml as committed on the default branch. Approve only if"
    )


def test_run_fails_closed() -> None:
    def broken() -> Guard:
        msg = "no manifest"
        raise OSError(msg)

    asked = json.loads(hook.run(json.dumps(payload("Bash", command="ls")), broken))
    assert asked["hookSpecificOutput"]["permissionDecision"] == "ask"
    assert "OSError: no manifest" in asked["hookSpecificOutput"]["permissionDecisionReason"]
    garbled = json.loads(hook.run("{not json", make_guard))
    assert "JSONDecodeError" in garbled["hookSpecificOutput"]["permissionDecisionReason"]
    assert hook.run(json.dumps(payload("Bash", command="ls")), make_guard) == ""


def checkout(tmp_path: Path, *, manifest: bool | str = True, name: str = "checkout") -> Path:
    """A repository on ``main`` under ``tmp_path``.

    The fixture manifest is committed by default; a string commits that text instead.
    """
    root = tmp_path / name
    root.mkdir(parents=True)
    _git(root, "init", "-q", "-b", "main")
    if manifest:
        (root / "guards").mkdir()
        text = manifest if isinstance(manifest, str) else MANIFEST_TEXT
        (root / MANIFEST_PATH).write_text(text, encoding="utf-8")
        _git(root, "add", "-A")
    _git(
        root,
        "-c",
        "user.name=t",
        "-c",
        "user.email=t@t",
        "commit",
        "-q",
        "--allow-empty",
        "-m",
        "x",
    )
    return root


def test_main_reads_stdin_and_writes_a_decision(tmp_path: Path) -> None:
    root = checkout(tmp_path)
    stdout = io.StringIO()
    case = {
        "tool_name": "Write",
        "tool_input": {"file_path": str(root / "CLAUDE.md")},
        "cwd": str(root),
    }
    assert hook.main(root, io.StringIO(json.dumps(case)), stdout) == 0
    assert json.loads(stdout.getvalue())["hookSpecificOutput"]["permissionDecision"] == "ask"
    quiet = io.StringIO()
    case["tool_input"] = {"file_path": str(root / "notes.md")}
    assert hook.main(root, io.StringIO(json.dumps(case)), quiet) == 0
    assert quiet.getvalue() == ""


def test_local_filesystem(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    filesystem = hook.LocalFilesystem()
    root = checkout(tmp_path)
    nested = root / "inner"
    nested.mkdir()
    _git(nested, "init", "-q", "-b", "main")
    (root / "a.toml").write_text("", encoding="utf-8")
    (root / ".hidden.toml").write_text("", encoding="utf-8")
    (root / "link").symlink_to(root / "a.toml")
    assert filesystem.home == Path.home()
    assert filesystem.canonical(root / "x/../link") == root / "a.toml"
    assert filesystem.guarded_checkout(root / "deep/file") == Checkout(root, parse(MANIFEST_TEXT))
    assert filesystem.guarded_checkout(nested / "file") is None
    assert filesystem.guarded_checkout(Path("/")) is None
    assert filesystem.glob(root / "*.toml") == [root / ".hidden.toml", root / "a.toml"]
    monkeypatch.setenv("GUARD_TEST_DIR", "value")
    assert filesystem.expand("$GUARD_TEST_DIR/x") == "value/x"
    assert filesystem.expand("~/x") == str(Path.home() / "x")


def _git(cwd: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(cwd), *args], check=True, capture_output=True)  # noqa: S603, S607 # -- fixed argv in a temporary repository


def test_git_repo_state(tmp_path: Path) -> None:
    state = hook.GitRepoState()
    assert state.current_branch(tmp_path) is None
    assert state.head_is_pushed(tmp_path) is None
    _git(tmp_path, "init", "-q", "-b", "trunk")
    _git(
        tmp_path,
        "-c",
        "user.name=t",
        "-c",
        "user.email=t@t",
        "commit",
        "-q",
        "--allow-empty",
        "-m",
        "x",
    )
    assert state.current_branch(tmp_path) == "trunk"
    assert state.head_is_pushed(tmp_path) is False
    assert state.full_name(tmp_path, "trunk") == "refs/heads/trunk"
    assert state.full_name(tmp_path, "HEAD") == "refs/heads/trunk"
    assert state.full_name(tmp_path, "nope") is None
    assert state.full_name(tmp_path, "trunk@{upstream}") is None
    _git(tmp_path, "tag", "trunk")
    assert state.current_branch(tmp_path) == "trunk"
    assert state.full_name(tmp_path, "trunk") is None
    _git(tmp_path, "tag", "-d", "trunk")
    _git(tmp_path, "update-ref", "refs/remotes/origin/trunk", "HEAD")
    assert state.head_is_pushed(tmp_path) is True
    _git(
        tmp_path,
        "-c",
        "user.name=t",
        "-c",
        "user.email=t@t",
        "commit",
        "-q",
        "--allow-empty",
        "-m",
        "y",
    )
    assert state.head_is_pushed(tmp_path) is False
    _git(tmp_path, "checkout", "-q", "--detach")
    assert state.current_branch(tmp_path) is None


def test_git_repo_state_survives_a_missing_git(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PATH", "")
    assert hook.GitRepoState().current_branch(Path.cwd()) is None


def run_shim(shim: Path, case: object, **env: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603 # -- the interpreter running these tests, on a fixed script
        [sys.executable, str(shim)],
        input=case if isinstance(case, str) else json.dumps(case),
        capture_output=True,
        text=True,
        check=False,
        env={**os.environ, "PYTHONPATH": "", **env},
    )


def decision(done: subprocess.CompletedProcess[str]) -> str:
    assert done.returncode == 0, done.stderr
    return str(json.loads(done.stdout)["hookSpecificOutput"]["permissionDecision"])


def test_the_shim_answers_from_the_checkout_named_by_cwd(tmp_path: Path) -> None:
    root = checkout(tmp_path)
    edit = {"tool_name": "Edit", "tool_input": {"file_path": "CLAUDE.md"}, "cwd": str(root)}
    assert decision(run_shim(SHIM, edit)) == "ask"
    locked = {"tool_name": "Edit", "tool_input": {"file_path": ".claude/settings.json"}}
    assert decision(run_shim(SHIM, {**locked, "cwd": str(root)})) == "deny"
    push = {"tool_name": "Bash", "tool_input": {"command": "git push --force"}, "cwd": str(root)}
    assert decision(run_shim(SHIM, push)) == "deny"
    status = {"tool_name": "Bash", "tool_input": {"command": "git status"}, "cwd": str(root)}
    assert run_shim(SHIM, status).stdout == ""


def test_the_shim_without_a_manifest_keeps_only_the_command_rules(tmp_path: Path) -> None:
    root = checkout(tmp_path, manifest=False)
    edit = {"tool_name": "Edit", "tool_input": {"file_path": "CLAUDE.md"}, "cwd": str(root)}
    assert run_shim(SHIM, edit).stdout == ""
    push = {"tool_name": "Bash", "tool_input": {"command": "git push --force"}, "cwd": str(root)}
    assert decision(run_shim(SHIM, push)) == "deny"
    own = {"tool_name": "Edit", "tool_input": {"file_path": str(SHIM)}, "cwd": str(root)}
    assert decision(run_shim(SHIM, own)) == "deny"


def test_the_shim_falls_back_to_the_project_directory_then_asks(tmp_path: Path) -> None:
    root = checkout(tmp_path)
    no_cwd = {"tool_name": "Edit", "tool_input": {"file_path": "CLAUDE.md"}}
    done = run_shim(SHIM, no_cwd, CLAUDE_PROJECT_DIR=str(root))
    assert decision(done) == "ask"
    assert "lacks tool_name, tool_input or cwd" in done.stdout


def test_the_shim_asks_when_the_guard_cannot_load(tmp_path: Path) -> None:
    shim = tmp_path / "a/hooks/guard.py"
    shim.parent.mkdir(parents=True)
    shutil.copy(SHIM, shim)
    done = run_shim(shim, {"tool_name": "Bash", "tool_input": {"command": "ls"}, "cwd": "/"})
    answer = json.loads(done.stdout)["hookSpecificOutput"]
    assert answer["permissionDecision"] == "ask"
    assert answer["permissionDecisionReason"].startswith("Ratchet guard could not run: ")


def test_the_shim_asks_on_garbled_input() -> None:
    done = run_shim(SHIM, "{not json")
    assert decision(done) == "ask"
    assert "JSONDecodeError" in done.stdout


def test_the_shim_is_an_executable_python3_script() -> None:
    assert os.access(SHIM, os.X_OK)
    assert SHIM.read_text(encoding="utf-8").startswith("#!/usr/bin/env python3\n")


def test_the_hook_reports_no_opinion_on_unknown_tools() -> None:
    assert hook.decide(payload("Glob", pattern="*"), make_guard(FakeRepo())) is None


def edit_from(session: Path, file: Path, tool: str = "Edit") -> dict[str, object]:
    """An edit of ``file`` by a session whose working directory is ``session``."""
    tool_input = {"file_path": str(file), "old_string": "a", "new_string": "b"}
    return {"tool_name": tool, "tool_input": tool_input, "cwd": str(session)}


def bash_from(session: Path, command: str) -> dict[str, object]:
    return {"tool_name": "Bash", "tool_input": {"command": command}, "cwd": str(session)}


def answer(root: Path, case: dict[str, object]) -> dict[str, str]:
    """The decision ``hook.main`` prints for a session rooted at ``root``, or ``{}``."""
    stdout = io.StringIO()
    assert hook.main(root, io.StringIO(json.dumps(case)), stdout) == 0
    return json.loads(stdout.getvalue())["hookSpecificOutput"] if stdout.getvalue() else {}


@pytest.fixture
def adopted(tmp_path: Path) -> Path:
    return checkout(tmp_path, name="adopted")


@pytest.fixture
def session(tmp_path: Path) -> Path:
    """A checkout with no committed manifest, where the agent's session is rooted."""
    return checkout(tmp_path, manifest=False, name="session")


def test_an_edit_is_judged_by_the_checkout_holding_the_file(adopted: Path, session: Path) -> None:
    asked = answer(session, edit_from(session, adopted / "justfile"))
    assert asked["permissionDecision"] == "ask"
    assert "justfile is in guard group 'gate-commands'" in asked["permissionDecisionReason"]
    locked = answer(session, edit_from(session, adopted / ".claude/settings.json", "Write"))
    assert locked["permissionDecision"] == "deny"
    assert answer(session, edit_from(session, adopted / "app/core/money.py")) == {}
    assert answer(session, edit_from(session, session / "justfile")) == {}


def test_an_edit_is_judged_when_the_session_is_in_no_checkout(
    adopted: Path, tmp_path: Path
) -> None:
    elsewhere = tmp_path / "not-a-repository"
    elsewhere.mkdir()
    asked = answer(elsewhere, edit_from(elsewhere, adopted / "quality/thresholds.toml"))
    assert asked["permissionDecision"] == "ask"


def test_a_file_reached_through_a_symlink_is_judged_where_it_lives(
    adopted: Path, session: Path
) -> None:
    (session / "link.toml").symlink_to(adopted / "quality/thresholds.toml")
    asked = answer(session, edit_from(session, session / "link.toml"))
    assert asked["permissionDecision"] == "ask"


def test_each_checkout_is_judged_by_its_own_manifest(tmp_path: Path, adopted: Path) -> None:
    own = (
        '[[group]]\nname = "notes"\nreason = "The notes are the owner\'s."\npaths = ["NOTES.md"]\n'
    )
    sibling = checkout(tmp_path, manifest=own, name="sibling")
    assert answer(adopted, edit_from(adopted, sibling / "NOTES.md"))["permissionDecision"] == "ask"
    assert answer(adopted, edit_from(adopted, sibling / "justfile")) == {}
    assert answer(sibling, edit_from(sibling, adopted / "justfile"))["permissionDecision"] == "ask"
    assert answer(sibling, edit_from(sibling, adopted / "NOTES.md")) == {}


def test_a_codex_patch_is_judged_by_the_checkout_it_names(adopted: Path, session: Path) -> None:
    patch = f"*** Begin Patch\n*** Update File: {adopted}/justfile\n@@\n-a\n+b\n*** End Patch\n"
    case = {"tool_name": "apply_patch", "tool_input": {"input": patch}, "cwd": str(session)}
    assert answer(session, case)["permissionDecision"] == "ask"


@pytest.mark.parametrize(
    "command",
    [
        "sed -i 's/a/b/' {adopted}/quality/thresholds.toml",
        "echo x > {adopted}/justfile",
        "cp /tmp/x {adopted}/scripts/gate.py",
        "cd {adopted} && sed -i s/a/b/ quality/thresholds.toml",
        "cd {adopted}/quality && sed -i s/a/b/ thresholds.toml",
        "cd {adopted} && cd quality && rm thresholds.toml",
        "git -C {adopted} rm quality/thresholds.toml",
        "python3 -c \"open('{adopted}/justfile', 'w').write('x')\"",
    ],
)
def test_a_shell_write_is_judged_by_the_checkout_it_lands_in(
    command: str, adopted: Path, session: Path
) -> None:
    denied = answer(session, bash_from(session, command.format(adopted=adopted)))
    assert denied["permissionDecision"] == "deny"
    assert "guards/manifest.toml as committed" in denied["permissionDecisionReason"]


def test_a_shell_write_outside_the_guard_paths_is_free_from_any_session(
    adopted: Path, session: Path
) -> None:
    for command in (f"sed -i s/a/b/ {adopted}/app/core/money.py", f"cd {adopted} && ls"):
        assert answer(session, bash_from(session, command)) == {}


def test_a_path_built_at_run_time_is_out_of_reach(adopted: Path, session: Path) -> None:
    command = f"D={adopted}; sed -i s/a/b/ $D/quality/thresholds.toml"
    assert answer(session, bash_from(session, command)) == {}


def test_a_manifest_that_does_not_parse_asks_for_a_path_in_its_checkout(
    tmp_path: Path, session: Path
) -> None:
    broken = checkout(tmp_path, manifest="[[group]]\nname = 1\n", name="broken")
    asked = answer(session, edit_from(session, broken / "justfile"))
    assert asked["permissionDecision"] == "ask"
    assert "ManifestError" in asked["permissionDecisionReason"]
    assert answer(session, edit_from(session, session / "justfile")) == {}


def test_the_session_manifest_is_still_read_when_it_does_not_parse(
    tmp_path: Path, adopted: Path
) -> None:
    broken = checkout(tmp_path, manifest="[[group]]\nname = 1\n", name="broken")
    asked = answer(broken, edit_from(broken, adopted / "app/core/money.py"))
    assert asked["permissionDecision"] == "ask"
    assert "ManifestError" in asked["permissionDecisionReason"]


def test_the_shim_judges_a_file_by_its_checkout_not_the_session_directory(
    adopted: Path, session: Path
) -> None:
    here = {**edit_from(adopted, adopted / "justfile")}
    assert decision(run_shim(SHIM, here)) == "ask"
    elsewhere = edit_from(session, adopted / "justfile")
    assert decision(run_shim(SHIM, elsewhere)) == "ask"
    sed = bash_from(session, f"sed -i s/a/b/ {adopted}/quality/thresholds.toml")
    assert decision(run_shim(SHIM, sed)) == "deny"
    free = edit_from(session, adopted / "app/core/money.py")
    assert run_shim(SHIM, free).stdout == ""


def test_the_manifest_of_a_checkout_is_read_from_git_once(
    adopted: Path, session: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    reads: list[Path] = []
    real = hook.committed_manifest

    def counting(root: Path) -> str | None:
        reads.append(root)
        return real(root)

    monkeypatch.setattr(hook, "committed_manifest", counting)
    guard = hook.local_guard(session)
    assert reads == [session]
    for name in ("justfile", "CLAUDE.md", "quality/a.toml", "app/core/money.py"):
        guard.locator.classify(adopted / name, destructive=False)
        guard.locator.classify(session / name, destructive=True)
    assert reads == [session, adopted]
    assert hook.LocalFilesystem().guarded_checkout(adopted / "x") is not None
    assert reads == [session, adopted, adopted]
