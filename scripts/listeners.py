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
PIDFD_GETFD = 438
POLL = 0.002
RESET = struct.pack("ii", 1, 0)
GONE = (errno.ESRCH, errno.EBADF)
LISTENING = "0A"
ZOMBIE = (b"Z", b"X")
EXITING = 0x4
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


def parse(stat: bytes) -> tuple[str, bytes, int, int, int]:
    head, _, rest = stat.rpartition(b")")
    fields = rest.split()
    name = head.partition(b"(")[2].decode(errors="replace")
    return name, fields[0], int(fields[1]), int(fields[2]), int(fields[6])


def read(path: str) -> bytes:
    descriptor = os.open(path, os.O_RDONLY)
    try:
        return os.read(descriptor, 4096)
    finally:
        os.close(descriptor)


def table() -> dict[int, tuple[int, int]]:
    found = {}
    for name in os.listdir(PROC):  # noqa: PTH208 # -- a Path per process doubles the scan
        if not name.isdigit() or int(name) == os.getpid():
            continue
        with contextlib.suppress(OSError):
            _, state, parent, group, _ = parse(read(f"{PROC}/{name}/stat"))
            if state not in ZOMBIE:
                found[int(name)] = (parent, group)
    return found


def hiding(pid: int) -> str | None:
    with contextlib.suppress(OSError):
        name, state, _, _, flags = parse(read(f"{PROC}/{pid}/stat"))
        if state not in ZOMBIE and not flags & EXITING:
            return name
    return None


def hidden(pid: int, name: str, error: OSError) -> str:
    return (
        f"the gate is not allowed to look at the open files of process {pid} ({name}), one of "
        f"the service's ({error.strerror}), as when a process runs as another user or has made "
        "itself non-dumpable"
    )


@dataclass
class Service:
    group: int
    known: set[int]

    def scan(self, *, everyone: bool) -> tuple[dict[str, list[Path]], str | None]:
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
    copy: socket.socket
    port: int
    holders: list[Path]
    service: Service

    def released(self) -> bool | str:
        link = f"socket:[{os.fstat(self.copy.fileno()).st_ino}]"
        if any(points(holder, link) for holder in self.holders):
            return False
        found, unseen = self.service.scan(everyone=True)
        if unseen:
            return unseen
        self.holders = found.get(link, [])
        return not self.holders


def points(descriptor: Path, link: str) -> bool:
    try:
        return str(descriptor.readlink()) == link
    except OSError:
        return False


def tcp_listeners(pid: int) -> dict[str, int]:
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
    for descriptor in descriptors:
        try:
            return copy_of(descriptor)
        except OSError as error:
            if error.errno not in GONE:
                raise
    return None


def refused(port: int, error: OSError) -> str:
    if error.errno == errno.ENOSYS:
        return OLD_KERNEL
    return (
        f"the gate is not allowed to copy the service's listener on port {port} "
        f"({error.strerror}); the host's ptrace rules or a container's seccomp profile forbid it"
    )


def listens(sock: socket.socket) -> bool:
    return bool(sock.getsockopt(socket.SOL_SOCKET, socket.SO_ACCEPTCONN))


def grab(group: int) -> list[Listener] | str:
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
    waiting = select.poll()
    waiting.register(listener, select.POLLIN)
    taken: list[tuple[socket.socket, gatelib.Connection]] = []
    while waiting.poll(0):
        connection, client = listener.accept()
        taken.append((connection, gatelib.connection(connection.getsockname(), client)))
    return taken


def told(held: list[Listener] | str) -> gatelib.Stopping:
    if isinstance(held, str):
        return gatelib.Stopping({}, held, frozenset())
    return gatelib.Stopping({listener.port: None for listener in held}, ELSEWHERE, frozenset())


def take(held: list[Listener] | str, marker: Path, ended: threading.Event) -> None:
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
