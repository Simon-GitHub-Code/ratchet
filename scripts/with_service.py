#!/usr/bin/env python3
"""The service gate: boot a deliverable, wait until it answers, run a command against it, stop it.

It knows nothing about the language the service is written in. It runs the boot command in a
shell of its own, polls a URL until the service answers, runs the command you give it, and stops
the service whatever happened. Python 3.11 standard library only, Linux and macOS.

Usage::

    with_service.py --boot "<command>" --ready <url> [--timeout S] [--env K=V]... [-- <command>...]

* With a command after ``--``, the exit code is the command's. The service is up for exactly
  as long as the command runs: a load smoke, a contract test, an end-to-end suite, a request.
* With no command it is the ``smoke`` gate: boot, probe, stop. Green means the service started
  and answered; the verdict line is ``with_service: green``.
* Ready means the URL answers with a status below 400. The probe repeats every 100 ms until
  ``--timeout`` (30 s by default; raise it for a service that compiles on start). A service that
  exits before it answers, or never answers, is red, with the last lines of its output.
* ``{port}`` in ``--boot``, ``--ready``, an ``--env`` value or the command is replaced by a free
  port the kernel picked, and ``PORT`` is exported with it, so parallel CI jobs on one host do
  not collide on a fixed port. The boot output is shown only when the run is red.
* Something already answering on the ready URL's port before the boot is red, since the probe
  would then judge a service this run did not start. A port still bound after the stop is red
  too: a process escaped the stop.

The boot command runs in its own session, and the stop signals that whole process group: a shell
that started a server that forked workers loses all of them. SIGTERM goes first, SIGKILL after
five seconds. SIGINT, SIGTERM and SIGHUP to this script stop the service and the command
too, and exit 128 plus the signal. What no script can survive is SIGKILL, and a daemon that
calls ``setsid`` leaves the group; the bound-port check above is what notices the second.

The boot command stays in the foreground: ``docker compose up`` and ``pnpm start`` do,
``docker compose up -d`` does not, and a boot that returns is "exited before it answered".

Pair it with ``load_smoke.py`` and ``telemetry_sink.py``, which are commands to run inside it.
"""

from __future__ import annotations

import argparse
import http.client
import os
import subprocess
import sys
import tempfile
import time
from typing import IO
from urllib.parse import urlsplit

import gatelib

POLL = 0.1
TAIL = 30
PORT_TOKEN = "{port}"  # noqa: S105 # -- a placeholder, not a credential


def probe(url: str) -> str | None:
    """``None`` when the URL answers below 400, else what the probe saw."""
    try:
        status = gatelib.http_status("GET", url, timeout=2.0)
    except (OSError, http.client.HTTPException) as error:
        return f"{type(error).__name__}: {error}"
    return None if status < 400 else f"HTTP {status}"  # noqa: PLR2004 # -- the HTTP error class


def wait_ready(process: subprocess.Popen[bytes], url: str, timeout: float) -> str | None:
    """``None`` once ``url`` answers; else why the service is not ready."""
    deadline = time.monotonic() + timeout
    while True:
        code = process.poll()
        if code is not None:
            return f"the service exited with code {code} before {url} answered"
        seen = probe(url)
        if seen is None:
            return None
        if time.monotonic() >= deadline:
            return f"{url} did not answer within {timeout:g}s; the last probe saw {seen}"
        time.sleep(POLL)


def tail(log: IO[bytes]) -> list[str]:
    """The last lines the service wrote, indented, for a red verdict."""
    log.seek(0)
    lines = log.read().decode("utf-8", errors="replace").splitlines()[-TAIL:]
    return ["boot output (tail):", *(f"  {line}" for line in lines)] if lines else []


def serve(
    options: argparse.Namespace, command: list[str], env: dict[str, str]
) -> tuple[int, list[str]]:
    """Boot, wait, run the command, stop; the exit code and the problems."""
    code, problems = 1, []
    with tempfile.TemporaryFile() as log:
        process = subprocess.Popen(  # noqa: S602 # -- the boot command is a shell line by contract
            options.boot,
            shell=True,
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=log,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
        try:
            not_ready = wait_ready(process, options.ready, options.timeout)
            if not_ready:
                problems = [not_ready]
            elif command:
                code, problems = gatelib.run_command(command, env)
            else:
                code = 0
        except gatelib.Interrupted as stop:
            code, problems = 128 + stop.signum, [str(stop)]
        finally:
            gatelib.stop_group(process)
        if gatelib.listening(options.ready):
            code, problems = 1, [*problems, f"{options.ready} still answers after the stop"]
        if problems:
            problems += tail(log)
    return code, problems


def pair(text: str) -> tuple[str, str]:
    key, separator, value = text.partition("=")
    if not key or not separator:
        message = f"{text!r} is not KEY=VALUE"
        raise argparse.ArgumentTypeError(message)
    return key, value


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="with_service.py", usage="%(prog)s [options] [-- cmd...]")
    parser.add_argument("--boot", required=True, help="shell command that starts the service")
    parser.add_argument("--ready", required=True, help="URL that answers below 400 when ready")
    parser.add_argument("--timeout", type=float, default=30.0, help="seconds to wait for ready")
    parser.add_argument("--env", action="append", type=pair, default=[], metavar="K=V")
    rest = argv[1:]
    split = rest.index("--") if "--" in rest else len(rest)
    options = parser.parse_args(rest[:split])
    command = rest[split + 1 :]
    env = dict(os.environ)
    texts = [options.boot, options.ready, *command, *(value for _, value in options.env)]
    if any(PORT_TOKEN in text for text in texts):
        env["PORT"] = str(gatelib.free_port())

    def expand(text: str) -> str:
        return text.replace(PORT_TOKEN, env.get("PORT", PORT_TOKEN))

    options.boot, options.ready = expand(options.boot), expand(options.ready)
    env.update({key: expand(value) for key, value in options.env})
    command = [expand(word) for word in command]
    if urlsplit(options.ready).scheme not in ("http", "https"):
        parser.error(f"--ready {options.ready!r} is not an http or https URL")
    if gatelib.listening(options.ready):
        problem = f"{options.ready} already answers before the boot: free the port or use {{port}}"
        return gatelib.report("with_service", [problem])
    with gatelib.interrupts_raise():
        code, problems = serve(options, command, env)
    gatelib.report("with_service", problems)
    return code


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
