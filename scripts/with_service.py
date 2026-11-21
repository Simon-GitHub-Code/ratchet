#!/usr/bin/env python3
from __future__ import annotations

import argparse
import contextlib
import http.client
import ipaddress
import os
import re
import signal
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path
from typing import IO
from urllib.parse import urlsplit

import gatelib
import listeners

POLL = 0.1
SENDING_POLL = 0.01
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
    "when the product first answered, each listener held by a process of the boot command's group "
    "or a child of one, the ready URL's among them, was on loopback or declared; a listener of "
    "another process, such as a daemon that left the group, or one opened later, is not judged"
)
DECLARE = "or declare the address with --listens"
NOT_CHECKED_HERE = "where the product listens was not checked here"
UNCHECKED = "where the product listens was not checked, so the command after -- did not run"
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
    )


def sends_within(sending: Path, within: float, ended: threading.Event) -> bool:
    deadline = time.monotonic() + within
    while not sending.exists():
        if ended.wait(SENDING_POLL) or time.monotonic() >= deadline:
            return False
    return True


def signal_midway(
    process: subprocess.Popen[bytes],
    options: argparse.Namespace,
    marker: Path,
    *,
    sending: Path,
    ended: threading.Event,
) -> list[str]:
    after = options.signal_after
    if not sends_within(sending, options.timeout, ended) and not ended.is_set():
        return [
            (
                f"the command did not start sending within {options.timeout:g}s: the signal "
                f"waits until it creates ${gatelib.SENDING_ENV}, as load_smoke.py "
                "--until-stopped does"
            )
        ]
    if ended.is_set() or ended.wait(after):
        return [f"the command ended before the {after:g}s signal: nothing was in flight"]
    if process.poll() is not None:
        return [f"the service exited with code {process.returncode} before it was signalled"]
    held = listeners.grab(process.pid)
    gatelib.write_stopping(marker, listeners.told(held))
    signalled = time.monotonic()
    process.send_signal(signal.SIGTERM)
    taker = threading.Thread(target=listeners.take, args=(held, marker, ended))
    taker.start()
    try:
        return stopped(process, options.graceful, signalled)
    finally:
        taker.join()


def stopped(process: subprocess.Popen[bytes], graceful: float, signalled: float) -> list[str]:
    try:
        code = process.wait(timeout=graceful)
    except subprocess.TimeoutExpired:
        return [f"the service was still running {graceful:g}s after SIGTERM"]
    took = time.monotonic() - signalled
    if code == 0:
        return []
    how = (
        f"was ended by signal {-code}, so boot with `exec`"
        if code < 0
        else f"exited with code {code}"
    )
    return [f"the service {how} {took:.1f}s after SIGTERM, not 0"]


def run_signalled(
    process: subprocess.Popen[bytes],
    options: argparse.Namespace,
    command: list[str],
    *,
    env: dict[str, str],
) -> tuple[int, list[str]]:
    with tempfile.TemporaryDirectory() as scratch:
        marker, sending = Path(scratch) / "stopping", Path(scratch) / "sending"
        env[gatelib.STOPPING_ENV], env[gatelib.SENDING_ENV] = str(marker), str(sending)
        ended, found = threading.Event(), []
        watcher = threading.Thread(
            target=lambda: found.extend(
                signal_midway(process, options, marker, sending=sending, ended=ended)
            )
        )
        watcher.start()
        try:
            code, problems = gatelib.run_command(command, env)
        finally:
            ended.set()
            watcher.join()
    return (code or int(bool(found))), [*problems, *found]


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
        help="seconds to wait for ready, and with --graceful for the command to start sending",
    )
    parser.add_argument("--env", action="append", type=pair, default=[], metavar="K=V")
    parser.add_argument(
        "--graceful", type=float, metavar="S", help="SIGTERM mid-command; exit 0 in S"
    )
    parser.add_argument("--signal-after", type=float, default=1.0, metavar="S")
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
    gatelib.report("with_service", problems)
    return code


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
