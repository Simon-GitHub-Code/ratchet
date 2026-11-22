from __future__ import annotations

import contextlib
import ctypes
import errno
import functools
import ipaddress
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
    from collections.abc import Iterator

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
UNREADABLE = "this system has no /proc/<pid>/net/tcp or tcp6 to read a listener's address from"
NOT_FOUND = (
    "no process of the boot command's group, nor any child of one, listens on the ready URL's "
    "port where this system shows it"
)
Address = ipaddress.IPv4Address | ipaddress.IPv6Address
End = tuple[Address, int]
Table = dict[str, End]
TAKEN_ELSEWHERE = (
    "it left the listener's queue, but no process of the service holds it, as when a process "
    "outside the boot command's group accepts connections for the service"
)
ABSENT = (
    "the gate found the service's end of that connection nowhere in this system's TCP table, "
    "as when the service runs in another network namespace, such as a container's"
)
NO_TABLE = "this system shows no TCP table the gate can read"


@dataclass(frozen=True)
class Seen:
    product: list[tuple[Address, int]]
    others: list[tuple[Address, int]]
    unchecked: list[str]


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

    def scan(self, *, everyone: bool) -> tuple[dict[str, list[Path]], list[str]]:
        processes = table()
        ours = (self.known & processes.keys()) | {
            pid for pid, (_, group) in processes.items() if group == self.group
        }
        while grown := {pid for pid, (parent, _) in processes.items() if parent in ours} - ours:
            ours |= grown
        self.known |= ours
        found: dict[str, list[Path]] = {}
        unseen: list[str] = []
        for pid in processes if everyone else ours:
            try:
                descriptors = os.listdir(f"{PROC}/{pid}/fd")  # noqa: PTH208 # -- as in table
            except OSError as error:
                name = hiding(pid) if pid in ours and isinstance(error, PermissionError) else None
                if name is not None:
                    unseen.append(hidden(pid, name, error))
                continue
            for descriptor in descriptors:
                with contextlib.suppress(OSError):
                    link = os.readlink(f"{PROC}/{pid}/fd/{descriptor}")  # noqa: PTH115 # -- as above
                    if link.startswith("socket:["):
                        found.setdefault(link, []).append(Path(f"{PROC}/{pid}/fd/{descriptor}"))
        return found, unseen


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
            return unseen[0]
        self.holders = found.get(link, [])
        return not self.holders


def points(descriptor: Path, link: str) -> bool:
    try:
        return str(descriptor.readlink()) == link
    except OSError:
        return False


def end(address: tuple[object, ...]) -> End:
    host, port = gatelib.end(address)
    return ipaddress.ip_address(host), port


def rows() -> Iterator[tuple[End, End, str]]:
    for kind in ("tcp", "tcp6"):
        with (
            contextlib.suppress(OSError),
            Path(f"{PROC}/{os.getpid()}/net/{kind}").open(errors="replace") as lines,
        ):
            for line in lines:
                fields = line.split()
                try:
                    row = (bound_at(fields[1]), bound_at(fields[2]), f"socket:[{fields[9]}]")
                except (IndexError, ValueError):
                    continue
                yield row


def accepted(service: Service, at: End, client: End) -> bool | str:
    seen = list(rows())
    if not seen:
        return NO_TABLE
    link = next((link for local, remote, link in seen if (local, remote) == (at, client)), None)
    if link is None:
        return ABSENT
    if link == "socket:[0]":
        return False
    sockets, unseen = service.scan(everyone=False)
    if unseen:
        return unseen[0]
    return link in sockets or TAKEN_ELSEWHERE


def unmapped(found: Address) -> Address:
    return getattr(found, "ipv4_mapped", None) or found


def address(hexadecimal: str) -> Address:
    raw = bytes.fromhex(hexadecimal)
    words = (raw[at : at + 4] for at in range(0, len(raw), 4))
    return unmapped(
        ipaddress.ip_address(
            b"".join(int.from_bytes(word, sys.byteorder).to_bytes(4) for word in words)
        )
    )


def bound_at(field: str) -> tuple[Address, int]:
    where, _, port = field.rpartition(":")
    return address(where), int(port, 16)


def rows_of(kind: str, lines: Iterator[str], found: Table) -> list[str]:
    if next(lines, None) is None:
        return [f"the system's net/{kind} table is empty"]
    unread = []
    for line in lines:
        fields = line.split()
        try:
            if fields[3] != LISTENING:
                break
            found[f"socket:[{fields[9]}]"] = bound_at(fields[1])
        except (IndexError, ValueError):
            row = line.strip()
            unread.append(f"a row of the system's net/{kind} table cannot be read: {row!r}")
    return unread


def tcp_listeners(pid: int) -> tuple[Table, list[str]] | None:
    found: Table = {}
    unread: list[str] = []
    tables = 0
    for kind in ("tcp", "tcp6"):
        try:
            with Path(f"{PROC}/{pid}/net/{kind}").open(errors="replace") as lines:
                tables += 1
                unread += rows_of(kind, lines, found)
        except FileNotFoundError:
            continue
        except OSError as error:
            tables += 1
            unread.append(f"the system's net/{kind} table cannot be read: {error.strerror}")
    return (found, unread) if tables else None


def bound(group: int, port: int) -> Seen:
    own = tcp_listeners(os.getpid())
    if own is None:
        return Seen([], [], [UNREADABLE])
    table, unread = own
    sockets, unseen = Service(group, {group}).scan(everyone=False)
    unchecked = [*unread, *unseen]
    listening = functools.cache(tcp_listeners)
    product: Table = {}
    for link, descriptors in sockets.items():
        at = table.get(link)
        theirs = None if at else listening(int(descriptors[0].parents[1].name))
        if theirs:
            unchecked += theirs[1]
            at = theirs[0].get(link)
        if at:
            product[link] = at
    others = list(
        dict.fromkeys(at for link, at in table.items() if at[1] == port and link not in sockets)
    )
    if all(at[1] != port for at in product.values()):
        judged = dict.fromkeys([*product.values(), *others])
        return Seen(list(judged), [], list(dict.fromkeys(unchecked or [NOT_FOUND])))
    return Seen(list(dict.fromkeys(product.values())), others, list(dict.fromkeys(unchecked)))


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
    if not Path(PROC).is_dir():
        return UNREADABLE
    service = Service(group, {group})
    sockets, unseen = service.scan(everyone=False)
    if unseen:
        return unseen[0]
    listening = functools.cache(tcp_listeners)
    found: list[Listener] = []
    for link, descriptors in sockets.items():
        at = (listening(int(descriptors[0].parents[1].name)) or ({}, []))[0].get(link)
        if at is None:
            continue
        port = at[1]
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
        last = gatelib.Stopping(ports, first.elsewhere, frozenset(taken), answered=True)
        gatelib.write_stopping(marker, last)
        for listener in copies:
            listener.copy.close()
