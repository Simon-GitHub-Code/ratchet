"""The listening sockets of a service the gate is stopping, held so it can tell who reset what.

A server that stops closes its listener, and Linux then resets every connection still waiting
in that listener's accept queue. The service never accepted those, so it dropped nothing, but
the client sees the same reset as a request dropped mid-flight. ``with_service.py --graceful``
tells the two apart with this module, on Linux 5.6 or later:

* Before the stop signal, ``grab`` takes a copy of each TCP listening socket a process of the
  service holds, with ``pidfd_getfd``. The service is the boot command's process group and every
  process descended from one of its processes. The copy needs the permission ptrace needs over
  the process, which the gate has over a service it started unless the host forbids ptrace.
  The copy keeps a listener open after the service closes it, so the kernel resets nothing.
* After the signal, ``take`` waits until no process but the gate holds a listener, so nothing of
  the service can accept from it again. It then accepts every connection queued there, writes
  each one's ports to the stopping marker, and only after that resets it.

So a reset the client sees after the signal is either the gate's, and its ports are in the
marker, or the service's own: a connection it accepted and dropped. No clock decides which.

The marker says this port by port. Where the gate cannot hold the listeners, ``grab`` says why,
and every port is judged as the smoke judged it before the gate held listeners. ``take`` says why
for one port when the service shuts that listener down rather than closing it, since the
shutdown resets the queue before the gate can take it. It says why for every port not yet let go
of when a process of the service hides its open files after the signal, since the gate can then
no longer tell that the service has let go. A port no process of the service listened on before
the signal is judged the older way too. The command asks before it judges, and the gate answers
once it has looked at every listener again, so a shutdown behind a reset the command saw is in
the answer.
"""

from __future__ import annotations

import contextlib
import ctypes
import errno
import functools
import itertools
import os
import select
import socket
import struct
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

import gatelib

if TYPE_CHECKING:
    import threading

PROC = "/proc"
PIDFD_GETFD = 438  # the syscall's number, the same on every Linux architecture
POLL = 0.002
RESET = struct.pack("ii", 1, 0)  # SO_LINGER on with a zero timeout: close sends a reset
GONE = (errno.ESRCH, errno.EBADF)  # the process or the descriptor ended since it was listed
LISTENING = "0A"  # the state of a listening socket in /proc/net/tcp
ZOMBIE = (b"Z", b"X")  # the states of a process that has exited
NOT_LINUX = "the gate can copy a service's listener only on Linux 5.6 or later"
OLD_KERNEL = (
    "this Linux kernel cannot copy another process's socket, which needs Linux 5.6 or later"
)
ELSEWHERE = (
    "no process the boot command started, nor any child of one, was listening on that port "
    "just before the signal"
)
SHUT = (
    "the service shut its listener down instead of closing it, which reset the connections "
    "waiting on it before the gate could take them"
)
NONE_FOUND = (
    "no process the boot command started, nor any child of one, was listening on a TCP port "
    "just before the signal, as when the server runs in a container"
)


def parse(stat: bytes) -> tuple[str, bytes, int, int]:
    """The name, state, parent and process group a process's ``stat`` gives."""
    head, _, rest = stat.rpartition(b")")
    state, parent, group = rest.split()[:3]
    return head.partition(b"(")[2].decode(errors="replace"), state, int(parent), int(group)


def read(path: str) -> bytes:
    """A small file of ``/proc``, read without a buffer; raises ``OSError`` once it is gone."""
    descriptor = os.open(path, os.O_RDONLY)
    try:
        return os.read(descriptor, 4096)
    finally:
        os.close(descriptor)


def table() -> dict[int, tuple[int, int]]:
    """Every live process but the gate, with its parent and its process group.

    A process's ``stat`` is readable even when its open files are not. A zombie is left out: it
    holds no files, and the kernel refuses to list them as it does a hidden process's.
    """
    found = {}
    for name in os.listdir(PROC):  # noqa: PTH208 # -- a Path per process doubles the scan
        if not name.isdigit() or int(name) == os.getpid():
            continue
        with contextlib.suppress(OSError):  # Why: it may exit while the gate looks.
            _, state, parent, group = parse(read(f"{PROC}/{name}/stat"))
            if state not in ZOMBIE:
                found[int(name)] = (parent, group)
    return found


def hiding(pid: int) -> str | None:
    """The name of process ``pid`` while it is alive, so its unreadable files are hidden.

    ``None`` once it has exited: a process that exits while the gate looks is not hiding.
    """
    with contextlib.suppress(OSError):
        name, state, _, _ = parse(read(f"{PROC}/{pid}/stat"))
        if state not in ZOMBIE:
            return name
    return None


def hidden(pid: int, name: str, error: OSError) -> str:
    """Why the gate cannot tell who holds a listener while process ``pid`` hides its files."""
    return (
        f"the gate is not allowed to look at the open files of process {pid} ({name}), one of "
        f"the service's ({error.strerror}), as when a process runs as another user or has made "
        "itself non-dumpable"
    )


@dataclass
class Service:
    """The processes of the service: its process group, and every process descended from one.

    ``known`` keeps each one seen, so a child is still the service's once its parent has exited.
    """

    group: int
    known: set[int]

    def scan(self, *, everyone: bool) -> tuple[dict[str, list[Path]], str | None]:
        """The socket descriptors of the service's processes, and why one cannot be looked at.

        With ``everyone`` it is the descriptors of every process the gate may look at, by socket.
        """
        processes = table()
        ours = (self.known & processes.keys()) | {
            pid for pid, (_, group) in processes.items() if group == self.group
        }
        while grown := {pid for pid, (parent, _) in processes.items() if parent in ours} - ours:
            ours |= grown
        self.known |= ours
        found: dict[str, list[Path]] = {}
        for pid in processes if everyone else ours:
            try:
                descriptors = os.listdir(f"{PROC}/{pid}/fd")  # noqa: PTH208 # -- as in table
            except OSError as error:
                name = hiding(pid) if pid in ours and isinstance(error, PermissionError) else None
                if name is not None:
                    return found, hidden(pid, name, error)
                continue
            for descriptor in descriptors:
                with contextlib.suppress(OSError):
                    link = os.readlink(f"{PROC}/{pid}/fd/{descriptor}")  # noqa: PTH115 # -- as above
                    if link.startswith("socket:["):
                        found.setdefault(link, []).append(Path(f"{PROC}/{pid}/fd/{descriptor}"))
        return found, None


@dataclass
class Listener:
    """The gate's copy of a listening socket of the service, and every other descriptor of it."""

    copy: socket.socket
    port: int
    holders: list[Path]
    service: Service

    def released(self) -> bool | str:
        """Whether no process but the gate holds the socket, or why the gate cannot tell.

        Once released, nothing else can accept from it. The holders found before are checked
        first; once none holds it, every process is looked at again, since one may have forked
        after the signal. A process of the service that hides its open files leaves it unknown.
        """
        link = f"socket:[{os.fstat(self.copy.fileno()).st_ino}]"
        if any(points(holder, link) for holder in self.holders):
            return False
        found, unseen = self.service.scan(everyone=True)
        if unseen:
            return unseen
        self.holders = found.get(link, [])
        return not self.holders


def points(descriptor: Path, link: str) -> bool:
    """Whether ``descriptor`` is still open on the socket ``link`` names."""
    try:
        return str(descriptor.readlink()) == link
    except OSError:
        return False


def tcp_listeners(pid: int) -> dict[str, int]:
    """Every TCP socket listening in the network namespace of ``pid``: its link, with its port.

    The kernel lists the listening sockets first, so the read stops at the first that is not, and
    takes no longer however many connections the host holds.
    """
    found: dict[str, int] = {}
    for kind in ("tcp", "tcp6"):
        with contextlib.suppress(OSError), Path(f"{PROC}/{pid}/net/{kind}").open() as rows:
            next(rows)
            listening = itertools.takewhile(
                lambda fields: fields[3] == LISTENING, map(str.split, rows)
            )
            found.update((f"socket:[{f[9]}]", int(f[1].rpartition(":")[2], 16)) for f in listening)
    return found


def copy_of(descriptor: Path) -> socket.socket:
    """The gate's own descriptor of the socket ``descriptor`` is open on; raises ``OSError``."""
    syscall = ctypes.CDLL(None, use_errno=True).syscall
    pidfd = os.pidfd_open(int(descriptor.parents[1].name))
    try:
        copied = int(syscall(PIDFD_GETFD, pidfd, int(descriptor.name), 0))
    finally:
        os.close(pidfd)
    if copied < 0:
        code = ctypes.get_errno()
        raise OSError(code, os.strerror(code))
    return socket.socket(fileno=copied)


def first_copy(descriptors: list[Path]) -> socket.socket | None:
    """A copy of the socket through the first of ``descriptors`` still open; raises ``OSError``."""
    for descriptor in descriptors:
        try:
            return copy_of(descriptor)
        except OSError as error:
            if error.errno not in GONE:
                raise
    return None


def refused(port: int, error: OSError) -> str:
    """Why the gate could not copy the listener on ``port``."""
    if error.errno == errno.ENOSYS:
        return OLD_KERNEL
    return (
        f"the gate is not allowed to copy the service's listener on port {port} "
        f"({error.strerror}); the host's ptrace rules or a container's seccomp profile forbid it"
    )


def listens(sock: socket.socket) -> bool:
    """Whether ``sock`` still accepts connections."""
    return bool(sock.getsockopt(socket.SOL_SOCKET, socket.SO_ACCEPTCONN))


def grab(group: int) -> list[Listener] | str:
    """A copy of each TCP listener a process of the service holds, or why the gate has none.

    The service is the process group ``group`` and every process descended from it. Only the
    open files of its processes are read, so the grab is quick on a busy host.
    """
    if sys.platform != "linux" or not hasattr(os, "pidfd_open"):
        return NOT_LINUX
    service = Service(group, {group})
    sockets, unseen = service.scan(everyone=False)
    if unseen:
        return unseen
    listening = functools.cache(tcp_listeners)
    found: list[Listener] = []
    for link, descriptors in sockets.items():
        port = listening(int(descriptors[0].parents[1].name)).get(link)
        if port is None:
            continue
        try:
            copied = first_copy(descriptors)
        except OSError as error:
            for listener in found:
                listener.copy.close()
            return refused(port, error)
        if copied:
            found.append(Listener(copied, port, descriptors, service))
    return found or NONE_FOUND


def queued(listener: socket.socket) -> list[tuple[socket.socket, gatelib.Connection]]:
    """Every connection waiting on ``listener``, accepted, with its name.

    The listener keeps the flags the service gave it: they belong to the open file the gate's
    copy shares with the service. So it is polled, and accepted from only while a connection
    waits, which nothing else can take first: no process but the gate holds the listener.
    """
    waiting = select.poll()
    waiting.register(listener, select.POLLIN)
    taken: list[tuple[socket.socket, gatelib.Connection]] = []
    while waiting.poll(0):
        connection, client = listener.accept()
        taken.append((connection, gatelib.connection(connection.getsockname(), client)))
    return taken


def told(held: list[Listener] | str) -> gatelib.Stopping:
    """What the gate tells the command at the signal, from what ``grab`` found."""
    if isinstance(held, str):
        return gatelib.Stopping({}, held, frozenset())
    return gatelib.Stopping({listener.port: None for listener in held}, ELSEWHERE, frozenset())


def take(held: list[Listener] | str, marker: Path, ended: threading.Event) -> None:
    """Until ``ended``, take what queues on each listener the service has let go of.

    ``held`` is what ``grab`` found, and ``marker`` already says ``told(held)``. Each connection's
    name, both its addresses and ports, goes into ``marker`` before the gate resets it, so a
    client that sees the reset finds it there. A listener that stops listening was shut down
    rather than closed, which reset its queue before the gate could take it, so its port is no
    longer judged exactly; a process of the service that hides its open files does the same for
    every port not yet let go of. When the command asks, the gate answers once it has looked at
    every listener again. The copies are closed on return.
    """
    first = told(held)
    copies = [] if isinstance(held, str) else held
    ports, taken, answered = dict(first.ports), set(first.taken), False
    waiting, free = list(copies), []
    try:
        while not ended.wait(POLL):
            asked = gatelib.asking(marker).exists()
            before = dict(ports)
            for listener in list(waiting):
                state = listener.released() if listens(listener.copy) else SHUT
                if state is True:
                    waiting.remove(listener)
                    free.append(listener)
                elif state:
                    waiting.remove(listener)
                    listener.copy.close()
                    ports[listener.port] = state
            fresh = [one for each in free for one in queued(each.copy)]
            if fresh or ports != before or (asked and not answered):
                taken.update(name for _, name in fresh)
                answered = asked
                now = gatelib.Stopping(ports, first.elsewhere, frozenset(taken), answered)
                gatelib.write_stopping(marker, now)
            for connection, _ in fresh:
                connection.setsockopt(socket.SOL_SOCKET, socket.SO_LINGER, RESET)
                connection.close()
    finally:
        for listener in copies:
            listener.copy.close()
