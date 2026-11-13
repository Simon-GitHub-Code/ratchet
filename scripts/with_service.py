#!/usr/bin/env python3
"""The service gate: boot a deliverable, wait until it answers, run a command against it, stop it.

It knows nothing about the language the service is written in. It runs the boot command in a
shell of its own, polls a URL until the service answers, runs the command you give it, and stops
the service whatever happened. Python 3.11 standard library only, Linux and macOS.

Usage::

    with_service.py --boot "<command>" --ready <url> [--timeout S] [--env K=V]...
                    [--graceful S [--signal-after S]] [-- <command>...]
    with_service.py --boot "<command>" --ready <url> --refuses NAME [--refuses NAME]...

* With a command after ``--``, the exit code is the command's. The service is up for exactly
  as long as the command runs: a load smoke, a contract test, an end-to-end suite, a request.
* With no command it is the ``smoke`` gate: boot, probe, stop. Green means the service started
  and answered; the verdict line is ``with_service: green``.
* Ready means the URL answers with a status below 400. The probe repeats every 100 ms until
  ``--timeout`` (30 s by default; raise it for a service that compiles on start). A service that
  never answers is red, with the last lines of its output. One that exits before it answers is
  red with everything it wrote, the start and the end when that is long, since the cause of an
  exit is usually the first thing it said; that also says the command as it ran, where, and for
  how long, and says so when the command printed nothing.
* ``{port}`` in ``--boot``, ``--ready``, an ``--env`` value or the command is replaced by a free
  port the kernel picked, and ``PORT`` is exported with it, so parallel CI jobs on one host do
  not collide on a fixed port. The boot output is shown only when the run is red.
* The kernel's pick is only free until the service binds it, and another gate can take it in
  between. So when a service on a port picked that way exits before it answers, the gate says so
  on stderr with that first boot's output and boots once more on a new port. A second failure is
  red as before, and a service that always fails ends red after the one retry. A port the caller
  fixed in the command is never changed, so it is not retried.
* ``RATCHET_READY`` is exported with the ready URL, port filled in, so the command can tell the
  readiness route from the rest: ``load_smoke.py`` is red while it requests nothing else.
* Something already answering on the ready URL's port before the boot is red, since the probe
  would then judge a service this run did not start. A port still bound after the stop is red
  too: a process escaped the stop.

The boot command runs in its own session, and the stop signals that whole process group: a shell
that started a server that forked workers loses all of them. SIGTERM goes first, SIGKILL after
five seconds. SIGINT, SIGTERM and SIGHUP to this script stop the service and the command
too, and exit 128 plus the signal. What no script can survive is SIGKILL, and a daemon that
calls ``setsid`` leaves the group; the bound-port check above is what notices the second.

``--graceful SECONDS`` is the "shuts down gracefully" check, and needs a command after ``--``,
typically the load smoke run with ``--until-stopped``. While the command runs, partway through,
the gate sends SIGTERM to the service process alone, not its group, as an orchestrator does on a
deploy. Green needs the service to exit with code 0 within SECONDS of the signal and the command
to still succeed, so the requests in flight when the signal came were finished. "Partway" is a
fixed delay, ``--signal-after`` (1 s by default), counted from when the command starts: it does
not depend on how fast the service answers, so a slow runner and a fast one signal at the same
point. A command that ends before the signal is red, since nothing was in flight. The service
process is the boot command's own process, so boot with ``exec`` (``exec ./server``) or the shell
takes the signal and the server never sees it; an exit by signal is red and says so. Before the
signal the gate exports ``RATCHET_STOPPING``, a path that exists only from the signal on, which
``load_smoke.py --until-stopped`` reads to tell a refused connection after the signal (expected,
the listener closed) from a refusal before it (a fault). A service that ignores the signal is
red, and is stopped with its whole group at the end as before.

``--refuses NAME`` (repeatable) is the "refuses bad configuration" check, with no command. For each
NAME the service boots with the environment variable NAME removed, an ``--env`` of that name too.
Green only when it exits non-zero before the ready URL answers, within ``--timeout``, and what it
wrote names NAME. Red, with the boot output, if it becomes ready, hangs, exits 0, or exits without
naming the setting. A setting the service reads from a file it finds itself is not removed.

The boot command stays in the foreground: ``docker compose up`` and ``pnpm start`` do,
``docker compose up -d`` does not, and a boot that returns is "exited before it answered".

Pair it with ``load_smoke.py`` and ``telemetry_sink.py``, which are commands to run inside it.
"""

from __future__ import annotations

import argparse
import contextlib
import http.client
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

POLL = 0.1
TAIL = 30
# An exited service shows its first lines as well: the cause is usually the first thing it
# said, and a long start-up can push it out of the tail.
HEAD = 30
PORT_TOKEN = "{port}"  # noqa: S105 # -- a placeholder, not a credential


def probe(url: str) -> str | None:
    """``None`` when the URL answers below 400, else what the probe saw."""
    try:
        status = gatelib.http_status("GET", url, timeout=2.0)
    except (OSError, http.client.HTTPException) as error:
        return f"{type(error).__name__}: {error}"
    return None if status < 400 else f"HTTP {status}"  # noqa: PLR2004 # -- the HTTP error class


def wait_ready(
    process: subprocess.Popen[bytes], options: argparse.Namespace
) -> tuple[str | None, bool]:
    """``(None, False)`` once the ready URL answers; else why not, and whether it exited."""
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
    """What the service wrote, indented, for a red verdict.

    The last lines; for a service that exited, the first lines too, with the count left out
    between them when there are more than both hold.
    """
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
    """The boot command, in a shell and a session of its own, writing to ``log``."""
    return subprocess.Popen(  # noqa: S602 # -- the boot command is a shell line by contract
        options.boot,
        shell=True,
        env=env,
        stdin=subprocess.DEVNULL,
        stdout=log,
        stderr=subprocess.STDOUT,
        start_new_session=True,
    )


def signal_midway(
    process: subprocess.Popen[bytes],
    options: argparse.Namespace,
    marker: Path,
    *,
    ended: threading.Event,
) -> list[str]:
    """SIGTERM the service ``--signal-after`` seconds in; what was wrong with how it stopped.

    Why a fixed delay and not "after N requests": the gate cannot see the requests, a count
    would hang on a service too slow to reach it, and a delay signals at the same point of the
    command on every runner. ``ended`` is set when the command finishes first.
    """
    after = options.signal_after
    if ended.wait(after):
        return [f"the command ended before the {after:g}s signal: nothing was in flight"]
    if process.poll() is not None:
        return [f"the service exited with code {process.returncode} before it was signalled"]
    marker.touch()  # Why: before the signal, so a refusal that follows it is known to be expected.
    signalled = time.monotonic()
    process.send_signal(signal.SIGTERM)
    try:
        code = process.wait(timeout=options.graceful)
    except subprocess.TimeoutExpired:
        return [f"the service was still running {options.graceful:g}s after SIGTERM"]
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
    """Run ``command`` and stop the service partway; the command's code and every problem."""
    with tempfile.TemporaryDirectory() as scratch:
        marker = Path(scratch) / "stopping"
        env[gatelib.STOPPING_ENV] = str(marker)
        ended, found = threading.Event(), []
        watcher = threading.Thread(
            target=lambda: found.extend(signal_midway(process, options, marker, ended=ended))
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
    """What is wrong with how a service met a missing setting; ``code`` is None if it ran on."""
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
    """Boot, wait, run the command, stop; the exit code, the problems, and whether it exited.

    With ``options.refuses`` set to a name, the command is not run: the verdict is on how the
    service met that setting missing.
    """
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
    """The options and the command after ``--``; the parser exits on a bad combination."""
    parser = argparse.ArgumentParser(prog="with_service.py", usage="%(prog)s [options] [-- cmd...]")
    parser.add_argument("--boot", required=True, help="shell command that starts the service")
    parser.add_argument("--ready", required=True, help="URL that answers below 400 when ready")
    parser.add_argument("--timeout", type=float, default=30.0, help="seconds to wait for ready")
    parser.add_argument("--env", action="append", type=pair, default=[], metavar="K=V")
    parser.add_argument(
        "--graceful", type=float, metavar="S", help="SIGTERM mid-command; exit 0 in S"
    )
    parser.add_argument("--signal-after", type=float, default=1.0, metavar="S")
    parser.add_argument("--refuses", action="append", default=[], metavar="NAME")
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
        """One boot on a port picked now, with ``missing`` removed from the environment."""
        env = dict(os.environ)
        if picked:
            env["PORT"] = str(gatelib.free_port())

        def expand(text: str) -> str:
            return text.replace(PORT_TOKEN, env.get("PORT", PORT_TOKEN))

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
        # Why: the kernel's pick is free only until the service binds it, and another gate may
        # have taken it first. A service that always fails still ends red, after this one retry.
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
