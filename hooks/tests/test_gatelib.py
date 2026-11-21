import contextlib
import errno
import http.server
import json
import os
import re
import signal
import socket
import subprocess
import sys
import tempfile
import threading
import time
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

import gatelib
import listeners
import unproven
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


def ephemeral() -> range:
    low, high = gatelib.PORT_RANGE.read_text(encoding="utf-8").split()
    return range(int(low), int(high) + 1)


def test_a_free_port_is_never_one_the_kernel_gives_an_outgoing_connection() -> None:
    ports = [gatelib.free_port() for _ in range(500)]
    assert all(gatelib.UNPRIVILEGED <= port < ephemeral().start for port in ports)
    assert len(set(ports)) > 1, "two gates picking at once rarely pick the same port"


@pytest.mark.parametrize("content", [None, "", "not a range\n"])
def test_where_the_platform_names_no_range_a_free_port_is_below_the_dynamic_ports(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, content: str | None
) -> None:
    told = tmp_path / "range"
    if content is not None:
        told.write_text(content, encoding="utf-8")
    monkeypatch.setattr(gatelib, "PORT_RANGE", told)
    assert all(gatelib.UNPRIVILEGED <= gatelib.free_port() < 49152 for _ in range(100))


@pytest.mark.parametrize("start", [1027, 1024, 600])
def test_with_no_port_free_below_the_range_the_kernel_picks_one(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, start: int
) -> None:
    told = tmp_path / "range"
    told.write_text(f"{start}\t60999\n", encoding="utf-8")
    real = ephemeral()
    monkeypatch.setattr(gatelib, "PORT_RANGE", told)
    with contextlib.ExitStack() as taken:
        for port in range(gatelib.UNPRIVILEGED, start):
            bound = taken.enter_context(socket.socket())
            with contextlib.suppress(OSError):
                bound.bind(("127.0.0.1", port))
        assert gatelib.free_port() in real


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


def test_reach_names_the_connection_before_it_waits_for_the_connect(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    order: list[str] = []
    sock = MagicMock()
    sock.connect_ex.return_value = errno.EINPROGRESS
    sock.getsockname.return_value = ("127.0.0.1", 5)
    sock.getsockopt.return_value = 0
    selector = MagicMock()
    selector.__enter__.return_value = selector
    selector.select.side_effect = lambda _: order.append("waited") or [object()]
    monkeypatch.setattr(gatelib.selectors, "DefaultSelector", lambda: selector)
    gatelib.reach(sock, ("127.0.0.1", 80), timeout=1, connecting=lambda _: order.append("named"))
    assert order == ["named", "waited"]


def test_http_status_says_why_a_connect_failed_or_that_it_timed_out() -> None:
    tried: list[gatelib.Connection] = []
    with pytest.raises(OSError, match="Network is unreachable"):
        gatelib.http_status("GET", "http://255.255.255.255/", connecting=tried.append)
    assert [each.host for each in tried] == ["255.255.255.255"], (
        "the connection is named even when the connect fails at once"
    )
    with socket.create_server(("127.0.0.1", 0), backlog=0) as listener:
        url = f"http://127.0.0.1:{listener.getsockname()[1]}/"
        with socket.create_connection(listener.getsockname()), pytest.raises(TimeoutError):
            gatelib.http_status("GET", url, timeout=0.2)


def test_an_ipv4_client_of_a_dual_stack_listener_is_named_alike_from_both_ends() -> None:
    seen_by_gate = gatelib.connection(("::ffff:127.0.0.1", 80, 0, 0), ("::ffff:127.0.0.1", 5, 0, 0))
    seen_by_client = gatelib.connection(("127.0.0.1", 80), ("127.0.0.1", 5))
    assert seen_by_gate == seen_by_client == gatelib.Connection("127.0.0.1", 80, "127.0.0.1", 5)
    assert gatelib.connection(("::1", 80, 0, 0), ("::1", 5, 0, 0)).host == "::1"


def test_the_stop_marker_is_replaced_whole_and_read_back(tmp_path: Path) -> None:
    marker = tmp_path / "stopping"
    first = gatelib.Connection("127.0.0.1", 80, "127.0.0.1", 5)
    then = gatelib.Connection("::1", 81, "::1", 6)
    gatelib.write_stopping(marker, gatelib.Stopping({80: None}, "none", frozenset({first})))
    told = gatelib.Stopping({80: None, 81: "shut"}, "no Linux", frozenset({then}), answered=True)
    gatelib.write_stopping(marker, told)
    assert gatelib.read_stopping(marker) == told
    assert [told.inexact(port) for port in (80, 81, 82)] == [None, "shut", "no Linux"]
    assert [path.name for path in tmp_path.iterdir()] == ["stopping"]


def test_asking_the_gate_waits_for_its_answer(tmp_path: Path) -> None:
    marker = tmp_path / "stopping"
    taken = frozenset({gatelib.Connection("127.0.0.1", 80, "127.0.0.1", 5)})
    gatelib.write_stopping(marker, gatelib.Stopping({80: None}, "none", taken))
    answer = gatelib.Stopping({80: "shut"}, "none", taken, answered=True)

    def gate() -> None:
        while not gatelib.asking(marker).exists():
            time.sleep(0.01)
        gatelib.write_stopping(marker, answer)

    answering = threading.Thread(target=gate)
    answering.start()
    assert gatelib.ask(marker) == answer
    answering.join()


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
    proc = tmp_path / "proc"
    for process in (inside, outside):
        entry = proc / str(process.pid)
        entry.mkdir(parents=True)
        (entry / "exe").symlink_to(Path(f"/proc/{process.pid}/exe").readlink())
    try:
        assert gatelib.end_running_from(tree.resolve(), proc) == [inside.pid]
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


@given(text=st.text(), current=st.text())
def test_created_gives_the_text_to_an_absent_file_and_leaves_any_other_alone(
    text: str, current: str
) -> None:
    want = gatelib.created(text)
    assert want(None) == text
    assert want(current) == current
    assert want(want(None)) == want(None)


@given(text=st.text(), current=st.one_of(st.none(), st.text()))
def test_chained_wants_apply_in_order_and_an_empty_chain_changes_nothing(
    text: str, current: str | None
) -> None:
    assert gatelib.chained()(current) == (current or "")
    both = gatelib.chained(gatelib.created(text), lambda made: f"{made}!")
    assert both(current) == f"{text if current is None else current}!"


def test_a_git_a_hook_pointed_at_another_repository_still_reads_the_repository_it_is_given(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    here = repository(tmp_path / "here")
    elsewhere = repository(tmp_path / "elsewhere")
    monkeypatch.setenv("GIT_DIR", str(elsewhere / ".git"))
    monkeypatch.setenv("GIT_WORK_TREE", str(elsewhere))
    assert Path(gatelib.git_checked(here, "rev-parse", "--show-toplevel").strip()) == here.resolve()


def test_a_git_that_fails_raises_instead_of_answering(tmp_path: Path) -> None:
    gatelib.git_checked(tmp_path, "init", "-q")
    assert gatelib.git_checked(tmp_path, "check-ignore", "x", ok=(0, 1)) == ""
    with pytest.raises(subprocess.CalledProcessError):
        gatelib.git_checked(tmp_path, "check-ignore", "x")


def test_a_grandchild_started_through_the_interpreter_helper_ignores_the_environment(
    tmp_path: Path,
) -> None:
    flag = tmp_path / "ran-evil"
    folder = tmp_path / "hijack"
    folder.mkdir()
    (folder / "json.py").write_text(f"open({str(flag)!r}, 'w').close()\n", encoding="utf-8")
    scripts = str(Path(gatelib.__file__).resolve().parent)
    start = f"import sys; sys.path.insert(0, {scripts!r}); import gatelib, subprocess; "
    child = start + "subprocess.run([*gatelib.interpreter(), '-c', 'import json'], check=True)"
    parent = start + f"subprocess.run([*gatelib.interpreter(), '-c', {child!r}], check=True)"
    done = subprocess.run(  # noqa: S603 # -- a fixed argv, no shell
        ["python3", "-E", "-s", "-c", parent],  # noqa: S607 # -- PATH
        env={**os.environ, "PYTHONPATH": str(folder)},
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=False,
    )
    assert done.returncode == 0, done.stderr
    assert not flag.exists()


def test_a_child_keeps_the_options_its_parent_was_started_with(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    flags = SimpleNamespace(ignore_environment=1, no_user_site=1, dont_write_bytecode=1)
    monkeypatch.setattr(sys, "flags", flags)
    monkeypatch.setattr(sys, "pycache_prefix", "/private/cache")
    options = ["-E", "-s", "-B", "-X", "pycache_prefix=/private/cache"]
    assert gatelib.interpreter()[1:] == options
    assert gatelib.interpreter(inline=True)[1:] == [*options, "-P"]


def test_a_listing_held_open_reads_a_tree_once_and_one_not_held_reads_each_time(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = repository(tmp_path)
    listed: list[Path] = []
    real = gatelib.git

    def counting(where: Path, *args: str) -> str | None:
        if args[0] == "ls-files":
            listed.append(where)
        return real(where, *args)

    monkeypatch.setattr(gatelib, "git", counting)
    held = gatelib.listings.set({})
    try:
        first = gatelib.tracked_paths(root)
        write(root, "late.txt", "x\n")
        assert gatelib.tracked_paths(root) == first
        assert "late.txt" not in gatelib.tracked_paths(root)
        assert len(listed) == 1
    finally:
        gatelib.listings.reset(held)
    assert "late.txt" in gatelib.tracked_paths(root)
    assert len(listed) == 2


def test_an_export_holds_what_git_holds_at_the_ref_and_none_of_what_the_work_tree_says(
    tmp_path: Path,
) -> None:
    root = repository(tmp_path / "repo", {"a.txt": "committed\n", "run.sh": "#!/bin/sh\n"})
    git(root, "update-index", "--chmod=+x", "run.sh")
    git(
        root, "update-index", "--add", "--cacheinfo", f"160000,{git(root, 'rev-parse', 'HEAD')},sub"
    )
    git(root, "commit", "-q", "-m", "feat(core): an executable and a submodule")
    ref = git(root, "rev-parse", "HEAD")
    write(root, "a.txt", "edited and not committed\n")
    write(root, "untracked.txt", "x\n")
    target = tmp_path / "copy"
    before = git(root, "status", "--porcelain")
    gatelib.export(root, ref, target)
    assert git(root, "status", "--porcelain") == before
    assert git(root, "rev-parse", "HEAD") == ref
    assert (target / "a.txt").read_text(encoding="utf-8") == "committed\n"
    assert os.access(target / "run.sh", os.X_OK)
    assert not (target / "sub").exists()
    assert not (target / "untracked.txt").exists()
    assert git(target, "ls-files").splitlines() == ["a.txt", "run.sh"]


def test_what_a_leader_left_in_its_group_is_ended_after_the_leader_is_reaped(
    tmp_path: Path,
) -> None:
    leader = start(
        "import subprocess, sys\n"
        "from pathlib import Path\n"
        "child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'])\n"
        "Path(sys.argv[1], 'child').write_text(str(child.pid))\n",
        tmp_path,
    )
    child = wait_for(tmp_path / "child")
    leader.wait(timeout=30)

    def running() -> bool:
        try:
            return Path(f"/proc/{child}/stat").read_text().rsplit(")", 1)[1].split()[0] != "Z"
        except OSError:
            return False

    assert running()
    assert gatelib.end_group(leader.pid)
    deadline = time.monotonic() + 10
    while running() and time.monotonic() < deadline:
        time.sleep(0.02)
    assert not running()
    assert not gatelib.end_group(leader.pid)


def test_a_process_whose_program_is_outside_the_tree_is_left_whatever_else_the_listing_holds(
    tmp_path: Path,
) -> None:
    tree, proc = tmp_path / "tree", tmp_path / "proc"
    inside = start("import time\ntime.sleep(60)\n", tmp_path)
    outside = start("import time\ntime.sleep(60)\n", tmp_path)
    try:
        for process, program in (
            (inside, tree / "bin" / "service"),
            (outside, tmp_path / "elsewhere" / "service"),
        ):
            entry = proc / str(process.pid)
            entry.mkdir(parents=True)
            (entry / "exe").symlink_to(program)
        assert gatelib.end_running_from(tree, proc) == [inside.pid]
        assert inside.wait(timeout=5) == -signal.SIGKILL
        assert outside.poll() is None
    finally:
        for process in (inside, outside):
            process.kill()
            process.wait()


def test_a_stop_marker_is_replaced_whole_so_a_reader_never_sees_half_of_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    marker = tmp_path / "stopping"
    before = gatelib.Stopping({80: None}, "none", frozenset())
    after = gatelib.Stopping({80: "shut", 81: None}, "no Linux", frozenset(), answered=True)
    gatelib.write_stopping(marker, before)
    write_text = Path.write_text
    reads: list[str] = []

    def halfway(self: Path, data: str, encoding: str | None = None) -> int:
        write_text(self, data[: len(data) // 2], encoding=encoding)
        reads.append(marker.read_text(encoding="utf-8"))
        return write_text(self, data, encoding=encoding)

    monkeypatch.setattr(Path, "write_text", halfway)
    gatelib.write_stopping(marker, after)
    monkeypatch.undo()
    assert [json.loads(read)["ports"] for read in reads] == [{"80": None}]
    assert gatelib.read_stopping(marker) == after


def side_merged(root: Path) -> dict[str, str]:
    repository(root)
    shas = {"first": git(root, "rev-parse", "HEAD")}
    git(root, "switch", "-q", "-c", "side")
    shas["aside"] = commit(root, "feat(core): on the side")
    git(root, "switch", "-q", "-c", "never")
    shas["never"] = commit(root, "feat(core): never merged")
    git(root, "switch", "-q", "main")
    shas["second"] = commit(root, "feat(core): second")
    git(root, "merge", "-q", "--no-ff", "-m", "Merge side", "side")
    shas["merged"] = git(root, "rev-parse", "HEAD")
    return shas


def test_the_chain_is_the_first_parent_line_and_each_merge_holds_the_runs_it_brought(
    tmp_path: Path,
) -> None:
    shas = side_merged(tmp_path)
    aside = shas["aside"]
    asked = [aside, shas["first"], shas["never"], "--all", aside.upper(), aside[:12]]
    assert gatelib.chain(tmp_path, "main", asked) == (
        unproven.Landed(shas["merged"], "Merge side", frozenset({aside})),
        unproven.Landed(shas["second"], "feat(core): second"),
        unproven.Landed(shas["first"], "feat(core): the clean state"),
    )
    assert gatelib.chain(tmp_path, "nowhere", asked) == ()


def undecodable(root: Path, parent: str, subject: bytes) -> str:
    tree, above = git(root, "rev-parse", f"{parent}^{{tree}}"), git(root, "rev-parse", parent)
    body = (
        (
            f"tree {tree}\nparent {above}\nauthor A <a@example.com> 0 +0000\n"
            "committer A <a@example.com> 0 +0000\nencoding no-such-charset\n\n"
        ).encode()
        + subject
        + b"\n"
    )
    return (
        subprocess.run(  # noqa: S603 # -- a fixed argv, no shell
            ["git", "-C", str(root), "hash-object", "-t", "commit", "-w", "--stdin"],  # noqa: S607 # -- PATH
            input=body,
            capture_output=True,
            check=True,
        )
        .stdout.decode()
        .strip()
    )


def test_a_subject_that_is_not_utf_8_is_read_with_a_stand_in(tmp_path: Path) -> None:
    made = undecodable(repository(tmp_path), "HEAD", b"caf\xe9 \xff")
    assert gatelib.chain(tmp_path, made)[0] == unproven.Landed(made, "caf� �")


def two_merges(root: Path) -> dict[str, str]:
    repository(root)
    shas = {"base": git(root, "rev-parse", "HEAD")}
    for name in ("older", "newer"):
        git(root, "switch", "-q", "-c", name, shas["base"])
        shas[f"by {name}"] = commit(root, f"feat(core): brought by the {name} merge")
    git(root, "switch", "-q", "main")
    for name in ("older", "newer"):
        git(root, "merge", "-q", "--no-ff", "-m", f"Merge {name}", name)
        shas[name] = git(root, "rev-parse", "HEAD")
    shas["tip"] = commit(root, "feat(core): after both merges")
    return shas


@pytest.mark.parametrize(("red", "green"), [("older", "newer"), ("newer", "older")])
def test_an_off_line_red_sits_on_the_merge_that_brought_it_whichever_merge_is_green(
    tmp_path: Path, red: str, green: str
) -> None:
    shas = two_merges(tmp_path)
    when = datetime(2026, 11, 20, 9, tzinfo=UTC)
    runs = (
        unproven.DeepRun(shas[f"by {red}"], "red", when, "the red"),
        unproven.DeepRun(shas[green], "green", when, "the green"),
    )
    line = gatelib.chain(tmp_path, "main", [run.commit for run in runs])
    assert [landed.merged for landed in line] == [
        frozenset(),
        {shas["by newer"]} if red == "newer" else frozenset(),
        {shas["by older"]} if red == "older" else frozenset(),
        frozenset(),
    ]
    deep = unproven.Deep(runs, "start one")
    hold = unproven.held(line, deep, gated=False, bound=None)
    if red == "older":
        assert hold is None, "a green on the newer merge judged a tree that holds the red's commit"
    else:
        assert hold is not None
        assert hold.code == "tier2"
        assert unproven.speaks_for(line, deep, runs[0]) == (line[1],)


STEPS = st.lists(
    st.tuples(
        st.sampled_from(["commit", "merge", "pull", "octopus"]),
        st.integers(0, 5),
        st.integers(1, 2),
    ),
    min_size=1,
    max_size=8,
)


def grown(root: Path, steps: list[tuple[str, int, int]]) -> list[str]:
    tree = git(root, "rev-parse", "HEAD^{tree}")
    shas = [git(root, "rev-parse", "HEAD")]

    def made(*parents: int) -> int:
        on = [argument for one in parents for argument in ("-p", shas[one])]
        shas.append(git(root, "commit-tree", tree, *on, "-m", f"commit {len(shas)}"))
        return len(shas) - 1

    tip = 0
    for kind, back, length in steps:
        if kind == "commit":
            tip = made(tip)
            continue
        side = max(0, len(shas) - 1 - back)
        for _ in range(length):
            side = made(side)
        if kind == "merge":
            tip = made(tip, side)
        elif kind == "pull":
            tip = made(side, tip)
        else:
            tip = made(tip, side, made(max(0, len(shas) - 2 - back)))
    return shas


def one_walk_per_commit(root: Path, tip: str, commits: list[str]) -> tuple[unproven.Landed, ...]:
    listed = gatelib.git(root, "log", "--first-parent", "--format=%H %s", tip, "--") or ""
    line = [row.partition(" ")[::2] for row in listed.splitlines()]
    where = {sha: index for index, (sha, _) in enumerate(line)}
    merged: dict[int, set[str]] = {}
    for sha in {sha for sha in commits if re.fullmatch("[0-9a-f]{40}", sha)} - where.keys():
        later = gatelib.git(root, "rev-list", "--ancestry-path", f"{sha}..{tip}", "--") or ""
        landing = max((where[one] for one in later.split() if one in where), default=None)
        if landing is not None:
            merged.setdefault(landing, set()).add(sha)
    return tuple(
        unproven.Landed(sha, subject, frozenset(merged.get(index, ())))
        for index, (sha, subject) in enumerate(line)
    )


@settings(max_examples=40, deadline=None)
@given(STEPS, st.integers(0, 40))
def test_the_chain_places_every_commit_where_one_walk_per_commit_places_it(
    steps: list[tuple[str, int, int]], at: int
) -> None:
    with tempfile.TemporaryDirectory() as scratch:
        root = repository(Path(scratch) / "repo")
        shas = grown(root, steps)
        tip = shas[at % len(shas)]
        asked = [*shas, "f" * 40, "--all"]
        assert gatelib.chain(root, tip, asked) == one_walk_per_commit(root, tip, asked)


def test_the_chain_reads_git_as_often_for_one_run_off_its_line_as_for_many(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repository(tmp_path)
    shas = grown(tmp_path, [("merge", 0, 2), ("commit", 0, 1)] * 8 + [("pull", 3, 1)])
    calls: list[object] = []
    real = subprocess.run

    def counted(*args: Any, **kwargs: Any) -> subprocess.CompletedProcess[Any]:  # noqa: ANN401
        calls.append(args[0])
        return real(*args, **kwargs)

    monkeypatch.setattr(subprocess, "run", counted)
    line = gatelib.chain(tmp_path, shas[-1], shas[1:2])
    one = len(calls)
    many = gatelib.chain(tmp_path, shas[-1], shas)
    assert len(calls) - one == one, calls
    assert sum(len(landed.merged) for landed in many) > sum(len(landed.merged) for landed in line)
