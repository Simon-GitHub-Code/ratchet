import io
import json
import os
import shutil
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest

import adapter
from ratchet_guards import bash, events, git, hook, tickets
from ratchet_guards.context import Guard
from ratchet_guards.manifest import COMMITTED, EMPTY, MANIFEST_PATH, parse
from ratchet_guards.paths import Checkout
from ratchet_guards.verdict import RULES, Decision, Verdict
from tests.support import MANIFEST_TEXT, PLUGIN_ROOT, ROOT, FakeRepo, make_guard

SHIM = PLUGIN_ROOT / "hooks/guard.py"


def on_main() -> Guard:
    return make_guard(FakeRepo(branch="main"))


def payload(tool: str, **tool_input: str) -> dict[str, object]:
    return {"tool_name": tool, "tool_input": tool_input, "cwd": str(ROOT), "session_id": "s"}


UNLOCKED_EDITS = [
    ("Edit", "file_path", "/repo/justfile"),
    ("Write", "file_path", "config/lint.toml"),
    ("MultiEdit", "file_path", "/repo/.github/dependabot.yml"),
    ("NotebookEdit", "notebook_path", "/repo/formal/x.ipynb"),
]


@pytest.mark.parametrize(("tool", "key", "path"), UNLOCKED_EDITS)
def test_edits_to_guard_paths_are_denied_on_main(tool: str, key: str, path: str) -> None:
    verdict = hook.decide(payload(tool, **{key: path}), on_main())
    assert verdict is not None
    assert verdict.rule == "guard-path-default"
    assert verdict.decision is Decision.DENY


@pytest.mark.parametrize(("tool", "key", "path"), UNLOCKED_EDITS)
def test_edits_to_guard_paths_get_no_verdict_on_a_branch(tool: str, key: str, path: str) -> None:
    assert hook.decide(payload(tool, **{key: path}), make_guard()) is None


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
@pytest.mark.parametrize("guard", [make_guard(), on_main()], ids=["branch", "main"])
def test_edits_to_locked_guard_paths_are_denied(path: str, guard: Guard) -> None:
    verdict = hook.decide(payload("Write", file_path=path), guard)
    assert verdict is not None
    assert verdict.rule == "guard-path-locked"
    assert verdict.decision is Decision.DENY


def test_the_edit_detail_names_group_and_reason() -> None:
    verdict = hook.decide(payload("Edit", file_path="/repo/justfile"), on_main())
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
    write = payload("Bash", command="echo x > justfile")
    assert hook.decide(write, on_main()) == Verdict(
        "guard-path-default", "redirects into justfile (gate-commands)", COMMITTED
    )
    assert hook.decide(write, make_guard()) is None
    locked = payload("Bash", command="echo x > .claude/settings.json")
    assert hook.decide(locked, make_guard()) == Verdict(
        "guard-path-write", "redirects into .claude/settings.json (harness)", COMMITTED
    )


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
        "Ratchet: an agent asks to run a tool call that merges a pull request: gh pr merge. "
        f"{RULES['merge-pr'].way_out} Approve only if you have read them and want this change "
        "in now."
    )
    merge = hook.reason(Verdict("merge-default", "git merge feat/x"))
    assert merge == (
        "Ratchet: an agent asks to run a tool call that merges another branch into the default "
        f"branch: git merge feat/x. {RULES['merge-default'].way_out} Approve only if you have "
        "read them and want this change in now."
    )
    unreadable = hook.reason(Verdict("unreadable", "the hook input is not a JSON object"))
    assert unreadable == (
        "Ratchet: The guard could not check this tool call (the hook input is not a JSON "
        "object). Allow it only if you know what it does."
    )
    label = hook.reason(Verdict("agent-ready-label", "0001-x.md gets the status `agent:ready`"))
    assert label == (
        "Ratchet: This marks a ticket ready for agents to build without you, which is yours to "
        "decide (0001-x.md gets the status `agent:ready`). Allow it?"
    )
    assert hook.respond(None) == ""


def test_a_deny_ends_with_the_way_out_of_its_rule() -> None:
    detail = "CLAUDE.md is in guard group 'g'. Because."
    edit = hook.reason(Verdict("guard-path-default", detail, COMMITTED))
    assert edit == (
        "Ratchet guard denied this tool call; no part of it ran. It changes a guard path where "
        "the commit lands on the default branch, which no merge judge reads: CLAUDE.md is in "
        "guard group 'g'. Because. Guard paths come from guards/manifest.toml as committed on "
        f"the default branch. {RULES['guard-path-default'].way_out} If the call also did "
        "unrelated work, redo that in a tool call of its own."
    )
    assert "gate dispute" not in edit
    lost = hook.reason(Verdict("unresolved-directory", "writes notes.md"))
    assert lost.endswith(
        f"{RULES['unresolved-directory'].way_out} If the call also did unrelated work, redo "
        "that in a tool call of its own."
    )
    assert "gate dispute" not in lost
    locked = hook.reason(Verdict("guard-path-locked", "x is in guard group 'g'. Because."))
    assert "gate dispute (docs/disputes/NNNN-<slug>.md)" in locked
    assert COMMITTED not in locked


def test_a_deny_for_rewriting_history_names_ratchets_step_not_a_dispute() -> None:
    way_out = (
        "Bring the branch up to date without rewriting it: merge the default branch into it, or "
        "add a new commit. The merge job lands it, or with no forge the `merge` entry point "
        "does."
    )
    for rule, because in (
        ("force-push", "force-pushes, which rewrites published history"),
        ("amend-pushed", "amends a commit that is already pushed"),
    ):
        assert RULES[rule].decision is Decision.DENY
        assert RULES[rule].way_out == way_out
        text = hook.reason(Verdict(rule, "git push -f"))
        assert f"It {because}: git push -f. {way_out} If the call" in text
        assert "gate dispute" not in text


def test_a_deny_about_a_folder_the_guard_cannot_work_out_says_how_to_write_it_out() -> None:
    command = "cd $S && sed -i s/a/b/ .github/workflows/ci.yml"
    verdict = hook.decide(payload("Bash", command=command), make_guard(manifest=EMPTY))
    assert verdict is not None
    assert hook.reason(verdict) == (
        "Ratchet guard denied this tool call; no part of it ran. It writes a relative path after "
        "a cd the guard cannot follow; it may be a guard path that only the owner changes, or one "
        "on the default branch: .github/workflows/ci.yml, changed by sed. Give the file a path "
        "the guard can follow, an absolute one or one after a `cd` to a literal directory, and it "
        "is judged where it lands. If the call also did unrelated work, redo that in a tool call "
        "of its own."
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


def agy(name: str, cwd: str = str(ROOT), **args: str) -> dict[str, object]:
    return {"toolCall": {"id": "t", "name": name, "args": args}, "cwd": cwd, "stepIdx": 1}


def test_antigravity_gets_its_own_output_shape() -> None:
    verdict = Verdict("labels", "gh label create x")
    deny = json.loads(hook.respond(verdict, antigravity=True))
    assert deny == {"decision": "deny", "reason": hook.reason(verdict)}
    merge = Verdict("merge-pr", "gh pr merge")
    ask = json.loads(hook.respond(merge, antigravity=True))
    assert ask == {"decision": "force_ask", "reason": hook.reason(merge)}
    assert json.loads(hook.respond(None, antigravity=True)) == {"decision": "ask"}


def test_run_answers_antigravity_in_its_form() -> None:
    denied = json.loads(
        hook.run(json.dumps(agy("run_command", CommandLine="git push -f")), make_guard)
    )
    assert denied["decision"] == "deny"
    assert "force-pushes" in denied["reason"]
    edit = json.dumps(agy("edit_file", TargetFile="justfile"))
    refused = json.loads(hook.run(edit, on_main))
    assert refused["decision"] == "deny"
    assert "changes a guard path" in refused["reason"]
    assert json.loads(hook.run(edit, make_guard)) == {"decision": "ask"}
    quiet = hook.run(json.dumps(agy("run_command", CommandLine="just check")), make_guard)
    assert json.loads(quiet) == {"decision": "ask"}

    def broken() -> Guard:
        msg = "no manifest"
        raise OSError(msg)

    failed = json.loads(hook.run(json.dumps(agy("run_command", CommandLine="ls")), broken))
    assert failed["decision"] == "force_ask"
    assert "OSError: no manifest" in failed["reason"]


def checkout(tmp_path: Path, *, manifest: bool | str = True, name: str = "checkout") -> Path:
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
        "tool_input": {"file_path": str(root / "justfile")},
        "cwd": str(root),
    }
    assert hook.main(root, io.StringIO(json.dumps(case)), stdout) == 0
    assert json.loads(stdout.getvalue())["hookSpecificOutput"]["permissionDecision"] == "deny"
    quiet = io.StringIO()
    case["tool_input"] = {"file_path": str(root / "notes.md")}
    assert hook.main(root, io.StringIO(json.dumps(case)), quiet) == 0
    assert quiet.getvalue() == ""
    case["tool_input"] = {"file_path": str(root / "justfile")}
    switch_to_a_branch(root)
    on_a_branch = io.StringIO()
    assert hook.main(root, io.StringIO(json.dumps(case)), on_a_branch) == 0
    assert on_a_branch.getvalue() == ""


def _stalled(seconds: float) -> object:
    judge = hook.decide

    def decide(payload: object, guard: Guard) -> Verdict | None:
        time.sleep(seconds)
        return judge(payload, guard)

    return decide


def _answer(root: Path, command: str, *, deadline: float) -> tuple[dict[str, str], float]:
    stdout = io.StringIO()
    case = {"tool_name": "Bash", "tool_input": {"command": command}, "cwd": str(root)}
    start = time.monotonic()
    assert hook.main(root, io.StringIO(json.dumps(case)), stdout, deadline=deadline) == 0
    spent = time.monotonic() - start
    return json.loads(stdout.getvalue() or "{}").get("hookSpecificOutput", {}), spent


def test_the_hook_asks_before_its_limit_when_the_judging_stalls(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = checkout(tmp_path)
    monkeypatch.setattr(hook, "decide", _stalled(30))
    answer, spent = _answer(root, "ls", deadline=0.2)
    assert answer["permissionDecision"] == "ask"
    assert "it used the 7 s it gives itself" in answer["permissionDecisionReason"]
    assert spent < 5


def test_a_deny_the_guard_reached_before_its_deadline_stands(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = checkout(tmp_path)

    def due(*_args: object, **_kwargs: object) -> list[object]:
        signal.raise_signal(signal.SIGALRM)
        return []

    monkeypatch.setattr(bash._Checker, "_advance", due)  # noqa: SLF001 # -- the step after a command's verdicts
    answer = _answer(root, "git push -f; ls", deadline=60)[0]
    assert answer["permissionDecision"] == "deny"
    assert "force-pushes" in answer["permissionDecisionReason"]
    assert _answer(root, "ls; git push -f", deadline=60)[0]["permissionDecision"] == "ask"


def _held(monkeypatch: pytest.MonkeyPatch, owner: object, name: str, *, call: int) -> None:
    real = getattr(owner, name)
    calls = [0]

    def held(*args: object, **kwargs: object) -> object:
        calls[0] += 1
        if calls[0] == call:
            time.sleep(30)
        return real(*args, **kwargs)

    monkeypatch.setattr(owner, name, held)


@pytest.mark.parametrize(
    "command",
    ["env -C d1 git push -f", "sudo -D d1 git push -f", "x=d1 || x=d2; cd $x && git push -f"],
)
def test_a_deny_found_in_one_directory_stands_when_the_next_one_runs_past_the_deadline(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, command: str
) -> None:
    root = checkout(tmp_path)
    _held(monkeypatch, git, "check", call=2)
    answer, spent = _answer(root, command, deadline=1)
    assert answer["permissionDecision"] == "deny"
    assert "force-pushes" in answer["permissionDecisionReason"]
    assert spent < 5


def test_a_deny_judged_in_full_stands_when_the_deadline_falls_after_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = checkout(tmp_path)
    _held(monkeypatch, tickets, "together", call=1)
    assert _answer(root, "git push -f", deadline=1)[0]["permissionDecision"] == "deny"
    monkeypatch.undo()
    judge = hook.decide

    def rung(payload: object, guard: Guard) -> Verdict | None:
        verdict = judge(payload, guard)
        signal.raise_signal(signal.SIGALRM)
        return verdict

    monkeypatch.setattr(hook, "decide", rung)
    assert _answer(root, "git push -f", deadline=60)[0]["permissionDecision"] == "deny"


def test_an_alarm_as_the_answer_is_chosen_leaves_one_line_with_that_answer(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = checkout(tmp_path)
    arm = signal.setitimer

    def disarm(which: int, seconds: float) -> tuple[float, float]:
        left = arm(which, seconds)
        if not seconds:
            signal.raise_signal(signal.SIGALRM)
        return left

    monkeypatch.setattr(signal, "setitimer", disarm)
    stdout = io.StringIO()
    case = {"tool_name": "Bash", "tool_input": {"command": "git push -f"}, "cwd": str(root)}
    assert hook.main(root, io.StringIO(json.dumps(case)), stdout, deadline=60) == 0
    [line] = stdout.getvalue().splitlines()
    assert json.loads(line)["hookSpecificOutput"]["permissionDecision"] == "deny"


def test_the_ask_at_the_deadline_is_logged_like_any_answer(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = checkout(tmp_path)
    monkeypatch.setattr(hook, "decide", _stalled(30))
    _answer(root, "ls", deadline=0.2)
    [line] = (root / ".git" / events.LOG_NAME).read_text(encoding="utf-8").splitlines()
    logged = json.loads(line)
    assert (logged["decision"], logged["rule"]) == ("ask", "unreadable")
    assert logged["detail"] == hook.LATE
    page = (PLUGIN_ROOT / "skills/method/guards.md").read_text(encoding="utf-8")
    assert f"cannot finish judging in {hook.DEADLINE_SECONDS:g} s" in page


def test_a_hook_that_finishes_in_time_answers_as_it_judged(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = checkout(tmp_path)
    monkeypatch.setattr(hook, "decide", _stalled(0.05))
    assert _answer(root, "git push -f", deadline=5)[0]["permissionDecision"] == "deny"
    assert _answer(root, "ls", deadline=5)[0] == {}
    monkeypatch.delattr(signal, "setitimer")
    assert _answer(root, "git push -f", deadline=0.01)[0]["permissionDecision"] == "deny"


def test_the_hook_answers_well_inside_every_limit_a_harness_gives_it() -> None:
    config = json.loads((PLUGIN_ROOT / "hooks/hooks.json").read_text(encoding="utf-8"))
    limits = [h["timeout"] for e in config["hooks"]["PreToolUse"] for h in e["hooks"]]
    limits.append(adapter.TIMEOUT_SECONDS)
    started = 2
    assert hook.DEADLINE_SECONDS + hook.LOG_TIMEOUT_SECONDS + started <= min(limits)


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


def switch_to_a_branch(root: Path) -> None:
    _git(root, "switch", "-q", "-c", "feature/x")


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
    assert hook.GitRepoState().current_branch(tmp_path) == "trunk"
    assert state.head_is_pushed(tmp_path) is False
    assert state.full_name(tmp_path, "trunk") == "refs/heads/trunk"
    assert state.full_name(tmp_path, "HEAD") == "refs/heads/trunk"
    assert state.full_name(tmp_path, "nope") is None
    assert state.full_name(tmp_path, "trunk@{upstream}") is None
    _git(tmp_path, "tag", "trunk")
    assert hook.GitRepoState().current_branch(tmp_path) == "trunk"
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
    assert hook.GitRepoState().current_branch(tmp_path) is None


def test_git_repo_state_survives_a_missing_git(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PATH", "")
    assert hook.GitRepoState().current_branch(Path.cwd()) is None
    assert hook.GitRepoState().checked_out(Path.cwd()) is None


def test_checked_out_names_the_branch_the_detached_head_or_nothing(tmp_path: Path) -> None:
    def head() -> str | None:
        return hook.GitRepoState().checked_out(tmp_path)

    assert head() is None
    _git(tmp_path, "init", "-q", "-b", "trunk")
    assert head() == "trunk"
    commit = ["-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "--allow-empty", "-m"]
    _git(tmp_path, *commit, "x")
    assert head() == "trunk"
    switch_to_a_branch(tmp_path)
    assert head() == "feature/x"
    _git(tmp_path, "checkout", "-q", "--detach")
    assert head() == "HEAD"


def test_one_state_reads_each_checkout_once(tmp_path: Path) -> None:
    root = checkout(tmp_path)
    state = hook.GitRepoState()
    assert (state.checked_out(root), state.default_branches(root)) == ("main", {"main", "master"})
    assert state.current_branch(root) == "main"
    switch_to_a_branch(root)
    _git(root, "update-ref", "refs/remotes/origin/trunk", "HEAD")
    _git(root, "symbolic-ref", "refs/remotes/origin/HEAD", "refs/remotes/origin/trunk")
    assert (state.checked_out(root), state.default_branches(root)) == ("main", {"main", "master"})
    assert state.current_branch(root) == "main"
    fresh = hook.GitRepoState()
    assert fresh.current_branch(root) == "feature/x"
    assert fresh.checked_out(root) == "feature/x"
    assert fresh.default_branches(root) == {"main", "master", "trunk"}


def test_the_default_branches_are_main_master_and_the_one_the_manifest_is_read_from(
    tmp_path: Path,
) -> None:
    def defaults(root: Path) -> frozenset[str]:
        return hook.GitRepoState().default_branches(root)

    assert defaults(tmp_path) == {"main", "master"}
    root = checkout(tmp_path)
    assert defaults(root) == {"main", "master"}
    _git(root, "branch", "-m", "main", "trunk")
    assert defaults(root) == {"main", "master", "trunk"}
    _git(root, "update-ref", "refs/remotes/origin/trunk", "HEAD")
    _git(root, "symbolic-ref", "refs/remotes/origin/HEAD", "refs/remotes/origin/trunk")
    assert defaults(root) == {"main", "master", "trunk"}


def test_a_default_branch_that_is_not_called_main_still_takes_the_edit_where_it_lands(
    tmp_path: Path,
) -> None:
    root = checkout(tmp_path)
    _git(root, "branch", "-m", "main", "trunk")
    _git(root, "update-ref", "refs/remotes/origin/trunk", "HEAD")
    _git(root, "symbolic-ref", "refs/remotes/origin/HEAD", "refs/remotes/origin/trunk")
    case = {"tool_name": "Edit", "tool_input": {"file_path": "justfile"}, "cwd": str(root)}
    assert answer(root, case)["permissionDecision"] == "deny"
    switch_to_a_branch(root)
    assert answer(root, case) == {}


def test_a_detached_head_is_behind_a_merge(adopted: Path) -> None:
    _git(adopted, "checkout", "-q", "--detach")
    assert answer(adopted, edit_from(adopted, adopted / "justfile")) == {}
    locked = edit_from(adopted, adopted / ".claude/settings.json", "Write")
    assert answer(adopted, locked)["permissionDecision"] == "deny"


def test_a_checkout_git_cannot_read_takes_the_edit_where_it_lands(
    adopted: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    switch_to_a_branch(adopted)
    monkeypatch.setattr(hook.GitRepoState, "checked_out", lambda _self, _root: None)
    assert answer(adopted, edit_from(adopted, adopted / "justfile"))["permissionDecision"] == "deny"


def test_a_double_star_reads_as_a_star_in_a_shell_without_globstar(tmp_path: Path) -> None:
    filesystem = hook.LocalFilesystem()
    deep = tmp_path / "a/b/c.txt"
    deep.parent.mkdir(parents=True)
    deep.write_text("", encoding="utf-8")
    assert filesystem.glob(tmp_path / "**") == [tmp_path / "a"]
    assert filesystem.glob(tmp_path / "**/c.txt") == []
    assert filesystem.glob(tmp_path / "a/**/c.txt") == [deep]
    assert filesystem.glob(tmp_path / "a/***/c.txt") == [deep]
    top = filesystem.glob(Path("/**"))
    assert top
    assert {len(path.parts) for path in top} == {2}


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
    edit = {"tool_name": "Edit", "tool_input": {"file_path": "justfile"}, "cwd": str(root)}
    assert decision(run_shim(SHIM, edit)) == "deny"
    locked = {"tool_name": "Edit", "tool_input": {"file_path": ".claude/settings.json"}}
    assert decision(run_shim(SHIM, {**locked, "cwd": str(root)})) == "deny"
    push = {"tool_name": "Bash", "tool_input": {"command": "git push --force"}, "cwd": str(root)}
    assert decision(run_shim(SHIM, push)) == "deny"
    status = {"tool_name": "Bash", "tool_input": {"command": "git status"}, "cwd": str(root)}
    assert run_shim(SHIM, status).stdout == ""


def test_the_shim_lets_a_guard_path_edit_through_on_a_branch(tmp_path: Path) -> None:
    root = checkout(tmp_path)
    switch_to_a_branch(root)
    edit = {"tool_name": "Edit", "tool_input": {"file_path": "justfile"}, "cwd": str(root)}
    assert run_shim(SHIM, edit).stdout == ""
    locked = {"tool_name": "Edit", "tool_input": {"file_path": ".claude/settings.json"}}
    assert decision(run_shim(SHIM, {**locked, "cwd": str(root)})) == "deny"


def test_the_shim_without_a_manifest_keeps_only_the_command_rules(tmp_path: Path) -> None:
    root = checkout(tmp_path, manifest=False)
    edit = {"tool_name": "Edit", "tool_input": {"file_path": "justfile"}, "cwd": str(root)}
    assert run_shim(SHIM, edit).stdout == ""
    push = {"tool_name": "Bash", "tool_input": {"command": "git push --force"}, "cwd": str(root)}
    assert decision(run_shim(SHIM, push)) == "deny"
    own = {"tool_name": "Edit", "tool_input": {"file_path": str(SHIM)}, "cwd": str(root)}
    assert decision(run_shim(SHIM, own)) == "deny"


ADOPTION_WRITES = [
    ".github/workflows/ci.yml",
    ".github/workflows/deep.yml",
    "AGENTS.md",
    "CLAUDE.md",
    ".claude/rules/method.md",
]


def test_the_adoption_writes_its_workflows_and_instructions_without_a_prompt(
    tmp_path: Path,
) -> None:
    root = checkout(tmp_path, manifest=False)
    switch_to_a_branch(root)
    (root / "guards").mkdir()
    (root / MANIFEST_PATH).write_text(MANIFEST_TEXT, encoding="utf-8")
    for name in ADOPTION_WRITES:
        assert answer(root, edit_from(root, root / name, "Write")) == {}
    heredoc = "cat > .github/workflows/ci.yml <<'EOF'\non: push\nEOF"
    assert answer(root, bash_from(root, heredoc)) == {}
    _git(root, "add", "-A")
    _git(root, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", "adopt")
    for name in ADOPTION_WRITES:
        assert answer(root, edit_from(root, root / name, "Write")) == {}
    _git(root, "switch", "-q", "main")
    _git(root, "-c", "user.name=t", "-c", "user.email=t@t", "merge", "-q", "feature/x")
    _git(root, "switch", "-q", "feature/x")
    asked = answer(root, edit_from(root, root / ".github/workflows/ci.yml", "Write"))
    assert asked["permissionDecision"] == "ask"
    nested = answer(root, edit_from(root, root / "src/x/CLAUDE.md", "Write"))
    assert nested["permissionDecision"] == "ask"


def test_the_shim_asks_the_owner_in_one_plain_sentence(tmp_path: Path) -> None:
    root = checkout(tmp_path)
    switch_to_a_branch(root)
    for name, sentence in [
        (
            ".github/workflows/ci.yml",
            "This changes what GitHub runs with this repository's secrets, before any merge",
        ),
        (
            ".scratch/feature/CLAUDE.md",
            "This changes the instructions agents follow in this project, starting now",
        ),
    ]:
        write = {"tool_name": "Write", "tool_input": {"file_path": name}, "cwd": str(root)}
        done = run_shim(SHIM, write)
        assert decision(done) == "ask"
        reason = json.loads(done.stdout)["hookSpecificOutput"]["permissionDecisionReason"]
        assert reason == f"Ratchet: {sentence} ({name}). Allow it?"


def test_the_shim_falls_back_to_the_project_directory_then_asks(tmp_path: Path) -> None:
    root = checkout(tmp_path)
    no_cwd = {"tool_name": "Edit", "tool_input": {"file_path": "justfile"}}
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


def test_the_shim_answers_antigravity_in_its_form(tmp_path: Path) -> None:
    root = str(checkout(tmp_path))
    edit = run_shim(SHIM, agy("replace_file_content", root, TargetFile="justfile"))
    assert json.loads(edit.stdout)["decision"] == "deny"
    push = run_shim(SHIM, agy("run_command", root, CommandLine="git push --force"))
    assert json.loads(push.stdout)["decision"] == "deny"
    status = run_shim(SHIM, agy("run_command", root, CommandLine="git status"))
    assert json.loads(status.stdout) == {"decision": "ask"}


def test_the_shim_asks_antigravity_when_the_guard_cannot_load(tmp_path: Path) -> None:
    shim = tmp_path / "a/hooks/guard.py"
    shim.parent.mkdir(parents=True)
    shutil.copy(SHIM, shim)
    answer = json.loads(run_shim(shim, agy("run_command", "/", CommandLine="ls")).stdout)
    assert answer["decision"] == "force_ask"
    assert answer["reason"].startswith("Ratchet guard could not run: ")


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
    tool_input = {"file_path": str(file), "old_string": "a", "new_string": "b"}
    return {"tool_name": tool, "tool_input": tool_input, "cwd": str(session)}


def bash_from(session: Path, command: str) -> dict[str, object]:
    return {"tool_name": "Bash", "tool_input": {"command": command}, "cwd": str(session)}


def answer(root: Path, case: dict[str, object]) -> dict[str, str]:
    stdout = io.StringIO()
    assert hook.main(root, io.StringIO(json.dumps(case)), stdout) == 0
    return json.loads(stdout.getvalue())["hookSpecificOutput"] if stdout.getvalue() else {}


@pytest.fixture
def adopted(tmp_path: Path) -> Path:
    return checkout(tmp_path, name="adopted")


@pytest.fixture
def session(tmp_path: Path) -> Path:
    return checkout(tmp_path, manifest=False, name="session")


def test_an_edit_is_judged_by_the_checkout_holding_the_file(adopted: Path, session: Path) -> None:
    denied = answer(session, edit_from(session, adopted / "justfile"))
    assert denied["permissionDecision"] == "deny"
    assert "justfile is in guard group 'gate-commands'" in denied["permissionDecisionReason"]
    locked = answer(session, edit_from(session, adopted / ".claude/settings.json", "Write"))
    assert locked["permissionDecision"] == "deny"
    assert answer(session, edit_from(session, adopted / "app/core/money.py")) == {}
    assert answer(session, edit_from(session, session / "justfile")) == {}


def test_an_edit_lands_where_the_checkout_holding_the_file_is_not_where_the_session_is(
    adopted: Path, session: Path
) -> None:
    switch_to_a_branch(adopted)
    assert answer(session, edit_from(session, adopted / "justfile")) == {}
    locked = answer(session, edit_from(session, adopted / ".claude/settings.json", "Write"))
    assert locked["permissionDecision"] == "deny"
    switch_to_a_branch(session)
    _git(adopted, "switch", "-q", "main")
    assert answer(session, edit_from(session, adopted / "justfile"))["permissionDecision"] == "deny"


def test_an_edit_is_judged_when_the_session_is_in_no_checkout(
    adopted: Path, tmp_path: Path
) -> None:
    elsewhere = tmp_path / "not-a-repository"
    elsewhere.mkdir()
    case = edit_from(elsewhere, adopted / "quality/thresholds.toml")
    assert answer(elsewhere, case)["permissionDecision"] == "deny"
    switch_to_a_branch(adopted)
    assert answer(elsewhere, case) == {}


def test_a_file_reached_through_a_symlink_is_judged_where_it_lives(
    adopted: Path, session: Path
) -> None:
    (session / "link.toml").symlink_to(adopted / "quality/thresholds.toml")
    case = edit_from(session, session / "link.toml")
    assert answer(session, case)["permissionDecision"] == "deny"
    switch_to_a_branch(adopted)
    assert answer(session, case) == {}


def test_each_checkout_is_judged_by_its_own_manifest(tmp_path: Path, adopted: Path) -> None:
    own = (
        '[[group]]\nname = "notes"\nreason = "The notes are the owner\'s."\npaths = ["NOTES.md"]\n'
    )
    sibling = checkout(tmp_path, manifest=own, name="sibling")
    notes = answer(adopted, edit_from(adopted, sibling / "NOTES.md"))
    assert notes["permissionDecision"] == "deny"
    assert answer(adopted, edit_from(adopted, sibling / "justfile")) == {}
    justfile = answer(sibling, edit_from(sibling, adopted / "justfile"))
    assert justfile["permissionDecision"] == "deny"
    assert answer(sibling, edit_from(sibling, adopted / "NOTES.md")) == {}


def test_a_codex_patch_is_judged_by_the_checkout_it_names(adopted: Path, session: Path) -> None:
    patch = f"*** Begin Patch\n*** Update File: {adopted}/justfile\n@@\n-a\n+b\n*** End Patch\n"
    case = {"tool_name": "apply_patch", "tool_input": {"input": patch}, "cwd": str(session)}
    assert answer(session, case)["permissionDecision"] == "deny"
    switch_to_a_branch(adopted)
    assert answer(session, case) == {}


SHELL_WRITES = [
    "sed -i 's/a/b/' {adopted}/quality/thresholds.toml",
    "echo x > {adopted}/justfile",
    "cp /tmp/x {adopted}/scripts/gate.py",
    "cd {adopted} && sed -i s/a/b/ quality/thresholds.toml",
    "cd {adopted}/quality && sed -i s/a/b/ thresholds.toml",
    "cd {adopted} && cd quality && rm thresholds.toml",
    "git -C {adopted} rm quality/thresholds.toml",
    "python3 -c \"open('{adopted}/justfile', 'w').write('x')\"",
]


@pytest.mark.parametrize("command", SHELL_WRITES)
def test_a_shell_write_is_judged_by_the_checkout_it_lands_in(
    command: str, adopted: Path, session: Path
) -> None:
    denied = answer(session, bash_from(session, command.format(adopted=adopted)))
    assert denied["permissionDecision"] == "deny"
    assert "guards/manifest.toml as committed" in denied["permissionDecisionReason"]


@pytest.mark.parametrize("command", SHELL_WRITES)
def test_a_shell_write_to_a_checkout_on_a_branch_is_left_to_the_merge_judge(
    command: str, adopted: Path, session: Path
) -> None:
    switch_to_a_branch(adopted)
    assert answer(session, bash_from(session, command.format(adopted=adopted))) == {}
    locked = bash_from(session, f"echo x > {adopted}/.claude/settings.json")
    assert answer(session, locked)["permissionDecision"] == "deny"


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


def test_a_manifest_that_does_not_parse_asks_for_every_path_in_its_checkout(
    tmp_path: Path, session: Path
) -> None:
    broken = checkout(tmp_path, manifest="[[group]]\nname = 1\n", name="broken")
    guard = hook.local_guard(session)
    for name in ("justfile", "notes.md", "justfile"):
        case = json.dumps(edit_from(session, broken / name))
        asked = json.loads(hook.run(case, lambda: guard))["hookSpecificOutput"]
        assert asked["permissionDecision"] == "ask"
        assert "ManifestError" in asked["permissionDecisionReason"]


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
    assert decision(run_shim(SHIM, here)) == "deny"
    elsewhere = edit_from(session, adopted / "justfile")
    assert decision(run_shim(SHIM, elsewhere)) == "deny"
    sed = bash_from(session, f"sed -i s/a/b/ {adopted}/quality/thresholds.toml")
    assert decision(run_shim(SHIM, sed)) == "deny"
    free = edit_from(session, adopted / "app/core/money.py")
    assert run_shim(SHIM, free).stdout == ""
    switch_to_a_branch(adopted)
    assert run_shim(SHIM, elsewhere).stdout == ""
    assert run_shim(SHIM, here).stdout == ""


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


def test_git_repo_state_reads_a_file_from_the_default_branch_not_the_work_tree(
    tmp_path: Path,
) -> None:
    state = hook.GitRepoState()
    assert state.default_file(tmp_path, "t.md") is None, "no repository, no default branch"
    _git(tmp_path, "init", "-q", "-b", "main")
    (tmp_path / "t.md").write_text("held\n", encoding="utf-8")
    _git(tmp_path, "add", "t.md")
    _git(tmp_path, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", "x")
    (tmp_path / "t.md").write_text("edited\n", encoding="utf-8")
    assert state.default_file(tmp_path, "t.md") == "held\n"
    assert state.default_file(tmp_path, "gone.md") is None
