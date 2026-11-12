"""``with_service``: boot, wait, run, stop, against real child processes on loopback."""

import errno
import os
import shlex
import signal
import subprocess
import sys
from pathlib import Path

import pytest

import gatelib
import with_service
from tests import servers
from tests.servers import SERVE, gone, pid_of, python

READY = "http://127.0.0.1:{port}/"
SERVING = python(SERVE)
RECORDS_PID = "import os, sys\nopen(sys.argv[1], 'w').write(str(os.getpid()))\n"
PROBE = (
    "import os, sys, urllib.request as u\n"
    "status = u.urlopen(sys.argv[1]).status\n"
    "open(sys.argv[2], 'w').write(f'{status} {os.environ[\"PORT\"]} {os.environ[\"K\"]}')"
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


def test_smoke_boots_probes_and_stops_the_whole_process_tree(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    worker = tmp_path / "worker"
    boot = f"{python(FORKS_A_WORKER)} {worker}"
    code, out = run("--boot", boot, "--ready", READY, capsys=capsys)
    assert (code, out) == (0, "with_service: green\n")
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
    status, port, value = seen.read_text(encoding="utf-8").split()
    assert (code, out, status) == (0, "with_service: green\n", "200")
    assert value == f"v{port}", "{port} is replaced in --env and exported as PORT"
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
    assert "boot output (tail):\n  boom" in out


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
