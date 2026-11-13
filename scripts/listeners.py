"""The listening sockets of a service the gate is stopping, held so it can tell who reset what.

A server that stops closes its listener, and Linux then resets every connection still waiting
in that listener's accept queue. The service never accepted those, so it dropped nothing, but
the client sees the same reset as a request dropped mid-flight. ``with_service.py --graceful``
tells the two apart with this module, on Linux 5.6 or later:

* Before the stop signal, ``grab`` takes a copy of each TCP listening socket a process of the
  service's group holds, with ``pidfd_getfd``. That needs the permission ptrace needs over the
  service, which the gate has over a service it started unless the host forbids ptrace outright.
  The copy keeps a listener open after the service closes it, so the kernel resets nothing.
* After the signal, ``take`` waits until no process but the gate holds a listener, so nothing of
  the service can accept from it again. It then accepts every connection queued there, writes
  each one's ports to the stopping marker, and only after that resets it.

So a reset the client sees after the signal is either the gate's, and its ports are in the
marker, or the service's own: a connection it accepted and dropped. No clock decides which.
Where the gate cannot hold the listeners, ``grab`` says why, and every reset counts.
"""

from __future__ import annotations

import contextlib
import ctypes
import errno
import os
import socket
import struct
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

import gatelib

if TYPE_CHECKING:
    import threading

PROC = Path("/proc")
PIDFD_GETFD = 438  # the syscall's number, the same on every Linux architecture
POLL = 0.002
RESET = struct.pack("ii", 1, 0)  # SO_LINGER on with a zero timeout: close sends a reset
GONE = (errno.ESRCH, errno.EBADF)  # the process or the descriptor ended since it was listed


@dataclass
class Listener:
    """The gate's copy of a listening socket of the service, and every other descriptor of it."""

    copy: socket.socket
    port: int
    holders: list[Path]

    def released(self) -> bool:
        """Whether no process but the gate holds the socket, so nothing else can accept from it.

        The holders found before the signal are checked first; once none holds it, every process
        is looked at again, since one may have forked after the signal.
        """
        link = f"socket:[{os.fstat(self.copy.fileno()).st_ino}]"
        if any(points(holder, link) for holder in self.holders):
            return False
        self.holders = sockets().get(link, [])
        return not self.holders


def points(descriptor: Path, link: str) -> bool:
    """Whether ``descriptor`` is still open on the socket ``link`` names."""
    try:
        return str(descriptor.readlink()) == link
    except OSError:
        return False


def sockets() -> dict[str, list[Path]]:
    """Every socket descriptor of every process the gate may look at, but its own, by socket."""
    found: dict[str, list[Path]] = {}
    for process in PROC.glob("[0-9]*"):
        if process.name == str(os.getpid()):
            continue
        with contextlib.suppress(OSError):
            for descriptor in (process / "fd").iterdir():
                with contextlib.suppress(OSError):
                    link = str(descriptor.readlink())
                    if link.startswith("socket:["):
                        found.setdefault(link, []).append(descriptor)
    return found


def group_of(descriptor: Path) -> int | None:
    """The process group of the process holding ``descriptor``, ``None`` once it has exited."""
    try:
        stat = (descriptor.parents[1] / "stat").read_text(encoding="utf-8")
    except OSError:
        return None
    return int(stat.rpartition(")")[2].split()[2])


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


def listens(sock: socket.socket) -> bool:
    """Whether ``sock`` is a TCP socket accepting connections."""
    return (
        sock.family in (socket.AF_INET, socket.AF_INET6)
        and sock.type == socket.SOCK_STREAM
        and bool(sock.getsockopt(socket.SOL_SOCKET, socket.SO_ACCEPTCONN))
    )


def grab(group: int) -> list[Listener] | str:
    """A copy of each TCP listener a process of ``group`` holds, or why the gate has none."""
    if sys.platform != "linux" or not hasattr(os, "pidfd_open"):
        return "the gate can hold a service's listeners only on Linux 5.6 or later"
    found: list[Listener] = []
    for descriptors in sockets().values():
        ours = [descriptor for descriptor in descriptors if group_of(descriptor) == group]
        if not ours:
            continue
        try:
            copied = copy_of(ours[0])
        except OSError as error:
            if error.errno in GONE:
                continue
            for listener in found:
                listener.copy.close()
            return f"the gate could not take a copy of the service's listener: {error.strerror}"
        if listens(copied):
            found.append(Listener(copied, copied.getsockname()[1], descriptors))
        else:
            copied.close()
    return found or "no process of the service listened on a TCP port when it was signalled"


def queued(listener: socket.socket) -> list[tuple[socket.socket, int]]:
    """Every connection waiting on ``listener``, accepted, with its client's port."""
    taken: list[tuple[socket.socket, int]] = []
    while True:
        try:
            connection, client = listener.accept()
        except BlockingIOError:
            return taken
        taken.append((connection, int(client[1])))


def take(held: list[Listener], marker: Path, ended: threading.Event) -> None:
    """Until ``ended``, take what queues on each listener the service has let go of.

    Each connection's ports go into ``marker`` before the gate resets it, so a client that sees
    the reset finds them there. A listener that no longer listens when the service lets go of it
    was shut down rather than closed, which reset its queue before the gate could take it; the
    marker's ``unread`` then says so. The copies are closed on return.
    """
    taken: set[tuple[int, int]] = set()
    waiting, free = list(held), []
    unread = said = None
    try:
        while not ended.wait(POLL):
            for listener in [each for each in waiting if each.released()]:
                waiting.remove(listener)
                if listens(listener.copy):
                    listener.copy.settimeout(0)  # Why: no one else can accept from it now.
                    free.append(listener)
                else:
                    unread = (
                        f"the service shut its listener on port {listener.port} down instead of "
                        "closing it, which reset the connections waiting on it"
                    )
            fresh = [(each.port, *one) for each in free for one in queued(each.copy)]
            if fresh or unread != said:
                taken.update((port, client) for port, _, client in fresh)
                gatelib.write_stopping(marker, gatelib.Stopping(frozenset(taken), unread))
                said = unread
            for _, connection, _ in fresh:
                connection.setsockopt(socket.SOL_SOCKET, socket.SO_LINGER, RESET)
                connection.close()
    finally:
        for listener in held:
            listener.copy.close()
