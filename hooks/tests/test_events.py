import io
import json
import os
import re
import select
import shlex
import shutil
import subprocess
import sys
import time
from collections import Counter
from collections.abc import Callable
from pathlib import Path

import pytest

from ratchet_guards import events, hook
from ratchet_guards.manifest import MANIFEST_PATH
from tests.repos import branch, git, repository
from tests.support import MANIFEST_TEXT, PLUGIN_ROOT, make_guard

SHIM = PLUGIN_ROOT / "hooks/guard.py"
STAMP = r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ"


@pytest.fixture
def adopted(tmp_path: Path) -> Path:
    root = tmp_path / "adopted"
    root.mkdir()
    return repository(root, {MANIFEST_PATH: MANIFEST_TEXT})


def log_of(root: Path) -> Path:
    return root / ".git" / events.LOG_NAME


def edit(root: Path, file: Path) -> dict[str, object]:
    tool_input = {"file_path": str(file), "old_string": "a", "new_string": "b"}
    return {"tool_name": "Edit", "tool_input": tool_input, "cwd": str(root)}


def bash(root: Path, command: str) -> dict[str, object]:
    return {"tool_name": "Bash", "tool_input": {"command": command}, "cwd": str(root)}


def answer(root: Path, case: object, project: Path | None = None) -> str:
    text = case if isinstance(case, str) else json.dumps(case)
    stdout = io.StringIO()
    assert hook.main(root, io.StringIO(text), stdout, project=project) == 0
    return stdout.getvalue()


def unlogged(root: Path, case: object) -> str:
    text = case if isinstance(case, str) else json.dumps(case)
    printed = hook.run(text, lambda: hook.local_guard(root))
    return printed + "\n" if printed else ""


def decision(printed: str) -> str:
    return str(json.loads(printed)["hookSpecificOutput"]["permissionDecision"])


def lines(log: Path) -> list[dict[str, object]]:
    return [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()]


def test_a_deny_writes_one_line_with_the_fields(adopted: Path) -> None:
    printed = answer(adopted, edit(adopted, adopted / "justfile"))
    assert decision(printed) == "deny"
    [line] = lines(log_of(adopted))
    detail = str(line["detail"])
    assert line == {
        "at": line["at"],
        "decision": "deny",
        "tool": "Edit",
        "rule": "guard-path-default",
        "detail": detail,
        "branch": "main",
    }
    assert detail.startswith("justfile is in guard group 'gate-commands'.")
    assert detail in json.loads(printed)["hookSpecificOutput"]["permissionDecisionReason"]
    assert re.fullmatch(STAMP, str(line["at"]))


def test_an_ask_writes_one_line(adopted: Path) -> None:
    printed = answer(adopted, bash(adopted, "gh pr merge 7"))
    assert decision(printed) == "ask"
    [line] = lines(log_of(adopted))
    assert (line["decision"], line["tool"], line["rule"]) == ("ask", "Bash", "merge-pr")
    assert line["detail"] == "gh pr merge"


def test_an_allow_writes_nothing(adopted: Path) -> None:
    assert answer(adopted, bash(adopted, "git status")) == ""
    assert answer(adopted, edit(adopted, adopted / "notes.md")) == ""
    assert not log_of(adopted).exists()


def test_an_allow_starts_no_process_for_the_log(
    adopted: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    started: list[tuple[str, ...]] = []
    real = hook._run  # noqa: SLF001 # -- counts the git processes the hook starts

    def counting(
        cwd: Path, *args: str, timeout: float = hook.GIT_TIMEOUT_SECONDS
    ) -> subprocess.CompletedProcess[str] | None:
        started.append(args)
        return real(cwd, *args, timeout=timeout)

    monkeypatch.setattr(hook, "_run", counting)
    case = bash(adopted, "git status")
    hook.run(json.dumps(case), lambda: hook.local_guard(adopted))
    without = len(started)
    started.clear()
    assert answer(adopted, case) == ""
    assert len(started) == without


def test_an_unreadable_call_and_another_harness_are_logged(adopted: Path) -> None:
    assert decision(answer(adopted, "{not json")) == "ask"
    agy = {"toolCall": {"name": "run_command", "args": {"CommandLine": "git push -f"}}}
    printed = answer(adopted, {**agy, "cwd": str(adopted)})
    assert json.loads(printed)["decision"] == "deny"
    garbled, pushed = lines(log_of(adopted))
    assert (garbled["rule"], garbled["tool"]) == ("unreadable", None)
    assert (pushed["rule"], pushed["tool"]) == ("force-push", "run_command")


def test_two_worktrees_of_one_repository_append_to_one_file(adopted: Path, tmp_path: Path) -> None:
    tree = tmp_path / "tree"
    git(adopted, "worktree", "add", "-q", "-b", "feature/x", str(tree))
    below = tree / "src/deep"
    below.mkdir(parents=True)
    assert decision(answer(tree, bash(tree, "git push --force"))) == "deny"
    assert decision(answer(below, bash(below, "git push --force"))) == "deny"
    assert decision(answer(adopted, bash(adopted, "git push --force"))) == "deny"
    branches = [line["branch"] for line in lines(log_of(adopted))]
    assert branches == ["feature/x", "feature/x", "main"]
    assert (tree / ".git").is_file()
    assert not (tree / events.LOG_NAME).exists()
    assert not (below / events.LOG_NAME).exists()


def test_a_detached_head_is_logged_as_head(adopted: Path) -> None:
    git(adopted, "checkout", "-q", "--detach")
    answer(adopted, bash(adopted, "git push --force"))
    assert [line["branch"] for line in lines(log_of(adopted))] == ["HEAD"]


def test_outside_a_repository_nothing_is_written(adopted: Path, tmp_path: Path) -> None:
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    case = edit(elsewhere, adopted / "justfile")
    assert answer(elsewhere, case) == unlogged(elsewhere, case)
    assert decision(answer(elsewhere, case)) == "deny"
    assert list(elsewhere.iterdir()) == []
    assert not log_of(adopted).exists()


def make_directory(log: Path) -> None:
    log.mkdir()


def make_dangling_link(log: Path) -> None:
    log.symlink_to(log.parent.parent / "planted.jsonl")


def make_link_to_a_file(log: Path) -> None:
    log.symlink_to(log.parent.parent / MANIFEST_PATH)


@pytest.mark.parametrize("breaking", [make_directory, make_dangling_link, make_link_to_a_file])
def test_a_log_that_cannot_be_written_leaves_the_answer_as_it_was(
    adopted: Path, breaking: Callable[[Path], None]
) -> None:
    breaking(log_of(adopted))
    manifest = (adopted / MANIFEST_PATH).read_text(encoding="utf-8")
    for case in (edit(adopted, adopted / "justfile"), bash(adopted, "gh pr merge 7")):
        assert answer(adopted, case) == unlogged(adopted, case)
    assert (adopted / MANIFEST_PATH).read_text(encoding="utf-8") == manifest
    assert not (adopted / "planted.jsonl").exists()


@pytest.mark.skipif(os.geteuid() == 0, reason="root writes through a read-only directory")
def test_a_git_directory_that_is_not_writable_leaves_the_answer_as_it_was(adopted: Path) -> None:
    common = adopted / ".git"
    common.chmod(0o555)
    try:
        case = edit(adopted, adopted / "justfile")
        assert answer(adopted, case) == unlogged(adopted, case)
    finally:
        common.chmod(0o755)
    assert not log_of(adopted).exists()


def test_a_fifo_at_the_log_path_does_not_hold_the_answer(adopted: Path) -> None:
    os.mkfifo(log_of(adopted))
    case = json.dumps(bash(adopted, "git push --force"))
    done = subprocess.run(  # noqa: S603 # -- the interpreter running these tests, on a fixed script
        [sys.executable, str(SHIM)],
        input=case,
        capture_output=True,
        text=True,
        check=False,
        timeout=60,
        env={**os.environ, "PYTHONPATH": ""},
    )
    assert done.returncode == 0, done.stderr
    assert done.stdout == unlogged(adopted, case)


HANGING_GIT = """#!/bin/sh
for arg in "$@"; do
    if [ "$arg" = --git-common-dir ]; then
        cat "$ANSWER" > "$SEEN"
        exec sleep 60
    fi
done
exec {git} "$@"
"""


def test_a_hung_git_holds_neither_the_answer_nor_the_exit(adopted: Path, tmp_path: Path) -> None:
    real = shutil.which("git")
    assert real is not None
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    fake = bin_dir / "git"
    fake.write_text(HANGING_GIT.format(git=shlex.quote(real)), encoding="utf-8")
    fake.chmod(0o755)
    answer_file, seen = tmp_path / "answer", tmp_path / "seen"
    case = json.dumps(bash(adopted, "git push --force"))
    path = f"{bin_dir}{os.pathsep}{os.environ['PATH']}"
    env = {
        **os.environ,
        "PATH": path,
        "PYTHONPATH": "",
        "ANSWER": str(answer_file),
        "SEEN": str(seen),
    }
    with answer_file.open("w", encoding="utf-8") as stdout:
        done = subprocess.run(  # noqa: S603 # -- the interpreter running these tests, on a fixed script
            [sys.executable, str(SHIM)],
            input=case,
            stdout=stdout,
            stderr=subprocess.PIPE,
            text=True,
            check=False,
            timeout=60,
            env=env,
        )
    exited = time.time()
    assert done.returncode == 0, done.stderr
    expected = unlogged(adopted, case)
    assert decision(expected) == "deny"
    assert seen.read_text(encoding="utf-8") == expected
    assert answer_file.read_text(encoding="utf-8") == expected
    assert exited - seen.stat().st_mtime < hook.LOG_TIMEOUT_SECONDS + 2
    assert not log_of(adopted).exists()


def test_a_failure_of_any_kind_leaves_the_answer_as_it_was() -> None:
    nowhere = Path("/nonexistent\x00directory")
    case = bash(nowhere, "git push --force")
    assert decision(answer(nowhere, case)) == "ask"
    assert answer(nowhere, case) == unlogged(nowhere, case)


def test_a_long_line_is_cut_under_the_atomic_size_and_marked(adopted: Path) -> None:
    command = "git push --force origin " + "x" * 5000
    answer(adopted, bash(adopted, command))
    raw = log_of(adopted).read_bytes()
    assert len(raw) < events.LIMIT
    [line] = lines(log_of(adopted))
    assert line["truncated"] is True
    assert line["detail"] == command[: events.CLIP]


def test_every_field_is_cut_whatever_its_characters() -> None:
    event = events.Event(
        at="2026-11-15T00:00:00Z",
        decision="deny",
        tool="\U0001f600" * 5000,
        rule="force-push",
        detail="\ud800" * 5000,
        branch="\u00e9" * 5000,
    )
    raw = event.line()
    assert raw.endswith(b"\n")
    assert raw.count(b"\n") == 1
    assert len(raw) < events.LIMIT
    line = json.loads(raw)
    assert line["tool"] == "\U0001f600" * events.CLIP
    assert line["truncated"] is True
    short = events.Event("2026-11-15T00:00:00Z", "ask", None, "merge-pr", "gh pr merge", None)
    assert "truncated" not in json.loads(short.line())


def test_a_line_of_the_longest_escapes_in_every_text_field_still_fits() -> None:
    widest = "\U0001f600" * 5000
    event = events.Event("2026-11-15T00:00:00Z", "deny", widest, widest, widest, widest)
    raw = event.line()
    assert len(raw) < events.LIMIT
    line = json.loads(raw)
    assert line["truncated"] is True
    assert {line[name] for name in ("tool", "rule", "detail", "branch")} == {widest[: events.CLIP]}


@pytest.mark.parametrize("length", [4000, 6000, 100_000])
def test_a_line_of_any_length_is_one_write_a_pipe_takes_whole(length: int) -> None:
    event = events.Event("2026-11-15T00:00:00Z", "deny", "Bash", "force-push", "x" * length, "main")
    assert len(event.line()) <= select.PIPE_BUF


WRITER = """
import sys
from pathlib import Path
from ratchet_guards import events
log, tag, count = Path(sys.argv[1]), sys.argv[2], int(sys.argv[3])
for n in range(count):
    detail = f"{tag} {n} " + tag * 3000
    event = events.Event("2026-11-15T00:00:00Z", "deny", "Bash", "force-push", detail, tag)
    events.append(log, event.line())
"""


def test_concurrent_writers_leave_every_line_whole(tmp_path: Path) -> None:
    log = tmp_path / events.LOG_NAME
    tags, count = "abcdef", 200
    writers = [
        subprocess.Popen(  # noqa: S603 # -- the interpreter running these tests, on a fixed script
            [sys.executable, "-c", WRITER, str(log), tag, str(count)],
            env={**os.environ, "PYTHONPATH": str(PLUGIN_ROOT / "hooks")},
        )
        for tag in tags
    ]
    assert [writer.wait(timeout=120) for writer in writers] == [0] * len(tags)
    found = lines(log)
    assert Counter(str(line["branch"]) for line in found) == dict.fromkeys(tags, count)
    for line in found:
        tag = str(line["branch"])
        assert str(line["detail"]).endswith(tag * 3000)
        assert "truncated" not in line


def test_the_log_is_a_locked_guard_path_in_every_checkout() -> None:
    hit = make_guard().locator.classify(
        Path("/elsewhere/.git") / events.LOG_NAME, destructive=False
    )
    assert hit is not None
    assert hit.group.locked
    assert hit.group.name == "guard-log"


def test_an_agent_cannot_write_the_log(adopted: Path, tmp_path: Path) -> None:
    log = log_of(adopted)
    relative = f".git/{events.LOG_NAME}"
    for case in (
        edit(adopted, log),
        bash(adopted, f"echo '{{}}' >> {relative}"),
        bash(adopted, f"rm {relative}"),
        bash(adopted, f"ln -f README.md {relative}"),
    ):
        printed = answer(adopted, case)
        assert decision(printed) == "deny"
        assert "guard-log" in printed
    branch(adopted)
    assert decision(answer(adopted, edit(adopted, log))) == "deny"
    bare = tmp_path / "unadopted"
    bare.mkdir()
    repository(bare)
    assert decision(answer(bare, edit(bare, log_of(bare)))) == "deny"


def test_an_agent_in_a_worktree_cannot_write_the_log(adopted: Path) -> None:
    tree = adopted / ".claude/worktrees/agent-1"
    git(adopted, "worktree", "add", "-q", "-b", "feature/x", str(tree))
    log = log_of(adopted)
    climb = os.path.relpath(log, tree)
    for project in (tree, adopted, None):
        for case in (edit(tree, log), bash(tree, f"echo '{{}}' >> {climb}")):
            printed = answer(tree, case, project=project)
            assert decision(printed) == "deny"
            assert "guard-log" in printed


def test_the_plugin_record_is_a_locked_guard_path_in_every_checkout() -> None:
    hit = make_guard().locator.classify(
        Path("/elsewhere/.git") / events.PLUGIN_RECORD, destructive=False
    )
    assert hit is not None
    assert hit.group.locked
    assert hit.group.name == "plugin-record"


def record_of(root: Path) -> Path:
    return root / ".git" / events.PLUGIN_RECORD


def test_an_agent_cannot_write_the_plugin_record(adopted: Path, tmp_path: Path) -> None:
    record = record_of(adopted)
    relative = f".git/{events.PLUGIN_RECORD}"
    for case in (
        edit(adopted, record),
        bash(adopted, f"echo /tmp/other > {relative}"),
        bash(adopted, f"printf /tmp/other | tee {relative}"),
        bash(adopted, f"cp /tmp/other {relative}"),
        bash(adopted, f"rm {relative}"),
        bash(adopted, f"ln -f README.md {relative}"),
    ):
        printed = answer(adopted, case)
        assert decision(printed) == "deny"
        assert "plugin-record" in printed
    branch(adopted)
    assert decision(answer(adopted, edit(adopted, record))) == "deny"
    bare = tmp_path / "unadopted"
    bare.mkdir()
    repository(bare)
    assert decision(answer(bare, edit(bare, record_of(bare)))) == "deny"


def test_an_agent_in_a_worktree_cannot_write_the_plugin_record(adopted: Path) -> None:
    tree = adopted / ".claude/worktrees/agent-2"
    git(adopted, "worktree", "add", "-q", "-b", "feature/y", str(tree))
    record = record_of(adopted)
    climb = os.path.relpath(record, tree)
    for project in (tree, adopted, None):
        for case in (edit(tree, record), bash(tree, f"echo /tmp/other > {climb}")):
            printed = answer(tree, case, project=project)
            assert decision(printed) == "deny"
            assert "plugin-record" in printed
