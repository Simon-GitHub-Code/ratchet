#!/usr/bin/env python3
from __future__ import annotations

import argparse
import contextlib
import ctypes
import http.client
import ipaddress
import os
import re
import select
import signal
import subprocess
import sys
import tempfile
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import IO, TYPE_CHECKING, cast
from urllib.parse import urlsplit

import gatelib
import listeners
import load_smoke

if TYPE_CHECKING:
    import socket
    from collections.abc import Callable

POLL = 0.1
SENDING_POLL = 0.01
WATCH = 0.01
PR_SET_PDEATHSIG = 1
TAIL = 30
HEAD = 30
PORT_TOKEN = "{port}"  # noqa: S105 # -- a placeholder, not a credential
HOST_TOKEN = "{host}"  # noqa: S105 # -- a placeholder, not a credential
CURE = (
    f"pass {HOST_TOKEN}, which stands for the ready URL's host, in the boot line where the "
    "product takes its address"
)
HOSTED = f"point the ready URL at a loopback address, which {HOST_TOKEN} stands for"
CHECKED = (
    "when the product first answered, each listener of the boot command's process group and its "
    "descendants, one on the ready URL's port among them, was on loopback or declared; a listener "
    "of a process that is neither in that group nor a descendant of it, or one opened later, is "
    "not judged"
)
DECLARE = "or declare the address with --listens"
NOT_CHECKED_HERE = "where the product listens was not checked here"
UNCHECKED = "where the product listens was not checked, so the command after -- did not run"
QUEUED = (
    "it was still waiting in the listener's queue; a service slower to accept a connection "
    "needs a longer --timeout"
)
LISTENS = (
    "declare with --listens each address the product listens on, a loopback address if that is all"
)


def host_of(ready: str) -> str:
    return urlsplit(ready).hostname or ""


def place(address: listeners.Address, port: int) -> str:
    return f"[{address}]:{port}" if ":" in str(address) else f"{address}:{port}"


def judged(process: subprocess.Popen[bytes], options: argparse.Namespace) -> list[str]:
    ready = urlsplit(options.ready)
    seen = listeners.bound(process.pid, ready.port or (443 if ready.scheme == "https" else 80))
    cure = HOSTED if options.hosted else CURE
    session, declared = options.loopback_only, options.listens
    wider = [(address, port) for address, port in seen.product if not address.is_loopback]
    said = [
        f"the product listened on {place(address, port)} when it first answered, as declared"
        for address, port in wider
        if address in declared
    ]
    said += [
        f"{place(address, port)}, on the ready URL's port, is held by a process the gate did not "
        "find among the boot command's, so it is not judged as the product's"
        for address, port in seen.others
        if not address.is_loopback
    ]
    wide = [
        f"the product listened on {place(address, port)}, "
        f"{'every interface' if address.is_unspecified else 'not a loopback address'}, when it "
        "first answered, so the network could reach it"
        for address, port in wider
        if address not in declared
    ]
    refused = [f"{line} around the command after --; {cure}, {DECLARE}" for line in wide]
    said += [] if session else [f"note: {line}; {cure}" for line in wide]
    why = "; ".join(seen.unchecked)
    addresses = " ".join(dict.fromkeys(str(address) for address in declared))
    if seen.unchecked and declared:
        said.append(f"{NOT_CHECKED_HERE}, as declared: {addresses} ({why})")
    elif seen.unchecked and session:
        refused.append(f"{UNCHECKED}: {why}; {LISTENS}")
    elif seen.unchecked:
        said += [f"where the product listens was not checked: {each}" for each in seen.unchecked]
    elif session and not refused:
        said.append(CHECKED)
    for line in said:
        print(f"with_service: {line}", flush=True)
    return refused if session else []


def probe(url: str) -> str | None:
    try:
        status = gatelib.http_status("GET", url, timeout=2.0)
    except (OSError, http.client.HTTPException) as error:
        return f"{type(error).__name__}: {error}"
    return None if status < 400 else f"HTTP {status}"  # noqa: PLR2004 # -- the HTTP error class


def wait_ready(
    process: subprocess.Popen[bytes], options: argparse.Namespace
) -> tuple[str | None, bool]:
    url, timeout = options.ready, options.timeout
    started = time.monotonic()
    deadline = started + timeout
    while True:
        code = process.poll()
        if code is not None:
            took = time.monotonic() - started
            return (
                f"the service exited with code {code} before {url} answered, {took:.1f}s after"
                f" `{options.boot}` started in {Path.cwd()}"
            ), True
        seen = probe(url)
        if seen is None:
            return None, False
        if time.monotonic() >= deadline:
            return f"{url} did not answer within {timeout:g}s; the last probe saw {seen}", False
        time.sleep(POLL)


def tail(log: IO[bytes], *, exited: bool = False) -> list[str]:
    log.seek(0)
    lines = log.read().decode("utf-8", errors="replace").splitlines()
    if not lines:
        return ["boot output: none, the boot command printed nothing"]
    if not exited:
        return ["boot output (tail):", *(f"  {line}" for line in lines[-TAIL:])]
    if len(lines) > HEAD + TAIL:
        left = len(lines) - HEAD - TAIL
        lines = [*lines[:HEAD], f"... {left} lines left out ...", *lines[-TAIL:]]
    return ["boot output:", *(f"  {line}" for line in lines)]


def on_parent_exit(parent: int) -> Callable[[], None] | None:
    if sys.platform != "linux":
        return None
    prctl = ctypes.CDLL(None).prctl

    def set_death_signal() -> None:
        prctl(PR_SET_PDEATHSIG, signal.SIGTERM, 0, 0, 0)
        if os.getppid() != parent:
            os.kill(os.getpid(), signal.SIGTERM)

    return set_death_signal


def start(
    options: argparse.Namespace, env: dict[str, str], log: IO[bytes]
) -> subprocess.Popen[bytes]:
    return subprocess.Popen(  # noqa: S602 # -- the boot command is a shell line by contract
        options.boot,
        shell=True,
        env=env,
        stdin=subprocess.DEVNULL,
        stdout=log,
        stderr=subprocess.STDOUT,
        start_new_session=True,
        preexec_fn=on_parent_exit(os.getpid()),  # noqa: PLW1509 # -- start runs before threads
    )


def sends_within(sending: Path, within: float, ended: threading.Event) -> bool:
    deadline = time.monotonic() + within
    while not sending.exists():
        if ended.wait(SENDING_POLL) or time.monotonic() >= deadline:
            return False
    return True


class Recorded:
    def __init__(self) -> None:
        self.data = bytearray()

    def sendall(self, data: bytes) -> None:
        self.data += data


@dataclass
class Hold:
    goal: load_smoke.Target
    connection: http.client.HTTPConnection
    last: bytes
    at: listeners.End
    client: listeners.End

    def label(self) -> str:
        return f"the request the gate held, {self.goal.label()},"

    def ended(self, within: float) -> bool:
        waiting = select.poll()
        waiting.register(self.connection.sock, select.POLLIN)
        return bool(waiting.poll(within * 1000))


def hold_request(targets: list[load_smoke.Target], timeout: float) -> Hold | str:
    goal = next((each for each in targets if each.body), None)
    if goal is None:
        named = ", ".join(each.label() for each in targets) or "none"
        return (
            f"no route that takes a body was found among the routes the command named ({named}): "
            "a byte the gate held back from a request without one would fall in the request's "
            "head, which a server may close at a stop; bind a route with a body, METHOD URL "
            "STATUS BODY, to have one of its requests held across the signal"
        )
    parts = urlsplit(goal.url)
    connect = gatelib.ToldTLS if parts.scheme == "https" else gatelib.Told
    connection = connect(parts.hostname or "", parts.port, timeout=timeout)
    recorded = Recorded()
    headers = {"Content-Type": "application/json"}
    try:
        connection.connect()
        sock = connection.sock
        connection.sock = cast("socket.socket", recorded)
        path = (parts.path or "/") + (f"?{parts.query}" if parts.query else "")
        connection.request(goal.method, path, goal.body, headers)
        connection.sock = sock
        sock.sendall(recorded.data[:-1])
        at, client = listeners.end(sock.getpeername()), listeners.end(sock.getsockname())
    except (OSError, http.client.HTTPException) as error:
        connection.close()
        return (
            f"the gate could not send {goal.label()} to hold it across the stop signal: "
            f"{load_smoke.described(error)}; bind a route the service answers"
        )
    return Hold(goal, connection, bytes(recorded.data[-1:]), at, client)


def early(hold: Hold) -> str:
    return (
        f"the service answered or closed {hold.label()} before the gate sent its last byte, so it "
        "was not in flight at the stop signal: bind a route that waits for its whole request, "
        "longer than --signal-after, before it answers"
    )


def until_signal(
    process: subprocess.Popen[bytes],
    options: argparse.Namespace,
    *,
    hold: Hold,
    stop: Stop,
    deadline: float,
) -> str | None:
    service = listeners.Service(process.pid, {process.pid})
    signal_at: float | None = None
    while not hold.ended(WATCH):
        if signal_at is not None:
            if stop.ended.is_set() or time.monotonic() >= signal_at:
                return None
            continue
        seen = listeners.accepted(service, hold.at, hold.client)
        if seen is True:
            stop.release(held=True)
            signal_at = time.monotonic() + options.signal_after
        elif seen:
            return f"the gate cannot see whether the service accepted {hold.label()} since {seen}"
        elif time.monotonic() >= deadline:
            return f"the service did not accept {hold.label()} in {options.timeout:g}s: {QUEUED}"
    return early(hold)


def take_hold(
    process: subprocess.Popen[bytes], options: argparse.Namespace, stop: Stop
) -> Hold | str:
    deadline = time.monotonic() + options.timeout
    hold = hold_request(load_smoke.announced(stop.sending), options.timeout)
    if isinstance(hold, str):
        return hold
    waited = until_signal(process, options, hold=hold, stop=stop, deadline=deadline)
    if waited is None:
        return hold
    hold.connection.close()
    return waited


def lost(error: BaseException) -> str:
    said = load_smoke.described(error)
    family = isinstance(error, ConnectionResetError) and type(error) is not ConnectionResetError
    return f"{said}, a ConnectionResetError" if family else said


def reply(hold: Hold, deadline: float) -> str | None:
    hold.connection.sock.settimeout(max(deadline - time.monotonic(), WATCH))
    try:
        hold.connection.sock.sendall(hold.last)
        response = hold.connection.getresponse()
        response.read()
    except TimeoutError:
        return "it was not answered before --graceful ran out"
    except (OSError, http.client.HTTPException) as error:
        return lost(error)
    finally:
        hold.connection.close()
    return None if hold.goal.accepts(response.status) else f"it was answered HTTP {response.status}"


def answered(hold: Hold, deadline: float) -> list[str]:
    wrong = reply(hold, deadline)
    if wrong is not None:
        return [
            (
                f"{hold.label()} which the service accepted before the stop signal, was dropped: "
                f"{wrong}"
            )
        ]
    print(
        f"with_service: the service accepted {hold.label()} before the stop signal and "
        "answered it after",
        flush=True,
    )
    return []


@dataclass
class Stop:
    marker: Path
    sending: Path
    ended: threading.Event = field(default_factory=threading.Event)
    started: list[threading.Thread] = field(default_factory=list)
    released: float | None = None

    def release(self, *, held: bool) -> None:
        path = load_smoke.holding(self.sending)
        pending = path.with_name(f"{path.name}.pending")
        pending.write_text(load_smoke.HELD if held else "", encoding="utf-8")
        pending.replace(path)
        if self.released is None:
            self.released = time.monotonic()


def signal_midway(
    process: subprocess.Popen[bytes], options: argparse.Namespace, stop: Stop
) -> tuple[int, list[str]]:
    hold: Hold | str = ""
    try:
        if not sends_within(stop.sending, options.timeout, stop.ended) and not stop.ended.is_set():
            return 1, [
                (
                    f"the command did not start sending within {options.timeout:g}s: the signal "
                    f"waits until it creates ${gatelib.SENDING_ENV}, as load_smoke.py "
                    "--until-stopped does"
                )
            ]
        hold = take_hold(process, options, stop)
    finally:
        stop.release(held=isinstance(hold, Hold))
    if isinstance(hold, str):
        return older(process, options, stop=stop, why=hold)
    with contextlib.closing(hold.connection):
        return stop_now(process, options, stop=stop, hold=hold)


def older(
    process: subprocess.Popen[bytes], options: argparse.Namespace, *, stop: Stop, why: str
) -> tuple[int, list[str]]:
    print(
        "with_service: the gate judged the stop the older way, by the command's own requests, "
        f"since {why}",
        flush=True,
    )
    released = stop.released or time.monotonic()
    if stop.ended.wait(max(released + options.signal_after - time.monotonic(), 0)):
        after = options.signal_after
        return gatelib.UNJUDGED, [
            f"the command ended before the {after:g}s signal: nothing was in flight"
        ]
    return stop_now(process, options, stop=stop, hold=None)


def stop_now(
    process: subprocess.Popen[bytes], options: argparse.Namespace, *, stop: Stop, hold: Hold | None
) -> tuple[int, list[str]]:
    if process.poll() is not None:
        return 1, [f"the service exited with code {process.returncode} before it was signalled"]
    held = listeners.grab(process.pid)
    gatelib.write_stopping(stop.marker, listeners.told(held))
    signalled = time.monotonic()
    process.send_signal(signal.SIGTERM)
    taker = threading.Thread(target=listeners.take, args=(held, stop.marker, stop.ended))
    taker.start()
    stop.started.append(taker)
    deadline = signalled + options.graceful
    said = [] if hold is None else answered(hold, deadline)
    found = [*said, *stopped(process, signalled, deadline)]
    return int(bool(found)), found


def stopped(process: subprocess.Popen[bytes], signalled: float, deadline: float) -> list[str]:
    try:
        code = process.wait(timeout=max(deadline - time.monotonic(), 0))
    except subprocess.TimeoutExpired:
        return [f"the service was still running {deadline - signalled:g}s after SIGTERM"]
    took = time.monotonic() - signalled
    if code == 0:
        return []
    how = (
        f"was ended by signal {-code}, so boot with `exec`"
        if code < 0
        else f"exited with code {code}"
    )
    return [f"the service {how} {took:.1f}s after SIGTERM, not 0"]


def combined_exit(command_code: int, watch_code: int) -> int:
    if command_code not in (0, gatelib.UNJUDGED):
        return command_code
    if watch_code not in (0, gatelib.UNJUDGED):
        return watch_code
    return gatelib.UNJUDGED if gatelib.UNJUDGED in (command_code, watch_code) else 0


def run_signalled(
    process: subprocess.Popen[bytes],
    options: argparse.Namespace,
    command: list[str],
    *,
    env: dict[str, str],
) -> tuple[int, list[str]]:
    with tempfile.TemporaryDirectory() as scratch:
        stop = Stop(Path(scratch) / "stopping", Path(scratch) / "sending")
        env[gatelib.STOPPING_ENV], env[gatelib.SENDING_ENV] = str(stop.marker), str(stop.sending)
        env[gatelib.HOLD_WITHIN_ENV] = str(options.timeout)
        watch_code = [0]
        found: list[str] = []

        def watch() -> None:
            try:
                exit_status, lines = signal_midway(process, options, stop)
                watch_code[0] = exit_status
                found.extend(lines)
            finally:
                gatelib.stop_group(process)

        watcher = threading.Thread(target=watch)
        watcher.start()
        try:
            code, problems = gatelib.run_command(command, env)
        finally:
            stop.ended.set()
            watcher.join()
            for thread in stop.started:
                thread.join()
    return combined_exit(code, watch_code[0]), [*problems, *found]


def refusal(
    code: int | None, name: str, not_ready: str | None, *, output: str, timeout: float
) -> list[str]:
    if not_ready is None:
        return [f"the service became ready without {name}: it did not refuse"]
    if code is None:
        return [f"the service neither refused nor became ready within {timeout:g}s without {name}"]
    if code == 0:
        return [f"the service exited with code 0 without {name}: a refusal is non-zero"]
    if not re.search(rf"(?<![A-Za-z0-9_]){re.escape(name)}(?![A-Za-z0-9_])", output):
        return [f"the service exited with code {code} without {name} but did not name it"]
    return []


def serve(
    options: argparse.Namespace, command: list[str], env: dict[str, str]
) -> tuple[int, list[str], bool]:
    code, problems, exited = 1, [], False
    with tempfile.TemporaryFile() as log:
        try:
            with contextlib.ExitStack() as running:
                with gatelib.signals_held():
                    process = start(options, env, log)
                    running.callback(gatelib.stop_group, process)
                not_ready, exited = wait_ready(process, options)
                if options.refuses:
                    log.seek(0)
                    output = log.read().decode("utf-8", errors="replace")
                    ended = process.returncode if exited else None
                    problems = refusal(
                        ended, options.refuses, not_ready, output=output, timeout=options.timeout
                    )
                    code = 1 if problems else 0
                elif not_ready:
                    problems = [not_ready]
                elif refused := judged(process, options):
                    problems = refused
                elif command and options.graceful is not None:
                    code, problems = run_signalled(process, options, command, env=env)
                elif command:
                    code, problems = gatelib.run_command(command, env)
                else:
                    code = 0
        except gatelib.Interrupted as stop:
            code, problems, exited = 128 + stop.signum, [str(stop)], False
        if gatelib.listening(options.ready):
            code, problems = 1, [*problems, f"{options.ready} still answers after the stop"]
        if problems:
            problems += tail(log, exited=exited)
    return code, problems, exited


def pair(text: str) -> tuple[str, str]:
    key, separator, value = text.partition("=")
    if not key or not separator:
        message = f"{text!r} is not KEY=VALUE"
        raise argparse.ArgumentTypeError(message)
    return key, value


def addresses(text: str) -> list[listeners.Address]:
    words = text.split()
    if not words:
        message = (
            "names no address: write each address the product listens on on purpose, separated "
            "by spaces, such as --listens '0.0.0.0 ::'"
        )
        raise argparse.ArgumentTypeError(message)
    try:
        return [listeners.unmapped(ipaddress.ip_address(word)) for word in words]
    except ValueError as error:
        raise argparse.ArgumentTypeError(str(error)) from None


def parse(argv: list[str]) -> tuple[argparse.Namespace, list[str]]:
    parser = argparse.ArgumentParser(prog="with_service.py", usage="%(prog)s [options] [-- cmd...]")
    parser.add_argument("--boot", required=True, help="shell command that starts the service")
    parser.add_argument("--ready", required=True, help="URL that answers below 400 when ready")
    parser.add_argument(
        "--timeout",
        type=float,
        default=30.0,
        help=(
            "seconds to wait for ready, and with --graceful for the command to start sending and "
            "for the service to accept the request the gate holds"
        ),
    )
    parser.add_argument("--env", action="append", type=pair, default=[], metavar="K=V")
    parser.add_argument(
        "--graceful",
        type=float,
        metavar="S",
        help="SIGTERM while a request is in flight; answer it and exit 0 within S",
    )
    parser.add_argument(
        "--signal-after",
        type=float,
        default=1.0,
        metavar="S",
        help="seconds of the command's load from the service accepting the held request to SIGTERM",
    )
    parser.add_argument("--refuses", action="append", default=[], metavar="NAME")
    parser.add_argument(
        "--loopback-only",
        action="store_true",
        help="refuse to run the command while the service listens beyond the loopback address",
    )
    parser.add_argument(
        "--listens",
        action="extend",
        type=addresses,
        default=[],
        metavar="ADDRESSES",
        help="addresses, separated by spaces, the service listens on on purpose: said, not refused",
    )
    rest = argv[1:]
    split = rest.index("--") if "--" in rest else len(rest)
    options = parser.parse_args(rest[:split])
    command = rest[split + 1 :]
    if urlsplit(options.ready).scheme not in ("http", "https"):
        parser.error(f"--ready {options.ready!r} is not an http or https URL")
    if HOST_TOKEN in options.ready:
        parser.error(f"--ready cannot hold {HOST_TOKEN}, which stands for the ready URL's host")
    if options.graceful is not None and (options.graceful <= 0 or options.signal_after <= 0):
        parser.error("--graceful and --signal-after must be positive")
    if options.graceful is not None and not command:
        parser.error("--graceful needs a command after --, such as the load smoke")
    if options.refuses and (command or options.graceful is not None):
        parser.error("--refuses boots with a setting missing and runs no command; use it alone")
    return options, command


def main(argv: list[str]) -> int:
    options, command = parse(argv)
    pairs = options.env
    texts = [options.boot, options.ready, *command, *(value for _, value in pairs)]
    picked = any(PORT_TOKEN in text for text in texts)
    hosted = any(HOST_TOKEN in text for text in texts)

    def attempt(missing: str | None = None) -> tuple[int, list[str], bool]:
        env = dict(os.environ)
        if picked:
            env["PORT"] = str(gatelib.free_port())
        port = env.get("PORT", PORT_TOKEN)
        host = host_of(options.ready.replace(PORT_TOKEN, port))

        def expand(text: str) -> str:
            return text.replace(PORT_TOKEN, port).replace(HOST_TOKEN, host)

        ran = argparse.Namespace(**{**vars(options), "boot": expand(options.boot)})
        ran.hosted = hosted
        ran.ready, ran.refuses = expand(options.ready), missing
        env[gatelib.READY_ENV] = ran.ready
        env.update({key: expand(value) for key, value in pairs})
        if missing:
            env.pop(missing, None)
        if gatelib.listening(ran.ready):
            problem = f"{ran.ready} already answers before the boot: free the port or use {{port}}"
            return 1, [problem], False
        with gatelib.interrupts_raise():
            return serve(ran, [expand(word) for word in command], env)

    if options.refuses:
        found = [line for name in options.refuses for line in attempt(name)[1]]
        return gatelib.report("with_service", found)
    code, problems, exited = attempt()
    if exited and picked:
        print(
            "with_service: exited before answering; booting once more on a new port",
            file=sys.stderr,
        )
        print("\n".join(problems), file=sys.stderr)
        code, problems, _ = attempt()
    gatelib.report(
        "with_service", problems, exit_code=gatelib.UNJUDGED if code == gatelib.UNJUDGED else 1
    )
    return code


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
