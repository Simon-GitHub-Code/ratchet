import contextlib
import ctypes
import errno
import ipaddress
import os
import re
import shlex
import signal
import socket
import subprocess
import sys
import threading
import time
from collections.abc import Callable
from pathlib import Path

import pytest

import gatelib
import listeners
import load_smoke
import with_service
from tests import servers
from tests.servers import (
    ACCEPTS_OUTSIDE_THE_GROUP,
    ANSWERS_UNREAD,
    CLOSES_HALF_SENT,
    EXITS_ON_SIGNAL,
    FORGE,
    HIDES,
    KEEPS_THE_LISTENER,
    LISTENS,
    NAMES_ITSELF,
    NEEDS,
    READS_THE_BODY,
    SERVE,
    SLOW_TO_ACCEPT,
    STOPS,
    gone,
    pid_of,
    python,
)

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
        *("--", sys.executable, "-c", "exit(4)"),
        capsys=capsys,
    )
    assert code == 4
    assert "the command exited with code 4" in out
    assert "boot output (tail):\n  booted-fine" in out
    assert out.endswith("with_service: red\n")

    code3, out3 = run(
        *("--boot", boot, "--ready", READY),
        *("--", sys.executable, "-c", "exit(3)"),
        capsys=capsys,
    )
    assert code3 == gatelib.UNJUDGED
    assert "the command exited with code 3" in out3
    assert out3.endswith("with_service: could not judge\n")


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


def listening_on(*addresses: str) -> str:
    return f"exec {python(LISTENS)} {' '.join(addresses)}"


CURE = (
    "pass {host}, which stands for the ready URL's host, in the boot line where the product "
    "takes its address"
)
HOSTED = "point the ready URL at a loopback address, which {host} stands for"


def noted(where: str, what: str, cure: str = CURE) -> str:
    return (
        rf"with_service: note: the product listened on {re.escape(where)}:(\d+), {what}, when it "
        rf"first answered, so the network could reach it; {re.escape(cure)}\n"
    )


def machine_address() -> str:
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as route:
        route.connect(("192.0.2.1", 9))
        return str(route.getsockname()[0])


@pytest.mark.parametrize(
    ("ready", "host"),
    [
        (READY, "127.0.0.1"),
        ("http://[::1]:{port}/", "::1"),
        ("http://localhost:{port}/", "localhost"),
    ],
)
def test_host_stands_for_the_ready_urls_host_and_nothing_is_exported(
    ready: str,
    host: str,
    *,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.delenv("HOST", raising=False)
    seen = tmp_path / "seen"
    boot = f'printf "%s|%s|" {{host}} "${{HOST-unset}}" > {seen}; {listening_on("{host}")}'
    appends = "import os, sys\nopen(sys.argv[1], 'a').write(os.environ['K'] + sys.argv[2])"
    code, out = run(
        *("--boot", boot, "--ready", ready, "--env", "K={host}"),
        *("--", sys.executable, "-c", appends, str(seen), "|{host}"),
        capsys=capsys,
    )
    assert (code, out) == (0, "with_service: green\n")
    assert seen.read_text(encoding="utf-8") == f"{host}|unset|{host}|{host}", (
        "the boot command, --env and the command get the ready URL's host as {host}, and no "
        "variable is exported"
    )


def test_a_ready_url_cannot_hold_the_host_it_stands_for(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as caught:
        with_service.main(["with_service.py", "--boot", "true", "--ready", "http://{host}:1/"])
    assert caught.value.code == 2
    assert "--ready cannot hold {host}, which stands for the ready URL's host" in (
        capsys.readouterr().err
    )


@pytest.mark.parametrize(
    ("boot", "ready", "said"),
    [
        (listening_on('"${HOST:-::}"'), "http://[::1]:{port}/", noted("[::]", "every interface")),
        (
            listening_on('"${HOST:-0.0.0.0}"'),
            "http://{machine}:{{port}}/",
            noted("0.0.0.0", "every interface"),  # noqa: S104 # -- the wide listener under test
        ),
        (f"exec {python(NAMES_ITSELF)}", "http://localhost:{port}/", ""),
    ],
    ids=["binds HOST, ready on IPv6 loopback", "binds HOST, ready on the machine", "HOST a name"],
)
def test_a_boot_line_without_host_boots_as_it_did_before_host_existed(
    boot: str,
    ready: str,
    said: str,
    *,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.delenv("HOST", raising=False)
    url = ready.format(machine=machine_address()) if "{machine}" in ready else ready
    code, out = run("--boot", boot, "--ready", url, "--timeout", "10", capsys=capsys)
    assert re.fullmatch(said + "with_service: green\n", out), out
    assert code == 0


@pytest.mark.parametrize(
    ("addresses", "ready"),
    [
        (("127.0.0.1",), READY),
        (("::1",), "http://[::1]:{port}/"),
        (("::ffff:127.0.0.1",), READY),
        (("127.0.0.1", "127.0.0.2", "::1"), READY),
    ],
)
def test_a_product_on_loopback_addresses_alone_says_nothing_of_where_it_listens(
    addresses: tuple[str, ...], ready: str, capsys: pytest.CaptureFixture[str]
) -> None:
    code, out = run("--boot", listening_on(*addresses), "--ready", ready, capsys=capsys)
    assert (code, out) == (0, "with_service: green\n")


@pytest.mark.parametrize(("address", "where"), [("0.0.0.0", "0.0.0.0"), ("::", "[::]")])  # noqa: S104 # -- the wide listener under test
def test_a_gate_whose_product_listens_on_every_interface_says_so_and_stays_green(
    address: str, where: str, capsys: pytest.CaptureFixture[str]
) -> None:
    code, out = run("--boot", listening_on(address), "--ready", READY, capsys=capsys)
    said = re.fullmatch(noted(where, "every interface") + "with_service: green\n", out)
    assert said is not None, out
    assert code == 0


def test_each_listener_of_each_process_the_boot_command_started_is_judged(
    capsys: pytest.CaptureFixture[str],
) -> None:
    boot = f"{python(LISTENS)} 127.0.0.1 0.0.0.0 :: & wait"
    code, out = run("--boot", boot, "--ready", READY, capsys=capsys)
    lines = out.splitlines(keepends=True)
    assert code == 0
    assert len(lines) == 3, out
    for where in ("0.0.0.0", "[::]"):  # noqa: S104 # -- the wide listeners under test
        assert any(re.fullmatch(noted(where, "every interface"), line) for line in lines), out
    assert lines[2] == "with_service: green\n"


def test_a_listener_on_an_address_other_machines_reach_is_named(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    seen = listeners.Seen([(ipaddress.ip_address("192.0.2.7"), 8080)], [], [])
    monkeypatch.setattr(listeners, "bound", lambda *_: seen)
    code, out = run("--boot", listening_on("127.0.0.1"), "--ready", READY, capsys=capsys)
    assert re.fullmatch(noted("192.0.2.7", "not a loopback address") + "with_service: green\n", out)
    assert code == 0


def test_a_listener_on_an_ipv4_mapped_address_is_named_as_the_ipv4_address(
    capsys: pytest.CaptureFixture[str],
) -> None:
    code, out = run("--boot", listening_on("::ffff:0.0.0.0"), "--ready", READY, capsys=capsys)
    said = noted("0.0.0.0", "every interface")  # noqa: S104 # -- the wide listener under test
    assert re.fullmatch(said + "with_service: green\n", out), out
    assert code == 0


def test_the_cure_of_a_boot_line_that_passes_host_is_the_ready_url(
    capsys: pytest.CaptureFixture[str],
) -> None:
    ready = "http://0.0.0.0:{port}/"
    code, out = run("--boot", listening_on("{host}"), "--ready", ready, capsys=capsys)
    wide = "0.0.0.0"  # noqa: S104 # -- the wide listener under test
    said = noted(wide, "every interface", HOSTED) + "with_service: green\n"
    assert re.fullmatch(said, out), out
    assert code == 0


def test_loopback_only_refuses_a_product_listening_wider_and_never_runs_the_command(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    ran = tmp_path / "ran"
    code, out = run(
        *("--boot", listening_on("0.0.0.0"), "--ready", READY, "--loopback-only"),  # noqa: S104 # -- under test
        *("--", "touch", str(ran)),
        capsys=capsys,
    )
    assert code == 1
    assert not ran.exists(), "the command after -- ran while the product listened wider"
    said = (
        r"the product listened on 0\.0\.0\.0:\d+, every interface, when it first answered, so the "
        rf"network could reach it around the command after --; {re.escape(CURE)}, or declare the "
        r"address with --listens\n"
    )
    assert re.match(said, out), out
    assert out.endswith("with_service: red\n")


def test_loopback_only_says_what_it_looked_at_and_when(capsys: pytest.CaptureFixture[str]) -> None:
    code, out = run(
        *("--boot", listening_on("127.0.0.1"), "--ready", READY, "--loopback-only"),
        *("--", "true"),
        capsys=capsys,
    )
    assert (code, out) == (0, CHECKED + "with_service: green\n")


CHECKED = (
    "with_service: when the product first answered, each listener of the boot command's process "
    "group and its descendants, one on the ready URL's port among them, was on loopback or "
    "declared; a listener of a process that is neither in that group nor a descendant of it, or "
    "one opened later, is not judged\n"
)
OVERCLAIMED = "each of its listeners was on loopback or declared"
UNTRUE = (
    "the ready URL's among them",
    "such as a daemon",
    "left the group",
    "a child of one",
)


def said_only_true(out: str) -> None:
    assert not [clause for clause in UNTRUE if clause in out], out


IN_ITS_OWN_SESSION = (
    "import os, socket, sys, time\n"
    "group = os.getpgrp()\n"
    "def hold():\n"
    "    held = socket.create_server((sys.argv[1], 0))\n"
    "    open(sys.argv[2], 'w').close()\n"
    "    while True:\n"
    "        time.sleep(0.02)\n"
    "        try: os.killpg(group, 0)\n"
    "        except ProcessLookupError: os._exit(0)\n"
    "if sys.argv[3] == 'parent exits':\n"
    "    if os.fork(): os._exit(0)\n"
    "    hold()\n"
    "if os.fork() == 0:\n"
    "    os.setsid()\n"
    "    hold()\n"
    "time.sleep(60)\n"
)


def forked_beside(marker: Path, address: str, shape: str) -> str:
    started = f"{python(IN_ITS_OWN_SESSION)} {address} {marker} {shlex.quote(shape)}"
    waits = f"while [ ! -e {marker} ]; do sleep 0.05; done"
    joined = ";" if shape == "parent exits" else " &"
    return f"{started}{joined} {waits}; {listening_on('127.0.0.1')}"


@pytest.mark.parametrize("address", ["127.0.0.1", "0.0.0.0"], ids=["loopback", "wide"])  # noqa: S104 # -- under test
def test_a_child_in_a_session_of_its_own_is_still_a_descendant_and_judged(
    address: str, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    ran = tmp_path / "ran"
    code, out = run(
        *("--boot", forked_beside(tmp_path / "opened", address, "child"), "--ready", READY),
        *("--loopback-only", "--", "touch", str(ran)),
        capsys=capsys,
    )
    wide = address != "127.0.0.1"
    refused = f"{WIDE_REFUSED}boot output: none, the boot command printed nothing\n"
    refused += "with_service: red\n"
    said = refused if wide else re.escape(CHECKED + "with_service: green\n")
    assert code == int(wide), out
    assert re.fullmatch(said, out), out
    assert ran.exists() != wide
    said_only_true(out)


def test_a_fork_whose_parent_exits_stays_in_the_group_and_is_judged(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    boot = forked_beside(tmp_path / "opened", "0.0.0.0", "parent exits")  # noqa: S104 # -- under test
    code, out = run("--boot", boot, "--ready", READY, capsys=capsys)
    assert code == 0
    assert re.fullmatch(noted("0.0.0.0", "every interface") + "with_service: green\n", out), out  # noqa: S104 # -- under test
    said_only_true(out)


HOLDS_THE_PORT_ON_IPV6_LOOPBACK = (
    "import os, socket, sys, time\n"
    "held = socket.socket(socket.AF_INET6)\n"
    "held.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, 1)\n"
    "held.bind(('::1', int(os.environ['PORT'])))\n"
    "held.listen()\n"
    "open(sys.argv[1], 'w').close()\n"
    "time.sleep(60)\n"
)


def test_the_ready_url_answered_from_outside_while_the_group_holds_its_port_is_said(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    marker, ran = tmp_path / "held", tmp_path / "ran"
    with servers.held_outside_the_group() as asking:
        boot = (
            f"{python(HOLDS_THE_PORT_ON_IPV6_LOOPBACK)} {marker} & "
            f"while [ ! -e {marker} ]; do sleep 0.05; done; {asking('0.0.0.0')}"  # noqa: S104 # -- under test
        )
        code, out = run(
            *("--boot", boot, "--ready", READY, "--loopback-only", "--", "touch", str(ran)),
            capsys=capsys,
        )
    outside = (
        r"with_service: 0\.0\.0\.0:\d+, on the ready URL's port, is held by a process the gate "
        r"did not find among the boot command's, so it is not judged as the product's\n"
    )
    assert code == 0, out
    assert re.fullmatch(outside + re.escape(CHECKED) + "with_service: green\n", out), out
    assert ran.exists()
    said_only_true(out)


def beside(kind: str, tmp_path: Path, asking: Callable[[str], str]) -> str:
    marker, product = tmp_path / "opened", listening_on("127.0.0.1")
    if kind == "daemon":
        return servers.beside_a_daemon(marker, product)
    return servers.beside_one_opened_outside(marker, asking, product)


@pytest.mark.parametrize("kind", ["daemon", "outside"])
def test_a_session_beside_a_wide_listener_no_process_of_the_group_holds_says_what_it_judged(
    kind: str, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    ran = tmp_path / "ran"
    with servers.held_outside_the_group() as asking:
        code, out = run(
            *("--boot", beside(kind, tmp_path, asking), "--ready", READY, "--loopback-only"),
            *("--", "touch", str(ran)),
            capsys=capsys,
        )
    assert (code, out) == (0, CHECKED + "with_service: green\n"), out
    assert ran.exists()
    assert OVERCLAIMED not in out
    said_only_true(out)


@pytest.mark.parametrize("kind", ["daemon", "outside"])
def test_a_gate_beside_a_wide_listener_no_process_of_the_group_holds_is_green_and_silent(
    kind: str, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    with servers.held_outside_the_group() as asking:
        code, out = run("--boot", beside(kind, tmp_path, asking), "--ready", READY, capsys=capsys)
    assert (code, out) == (0, "with_service: green\n"), out
    assert OVERCLAIMED not in out


DECLARED = (
    r"with_service: the product listened on {where}:\d+ when it first answered, as declared\n"
)


@pytest.mark.parametrize(
    ("declared", "addresses", "code"),
    [
        (["0.0.0.0"], ["0.0.0.0"], 0),  # noqa: S104 # -- under test
        (["0.0.0.0"], ["::"], 1),  # noqa: S104 # -- under test
        (["::"], ["127.0.0.1"], 0),
        (["0.0.0.0 ::"], ["0.0.0.0", "::"], 0),  # noqa: S104 # -- under test
        (["0.0.0.0", "::"], ["0.0.0.0", "::"], 0),  # noqa: S104 # -- under test
    ],
    ids=["the address", "another address", "loopback", "a list", "the flag twice"],
)
def test_the_addresses_declared_with_listens_are_allowed_said_and_held_to(
    declared: list[str], addresses: list[str], capsys: pytest.CaptureFixture[str], *, code: int
) -> None:
    flags = [word for each in declared for word in ("--listens", each)]
    ran, out = run(
        *("--boot", listening_on(*addresses), "--ready", READY, "--loopback-only", *flags),
        capsys=capsys,
    )
    assert ran == code, out
    if code:
        assert re.match(r"the product listened on \[::\]:\d+, every interface", out), out
        return
    lines = out.splitlines(keepends=True)
    wide = [where for where in ("0.0.0.0", "[::]") if where.strip("[]") in addresses]  # noqa: S104 # -- under test
    for where in wide:
        assert any(re.fullmatch(DECLARED.format(where=re.escape(where)), line) for line in lines)
    assert len(lines) == len(wide) + 2, out


def test_a_declared_address_is_matched_whole_not_by_its_start(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    seen = listeners.Seen([(ipaddress.ip_address("192.0.2.17"), 8080)], [], [])
    monkeypatch.setattr(listeners, "bound", lambda *_: seen)
    code, out = run(
        *("--boot", listening_on("127.0.0.1"), "--ready", READY, "--loopback-only"),
        *("--listens", "192.0.2.1"),
        capsys=capsys,
    )
    assert code == 1
    assert out.startswith("the product listened on 192.0.2.17:8080, not a loopback address"), out


def test_an_empty_listens_says_what_to_write(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as caught:
        with_service.main(["with_service.py", "--boot", "true", "--ready", READY, "--listens", ""])
    assert caught.value.code == 2
    assert (
        "argument --listens: names no address: write each address the product listens on on "
        "purpose, separated by spaces, such as --listens '0.0.0.0 ::'"
    ) in capsys.readouterr().err


def test_a_listener_on_the_ready_port_held_by_another_process_is_said_and_never_refused(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    other = socket.socket(socket.AF_INET6)
    try:
        other.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, 1)
        other.bind(("::", 0))
        other.listen()
        port = other.getsockname()[1]
        boot = f"PORT={port} {listening_on('127.0.0.1')}"
        ready = f"http://127.0.0.1:{port}/"
        ran = tmp_path / "ran"
        code, out = run(
            *("--boot", boot, "--ready", ready, "--loopback-only", "--", "touch", str(ran)),
            capsys=capsys,
        )
    finally:
        other.close()
    other_line = (
        f"with_service: [::]:{port}, on the ready URL's port, is held by a process the gate did "
        "not find among the boot command's, so it is not judged as the product's\n"
        f"{CHECKED}with_service: green\n"
    )
    assert (code, out) == (0, other_line), out
    assert ran.exists(), "another process's listener refused the session"


def hidden_beside(tmp_path: Path, *addresses: str) -> str:
    one, two = tmp_path / "one", tmp_path / "two"
    return (
        f"{python(HIDES)} {one} & {python(HIDES)} {two} & "
        f"while [ ! -e {one} ] || [ ! -e {two} ]; do sleep 0.05; done; "
        f"{listening_on(*addresses)}"
    )


UNSEEN = (
    r"the gate is not allowed to look at the open files of process \d+ \(python3?[\w.]*\), one "
    r"of the service's \(Permission denied\), as when a process runs as another user or has made "
    r"itself non-dumpable"
)


def test_a_process_the_gate_cannot_read_hides_no_other_listener_and_is_said(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    boot = hidden_beside(tmp_path, "127.0.0.1", "0.0.0.0")  # noqa: S104 # -- under test
    code, out = run("--boot", boot, "--ready", READY, capsys=capsys)
    lines = out.splitlines(keepends=True)
    assert code == 0
    wide = "0.0.0.0"  # noqa: S104 # -- the wide listener under test
    unseen = rf"with_service: where the product listens was not checked: {UNSEEN}\n"
    assert re.fullmatch(noted(wide, "every interface"), lines[0]), out
    assert re.fullmatch(unseen, lines[1]), out
    assert re.fullmatch(unseen, lines[2]), "each process the gate could not read is said"
    assert lines[1] != lines[2], out
    assert lines[3:] == ["with_service: green\n"], out


def test_loopback_only_refuses_what_it_could_not_check(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    ran = tmp_path / "ran"
    code, out = run(
        *("--boot", hidden_beside(tmp_path, "127.0.0.1"), "--ready", READY, "--loopback-only"),
        *("--", "touch", str(ran)),
        capsys=capsys,
    )
    assert code == 1
    assert not ran.exists(), "a session started on a product it could not check"
    said = "where the product listens was not checked, so the command after -- did not run: "
    assert re.match(rf"{said}{UNSEEN}; {UNSEEN}; {re.escape(LISTENS_CURE)}\n", out), out


def test_where_the_product_listens_is_not_checked_where_it_cannot_be_read(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(listeners, "PROC", str(tmp_path))
    code, out = run("--boot", listening_on("0.0.0.0"), "--ready", READY, capsys=capsys)  # noqa: S104 # -- under test
    assert (code, out) == (
        0,
        (
            f"with_service: where the product listens was not checked: {listeners.UNREADABLE}\n"
            "with_service: green\n"
        ),
    )


def test_a_table_row_that_cannot_be_read_is_not_checked_and_refuses_a_session(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    net = tmp_path / str(os.getpid()) / "net"
    net.mkdir(parents=True)
    (net / "tcp").write_text("header\n   0: 0100007F:1F90\n", encoding="utf-8")
    monkeypatch.setattr(listeners, "PROC", str(tmp_path))
    code, out = run(
        *("--boot", listening_on("127.0.0.1"), "--ready", READY, "--loopback-only"),
        *("--", "true"),
        capsys=capsys,
    )
    assert code == 1
    assert out.startswith(
        "where the product listens was not checked, so the command after -- did not run: a row of "
        f"the system's net/tcp table cannot be read: '0: 0100007F:1F90'; {LISTENS_CURE}\n"
    ), out


LISTENS_CURE = (
    "declare with --listens each address the product listens on, a loopback address if that is all"
)
NOT_CHECKED_HERE = "with_service: where the product listens was not checked here, as declared: "


@pytest.mark.parametrize(
    "case",
    [("127.0.0.1", "127.0.0.1"), ("0.0.0.0", "0.0.0.0 ::")],  # noqa: S104 # -- under test
    ids=["loopback declared", "wider declared"],
)
@pytest.mark.parametrize("session", [True, False], ids=["session", "gate"])
def test_where_there_is_no_table_what_the_project_declared_is_said_once_and_it_starts(
    case: tuple[str, str],
    *,
    session: bool,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    address, declared = case
    monkeypatch.setattr(listeners, "PROC", str(tmp_path))
    ran = tmp_path / "ran"
    code, out = run(
        *("--boot", listening_on(address), "--ready", READY, "--listens", declared),
        *(["--loopback-only"] if session else []),
        *("--", "touch", str(ran)),
        capsys=capsys,
    )
    assert (code, out) == (
        0,
        f"{NOT_CHECKED_HERE}{declared} ({listeners.UNREADABLE})\nwith_service: green\n",
    )
    assert ran.exists()


def test_where_there_is_no_table_and_nothing_is_declared_a_session_names_the_cure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(listeners, "PROC", str(tmp_path))
    ran = tmp_path / "ran"
    code, out = run(
        *("--boot", listening_on("127.0.0.1"), "--ready", READY, "--loopback-only"),
        *("--", "touch", str(ran)),
        capsys=capsys,
    )
    assert code == 1
    assert not ran.exists()
    assert out.startswith(
        "where the product listens was not checked, so the command after -- did not run: "
        f"{listeners.UNREADABLE}; {LISTENS_CURE}\n"
    ), out


ELSEWHERE = re.escape(listeners.NOT_FOUND)
EVERY = re.escape("0.0.0.0")  # noqa: S104 # -- the wide listener under test


@pytest.mark.parametrize(
    ("address", "flags", "code", "said"),
    [
        (
            "127.0.0.1",
            ["--listens", "127.0.0.1"],
            0,
            rf"{NOT_CHECKED_HERE}127\.0\.0\.1 \({ELSEWHERE}\)\n",
        ),
        (
            "0.0.0.0",  # noqa: S104 # -- under test
            ["--listens", "0.0.0.0"],  # noqa: S104 # -- under test
            0,
            DECLARED.format(where=EVERY) + rf"{NOT_CHECKED_HERE}{EVERY} \({ELSEWHERE}\)\n",
        ),
        (
            "0.0.0.0",  # noqa: S104 # -- under test
            [],
            1,
            (
                r"the product listened on 0\.0\.0\.0:\d+, every interface, when it first "
                r"answered, so the network could reach it around the command after --; "
            ),
        ),
        (
            "127.0.0.1",
            [],
            1,
            (
                "where the product listens was not checked, so the command after -- did not run: "
                rf"{ELSEWHERE}; {re.escape(LISTENS_CURE)}\n"
            ),
        ),
    ],
    ids=["loopback declared", "wide declared", "wide", "loopback"],
)
def test_a_session_judges_the_ready_ports_listener_as_the_products_when_the_group_holds_none(
    address: str, flags: list[str], *, code: int, said: str, capsys: pytest.CaptureFixture[str]
) -> None:
    with servers.held_outside_the_group() as asking:
        ran, out = run(
            *("--boot", asking(address), "--ready", READY, "--loopback-only", *flags),
            *("--", "true"),
            capsys=capsys,
        )
    assert re.match(said, out), out
    assert ran == code, out


@pytest.mark.parametrize(
    ("address", "said"),
    [
        ("127.0.0.1", ""),
        ("0.0.0.0", noted("0.0.0.0", "every interface")),  # noqa: S104 # -- under test
    ],
)
def test_a_gate_whose_listener_another_process_holds_keeps_its_colour(
    address: str, said: str, capsys: pytest.CaptureFixture[str]
) -> None:
    with servers.held_outside_the_group() as asking:
        code, out = run("--boot", asking(address), "--ready", READY, capsys=capsys)
    unseen = f"with_service: where the product listens was not checked: {ELSEWHERE}\n"
    assert re.fullmatch(f"{said}{unseen}with_service: green\n", out), out
    assert code == 0


WIDE_REFUSED = (
    r"the product listened on 0\.0\.0\.0:\d+, every interface, when it first answered, so the "
    rf"network could reach it around the command after --; {re.escape(CURE)}, or declare the "
    r"address with --listens\n"
)


@pytest.mark.parametrize(
    ("flags", "code", "said"),
    [
        (
            [],
            1,
            WIDE_REFUSED + "where the product listens was not checked, so the command after -- "
            rf"did not run: {ELSEWHERE}; {re.escape(LISTENS_CURE)}\n",
        ),
        (
            ["--listens", "127.0.0.1"],
            1,
            rf"{NOT_CHECKED_HERE}127\.0\.0\.1 \({ELSEWHERE}\)\n{WIDE_REFUSED}",
        ),
        (
            ["--listens", "0.0.0.0"],  # noqa: S104 # -- under test
            0,
            DECLARED.format(where=EVERY)
            + rf"{NOT_CHECKED_HERE}{EVERY} \({ELSEWHERE}\)\nwith_service: green\n$",
        ),
    ],
    ids=["none", "loopback declared", "wide declared"],
)
def test_a_session_judges_the_ready_ports_listener_when_the_group_holds_only_another_port(
    flags: list[str],
    *,
    code: int,
    said: str,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    ran = tmp_path / "ran"
    with servers.held_outside_the_group() as asking:
        boot = servers.helped_by(tmp_path / "helper", asking("0.0.0.0"))  # noqa: S104 # -- under test
        exited, out = run(
            *("--boot", boot, "--ready", READY, "--loopback-only", *flags),
            *("--", "touch", str(ran)),
            capsys=capsys,
        )
    assert re.match(said, out), out
    assert (exited, ran.exists()) == (code, not code), out
    assert "was on loopback or declared" not in out, out


def test_a_gate_whose_group_holds_only_another_port_notes_the_wide_ready_listener(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    with servers.held_outside_the_group() as asking:
        boot = servers.helped_by(tmp_path / "helper", asking("0.0.0.0"))  # noqa: S104 # -- under test
        code, out = run("--boot", boot, "--ready", READY, capsys=capsys)
    unseen = f"with_service: where the product listens was not checked: {ELSEWHERE}\n"
    wide = noted("0.0.0.0", "every interface")  # noqa: S104 # -- under test
    assert re.fullmatch(f"{wide}{unseen}with_service: green\n", out), out
    assert code == 0


def test_a_declaration_never_waives_a_wider_listener_seen_beside_one_not_checked(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    boot = hidden_beside(tmp_path, "127.0.0.1", "0.0.0.0")  # noqa: S104 # -- under test
    code, out = run(
        *("--boot", boot, "--ready", READY, "--loopback-only", "--listens", "127.0.0.1"),
        *("--", "true"),
        capsys=capsys,
    )
    assert code == 1
    refused = r"^the product listened on 0\.0\.0\.0:\d+, every interface"
    assert re.search(refused, out, re.MULTILINE), out


def test_a_bad_ready_url_or_env_pair_is_a_usage_error() -> None:
    with pytest.raises(SystemExit) as caught:
        with_service.main(["with_service.py", "--boot", "true", "--ready", "ftp://x/"])
    assert caught.value.code == 2
    with pytest.raises(SystemExit) as caught:
        with_service.main(["with_service.py", "--boot", "true", "--ready", READY, "--env", "NOPE"])
    assert caught.value.code == 2


LOAD = Path(servers.__file__).parents[2] / "scripts" / "load_smoke.py"
BUSY = ["--seconds", "2.5", "--concurrency", "4", "--min-requests", "1", "--until-stopped"]
STOPPING = (
    "import os, sys\n"
    "open(sys.argv[1], 'w').write(str(os.path.exists(os.environ['RATCHET_STOPPING'])))"
)
SENDS_THEN_WAITS = (
    "import os, time\n"
    "open(os.environ['RATCHET_SENDING'], 'w').close()\n"
    "deadline = time.monotonic() + 60\n"
    "while not os.path.exists(os.environ['RATCHET_STOPPING']):\n"
    "    assert time.monotonic() < deadline, 'the gate never signalled'\n"
    "    time.sleep(0.01)\n"
)
RECORDS_THE_RELEASE = (
    "import json, os, sys, time\n"
    "seen, url, body, late = sys.argv[1:5]\n"
    "time.sleep(float(late))\n"
    "sending = os.environ['RATCHET_SENDING']\n"
    "route = ['POST', url, 200, '{}'] if body == 'body' else ['GET', url, 200, None]\n"
    "open(sending + '.pending', 'w').write(json.dumps([route]))\n"
    "os.replace(sending + '.pending', sending)\n"
    "deadline = time.monotonic() + 20\n"
    "while not os.path.exists(sending + '.held'):\n"
    "    assert time.monotonic() < deadline, 'the gate never released the command'\n"
    "    time.sleep(0.01)\n"
    "open(seen, 'w').write(open(sending + '.held').read())\n"
    "while sys.argv[5:] and not os.path.exists(os.environ['RATCHET_STOPPING']):\n"
    "    assert time.monotonic() < deadline, 'the gate never signalled'\n"
    "    time.sleep(0.01)\n"
)
ANNOUNCES = (
    "import json, os, sys, time\n"
    "pending = os.environ['RATCHET_SENDING'] + '.pending'\n"
    "open(pending, 'w').write(json.dumps([['POST', sys.argv[1], 200, '{}']]))\n"
    "os.replace(pending, os.environ['RATCHET_SENDING'])\n"
    "deadline = time.monotonic() + 60\n"
    "while sys.argv[2:] and not os.path.exists(os.environ['RATCHET_STOPPING']):\n"
    "    assert time.monotonic() < deadline, 'the gate never signalled'\n"
    "    time.sleep(0.01)\n"
)
BETWEEN_TWO_REQUESTS = (
    f"import sys, urllib.request as u\nassert u.urlopen(sys.argv[1]).status == 200\n{ANNOUNCES}"
)
HOLDS = "http://127.0.0.1:{port}/work"
ENDS_FIRST = (
    "import os, sys\n"
    "open(os.environ['RATCHET_SENDING'], 'w').close()\n"
    "print('the load failed: every request was refused', flush=True)\n"
    "sys.exit(123)\n"
)
STOPS_AT_ONCE = (
    "import os, signal, threading, time, http.server as h\n"
    "class Handler(h.BaseHTTPRequestHandler):\n"
    "    def log_message(self, *args): pass\n"
    "    def do_GET(self):\n"
    "        self.send_response(200)\n"
    "        self.send_header('Content-Length', '2')\n"
    "        self.end_headers()\n"
    "        self.wfile.flush()\n"
    "        time.sleep(0.05)\n"
    "        self.wfile.write(b'ok')\n"
    f"{READS_THE_BODY}"
    "server = h.ThreadingHTTPServer(('127.0.0.1', int(os.environ['PORT'])), Handler)\n"
    "signal.signal(signal.SIGTERM, lambda *_: threading.Thread(target=server.shutdown).start())\n"
    "server.daemon_threads = False\n"
    "server.serve_forever()\n"
    "server.server_close()"
)
DROPPED = re.compile(r"^\d+ x POST http://127\.0\.0\.1:\d+/work: (\w+)(?::|$)", re.MULTILINE)
DROPPED_HELD = re.compile(
    r"^the request the gate held, POST http://127\.0\.0\.1:\d+/work, which the service accepted "
    r"before the stop signal, was dropped: (.+)$",
    re.MULTILINE,
)
ANSWERED_HELD = re.compile(
    r"^with_service: the service accepted the request the gate held, POST "
    r"http://127\.0\.0\.1:\d+/work, before the stop signal and answered it after$",
    re.MULTILINE,
)
OLDER = (
    "with_service: the gate judged the stop the older way, by the command's own requests, since "
)
NAMED_NONE = "no route that takes a body was found among the routes the command named (none)"


def smoke(
    *,
    starts_after: str = "0",
    reaches_late: bool = False,
    concurrency: str = "4",
    url: str = f"POST {HOLDS} 200 {{}}",
    timeout: str = "5",
) -> list[str]:
    late = f'touch "${gatelib.SENDING_ENV}" && sleep 1 && ' if reaches_late else ""
    return [
        *("sh", "-c", f'{late}sleep {starts_after} && exec "$@"', "late", sys.executable),
        *(str(LOAD), "--url", url, *BUSY),
        *("--concurrency", concurrency, "--timeout", timeout),
    ]


def announces(*, waits: bool = True) -> list[str]:
    return [sys.executable, "-c", ANNOUNCES, HOLDS, *(["waits"] if waits else [])]


def graceful(
    mode: str,
    *extra: str,
    capsys: pytest.CaptureFixture[str],
    limit: str = "60",
    command: list[str] | None = None,
    hidden: str = "",
) -> tuple[int, str]:
    boot = f"MODE={mode} HIDDEN={hidden} exec {python(STOPS)}"
    return run(
        *("--boot", boot, "--ready", READY),
        *("--graceful", limit, "--signal-after", "0.5", *extra),
        *("--", *(command or smoke())),
        capsys=capsys,
    )


def test_a_service_that_drains_and_exits_zero_is_green_under_load(
    capsys: pytest.CaptureFixture[str],
) -> None:
    code, out = graceful("drain", capsys=capsys)
    assert ANSWERED_HELD.search(out), out
    assert out.endswith("with_service: green\n"), out
    assert code == 0


def test_a_service_that_leaves_connections_in_its_accept_queue_is_green(
    capfd: pytest.CaptureFixture[str],
) -> None:
    code, out = graceful("queue", capsys=capfd)
    assert out.endswith("with_service: green\n"), out
    assert "load-smoke: 4 connection(s) reset after the stop signal are not counted" in out
    assert ANSWERED_HELD.search(out), out
    assert code == 0


def test_the_request_a_service_accepted_and_dropped_is_red_beside_those_it_never_accepted(
    capfd: pytest.CaptureFixture[str],
) -> None:
    code, out = graceful("abandon", capsys=capfd)
    assert "load-smoke: 4 connection(s) reset after the stop signal are not counted" in out
    assert " x POST " not in out, "the smoke counts none of the connections the gate took"
    assert DROPPED_HELD.search(out), out
    assert (code, out.endswith("with_service: red\n")) == (1, True)


@pytest.mark.parametrize("reaches_late", [False, True])
def test_a_service_that_forges_the_gates_excuse_from_another_address_is_red(
    capfd: pytest.CaptureFixture[str], *, reaches_late: bool
) -> None:
    code, out = run(
        *("--boot", f"exec {python(FORGE)}", "--ready", READY),
        *("--graceful", "60", "--signal-after", "0.5", "--"),
        *smoke(reaches_late=reaches_late),
        capsys=capfd,
    )
    dropped = DROPPED.search(out)
    assert dropped, out
    assert dropped.group(1) == "ConnectionResetError"
    assert code == 1


def test_a_service_that_shuts_its_listener_down_is_judged_the_older_way_and_says_why(
    capfd: pytest.CaptureFixture[str],
) -> None:
    code, out = graceful("shut", capsys=capfd)
    assert "is judged the older, less exact way, because the service shut its listener " in out
    assert re.search(r"\n4 x POST http://127\.0\.0\.1:\d+/work: ConnectionResetError", out), out
    assert ANSWERED_HELD.search(out), out
    assert code == 1


def test_a_correct_drain_the_gate_cannot_look_into_is_judged_as_before_and_green(
    capsys: pytest.CaptureFixture[str],
) -> None:
    code, out = graceful("drain", capsys=capsys, hidden="now")
    said = f"{OLDER}the gate cannot see whether the service accepted the request the gate held, "
    assert said in out, out
    assert "since the gate is not allowed to look at the open files of process " in out
    assert (code, out.endswith("with_service: green\n")) == (0, True)


def test_a_service_that_hides_only_once_signalled_is_judged_by_the_held_request(
    capsys: pytest.CaptureFixture[str],
) -> None:
    code, out = graceful("drain", capsys=capsys, hidden="later")
    assert ANSWERED_HELD.search(out), out
    assert (code, out.endswith("with_service: green\n")) == (0, True)


def test_where_no_table_can_be_read_the_stop_is_judged_as_before_at_once_and_green(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(listeners, "PROC", str(tmp_path / "no-process-table"))
    code, out = graceful("drain", capsys=capsys)
    said = f"{OLDER}the gate cannot see whether the service accepted the request the gate held, "
    assert f"{said}POST " in out, out
    assert ", since this system shows no TCP table the gate can read\n" in out
    assert (code, out.endswith("with_service: green\n")) == (0, True)


def test_a_service_whose_acceptor_is_outside_the_group_is_judged_as_before_at_once(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    daemon = tmp_path / "daemon"
    try:
        code, out = run(
            *("--boot", f"exec {python(ACCEPTS_OUTSIDE_THE_GROUP)} {daemon}", "--ready", READY),
            *("--timeout", "5", "--graceful", "60", "--signal-after", "0.5"),
            *("--", *announces()),
            capsys=capsys,
        )
    finally:
        if daemon.exists():
            with contextlib.suppress(ProcessLookupError):
                os.kill(pid_of(daemon), signal.SIGKILL)
    assert f"since {listeners.TAKEN_ELSEWHERE}\n" in out, out
    assert OLDER in out
    assert (code, out.endswith("with_service: green\n")) == (0, True)


def test_workers_that_keep_the_listener_while_they_finish_are_green(
    capfd: pytest.CaptureFixture[str],
) -> None:
    code, out = run(
        *("--boot", f"exec {python(KEEPS_THE_LISTENER)}", "--ready", READY),
        *("--graceful", "60", "--signal-after", "0.5", "--", *smoke()),
        capsys=capfd,
    )
    assert ANSWERED_HELD.search(out), out
    assert (code, out.endswith("with_service: green\n")) == (0, True)


@pytest.mark.parametrize(
    ("command", "said"),
    [
        (announces(), ""),
        (
            [sys.executable, "-c", SENDS_THEN_WAITS],
            f"{OLDER}{NAMED_NONE}",
        ),
    ],
    ids=["with a body", "without one"],
)
def test_a_server_that_closes_half_sent_requests_at_a_stop_is_green(
    command: list[str], said: str, capsys: pytest.CaptureFixture[str]
) -> None:
    code, out = run(
        *("--boot", f"exec {python(CLOSES_HALF_SENT)}", "--ready", READY),
        *("--graceful", "60", "--signal-after", "0.5", "--", *command),
        capsys=capsys,
    )
    if said:
        assert said in out, out
        assert ": a byte the gate held back from a request without one would fall in the " in out
    else:
        assert ANSWERED_HELD.search(out), out
    assert (code, out.endswith("with_service: green\n")) == (0, True)


NO_BODY_ROUTE = re.compile(
    rf"^{re.escape(OLDER)}no route that takes a body was found among the routes the command "
    r"named \(GET http://127\.0\.0\.1:\d+/work\): a byte the gate held back from a request "
    r"without one would fall in the request's head, which a server may close at a stop; bind a "
    r"route with a body, METHOD URL STATUS BODY, to have one of its requests held across the "
    r"signal$",
    re.MULTILINE,
)


def test_a_product_with_no_route_that_takes_a_body_is_judged_the_older_way_and_says_so(
    capsys: pytest.CaptureFixture[str],
) -> None:
    code, out = graceful("drain", capsys=capsys, command=smoke(url=f"GET {HOLDS} 200"))
    assert NO_BODY_ROUTE.search(out), out
    assert not ANSWERED_HELD.search(out), "no request was held"
    assert (code, out.endswith("with_service: green\n")) == (0, True)


def test_the_first_route_that_takes_a_body_is_held_whatever_route_comes_before_it(
    capsys: pytest.CaptureFixture[str],
) -> None:
    command = [*smoke(url=f"GET {HOLDS} 200"), "--url", f"POST {HOLDS} 200 {{}}"]
    code, out = graceful("drain", capsys=capsys, command=command)
    assert ANSWERED_HELD.search(out), out
    assert (code, out.endswith("with_service: green\n")) == (0, True)


def test_a_smoke_slow_to_start_still_has_requests_in_flight_at_the_signal(
    capsys: pytest.CaptureFixture[str],
) -> None:
    code, out = graceful("drain", capsys=capsys, command=smoke(starts_after="1"))
    assert out.endswith("with_service: green\n"), out
    assert code == 0


def test_the_signal_is_timed_from_the_command_sending_for_a_service_that_stops_at_once(
    capsys: pytest.CaptureFixture[str],
) -> None:
    code, out = run(
        *("--boot", f"exec {python(STOPS_AT_ONCE)}", "--ready", READY),
        *("--graceful", "60", "--signal-after", "0.5"),
        *("--", *smoke(starts_after="1")),
        capsys=capsys,
    )
    assert out.endswith("with_service: green\n"), out
    assert code == 0


def test_a_service_that_drops_the_requests_in_flight_is_red_for_them(
    capfd: pytest.CaptureFixture[str],
) -> None:
    code, out = graceful("drop", capsys=capfd)
    assert DROPPED.search(out), out
    held = DROPPED_HELD.search(out)
    assert held, out
    assert "ConnectionResetError" in held.group(1), "the kind of drop a project's bite expects"
    assert (code, out.endswith("with_service: red\n")) == (1, True)


def test_a_smoke_that_says_it_sends_after_the_gate_looked_is_judged_as_before_and_red(
    capfd: pytest.CaptureFixture[str],
) -> None:
    code, out = graceful("drop", capsys=capfd, command=smoke(reaches_late=True))
    assert f"{OLDER}{NAMED_NONE}" in out, out
    assert DROPPED.search(out), out
    assert (code, out.endswith("with_service: red\n")) == (1, True)


@pytest.mark.parametrize(
    ("mode", "said", "limit"),
    [
        ("late", "the service exited with code 1 ", "60"),
        ("ignore", "the service was still running 1s after SIGTERM", "1"),
        ("slow", "the service was still running 1s after SIGTERM", "1"),
        ("die", "the service exited with code 5 before it was signalled", "60"),
        ("stall", "was dropped: it was not answered before --graceful ran out", "1"),
        ("refuse", "was dropped: it was answered HTTP 503", "60"),
    ],
)
def test_each_way_of_stopping_badly_is_red(
    mode: str, said: str, capsys: pytest.CaptureFixture[str], *, limit: str
) -> None:
    code, out = graceful(mode, capsys=capsys, limit=limit)
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
    assert code == gatelib.UNJUDGED
    assert out.endswith("with_service: could not judge\n")


def test_an_unjudged_command_propagates_exit_3_only_without_a_red_failure(
    capsys: pytest.CaptureFixture[str],
) -> None:
    exits_three = (
        "import os, sys, time\n"
        "open(os.environ['RATCHET_SENDING'], 'w').close()\n"
        "time.sleep(0.1)\n"
        "sys.exit(3)\n"
    )
    code, out = run(
        *("--boot", f"MODE=drain exec {python(EXITS_ON_SIGNAL)}", "--ready", READY),
        *("--graceful", "60", "--signal-after", "10", "--", sys.executable, "-c", exits_three),
        capsys=capsys,
    )
    assert code == gatelib.UNJUDGED
    assert out.endswith("with_service: could not judge\n")

    exits_three_after_signal = (
        "import json, os, sys, time\n"
        "p = os.environ['RATCHET_SENDING'] + '.pending'\n"
        f"open(p, 'w').write(json.dumps([['POST', {HOLDS!r}, 200, '{{}}']]))\n"
        "os.replace(p, os.environ['RATCHET_SENDING'])\n"
        "while not os.path.exists(os.environ['RATCHET_STOPPING']): time.sleep(0.01)\n"
        "sys.exit(3)\n"
    )
    code_red, out_red = run(
        *("--boot", f"MODE=late exec {python(STOPS)}", "--ready", READY),
        *("--graceful", "60", "--signal-after", "0.2"),
        *("--", sys.executable, "-c", exits_three_after_signal),
        capsys=capsys,
    )
    assert code_red == 1
    assert out_red.endswith("with_service: red\n")


def exits_on_signal(
    mode: str, command: list[str], *, capsys: pytest.CaptureFixture[str], ready: str = READY
) -> tuple[int, str]:
    return run(
        *("--boot", f"MODE={mode} exec {python(EXITS_ON_SIGNAL)}", "--ready", ready),
        *("--graceful", "60", "--signal-after", "0.5", "--", *command),
        capsys=capsys,
    )


def test_a_service_that_drops_while_the_signal_falls_between_two_requests_is_red(
    capsys: pytest.CaptureFixture[str],
) -> None:
    command = [sys.executable, "-c", BETWEEN_TWO_REQUESTS, HOLDS, "waits"]
    code, out = exits_on_signal("drop", command, capsys=capsys)
    held = DROPPED_HELD.search(out)
    assert held, out
    assert "ConnectionResetError" in held.group(1), out
    assert (code, out.endswith("with_service: red\n")) == (1, True)


def test_a_clean_service_whose_first_request_arrives_after_the_signal_is_green(
    capsys: pytest.CaptureFixture[str],
) -> None:
    load = [str(LOAD), "--url", f"POST {HOLDS} 200 {{}}", *BUSY]
    late = [sys.executable, "-c", f"{ANNOUNCES}os.execv(sys.argv[3], sys.argv[3:])\n"]
    code, out = exits_on_signal(
        "drain", [*late, HOLDS, "waits", sys.executable, *load], capsys=capsys
    )
    assert ANSWERED_HELD.search(out), out
    assert (code, out.endswith("with_service: green\n")) == (0, True)


def accepting_after(
    delay: float, load: list[str], *, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> tuple[int, str]:
    waiting, accept = tmp_path / "waiting", tmp_path / "accept"

    def slow() -> None:
        try:
            while not waiting.exists() and not accept.exists():
                time.sleep(0.01)
            time.sleep(delay)
        finally:
            accept.touch()

    releasing = threading.Thread(target=slow, daemon=True)
    releasing.start()
    try:
        return run(
            *("--boot", f"exec {python(SLOW_TO_ACCEPT)} {waiting} {accept}"),
            *("--ready", "http://127.0.0.1:{port}/ready", "--graceful", "60"),
            *("--signal-after", "0.5", "--", *load),
            capsys=capsys,
        )
    finally:
        accept.touch()
        releasing.join()


def test_a_clean_service_however_slow_to_accept_is_signalled_only_once_it_has_accepted(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    code, out = accepting_after(1, smoke(concurrency="1"), tmp_path=tmp_path, capsys=capsys)
    assert ANSWERED_HELD.search(out), out
    assert (code, out.endswith("with_service: green\n")) == (0, True)


def test_a_service_slower_to_accept_than_the_smokes_timeout_is_still_judged_by_the_gate(
    tmp_path: Path, capfd: pytest.CaptureFixture[str]
) -> None:
    load = smoke(concurrency="1", timeout="0.5")
    slower = load_smoke.GATE_MARGIN + 1
    code, out = accepting_after(slower, load, tmp_path=tmp_path, capsys=capfd)
    assert "the gate did not say" not in out, out
    assert ANSWERED_HELD.search(out), out
    assert (code, out.endswith("with_service: green\n")) == (0, True)


def test_a_smoke_timeout_below_the_time_the_gate_takes_to_answer_leaves_a_clean_run_green(
    capfd: pytest.CaptureFixture[str],
) -> None:
    code, out = graceful("slow", capsys=capfd, command=smoke(timeout="0.001"))
    assert "the gate did not say" not in out, out
    assert ANSWERED_HELD.search(out), out
    assert (code, out.endswith("with_service: green\n")) == (0, True)


def test_a_service_that_never_accepts_the_held_request_is_judged_as_before(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    code, out = run(
        *("--boot", f"exec {python(SLOW_TO_ACCEPT)} {tmp_path / 'w'} {tmp_path / 'never'}"),
        *("--ready", "http://127.0.0.1:{port}/ready", "--timeout", "2", "--graceful", "60"),
        *("--", *smoke(concurrency="1")),
        capsys=capsys,
    )
    said = f"{OLDER}the service did not accept the request the gate held, POST http://127.0.0.1:"
    assert said in out, out
    assert " in 2s: it was still waiting in the listener's queue; a service slower" in out
    assert "the service exited with code 9 " in out, "signalled while it would not accept"
    assert (code, out.endswith("with_service: red\n")) == (1, True)


def test_a_command_that_ends_before_the_signal_is_red_and_says_why(
    capfd: pytest.CaptureFixture[str],
) -> None:
    code, out = exits_on_signal("drain", [sys.executable, "-c", ENDS_FIRST], capsys=capfd)
    assert out.startswith("the load failed: every request was refused\n"), out
    assert "the command exited with code 123" in out
    assert "the command ended before the 0.5s signal: nothing was in flight" in out
    assert out.endswith("with_service: red\n")
    assert code == 123


def test_a_command_that_ends_before_the_signal_leaves_the_held_request_to_judge_the_stop(
    capsys: pytest.CaptureFixture[str],
) -> None:
    code, out = run(
        *("--boot", f"MODE=drain exec {python(EXITS_ON_SIGNAL)}", "--ready", READY),
        *("--graceful", "60", "--signal-after", "600", "--", *announces(waits=False)),
        capsys=capsys,
    )
    assert ANSWERED_HELD.search(out), out
    assert (code, out.endswith("with_service: green\n")) == (0, True)


@pytest.mark.parametrize(
    "verdict", [(1, "drop", DROPPED_HELD), (0, "drain", ANSWERED_HELD)], ids=["drop", "drain"]
)
def test_the_tickets_planted_fault_under_eight_connections_is_red_and_its_cure_green(
    verdict: tuple[int, str, re.Pattern[str]], capfd: pytest.CaptureFixture[str]
) -> None:
    code, mode, line = verdict
    exited, out = exits_on_signal(mode, smoke(concurrency="8"), capsys=capfd)
    assert line.search(out), out
    assert exited == code


def test_a_route_that_answers_before_its_request_is_whole_is_judged_as_before(
    capsys: pytest.CaptureFixture[str],
) -> None:
    code, out = run(
        *("--boot", f"exec {python(ANSWERS_UNREAD)}", "--ready", READY),
        *("--graceful", "60", "--signal-after", "0.5", "--", *smoke(concurrency="1")),
        capsys=capsys,
    )
    said = f"{OLDER}the service answered or closed the request the gate held, POST http://"
    assert said in out, out
    assert "bind a route that waits for its whole request, longer than --signal-after," in out
    if "0 request(s) had been accepted and not yet answered at the stop signal" in out:
        assert (code, out.endswith("with_service: could not judge\n")) == (gatelib.UNJUDGED, True)
    else:
        assert (code, out.endswith("with_service: green\n")) == (0, True)


def test_the_marker_is_written_before_the_signal_so_a_failure_after_it_is_judged_by_it(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    written: list[Path] = []
    at_signal: list[bool] = []
    write_stopping = gatelib.write_stopping
    send_signal = subprocess.Popen.send_signal

    def writes(path: Path, stopping: gatelib.Stopping) -> None:
        written.append(path)
        write_stopping(path, stopping)

    def sends(process: subprocess.Popen[bytes], sig: int) -> None:
        if sig == signal.SIGTERM:
            at_signal.append(bool(written) and written[0].exists())
        send_signal(process, sig)

    monkeypatch.setattr(gatelib, "write_stopping", writes)
    monkeypatch.setattr(subprocess.Popen, "send_signal", sends)
    code, out = graceful("drain", capsys=capsys)
    assert at_signal == [True], out
    assert code == 0


def test_a_route_the_gate_cannot_reach_is_judged_as_before_and_says_why(
    capfd: pytest.CaptureFixture[str],
) -> None:
    load = [str(LOAD), "--url", "POST http://127.0.0.1:1/work 200 {}", *BUSY]
    code, out = exits_on_signal("drain", [sys.executable, *load], capsys=capfd)
    said = f"{OLDER}the gate could not send POST http://127.0.0.1:1/work to hold it across the "
    assert said in out, out
    assert "ConnectionRefusedError: [Errno 111] Connection refused; bind a route the " in out
    assert (code, out.endswith("with_service: red\n")) == (1, True)


def test_a_command_that_never_starts_sending_is_red_and_the_service_is_not_signalled(
    capsys: pytest.CaptureFixture[str],
) -> None:
    code, out = run(
        *("--boot", f"MODE=drain exec {python(STOPS)}", "--ready", READY, "--timeout", "2"),
        *("--graceful", "2", "--", sys.executable, "-c", "import time; time.sleep(3)"),
        capsys=capsys,
    )
    assert "the command did not start sending within 2s: the signal waits until it " in out
    assert f"creates ${gatelib.SENDING_ENV}, as load_smoke.py --until-stopped does" in out
    assert code == 1


def test_a_smoke_that_starts_sending_after_the_timeout_is_red_at_once_and_never_hangs() -> None:
    gate = subprocess.Popen(  # noqa: S603 # -- the gate under test, no shell
        [
            *(sys.executable, str(LOAD.with_name("with_service.py"))),
            *("--boot", f"MODE=drain exec {python(EXITS_ON_SIGNAL)}", "--ready", READY),
            *("--timeout", "1", "--graceful", "10", "--", *smoke(starts_after="2")),
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
    try:
        out, _ = gate.communicate(timeout=30)
    except subprocess.TimeoutExpired:
        gate.terminate()
        out, _ = gate.communicate(timeout=30)
        pytest.fail(f"the gate still ran 30s after it started:\n{out.decode()}")
    said = out.decode()
    assert "the command did not start sending within 1s: the signal waits until it " in said
    assert (gate.returncode, said.endswith("with_service: red\n")) == (1, True), said


@pytest.mark.parametrize(
    ("way", "verdict"),
    [
        (
            (["body", "2"], ["--timeout", "1"]),
            ("the command did not start sending within 1s: ", "", 1),
        ),
        (
            (["none", "0"], ["--signal-after", "5"]),
            ("the command ended before the 5s signal: nothing was in flight", "", gatelib.UNJUDGED),
        ),
        ((["none", "0", "waits"], []), (OLDER, "", 0)),
        (
            (["body", "0", "waits"], []),
            ("with_service: the service accepted the request the gate ", "held", 0),
        ),
    ],
    ids=["it never started sending", "it ended first", "the older way", "held"],
)
def test_every_way_out_of_the_wait_for_the_signal_releases_the_command(
    way: tuple[list[str], list[str]],
    verdict: tuple[str, str, int],
    *,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    (stand_in, flags), (said, released, code) = way, verdict
    seen = tmp_path / "seen"
    exited, out = run(
        *("--boot", f"MODE=drain exec {python(EXITS_ON_SIGNAL)}", "--ready", READY),
        *("--graceful", "60", "--signal-after", "0.5", *flags, "--", sys.executable, "-c"),
        *(RECORDS_THE_RELEASE, str(seen), HOLDS, *stand_in),
        capsys=capsys,
    )
    assert said in out, out
    assert (seen.read_text(encoding="utf-8") if seen.exists() else None) == released, out
    assert exited == code, out


def test_a_service_killed_by_the_signal_is_told_to_exec(
    capsys: pytest.CaptureFixture[str],
) -> None:
    boot = f"MODE=ignore exec {python(STOPS)}"
    shell_first = f"{boot} & wait"
    code, out = run(
        *("--boot", shell_first, "--ready", READY, "--graceful", "2", "--signal-after", "0.3"),
        *("--", sys.executable, "-c", SENDS_THEN_WAITS),
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
        ["--listens", "everywhere"],
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
    ("mode", "said", "timeout"),
    [
        ("boot", "the service became ready without DATABASE_URL: it did not refuse", "20"),
        (
            "zero",
            "the service exited with code 0 without DATABASE_URL: a refusal is non-zero",
            "20",
        ),
        ("silent", "the service exited with code 2 without DATABASE_URL but did not name it", "20"),
        ("hang", "neither refused nor became ready within 0.8s without DATABASE_URL", "0.8"),
    ],
)
def test_each_way_of_not_refusing_is_red_with_the_boot_output(
    mode: str, said: str, capsys: pytest.CaptureFixture[str], *, timeout: str
) -> None:
    code, out = refuses(mode, "DATABASE_URL", capsys=capsys, timeout=timeout)
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


def unmeasured_env() -> dict[str, str]:
    return {
        key: value
        for key, value in os.environ.items()
        if not key.startswith(("COV_CORE_", "COVERAGE_"))
    }


def test_killing_the_wrapper_outright_stops_its_product(
    tmp_path: Path, spawn: servers.Spawn
) -> None:
    pid_file = tmp_path / "product.pid"
    ready_file = tmp_path / "command.ready"
    boot = f"exec {python(RECORDS_PID + SERVE)} {pid_file}"
    wait_for_parent = (
        "import os, pathlib, sys, time\n"
        "parent = os.getppid()\n"
        "pathlib.Path(sys.argv[1]).touch()\n"
        "while os.getppid() == parent:\n"
        "    time.sleep(0.05)\n"
    )
    wrapper = spawn(
        [
            sys.executable,
            str(LOAD.with_name("with_service.py")),
            "--boot",
            boot,
            "--ready",
            READY,
            "--",
            sys.executable,
            "-c",
            wait_for_parent,
            str(ready_file),
        ],
        env=unmeasured_env(),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
    deadline = time.monotonic() + 10
    while not ready_file.is_file():
        assert wrapper.poll() is None, "with_service exited before the command ran"
        assert time.monotonic() < deadline, "the service never became ready"
        time.sleep(0.02)
    product = pid_of(pid_file)
    try:
        wrapper.kill()
        wrapper.wait(timeout=10)
        assert gone(product, wait=5), "the product outlived its SIGKILLed service wrapper"
    finally:
        with contextlib.suppress(ProcessLookupError):
            os.kill(product, signal.SIGKILL)


def test_on_parent_exit_sets_pdeathsig_on_linux_and_checks_for_parent_race(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(sys, "platform", "darwin")
    assert with_service.on_parent_exit(os.getpid()) is None

    prctl_calls: list[tuple[int, ...]] = []
    kill_calls: list[tuple[int, int]] = []

    class FakeLib:
        def prctl(self, *args: int) -> int:
            prctl_calls.append(args)
            return 0

    def fake_cdll(_name: str | None) -> FakeLib:
        return FakeLib()

    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setattr(ctypes, "CDLL", fake_cdll)
    monkeypatch.setattr(os, "kill", lambda pid, sig: kill_calls.append((pid, sig)))

    alive = with_service.on_parent_exit(os.getppid())
    assert alive is not None
    alive()
    assert prctl_calls == [(with_service.PR_SET_PDEATHSIG, signal.SIGTERM, 0, 0, 0)]
    assert kill_calls == []

    raced = with_service.on_parent_exit(os.getppid() + 1)
    assert raced is not None
    raced()
    assert prctl_calls == [(with_service.PR_SET_PDEATHSIG, signal.SIGTERM, 0, 0, 0)] * 2
    assert kill_calls == [(os.getpid(), signal.SIGTERM)]
