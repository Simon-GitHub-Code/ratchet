"""``with_service``: boot, wait, run, stop, against real child processes on loopback."""

import argparse
import errno
import os
import re
import shlex
import signal
import socket
import subprocess
import sys
import threading
from pathlib import Path

import pytest

import gatelib
import with_service
from tests import servers
from tests.servers import NEEDS, SERVE, STOPS, gone, pid_of, python

READY = "http://127.0.0.1:{port}/"
SERVING = python(SERVE)
RECORDS_PID = "import os, sys\nopen(sys.argv[1], 'w').write(str(os.getpid()))\n"
PROBE = (
    "import os, sys, urllib.request as u\n"
    "status = u.urlopen(sys.argv[1]).status\n"
    "seen = [str(status), *(os.environ[name] for name in ('PORT', 'K', 'RATCHET_READY'))]\n"
    "open(sys.argv[2], 'w').write(' '.join(seen))"
)
FORKS_A_WORKER = (
    "import subprocess, sys\n"
    "worker = subprocess.Popen(['sleep', '60'])\n"
    "open(sys.argv[1], 'w').write(str(worker.pid))\n"
    f"{SERVE}"
)
ESCAPES = (
    "import subprocess, sys, time\n"
    "child = subprocess.Popen([sys.executable, '-c', sys.argv[2]], start_new_session=True)\n"
    "open(sys.argv[1], 'w').write(str(child.pid))\n"
    "time.sleep(60)"
)


def run(*args: str, capsys: pytest.CaptureFixture[str]) -> tuple[int, str]:
    code = with_service.main(["with_service.py", *args])
    return code, capsys.readouterr().out


def run_both(*args: str, capsys: pytest.CaptureFixture[str]) -> tuple[int, str, str]:
    code = with_service.main(["with_service.py", *args])
    seen = capsys.readouterr()
    return code, seen.out, seen.err


def test_smoke_boots_probes_and_stops_the_whole_process_tree(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    worker = tmp_path / "worker"
    boot = f"{python(FORKS_A_WORKER)} {worker}"
    code, out = run("--boot", boot, "--ready", READY, capsys=capsys)
    assert out == "with_service: green\n"
    assert code == 0
    assert gone(pid_of(worker)), "the worker the service forked did not outlive the stop"


def test_the_command_runs_while_the_service_is_up_and_sees_the_port(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    seen = tmp_path / "seen"
    code, out = run(
        *("--boot", SERVING, "--ready", READY, "--env", "K=v{port}"),
        *("--", sys.executable, "-c", PROBE, "http://127.0.0.1:{port}/", str(seen)),
        capsys=capsys,
    )
    status, port, value, ready = seen.read_text(encoding="utf-8").split()
    assert (code, out, status) == (0, "with_service: green\n", "200")
    assert value == f"v{port}", "{port} is replaced in --env and exported as PORT"
    assert ready == READY.format(port=port), "the ready URL is exported with its port filled in"
    assert not gatelib.listening(f"http://127.0.0.1:{port}/"), "the service is stopped"


def test_the_exit_code_is_the_commands_and_the_boot_tail_is_shown(
    capsys: pytest.CaptureFixture[str],
) -> None:
    boot = f"echo booted-fine; exec {SERVING}"
    code, out = run(
        *("--boot", boot, "--ready", READY),
        *("--", sys.executable, "-c", "exit(3)"),
        capsys=capsys,
    )
    assert code == 3
    assert "the command exited with code 3" in out
    assert "boot output (tail):\n  booted-fine" in out
    assert out.endswith("with_service: red\n")


def test_the_probe_is_ready_below_400_and_says_what_it_saw_otherwise() -> None:
    answers = iter([200, 302, 503, 404])
    with servers.server(lambda handler: servers.reply(handler, next(answers))) as url:
        assert [with_service.probe(url) for _ in range(4)] == [None, None, "HTTP 503", "HTTP 404"]
    assert with_service.probe(url) is not None, "a closed port is not ready"


def test_a_service_that_never_answers_is_red_with_its_output(
    capsys: pytest.CaptureFixture[str],
) -> None:
    boot = "echo starting; sleep 60"
    code, out = run("--boot", boot, "--ready", READY, "--timeout", "0.6", capsys=capsys)
    assert code == 1
    assert "did not answer within 0.6s; the last probe saw ConnectionRefusedError" in out
    assert "boot output (tail):\n  starting" in out


def test_a_service_that_exits_before_it_answers_is_red(
    capsys: pytest.CaptureFixture[str],
) -> None:
    boot = "echo boom; exit 4"
    code, out = run("--boot", boot, "--ready", READY, capsys=capsys)
    assert code == 1
    assert "the service exited with code 4 before http://127.0.0.1:" in out
    assert "boot output:\n  boom" in out


def test_an_early_exit_shows_what_the_service_wrote_on_stdout_and_stderr(
    capsys: pytest.CaptureFixture[str],
) -> None:
    boot = "echo to-stdout; echo to-stderr >&2; exit 2 # {port}"
    code, out, _ = run_both("--boot", boot, "--ready", READY, capsys=capsys)
    assert code == 1
    assert "the service exited with code 2 before" in out
    assert "boot output:\n  to-stdout\n  to-stderr\n" in out


def test_an_early_exit_with_long_output_keeps_the_first_lines_the_cause_is_usually_in(
    capsys: pytest.CaptureFixture[str],
) -> None:
    boot = "echo cause; for i in $(seq 100); do echo line-$i; done; exit 2 # {port}"
    code, out, _ = run_both("--boot", boot, "--ready", READY, capsys=capsys)
    assert code == 1
    assert "boot output:\n  cause\n" in out
    assert "... 41 lines left out ...\n" in out, "101 lines, 30 of them first and 30 last"
    assert "  line-100\n" in out
    assert "line-50\n" not in out


def test_an_early_exit_says_what_ran_where_and_for_how_long_even_when_it_printed_nothing(
    capsys: pytest.CaptureFixture[str],
) -> None:
    code, out = run("--boot", "exit 2 # {port}", "--ready", READY, capsys=capsys)
    assert code == 1
    said = re.search(
        r"the service exited with code 2 before \S+ answered, (\d+\.\d)s after"
        r" `exit 2 # (\d+)` started in (\S+)\n",
        out,
    )
    assert said is not None, out
    assert float(said.group(1)) < 30
    assert said.group(2) != "{port}", "the command is printed as it ran, the port filled in"
    assert said.group(3) == str(Path.cwd())
    assert "boot output: none, the boot command printed nothing\n" in out


def boots_of(counter: Path) -> int:
    return len(counter.read_text(encoding="utf-8").splitlines())


def test_a_lost_port_race_is_retried_once_on_a_new_port_and_says_so(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    ports = tmp_path / "ports"
    # Why: the first boot stands for a service that lost the race for its port.
    boot = (
        f'echo "$PORT" >> {ports}; [ "$(wc -l < {ports})" -gt 1 ] && exec {SERVING};'
        " echo first-boot-output; exit 2"
    )
    code, out, err = run_both("--boot", boot, "--ready", READY, capsys=capsys)
    assert (code, out) == (0, "with_service: green\n")
    assert "booting once more on a new port" in err
    assert "the service exited with code 2 before" in err
    assert "boot output:\n  first-boot-output" in err
    first, second = ports.read_text(encoding="utf-8").split()
    assert first != second, "the retry is on a fresh port"


def test_a_service_that_always_fails_is_retried_once_then_red_with_both_outputs(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    boots = tmp_path / "boots"
    boots.touch()
    boot = f'echo "boot-$(wc -l < {boots})" ; echo x >> {boots}; exit 3 # {{port}}'
    code, out, err = run_both("--boot", boot, "--ready", READY, capsys=capsys)
    assert code == 1
    assert boots_of(boots) == 2, "one retry and no more"
    assert "boot output:\n  boot-0" in err
    assert "boot output:\n  boot-1" in out
    assert out.endswith("with_service: red\n")


def test_a_port_the_caller_fixed_is_never_retried(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    boots = tmp_path / "boots"
    ready = f"http://127.0.0.1:{gatelib.free_port()}/"
    code, out, err = run_both(
        *("--boot", f"echo x >> {boots}; exit 2", "--ready", ready), capsys=capsys
    )
    assert code == 1
    assert boots_of(boots) == 1
    assert "booting once more" not in err
    assert "the service exited with code 2 before" in out


def test_a_service_that_never_answers_is_not_retried(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    boots = tmp_path / "boots"
    boot = f"echo x >> {boots}; sleep 60"
    code, _, err = run_both(*("--boot", boot, "--ready", READY, "--timeout", "0.6"), capsys=capsys)
    assert code == 1
    assert boots_of(boots) == 1, "a slow service is not a lost race"
    assert err == ""


def test_something_already_answering_on_the_port_is_red_and_nothing_boots(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    marker = tmp_path / "booted"
    with servers.server(lambda handler: servers.reply(handler, 200)) as url:
        code, out = run("--boot", f"touch {marker}", "--ready", f"{url}/", capsys=capsys)
    assert code == 1
    assert "already answers before the boot" in out
    assert not marker.exists()


def test_a_process_that_escaped_the_group_is_found_by_the_port_it_holds(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    escaped = tmp_path / "escaped"
    boot = f"{python(ESCAPES)} {escaped} {shlex.quote(SERVE)}"
    try:
        code, out = run("--boot", boot, "--ready", READY, capsys=capsys)
    finally:
        os.kill(pid_of(escaped), signal.SIGKILL)
    assert code == 1
    assert "still answers after the stop" in out


def test_a_signal_stops_the_command_and_the_service(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    service, command = tmp_path / "service", tmp_path / "command"
    boot = f"{python(RECORDS_PID + SERVE)} {service}"
    interrupt = (
        f"import signal, time\n{RECORDS_PID}os.kill({os.getpid()}, signal.SIGTERM)\ntime.sleep(60)"
    )
    code, out = run(
        *("--boot", boot, "--ready", READY),
        *("--", sys.executable, "-c", interrupt, str(command)),
        capsys=capsys,
    )
    assert code == 128 + signal.SIGTERM
    assert "interrupted by signal" in out
    assert gone(pid_of(service))
    assert gone(pid_of(command))


def test_a_signal_between_popen_and_the_try_still_stops_the_service(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    real, started = subprocess.Popen, []

    def popen_then_signal(*args: object, **kwargs: object) -> subprocess.Popen[bytes]:
        process = real(*args, **kwargs)  # type: ignore[call-overload]
        if not started:
            started.append(process.pid)
            signal.raise_signal(signal.SIGTERM)
        return process  # type: ignore[no-any-return]

    monkeypatch.setattr(subprocess, "Popen", popen_then_signal)
    try:
        code, out = run("--boot", "sleep 60", "--ready", READY, capsys=capsys)
        stopped = gone(started[0])
    finally:
        monkeypatch.undo()
        if not gone(started[0], wait=0):
            os.killpg(started[0], signal.SIGKILL)
    assert code == 128 + signal.SIGTERM
    assert "interrupted by signal" in out
    assert stopped, "the service started just before the signal was left running"


def test_a_boot_command_that_cannot_start_leaves_nothing_to_stop(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def cannot_start(*_args: object, **_kwargs: object) -> subprocess.Popen[bytes]:
        raise OSError(errno.EAGAIN, "no process slots")

    monkeypatch.setattr(subprocess, "Popen", cannot_start)
    with pytest.raises(OSError, match="no process slots"):
        run("--boot", "sleep 60", "--ready", READY, capsys=capsys)


def test_a_bad_ready_url_or_env_pair_is_a_usage_error() -> None:
    with pytest.raises(SystemExit) as caught:
        with_service.main(["with_service.py", "--boot", "true", "--ready", "ftp://x/"])
    assert caught.value.code == 2
    with pytest.raises(SystemExit) as caught:
        with_service.main(["with_service.py", "--boot", "true", "--ready", READY, "--env", "NOPE"])
    assert caught.value.code == 2


LOAD = Path(servers.__file__).parents[2] / "scripts" / "load_smoke.py"
BUSY = ["--seconds", "2.5", "--concurrency", "4", "--min-requests", "4", "--until-stopped"]
STOPPING = (
    "import os, sys\n"
    "open(sys.argv[1], 'w').write(str(os.path.exists(os.environ['RATCHET_STOPPING'])))"
)


def graceful(
    mode: str, *extra: str, capsys: pytest.CaptureFixture[str], limit: str = "2"
) -> tuple[int, str]:
    """The load smoke run inside a service of ``mode`` that is signalled 0.5 s in."""
    command = [sys.executable, str(LOAD), "--url", "http://127.0.0.1:{port}/work", *BUSY]
    return run(
        *("--boot", f"MODE={mode} exec {python(STOPS)}", "--ready", READY),
        *("--graceful", limit, "--signal-after", "0.5", *extra),
        *("--", *command),
        capsys=capsys,
    )


def test_a_service_that_drains_and_exits_zero_is_green_under_load(
    capsys: pytest.CaptureFixture[str],
) -> None:
    code, out = graceful("drain", capsys=capsys)
    assert out.endswith("with_service: green\n"), out
    assert code == 0


@pytest.mark.parametrize(
    ("mode", "said"),
    [
        ("drop", "/work: RemoteDisconnected\n"),
        ("late", "the service exited with code 1 "),
        ("ignore", "the service was still running 1s after SIGTERM"),
        ("slow", "the service was still running 1s after SIGTERM"),
        ("die", "the service exited with code 5 before it was signalled"),
    ],
)
def test_each_way_of_stopping_badly_is_red(
    mode: str, said: str, capfd: pytest.CaptureFixture[str]
) -> None:
    code, out = graceful(mode, capsys=capfd, limit="1")
    assert said in out, out
    assert code == 1
    assert out.endswith("with_service: red\n")


def test_the_marker_exists_only_from_the_signal_and_a_command_that_ends_first_is_red(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    seen = tmp_path / "seen"
    code, out = run(
        *("--boot", f"MODE=drain exec {python(STOPS)}", "--ready", READY, "--graceful", "2"),
        *("--signal-after", "30", "--", sys.executable, "-c", STOPPING, str(seen)),
        capsys=capsys,
    )
    assert seen.read_text(encoding="utf-8") == "False", "no signal yet, so no marker"
    assert "the command ended before the 30s signal: nothing was in flight" in out
    assert code == 1


@pytest.mark.skipif(not Path("/proc/net/tcp").exists(), reason="Linux shows the accept queue")
def test_the_accept_queue_of_the_listener_is_read_from_the_kernel() -> None:
    with socket.create_server(("127.0.0.1", 0)) as listener:
        port = listener.getsockname()[1]
        assert with_service.queued(port) == 0
        clients = [socket.create_connection(("127.0.0.1", port)) for _ in range(2)]
        assert with_service.queued(port) == 2
        listener.accept()[0].close()
        assert with_service.queued(port) == 1
        for client in clients:
            client.close()
    assert with_service.queued(port) is None, "no listener on the port"


def test_no_table_to_read_is_no_queue_seen(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(with_service, "TABLES", ("/nonexistent/tcp",))
    assert with_service.queued(80) is None


class Stops:
    """A service process that exits 0 on SIGTERM, and the queue lengths read before it."""

    def __init__(self, reads: list[int]) -> None:
        self.returncode: int | None = None
        self.reads = reads
        self.read_before_signal = -1

    def poll(self) -> int | None:
        return self.returncode

    def send_signal(self, _signum: int) -> None:
        self.read_before_signal, self.returncode = len(self.reads), 0

    def wait(self, timeout: float) -> int:  # noqa: ARG002 # -- the Popen signature
        return 0


def signalled_after(
    lengths: list[int | None], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> int:
    """How many queue lengths ``signal_midway`` read before it signalled; each from port 8123."""
    seen: list[int] = []
    left = iter(lengths)

    def queued(port: int) -> int | None:
        seen.append(port)
        return next(left)

    monkeypatch.setattr(with_service, "queued", queued)
    process = Stops(seen)
    options = argparse.Namespace(ready="http://127.0.0.1:8123/", signal_after=0.01, graceful=0.2)
    found = with_service.signal_midway(
        process,  # type: ignore[arg-type] # -- a stand-in for Popen
        options,
        tmp_path / "stopping",
        ended=threading.Event(),
    )
    assert found == []
    assert set(seen) == {8123}
    return process.read_before_signal


@pytest.mark.parametrize(("lengths", "reads"), [([2, 1, 0], 3), ([None], 1)])
def test_the_signal_waits_until_the_service_has_accepted_every_queued_connection(
    lengths: list[int | None], reads: int, *, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    assert signalled_after(lengths, tmp_path, monkeypatch) == reads


def test_a_queue_that_never_empties_is_signalled_after_the_graceful_seconds(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    lengths: list[int | None] = [1] * 10_000
    assert 1 < signalled_after(lengths, tmp_path, monkeypatch) < len(lengths)


def test_a_service_killed_by_the_signal_is_told_to_exec(
    capsys: pytest.CaptureFixture[str],
) -> None:
    boot = f"MODE=ignore exec {python(STOPS)}"
    shell_first = f"{boot} & wait"
    code, out = run(
        *("--boot", shell_first, "--ready", READY, "--graceful", "2", "--signal-after", "0.3"),
        *("--", sys.executable, "-c", "import time; time.sleep(1)"),
        capsys=capsys,
    )
    assert "was ended by signal 15, so boot with `exec`" in out, out
    assert code == 1


@pytest.mark.parametrize(
    "args",
    [
        ["--graceful", "0"],
        ["--graceful", "1", "--signal-after", "0", "--", "true"],
        ["--graceful", "1"],
        ["--refuses", "A", "--", "true"],
        ["--refuses", "A", "--graceful", "1", "--", "true"],
    ],
)
def test_a_bad_combination_of_the_new_flags_is_a_usage_error(
    args: list[str], capsys: pytest.CaptureFixture[str]
) -> None:
    with pytest.raises(SystemExit) as exit_:
        with_service.main(["with_service.py", "--boot", "true", "--ready", READY, *args])
    assert exit_.value.code == 2
    capsys.readouterr()


def refuses(
    mode: str,
    name: str,
    *,
    capsys: pytest.CaptureFixture[str],
    timeout: str = "20",
    env: str = "A=1",
) -> tuple[int, str]:
    boot = f"NAME={shlex.quote(name)} MODE={mode} exec {python(NEEDS)}"
    return run(
        *("--boot", boot, "--ready", READY, "--timeout", timeout, "--env", env, "--refuses", name),
        capsys=capsys,
    )


def test_a_service_that_names_the_missing_setting_and_exits_nonzero_refuses(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("DATABASE_URL", "x")
    code, out = refuses("refuse", "DATABASE_URL", capsys=capsys, env="DATABASE_URL=again")
    assert (code, out) == (0, "with_service: green\n")


@pytest.mark.parametrize(
    ("mode", "said"),
    [
        ("boot", "the service became ready without DATABASE_URL: it did not refuse"),
        ("zero", "the service exited with code 0 without DATABASE_URL: a refusal is non-zero"),
        ("silent", "the service exited with code 2 without DATABASE_URL but did not name it"),
        ("hang", "neither refused nor became ready within 0.8s without DATABASE_URL"),
    ],
)
def test_each_way_of_not_refusing_is_red_with_the_boot_output(
    mode: str, said: str, capsys: pytest.CaptureFixture[str]
) -> None:
    code, out = refuses(mode, "DATABASE_URL", capsys=capsys, timeout="0.8")
    assert said in out, out
    assert code == 1
    assert out.endswith("with_service: red\n")


def test_the_name_must_be_the_whole_setting_and_every_name_is_tried(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("DB", "x")
    boot = f"NAME=DB_URL MODE=refuse exec {python(NEEDS)}"
    code, out = run(
        *("--boot", boot, "--ready", READY, "--refuses", "DB", "--refuses", "DB_URL"),
        capsys=capsys,
    )
    assert code == 1
    assert out.splitlines()[0] == (
        "the service exited with code 1 without DB but did not name it"
    ), "DB_URL is not DB"
    assert out.count("without DB") == 1, "DB_URL itself was named"
