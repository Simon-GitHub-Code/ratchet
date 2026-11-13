"""``listeners``: the gate holds a stopping service's listeners and takes what it never accepted."""

import errno
import fcntl
import os
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

# Why: a service of its own process group with one TCP listener it never accepts from, and a UDP
# socket beside it; it closes, shuts down, hides or forks on a line of stdin and answers each line.
# Its shape, the first argument, is how it starts: ``plain``; ``hidden``, made non-dumpable so no
# other process may look at its open files; ``hidden-worker``, with a non-dumpable child holding
# the listener; ``own-session``, listening from a child in a session of its own; and ``helper``,
# beside a double-forked helper of the group that holds only a UDP socket and, reparented away
# from the gate, may not be copied from under Yama's ptrace_scope 1; ``two`` holds a second TCP
# listener; ``zombie`` leaves a child that exited unreaped.
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
    "if shape == 'hidden-worker' and os.fork() == 0:\n"
    "    hide()\n"
    "    time.sleep(60)\n"
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
    """``take`` running on ``held`` until the block ends, from what the gate wrote at the signal."""
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
    """The child of ``pid`` once it has exited, unreaped."""
    child = child_of(pid)
    until(lambda: Path(f"/proc/{child}/stat").read_text(encoding="utf-8").split()[2] == "Z")
    return child


def until(seen: Callable[[], bool]) -> None:
    deadline = time.monotonic() + 10
    while not seen():
        assert time.monotonic() < deadline, "not seen within 10 s"
        time.sleep(0.01)


def test_a_server_in_a_session_of_its_own_under_the_boot_command_is_held() -> None:
    # Why: a wrapper that starts its server in a session of its own, as a supervisor may, takes
    # the server out of the boot command's process group but not out of its descendants.
    with service("own-session") as running:
        held = listeners.grab(running.process.pid)
        assert isinstance(held, list), held
        assert [listener.port for listener in held] == [running.port]
        held[0].copy.close()


def test_a_helper_holding_no_tcp_listener_does_not_stop_the_grab() -> None:
    # Why: only TCP listeners matter to the gate. A helper the gate may not copy from, holding a
    # UDP socket, once ended the whole grab with "Operation not permitted".
    with service("helper") as running:
        held = listeners.grab(running.process.pid)
        assert isinstance(held, list), held
        assert [listener.port for listener in held] == [running.port]
        held[0].copy.close()


def test_a_child_that_exited_unreaped_hides_nothing() -> None:
    # Why: the kernel refuses to list a zombie's open files, as it does a hidden process's, but a
    # zombie holds none. The service itself is one between its exit and the gate reaping it.
    with service("zombie") as running:
        zombie_of(running.process.pid)
        held = listeners.grab(running.process.pid)
        assert isinstance(held, list), held
        held[0].copy.close()


def test_a_process_that_exits_while_the_gate_looks_is_not_hiding() -> None:
    # Why: a process can exit between the gate reading its state and listing its files, and the
    # kernel then refuses the listing as for a hidden process, though it holds nothing.
    with service("zombie") as running:
        assert listeners.hiding(zombie_of(running.process.pid)) is None
        assert listeners.hiding(running.process.pid) == comm(running.process.pid)
    assert listeners.hiding(running.process.pid) is None


# Why: a process that closes many sockets as it exits stays a while between letting go of its
# memory and becoming a zombie.
EXITS_SLOWLY = (
    "import socket, time\nheld = [socket.socket() for _ in range(2000)]\ntime.sleep(0.05)"
)


def test_a_process_that_is_exiting_is_not_hiding() -> None:
    # Why: from the moment an exiting process lets go of its memory, and before it is a zombie,
    # the kernel refuses to list its open files as it does a hidden process's. A worker of the
    # service that exits as the gate looks once made the gate give up on the listener it held.
    refused = said = 0
    for _ in range(20):  # until a child is caught in that moment
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
    # Why: the gate may take connections only once no process of the service holds the listener,
    # which it cannot know while a process of the service hides its open files.
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


def test_the_gate_leaves_the_listener_as_blocking_as_the_service_made_it(tmp_path: Path) -> None:
    # Why: the gate's copy shares the service's open file, flags and all. A copy made
    # non-blocking makes every holder's accept non-blocking, and a worker the gate cannot see,
    # blocked in accept, then fails with BlockingIOError. The witness is such another holder.
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
    # Why: a shutdown resets the connections waiting on the listener at once, before the gate can
    # take them, and the service may hold the listener long after. A client that saw one of those
    # resets and then asks must be told why that port cannot be judged exactly.
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
