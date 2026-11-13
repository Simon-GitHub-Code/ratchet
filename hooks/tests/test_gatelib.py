"""The gate library: one merge base for every gate, the changed paths, the thresholds file."""

import http.server
import signal
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any

import pytest

import gatelib
import listeners
from tests import servers
from tests.repos import branch, commit, git, repository, write


def test_git_answers_none_outside_a_repository(tmp_path: Path) -> None:
    assert gatelib.git(tmp_path, "rev-parse", "HEAD") is None
    assert gatelib.git(repository(tmp_path), "rev-parse", "--abbrev-ref", "HEAD") == "main"


def test_default_branches_prefer_the_remote_head_then_the_binding(tmp_path: Path) -> None:
    root = repository(tmp_path)
    assert gatelib.default_branches(root) == ["origin/main", "main", "origin/master", "master"]
    write(root, "docs/process/binding.md", "# Binding\n\nNo default branch line.\n")
    assert gatelib.default_branches(root)[0] == "origin/main"
    write(root, "docs/process/binding.md", "# Binding\n\n- Default branch: `trunk`\n")
    assert gatelib.default_branches(root)[:2] == ["origin/trunk", "trunk"]
    upstream = tmp_path.parent / f"{tmp_path.name}-upstream.git"
    git(root, "init", "-q", "--bare", str(upstream))
    git(root, "remote", "add", "origin", str(upstream))
    git(root, "push", "-q", "origin", "main")
    git(root, "remote", "set-head", "origin", "main")
    assert gatelib.default_branches(root)[0] == "origin/main"


def test_base_commit_is_the_merge_base_or_the_parent_on_the_default_branch(
    tmp_path: Path,
) -> None:
    root = repository(tmp_path)
    assert gatelib.base_commit(root) is None, "one commit on main has no before"
    first = git(root, "rev-parse", "HEAD")
    second = commit(root, "feat(core): second")
    assert gatelib.base_commit(root) == first, "on main, before is HEAD~1"
    branch(root)
    assert gatelib.base_commit(root) == second, "a fresh branch compares with its tip"
    write(root, "x.txt", "x\n")
    third = commit(root, "feat(core): third")
    assert gatelib.base_commit(root) == second
    assert gatelib.base_commit(root, "main") == second
    assert gatelib.base_commit(root, "nope") == second, "an unknown base falls back to HEAD~1"
    assert third != second


def test_changed_and_tracked_paths_see_the_work_tree(tmp_path: Path) -> None:
    root = repository(tmp_path, {"a.txt": "a\n", "b/c.txt": "c\n"})
    base = git(root, "rev-parse", "HEAD")
    branch(root)
    write(root, "a.txt", "changed\n")
    write(root, "new.txt", "new\n")
    (root / "b/c.txt").unlink()
    assert gatelib.changed_paths(root, base) == ["a.txt", "b/c.txt", "new.txt"]
    assert gatelib.tracked_paths(root) == ["a.txt", "new.txt"]
    assert gatelib.tracked_paths(root, base) == ["a.txt", "b/c.txt"]
    assert gatelib.show(root, base, "a.txt") == "a"
    assert gatelib.show(root, base, "missing") is None
    assert gatelib.changed_paths(tmp_path / "nowhere", base) == []
    assert gatelib.tracked_paths(tmp_path / "nowhere") == []
    assert gatelib.tracked_paths(tmp_path / "nowhere", "HEAD") == []


def test_subjects_skip_merges(tmp_path: Path) -> None:
    root = repository(tmp_path)
    base = git(root, "rev-parse", "HEAD")
    branch(root)
    commit(root, "feat(core): one")
    git(root, "checkout", "-q", "main")
    commit(root, "feat(core): two")
    git(root, "merge", "-q", "--no-ff", "--no-edit", "change")
    assert gatelib.subjects(root, base) == ["feat(core): two", "feat(core): one"]
    assert gatelib.subjects(tmp_path / "nowhere", base) == []


def test_read_text_handles_missing_and_binary_files(tmp_path: Path) -> None:
    assert gatelib.read_text(tmp_path / "missing") is None
    (tmp_path / "bin").write_bytes(b"\x00\x01")
    assert gatelib.read_text(tmp_path / "bin") is None
    (tmp_path / "text").write_bytes(b"caf\xc3\xa9 \xff")
    assert gatelib.read_text(tmp_path / "text") == "café �"


def test_thresholds_and_tables(tmp_path: Path) -> None:
    assert gatelib.thresholds(tmp_path) == {}
    write(tmp_path, "quality/thresholds.toml", "[ceilings.change]\nfiles = 3\n")
    data = gatelib.thresholds(tmp_path)
    assert gatelib.table(data, "ceilings", "change") == {"files": 3}
    assert gatelib.table(data, "ceilings", "change", "files") == {}
    assert gatelib.table(data, "nope") == {}


def test_matches_spans_segments_with_a_double_star() -> None:
    assert gatelib.matches("src/core/a.py", ["src/core/**"])
    assert not gatelib.matches("src/core/a.py", ["src/*.py"])
    assert not gatelib.matches("src/core/a.py", [])


def test_report_prints_each_problem_and_the_verdict(capsys: pytest.CaptureFixture[str]) -> None:
    assert gatelib.report("x", []) == 0
    assert capsys.readouterr().out == "x: green\n"
    assert gatelib.report("x", ["one", "two"], "abcdef0123456789") == 1
    assert capsys.readouterr().out == "one\ntwo\nx: red against abcdef012345\n"


def test_a_free_port_can_be_bound_and_then_listens() -> None:
    port = gatelib.free_port()
    assert 0 < port < 65536
    with servers.server(lambda handler: servers.reply(handler, 200)) as url:
        assert gatelib.listening(url)
    assert not gatelib.listening(url), "closed again"
    assert not gatelib.listening(f"http://127.0.0.1:{port}"), "nothing took the free port"
    assert not gatelib.listening(f"https://127.0.0.1:{port}"), "https defaults to 443 otherwise"


def test_http_status_reads_the_status_and_sends_a_json_body() -> None:
    seen: list[tuple[str, str, bytes, str]] = []

    def handle(handler: http.server.BaseHTTPRequestHandler) -> None:
        seen.append(
            (
                handler.command,
                handler.path,
                getattr(handler, "body", b""),
                handler.headers.get("Content-Type", ""),
            )
        )
        servers.reply(handler, 201 if handler.command == "POST" else 302)

    with servers.server(handle) as url:
        assert gatelib.http_status("GET", f"{url}?a=1") == 302, "redirects are not followed"
        assert gatelib.http_status("POST", f"{url}/x", body=b"{}") == 201
    assert seen == [("GET", "/?a=1", b"", ""), ("POST", "/x", b"{}", "application/json")]


def test_http_status_refuses_what_is_not_an_http_url_and_raises_for_a_dead_port() -> None:
    with pytest.raises(ValueError, match="not an http"):
        gatelib.http_status("GET", "ftp://127.0.0.1/")
    with pytest.raises(ValueError, match="not an http"):
        gatelib.http_status("GET", "http:///path")
    port = gatelib.free_port()
    with pytest.raises(ConnectionRefusedError):
        gatelib.http_status("GET", f"http://127.0.0.1:{port}/")
    with (
        servers.server(lambda handler: servers.reply(handler, 200)) as url,
        pytest.raises(OSError),  # noqa: PT011 # -- TLS to a plain port: a reset or a timeout
    ):
        gatelib.http_status("GET", url.replace("http:", "https:"), timeout=0.5)


@pytest.mark.parametrize("scheme", ["http", "https"])
def test_http_status_names_the_client_port_however_soon_the_connection_is_reset(
    scheme: str,
) -> None:
    # Why: the load smoke matches a connection reset after the stop signal against the ones the
    # gate took, by both of its ends. A reset can land before the connect returns, or during the
    # TLS handshake, and the connection must be named all the same, as the gate names it.
    with socket.create_server(("127.0.0.1", 0)) as listener:
        clients: list[gatelib.Connection] = []
        accepted: list[gatelib.Connection] = []
        url = f"{scheme}://127.0.0.1:{listener.getsockname()[1]}/"

        def cut() -> None:
            connection, client = listener.accept()
            accepted.append(gatelib.connection(connection.getsockname(), client))
            connection.setsockopt(socket.SOL_SOCKET, socket.SO_LINGER, listeners.RESET)
            connection.close()

        cutter = threading.Thread(target=cut)
        cutter.start()
        with pytest.raises(OSError):  # noqa: PT011 # -- a reset, or a broken pipe while sending
            gatelib.http_status("GET", url, timeout=5, connecting=clients.append)
        cutter.join()
    assert clients == accepted


def test_http_status_says_why_a_connect_failed_or_that_it_timed_out() -> None:
    tried: list[gatelib.Connection] = []
    with pytest.raises(OSError, match="Network is unreachable"):
        gatelib.http_status("GET", "http://255.255.255.255/", connecting=tried.append)
    assert [each.host for each in tried] == ["255.255.255.255"], (
        "the connection is named even when the connect fails at once"
    )
    # A listener whose accept queue is full drops the next connect's SYN, so it never connects.
    with socket.create_server(("127.0.0.1", 0), backlog=0) as listener:
        url = f"http://127.0.0.1:{listener.getsockname()[1]}/"
        with socket.create_connection(listener.getsockname()), pytest.raises(TimeoutError):
            gatelib.http_status("GET", url, timeout=0.2)


def test_an_ipv4_client_of_a_dual_stack_listener_is_named_alike_from_both_ends() -> None:
    # Why: a listener on [::] sees an IPv4 client mapped into IPv6; the client writes it as IPv4.
    seen_by_gate = gatelib.connection(("::ffff:127.0.0.1", 80, 0, 0), ("::ffff:127.0.0.1", 5, 0, 0))
    seen_by_client = gatelib.connection(("127.0.0.1", 80), ("127.0.0.1", 5))
    assert seen_by_gate == seen_by_client == gatelib.Connection("127.0.0.1", 80, "127.0.0.1", 5)
    assert gatelib.connection(("::1", 80, 0, 0), ("::1", 5, 0, 0)).host == "::1"


def test_the_stop_marker_is_replaced_whole_and_read_back(tmp_path: Path) -> None:
    marker = tmp_path / "stopping"
    first = gatelib.Connection("127.0.0.1", 80, "127.0.0.1", 5)
    then = gatelib.Connection("::1", 81, "::1", 6)
    gatelib.write_stopping(marker, gatelib.Stopping(frozenset({first}), None))
    gatelib.write_stopping(marker, gatelib.Stopping(frozenset({then}), "no Linux"))
    assert gatelib.read_stopping(marker) == gatelib.Stopping(frozenset({then}), "no Linux")
    assert [path.name for path in tmp_path.iterdir()] == ["stopping"]


def test_interrupts_raise_once_then_ignore_and_restore_the_handlers() -> None:
    before = signal.getsignal(signal.SIGTERM)
    with gatelib.interrupts_raise():
        with pytest.raises(gatelib.Interrupted) as caught:
            signal.raise_signal(signal.SIGTERM)
        assert caught.value.signum == signal.SIGTERM
        assert "signal" in str(caught.value)
        signal.raise_signal(signal.SIGTERM)
        signal.raise_signal(signal.SIGINT)
    assert signal.getsignal(signal.SIGTERM) is before


def test_a_signal_inside_signals_held_waits_for_the_end_of_the_block() -> None:
    reached = []

    def hold() -> None:
        with gatelib.signals_held():
            signal.raise_signal(signal.SIGTERM)
            signal.raise_signal(signal.SIGINT)
            reached.append("the block ran on")
        reached.append("not reached")

    with gatelib.interrupts_raise(), pytest.raises(gatelib.Interrupted) as caught:
        hold()
    assert reached == ["the block ran on"]
    assert caught.value.signum == signal.SIGTERM


def test_signals_held_without_a_signal_raises_nothing_and_forgets_the_last_one() -> None:
    with gatelib.interrupts_raise():
        with gatelib.signals_held():
            pass
        with pytest.raises(gatelib.Interrupted):
            signal.raise_signal(signal.SIGHUP)


def start(code: str, tmp_path: Path) -> subprocess.Popen[bytes]:
    return subprocess.Popen(  # noqa: S603 # -- a fixed argv, no shell
        [sys.executable, "-c", code, str(tmp_path)], start_new_session=True
    )


def wait_for(path: Path) -> int:
    deadline = time.monotonic() + 30
    while not path.exists() and time.monotonic() < deadline:
        time.sleep(0.02)
    return servers.pid_of(path)


LEADER_AND_STUBBORN_CHILD = """
import signal, subprocess, sys, time, os
signal.signal(signal.SIGTERM, signal.SIG_IGN)
child = subprocess.Popen(["sleep", "60"])
signal.signal(signal.SIGTERM, signal.SIG_DFL)
open(sys.argv[1] + "/child.tmp", "w").write(str(child.pid))
os.rename(sys.argv[1] + "/child.tmp", sys.argv[1] + "/child")
time.sleep(60)
"""
IGNORES_TERM = """
import signal, sys, time, os
signal.signal(signal.SIGTERM, signal.SIG_IGN)
open(sys.argv[1] + "/leader.tmp", "w").write(str(os.getpid()))
os.rename(sys.argv[1] + "/leader.tmp", sys.argv[1] + "/leader")
time.sleep(60)
"""


def test_stop_group_ends_the_leader_and_the_worker_that_ignores_sigterm(tmp_path: Path) -> None:
    leader = start(LEADER_AND_STUBBORN_CHILD, tmp_path)
    child = wait_for(tmp_path / "child")
    assert servers.alive(child)
    gatelib.stop_group(leader, grace=5, settle=0.3)
    assert leader.returncode == -signal.SIGTERM
    assert servers.gone(child)


def test_stop_group_kills_a_leader_that_ignores_sigterm(tmp_path: Path) -> None:
    leader = start(IGNORES_TERM, tmp_path)
    wait_for(tmp_path / "leader")
    gatelib.stop_group(leader, grace=0.3, settle=0.3)
    assert leader.returncode == -signal.SIGKILL


def test_stop_group_on_a_process_that_already_ended(tmp_path: Path) -> None:
    done = start("pass", tmp_path)
    done.wait()
    gatelib.stop_group(done, grace=1, settle=0.1)
    assert done.returncode == 0


def test_run_command_reports_the_exit_code_and_a_command_that_cannot_run() -> None:
    assert gatelib.run_command([sys.executable, "-c", "pass"], {}) == (0, [])
    assert gatelib.run_command([sys.executable, "-c", "exit(7)"], {}) == (
        7,
        ["the command exited with code 7"],
    )
    killed = "import os, signal; os.kill(os.getpid(), signal.SIGKILL)"
    assert gatelib.run_command([sys.executable, "-c", killed], {}) == (
        128 + signal.SIGKILL,
        [f"the command exited with code {128 + signal.SIGKILL}"],
    )
    code, problems = gatelib.run_command(["/nonexistent/command"], {})
    assert code == 1
    assert problems[0].startswith("cannot run /nonexistent/command: ")
    with pytest.raises(ValueError, match="null"):
        gatelib.run_command(["bad\0name"], {})


def test_a_signal_just_after_the_command_starts_still_stops_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    started: list[subprocess.Popen[bytes]] = []
    real = subprocess.Popen

    def start_then_signal(*args: Any, **kwargs: Any) -> subprocess.Popen[bytes]:  # noqa: ANN401
        process = real(*args, **kwargs)
        started.append(process)
        signal.raise_signal(signal.SIGTERM)
        return process

    monkeypatch.setattr(subprocess, "Popen", start_then_signal)
    sleeper = [sys.executable, "-c", "import time; time.sleep(60)"]
    with gatelib.interrupts_raise(), pytest.raises(gatelib.Interrupted):
        gatelib.run_command(sleeper, {})
    assert started[0].poll() is not None


def test_the_state_directory_is_made_once_and_ignores_itself(tmp_path: Path) -> None:
    root = repository(tmp_path)
    state = gatelib.state_dir(root)
    assert state == root / ".ratchet"
    (state / "timing.jsonl").write_text("{}\n", encoding="utf-8")
    assert git(root, "status", "--porcelain") == ""
    (state / ".gitignore").write_text("*\n!kept\n", encoding="utf-8")
    assert gatelib.state_dir(root) == state
    assert (state / ".gitignore").read_text(encoding="utf-8") == "*\n!kept\n", "it is the owner's"


def test_a_process_run_from_a_tree_is_ended_and_every_other_one_is_left(tmp_path: Path) -> None:
    tree = tmp_path / "tree"
    inside = servers.sleeper(tree)
    outside = start("import time\ntime.sleep(60)\n", tmp_path)
    try:
        assert gatelib.end_running_from(tree.resolve()) == [inside.pid]
        assert inside.wait(timeout=5) == -signal.SIGKILL
        assert outside.poll() is None
    finally:
        for process in (inside, outside):
            process.kill()
            process.wait()


def test_a_process_that_is_gone_or_cannot_be_read_is_passed_over(tmp_path: Path) -> None:
    tree = tmp_path / "tree"
    proc = tmp_path / "proc"
    gone = proc / "999999999"
    gone.mkdir(parents=True)
    (gone / "exe").symlink_to(tree / "target" / "service")
    (proc / "12").mkdir()
    assert gatelib.end_running_from(tree, proc) == []
    assert gatelib.end_running_from(tree, tmp_path / "no-such-directory") == []
