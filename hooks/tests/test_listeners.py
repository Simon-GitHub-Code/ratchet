"""``listeners``: the gate holds a stopping service's listeners and takes what it never accepted."""

import errno
import os
import signal
import socket
import subprocess
import sys
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import pytest

import gatelib
import listeners
from tests.servers import gone

# Why: a service of its own process group with one TCP listener it never accepts from, and a UDP
# socket beside it; it closes, shuts down or forks on a line of stdin and answers each line.
SERVICE = (
    "import os, socket, sys, time\n"
    "listener = socket.create_server(('127.0.0.1', 0))\n"
    "other = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)\n"
    "print(listener.getsockname()[1], flush=True)\n"
    "for line in sys.stdin:\n"
    "    if line == 'fork\\n':\n"
    "        child = os.fork()\n"
    "        if child == 0: time.sleep(60)\n"
    "        print(child, flush=True)\n"
    "        continue\n"
    "    if line == 'close\\n': listener.close()\n"
    "    if line == 'shut\\n': listener.shutdown(socket.SHUT_RDWR)\n"
    "    print('done', flush=True)\n"
)


class Service:
    """The running ``SERVICE``: its process, the port it listens on, and a line to send it."""

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
def service() -> Iterator[Service]:
    process = subprocess.Popen(  # noqa: S603 # -- this interpreter running a fixed program
        [sys.executable, "-c", SERVICE],
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
    """``take`` running on ``held`` until the block ends."""
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


def test_a_connection_the_service_never_accepted_is_written_down_before_it_is_reset(
    tmp_path: Path,
) -> None:
    marker = tmp_path / "stopping"
    with service() as running:
        held = listeners.grab(running.process.pid)
        assert isinstance(held, list)
        waiting = socket.create_connection(("127.0.0.1", running.port))
        waiting.settimeout(10)
        client = waiting.getsockname()[1]
        with taking(held, marker):
            assert running.tell("close") == "done"
            with pytest.raises(ConnectionResetError):
                waiting.recv(1)
            stopping = gatelib.read_stopping(marker)
        waiting.close()
    assert stopping == gatelib.Stopping(frozenset({(running.port, client)}), None), (
        "the client found its connection in the marker once it saw the reset"
    )
    assert held[0].copy.fileno() == -1, "the copy is let go of when the command ends"


def test_a_listener_shut_down_rather_than_closed_is_said_to_be_unread(tmp_path: Path) -> None:
    marker = tmp_path / "stopping"
    with service() as running:
        held = listeners.grab(running.process.pid)
        assert isinstance(held, list)
        with taking(held, marker):
            assert running.tell("shut") == "done"
            assert running.tell("close") == "done"
            while not marker.exists():
                threading.Event().wait(0.01)
    assert gatelib.read_stopping(marker).unread == (
        f"the service shut its listener on port {running.port} down instead of closing it, "
        "which reset the connections waiting on it"
    )


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
        "no process of the service listened on a TCP port when it was signalled"
    ), "the gate's own sockets are never the service's"
    monkeypatch.setattr(sys, "platform", "darwin")
    assert listeners.grab(os.getpid()) == (
        "the gate can hold a service's listeners only on Linux 5.6 or later"
    )


def test_a_copy_the_kernel_refuses_ends_the_grab_and_lets_go_of_the_rest(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    copies: list[object] = []
    real = listeners.copy_of

    def copy_of(descriptor: Path) -> object:
        if copies:
            raise PermissionError(errno.EPERM, "Operation not permitted")
        copies.append(real(descriptor))
        return copies[0]

    monkeypatch.setattr(listeners, "copy_of", copy_of)
    with service() as running:
        reason = listeners.grab(running.process.pid)
    assert reason == (
        "the gate could not take a copy of the service's listener: Operation not permitted"
    )
    assert isinstance(copies[0], socket.socket)
    assert copies[0].fileno() == -1


def test_a_socket_gone_before_its_copy_is_skipped(monkeypatch: pytest.MonkeyPatch) -> None:
    def vanished(_: Path) -> socket.socket:
        raise OSError(errno.ESRCH, "No such process")

    monkeypatch.setattr(listeners, "copy_of", vanished)
    with service() as running:
        assert listeners.grab(running.process.pid) == (
            "no process of the service listened on a TCP port when it was signalled"
        )


def test_a_descriptor_that_is_not_open_cannot_be_copied_or_placed() -> None:
    with service() as running:
        with pytest.raises(OSError, match="Bad file descriptor") as caught:
            listeners.copy_of(Path(f"/proc/{running.process.pid}/fd/999"))
        assert caught.value.errno == errno.EBADF
    assert listeners.group_of(Path(f"/proc/{running.process.pid}/fd/0")) is None
