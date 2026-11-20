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
LOOPBACK = "127.0.0.1"
CURE = f"bind it to {LOOPBACK}, which the boot command gets as {HOST_TOKEN} and $HOST"


def judged(process: subprocess.Popen[bytes], options: argparse.Namespace) -> list[str]:
    ready = urlsplit(options.ready)
    seen = listeners.bound(process.pid, ready.port or (443 if ready.scheme == "https" else 80))
    if isinstance(seen, str):
        print(f"with_service: where the product listens was not checked: {seen}", flush=True)
        return []
    notes, problems = [], []
    for address, port in seen:
        plain = getattr(address, "ipv4_mapped", None) or address
        host = f"[{address}]" if ":" in str(address) else str(address)
        where = f"{host}:{port}"
        what = "every interface" if plain.is_unspecified else "not a loopback address"
        said = f"the product listens on {where}, {what}, so the network can reach it"
        if plain.is_loopback:
            continue
        if address == options.listens:
            notes.append(
                f"the product listens on {where}, as --listens declares, so the network can "
                "reach it while it runs"
            )
        elif options.loopback_only:
            problems.append(
                f"{said} around the command after --; {CURE}, or name the address with --listens"
            )
        else:
            notes.append(f"note: {said} while it runs; {CURE}")
    for note in notes:
        print(f"with_service: {note}", flush=True)
    return problems


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
        type=ipaddress.ip_address,
        metavar="ADDRESS",
        help="an address the service listens on on purpose: said, and never refused",
    )
    rest = argv[1:]
    split = rest.index("--") if "--" in rest else len(rest)
    options = parser.parse_args(rest[:split])
    command = rest[split + 1 :]
    if urlsplit(options.ready).scheme not in ("http", "https"):
        parser.error(f"--ready {options.ready!r} is not an http or https URL")
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

    def attempt(missing: str | None = None) -> tuple[int, list[str], bool]:
        env = {**os.environ, "HOST": LOOPBACK}
        if picked:
            env["PORT"] = str(gatelib.free_port())

        def expand(text: str) -> str:
            return text.replace(PORT_TOKEN, env.get("PORT", PORT_TOKEN)).replace(
                HOST_TOKEN, LOOPBACK
            )

        ran = argparse.Namespace(**{**vars(options), "boot": expand(options.boot)})
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
