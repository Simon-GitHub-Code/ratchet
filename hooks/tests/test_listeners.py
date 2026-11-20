import errno
import fcntl
import ipaddress
import os
import select
import signal
import socket
import subprocess
import sys
import threading
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path

import pytest

import gatelib
import listeners
from tests.servers import gone

SERVICE = (
    "import ctypes, os, socket, sys, time\n"
    "shape = sys.argv[1]\n"
    "def hide(): ctypes.CDLL(None).prctl(4, 0, 0, 0, 0)\n"
    "if shape == 'own-session' and os.fork():\n"
    "    os.wait()\n"
    "    sys.exit()\n"
    "if shape == 'own-session': os.setsid()\n"
    "if shape == 'helper' and os.fork() == 0:\n"
    "    if os.fork() == 0:\n"
    "        helper = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)\n"
    "        time.sleep(60)\n"
    "    os._exit(0)\n"
    "if shape == 'helper': os.wait()\n"
    "listener = socket.create_server(('127.0.0.1', 0))\n"
    "other = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)\n"
    "second = socket.create_server(('127.0.0.1', 0)) if shape == 'two' else None\n"
    "if shape == 'hidden': hide()\n"
    "if shape == 'zombie' and os.fork() == 0: os._exit(0)\n"
    "if shape == 'hidden-worker':\n"
    "    hidden, hides = os.pipe()\n"
    "    if os.fork() == 0:\n"
    "        hide()\n"
    "        os.write(hides, b'.')\n"
    "        time.sleep(60)\n"
    "    os.read(hidden, 1)\n"
    "print(listener.getsockname()[1], flush=True)\n"
    "for line in sys.stdin:\n"
    "    if line == 'fork\\n':\n"
    "        child = os.fork()\n"
    "        if child == 0: time.sleep(60)\n"
    "        print(child, flush=True)\n"
    "        continue\n"
    "    if line == 'second\\n':\n"
    "        print(second.getsockname()[1], flush=True)\n"
    "        continue\n"
    "    if line == 'close\\n': listener.close()\n"
    "    if line == 'shut\\n': listener.shutdown(socket.SHUT_RDWR)\n"
    "    if line == 'hide\\n': hide()\n"
    "    print('done', flush=True)\n"
)


class Service:
    def __init__(self, process: subprocess.Popen[str]) -> None:
        self.process = process
        self.port = int(self.said())

    def said(self) -> str:
        assert self.process.stdout
        return self.process.stdout.readline().strip()

    def tell(self, line: str) -> str:
        assert self.process.stdin
        self.process.stdin.write(f"{line}\n")
        self.process.stdin.flush()
        return self.said()


@contextmanager
def service(shape: str = "plain") -> Iterator[Service]:
    process = subprocess.Popen(  # noqa: S603 # -- this interpreter running a fixed program
        [sys.executable, "-c", SERVICE, shape],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        text=True,
        start_new_session=True,
    )
    try:
        yield Service(process)
    finally:
        os.killpg(process.pid, signal.SIGKILL)
        process.wait()
        assert process.stdin
        assert process.stdout
        process.stdin.close()
        process.stdout.close()


@contextmanager
def taking(held: list[listeners.Listener], marker: Path) -> Iterator[threading.Event]:
    gatelib.write_stopping(marker, listeners.told(held))
    ended = threading.Event()
    taker = threading.Thread(target=listeners.take, args=(held, marker, ended))
    taker.start()
    try:
        yield ended
    finally:
        ended.set()
        taker.join()


def test_the_gate_holds_each_listener_of_the_group_and_nothing_else() -> None:
    with service() as running:
        held = listeners.grab(running.process.pid)
        assert isinstance(held, list)
        assert [listener.port for listener in held] == [running.port]
        assert [holder.parents[1].name for holder in held[0].holders] == [str(running.process.pid)]
        assert not held[0].released()
        held[0].copy.close()


def comm(pid: int) -> str:
    return Path(f"/proc/{pid}/comm").read_text(encoding="utf-8").strip()


def child_of(pid: int) -> int:
    return int(Path(f"/proc/{pid}/task/{pid}/children").read_text(encoding="utf-8").split()[0])


def zombie_of(pid: int) -> int:
    child = child_of(pid)
    until(lambda: Path(f"/proc/{child}/stat").read_text(encoding="utf-8").split()[2] == "Z")
    return child


def until(seen: Callable[[], bool]) -> None:
    deadline = time.monotonic() + 10
    while not seen():
        assert time.monotonic() < deadline, "not seen within 10 s"
        time.sleep(0.01)


def test_a_server_in_a_session_of_its_own_under_the_boot_command_is_held() -> None:
    with service("own-session") as running:
        held = listeners.grab(running.process.pid)
        assert isinstance(held, list), held
        assert [listener.port for listener in held] == [running.port]
        held[0].copy.close()


def test_a_helper_holding_no_tcp_listener_does_not_stop_the_grab() -> None:
    with service("helper") as running:
        held = listeners.grab(running.process.pid)
        assert isinstance(held, list), held
        assert [listener.port for listener in held] == [running.port]
        held[0].copy.close()


def test_a_child_that_exited_unreaped_hides_nothing() -> None:
    with service("zombie") as running:
        zombie_of(running.process.pid)
        held = listeners.grab(running.process.pid)
        assert isinstance(held, list), held
        held[0].copy.close()


def test_a_process_that_exits_as_the_gate_lists_the_processes_is_left_out() -> None:
    sleeper = subprocess.Popen(["sleep", "30"])  # noqa: S607 # -- sleep on PATH
    try:
        assert sleeper.pid in listeners.table()
        read = listeners.read

        def vanishing(path: str) -> bytes:
            if path == f"{listeners.PROC}/{sleeper.pid}/stat":
                raise FileNotFoundError(errno.ENOENT, "gone")
            return read(path)

        with pytest.MonkeyPatch.context() as patch:
            patch.setattr(listeners, "read", vanishing)
            found = listeners.table()
        assert sleeper.pid not in found
        assert os.getppid() in found
    finally:
        sleeper.kill()
        sleeper.wait()


def test_a_process_that_exits_while_the_gate_looks_is_not_hiding() -> None:
    with service("zombie") as running:
        assert listeners.hiding(zombie_of(running.process.pid)) is None
        assert listeners.hiding(running.process.pid) == comm(running.process.pid)
    assert listeners.hiding(running.process.pid) is None


EXITS_SLOWLY = (
    "import socket, time\nheld = [socket.socket() for _ in range(2000)]\ntime.sleep(0.05)"
)


def test_a_process_that_is_exiting_is_not_hiding() -> None:
    refused = said = 0
    for _ in range(20):
        child = subprocess.Popen([sys.executable, "-c", EXITS_SLOWLY])  # noqa: S603 # -- no shell
        while True:
            try:
                state = listeners.parse(listeners.read(f"/proc/{child.pid}/stat"))[1]
            except OSError:
                break
            if state in listeners.ZOMBIE:
                break
            try:
                os.listdir(f"/proc/{child.pid}/fd")  # noqa: PTH208 # -- as the gate lists them
            except PermissionError:
                refused += 1
                said += listeners.hiding(child.pid) is not None
        child.wait()
        if refused:
            break
    assert refused, "no child was caught exiting"
    assert said == 0, f"{said} of {refused} refusals were taken for a hidden process"


@pytest.mark.parametrize("shape", ["hidden", "hidden-worker"])
def test_a_process_of_the_service_the_gate_may_not_look_into_leaves_it_holding_nothing(
    shape: str,
) -> None:
    with service(shape) as running:
        hidden = running.process.pid if shape == "hidden" else child_of(running.process.pid)
        name = comm(hidden)
        reason = listeners.grab(running.process.pid)
    assert reason == (
        f"the gate is not allowed to look at the open files of process {hidden} "
        f"({name}), one of the service's (Permission denied), as when a process "
        "runs as another user or has made itself non-dumpable"
    )


def test_a_process_that_hides_after_the_signal_stops_the_gate_taking(tmp_path: Path) -> None:
    marker = tmp_path / "stopping"
    with service() as running:
        held = listeners.grab(running.process.pid)
        assert isinstance(held, list)
        waiting = socket.create_connection(("127.0.0.1", running.port))
        with taking(held, marker):
            assert running.tell("hide") == "done"
            stopping = gatelib.ask(marker, 10)
        waiting.settimeout(0.2)
        with pytest.raises(TimeoutError):
            waiting.recv(1)
        waiting.close()
    assert stopping.taken == frozenset(), "it took nothing from a listener the service may hold"
    why = stopping.inexact(running.port)
    assert why is not None
    assert why.startswith(
        f"the gate is not allowed to look at the open files of process {running.process.pid} "
    )


def test_a_connection_the_service_never_accepted_is_written_down_before_it_is_reset(
    tmp_path: Path,
) -> None:
    marker = tmp_path / "stopping"
    with service() as running:
        held = listeners.grab(running.process.pid)
        assert isinstance(held, list)
        waiting = socket.create_connection(("127.0.0.1", running.port))
        waiting.settimeout(10)
        name = gatelib.connection(waiting.getpeername(), waiting.getsockname())
        with taking(held, marker):
            assert running.tell("close") == "done"
            with pytest.raises(ConnectionResetError):
                waiting.recv(1)
            stopping = gatelib.read_stopping(marker)
        waiting.close()
    assert stopping == gatelib.Stopping(
        {running.port: None}, listeners.ELSEWHERE, frozenset({name})
    ), "the client found its connection in the marker once it saw the reset"
    assert held[0].copy.fileno() == -1, "the copy is let go of when the command ends"


def test_a_connection_is_in_the_marker_while_the_client_has_not_yet_seen_a_reset(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    marker = tmp_path / "stopping"
    seen: list[str] = []
    with service() as running:
        held = listeners.grab(running.process.pid)
        assert isinstance(held, list)
        waiting = socket.create_connection(("127.0.0.1", running.port))
        waiting.settimeout(10)
        name = gatelib.connection(waiting.getpeername(), waiting.getsockname())
        write = gatelib.write_stopping

        def writes(path: Path, stopping: gatelib.Stopping) -> None:
            if name in stopping.taken:
                readable, _, _ = select.select([waiting], [], [], 0)
                seen.append("reset" if readable else "waiting")
            write(path, stopping)

        monkeypatch.setattr(gatelib, "write_stopping", writes)
        with taking(held, marker):
            assert running.tell("close") == "done"
            with pytest.raises(ConnectionResetError):
                waiting.recv(1)
        waiting.close()
    assert seen[0] == "waiting", "the marker named the connection before the gate reset it"


def test_the_gate_leaves_the_listener_as_blocking_as_the_service_made_it(tmp_path: Path) -> None:
    marker = tmp_path / "stopping"
    with service() as running:
        held = listeners.grab(running.process.pid)
        assert isinstance(held, list)
        witness = listeners.copy_of(held[0].holders[0])
        waiting = socket.create_connection(("127.0.0.1", running.port))
        waiting.settimeout(10)
        with taking(held, marker):
            assert running.tell("close") == "done"
            with pytest.raises(ConnectionResetError):
                waiting.recv(1)
            flags = fcntl.fcntl(witness.fileno(), fcntl.F_GETFL)
        waiting.close()
        witness.close()
    assert not flags & os.O_NONBLOCK, "the gate took the connection without touching the flags"


def test_a_listener_shut_down_is_said_to_be_so_while_the_service_still_holds_it(
    tmp_path: Path,
) -> None:
    marker = tmp_path / "stopping"
    with service("two") as running:
        held = listeners.grab(running.process.pid)
        assert isinstance(held, list)
        second = int(running.tell("second"))
        with taking(held, marker):
            assert running.tell("shut") == "done"
            told = gatelib.ask(marker, 10)
    assert told.ports == {
        running.port: (
            "the service shut its listener down instead of closing it, which reset the "
            "connections waiting on it before the gate could take them"
        ),
        second: None,
    }, "the other listener is still held"


def test_a_process_forked_after_the_signal_still_holds_the_listener() -> None:
    with service() as running:
        held = listeners.grab(running.process.pid)
        assert isinstance(held, list)
        child = int(running.tell("fork"))
        assert running.tell("close") == "done"
        assert not held[0].released(), "the fork holds it, though it was not a holder before"
        os.kill(child, signal.SIGKILL)
        assert gone(child)
        assert held[0].released()
        held[0].copy.close()


def test_the_gate_says_why_it_holds_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    assert listeners.grab(os.getpid()) == (
        "no process the boot command started, nor any child of one, was listening on a TCP port "
        "just before the signal, as when the server runs in a container"
    ), "the gate's own sockets are never the service's"
    monkeypatch.setattr(sys, "platform", "darwin")
    assert listeners.grab(os.getpid()) == (
        "the gate can copy a service's listener only on Linux 5.6 or later"
    )


@pytest.mark.parametrize(
    ("error", "said"),
    [
        (
            PermissionError(errno.EPERM, "Operation not permitted"),
            (
                "the gate is not allowed to copy the service's listener on port {port} (Operation "
                "not permitted); the host's ptrace rules or a container's seccomp profile forbid it"
            ),
        ),
        (
            OSError(errno.ENOSYS, "Function not implemented"),
            (
                "this Linux kernel cannot copy another process's socket, which needs Linux 5.6 or "
                "later"
            ),
        ),
    ],
)
def test_a_copy_the_kernel_refuses_ends_the_grab_and_lets_go_of_the_rest(
    error: OSError, said: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    copies: list[socket.socket] = []
    copied: list[int] = []
    real = listeners.copy_of

    def copy_of(descriptor: Path) -> socket.socket:
        if copies:
            raise error
        copies.append(real(descriptor))
        copied.append(copies[0].getsockname()[1])
        return copies[0]

    monkeypatch.setattr(listeners, "copy_of", copy_of)
    with service("two") as running:
        reason = listeners.grab(running.process.pid)
        both = {running.port, int(running.tell("second"))}
    assert reason == said.format(port=(both - set(copied)).pop())
    assert copies[0].fileno() == -1, "the copy taken before the refusal is let go of"


def test_a_socket_gone_before_its_copy_is_skipped(monkeypatch: pytest.MonkeyPatch) -> None:
    def vanished(_: Path) -> socket.socket:
        raise OSError(errno.ESRCH, "No such process")

    monkeypatch.setattr(listeners, "copy_of", vanished)
    with service() as running:
        assert listeners.grab(running.process.pid) == listeners.NONE_FOUND


def test_a_descriptor_that_is_not_open_cannot_be_copied() -> None:
    with service() as running:
        with pytest.raises(OSError, match="Bad file descriptor") as caught:
            listeners.copy_of(Path(f"/proc/{running.process.pid}/fd/999"))
        assert caught.value.errno == errno.EBADF


def test_a_child_of_the_service_stays_the_services_once_its_parent_has_exited() -> None:
    with service("own-session") as running:
        group = running.process.pid
        ours = listeners.Service(group, set())
        before, unseen = ours.scan(everyone=False)
        assert unseen == []
        assert before
        os.kill(group, signal.SIGKILL)
        until(lambda: Path(f"/proc/{group}/stat").read_text(encoding="utf-8").split()[2] == "Z")
        after, unseen = ours.scan(everyone=False)
    assert unseen == []
    assert after == before


def test_the_listening_rows_are_read_no_further_than_the_first_row_that_is_not(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    net = tmp_path / "7" / "net"
    net.mkdir(parents=True)
    listening = (
        "0: 0100007F:1F90 00000000:0000 0A 00000000:00000000 00:00000000 00000000 0 0 123 1 0"
    )
    connected = (
        "1: 0100007F:1F91 0100007F:C350 01 00000000:00000000 00:00000000 00000000 0 0 124 1 0"
    )
    (net / "tcp").write_text(f"header\n{listening}\n{connected}\nnot a row\n", encoding="utf-8")
    monkeypatch.setattr(listeners, "PROC", str(tmp_path))
    loopback = (ipaddress.ip_address("127.0.0.1"), 8080)
    assert listeners.tcp_listeners(7) == ({"socket:[123]": loopback}, [])
    assert listeners.tcp_listeners(8) is None, "a process with no table to read has no answer"


ROW = "0: {} 00000000:0000 0A 00000000:00000000 00:00000000 00000000 0 0 123 1 0"


@pytest.mark.parametrize(
    ("text", "unread"),
    [
        ("header\n0: 0100007F:1F90\n", "'0: 0100007F:1F90'"),
        (f"header\n{ROW.format('ZZ00007F:1F90')}\n", repr(ROW.format("ZZ00007F:1F90"))),
        (f"header\n{ROW.format('0100007F:ZZ')}\n", repr(ROW.format("0100007F:ZZ"))),
        (f"header\n{ROW.format('010:1F90')}\n", repr(ROW.format("010:1F90"))),
        (f"header\n{ROW.format('01000000AA:1F90')}\n", repr(ROW.format("01000000AA:1F90"))),
        ("", None),
    ],
    ids=["a short row", "not hex", "a port not hex", "odd hex", "a length no address has", "empty"],
)
def test_a_table_the_gate_cannot_read_is_said_not_raised(
    text: str, unread: str | None, *, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    net = tmp_path / "7" / "net"
    net.mkdir(parents=True)
    (net / "tcp").write_text(text, encoding="utf-8")
    monkeypatch.setattr(listeners, "PROC", str(tmp_path))
    said = (
        f"a row of the system's net/tcp table cannot be read: {unread}"
        if unread
        else "the system's net/tcp table is empty"
    )
    assert listeners.tcp_listeners(7) == ({}, [said])


def test_a_table_the_gate_may_not_open_is_said(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    net = tmp_path / "7" / "net"
    (net / "tcp").mkdir(parents=True)
    monkeypatch.setattr(listeners, "PROC", str(tmp_path))
    said = "the system's net/tcp table cannot be read: Is a directory"
    assert listeners.tcp_listeners(7) == ({}, [said])


def test_where_nothing_listens_for_the_boot_command_or_on_its_port_it_says_why() -> None:
    with service() as running:
        assert listeners.bound(running.process.pid, gatelib.free_port()) == listeners.Seen(
            [(ipaddress.ip_address("127.0.0.1"), running.port)], [], []
        )
    nothing = listeners.Seen([], [], [listeners.NOT_FOUND])
    assert listeners.bound(os.getpid(), gatelib.free_port()) == nothing


def test_a_listener_of_the_group_is_the_products_even_when_its_process_table_is_gone(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    real = listeners.tcp_listeners
    monkeypatch.setattr(
        listeners, "tcp_listeners", lambda pid: real(pid) if pid == os.getpid() else None
    )
    with service() as running:
        seen = listeners.bound(running.process.pid, running.port)
        port = running.port
    assert seen == listeners.Seen([(ipaddress.ip_address("127.0.0.1"), port)], [], [])


def fake_process(proc: Path, pid: int, table: str) -> None:
    (proc / str(pid) / "fd").mkdir(parents=True)
    stat = f"{pid} (product) S 1 {pid} 0 0 0 0 0 0\n"
    (proc / str(pid) / "stat").write_text(stat, encoding="utf-8")
    (proc / str(pid) / "fd" / "3").symlink_to("socket:[999]")
    (proc / str(pid) / "net").mkdir()
    (proc / str(pid) / "net" / "tcp").write_text(table, encoding="utf-8")


def test_a_row_of_a_product_process_table_that_cannot_be_read_is_said(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_process(tmp_path, os.getpid(), "header\n")
    fake_process(tmp_path, 4242, "header\n0: 0100007F\n")
    monkeypatch.setattr(listeners, "PROC", str(tmp_path))
    said = "a row of the system's net/tcp table cannot be read: '0: 0100007F'"
    assert listeners.bound(4242, 1) == listeners.Seen([], [], [said])
