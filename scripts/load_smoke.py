#!/usr/bin/env python3
"""The load smoke: seconds of concurrent requests against a running service, judged on errors only.

It asks one question of a pull request: does the service stay correct when requests overlap?
A connection that fails, a request that times out, a 5xx, or a status other than the one
expected is red. The request count and the latency percentiles are printed and never judged,
since a pull-request runner is no benchmark host. One judged number exists: ``--min-requests``,
a floor on requests answered, so a service that hangs or crawls is red and not vacuously green.

The smoke must overlap requests a user makes. It is red, before it sends anything, when every
``--url`` is the readiness URL: a health route answers while every other route is broken. The
readiness URL is ``--ready``, which ``with_service.py`` exports as ``RATCHET_READY``, so inside
it there is nothing to pass. A walking skeleton whose one route is readiness says so with
``--health-only`` and no ``--url``: the smoke then requests readiness, is green, and prints on
every run that it is health only. The flag does not expire on its own; the first route replaces
it with a ``--url``.

Usage::

    load_smoke.py --url URL [--url URL]... [--ready URL] [--concurrency 8] [--seconds 5]
                  [--min-requests 20] [--timeout 5]
    load_smoke.py --health-only [--ready URL] ...
    load_smoke.py --until-stopped --url URL ...        # inside with_service.py --graceful

A ``--url`` is ``[METHOD] URL [STATUS [BODY]]``, one argument, words separated by spaces:
``http://127.0.0.1:8000/health``, ``POST http://127.0.0.1:8000/links 201 {"url": "http://a.b"}``.
The method defaults to GET. The expected status defaults to any 2xx or 3xx, so a wrong path
(404) is red and not vacuously green; a status you name must match exactly. A body is sent as
JSON. Workers start at different URLs and take them in turn, each sending at least one request.
Run it inside ``with_service.py``.

Against a live deployment, with no ``with_service.py`` and nothing booted, pass the deployment's
own readiness route as ``--ready`` and its routes as ``--url``: ``load_smoke.py --ready
https://app.example.com/health --url https://app.example.com/links``. That is the "production
answers" probe. It is judged on errors only, as everywhere, and sends the same few seconds of
overlapping requests, so point it at a route that is safe to repeat.

``--until-stopped`` is for ``with_service.py --graceful``, which stops the service partway
through the run and exports ``RATCHET_STOPPING``, a path that exists from just before the signal
on and holds a ``time.monotonic()``: the line. The service had accepted every connection made
before the line, so a request whose connection was made before it is in flight and must
complete: a reset, a timeout, a 5xx or a wrong status is an error as always. One made after it
may meet a listener that is closing, so a refused or reset connection is expected, not counted,
and ends that worker; a timeout or an error status after the signal is still red. Without the
flag a refused connection is always an error. The flag is red without ``RATCHET_STOPPING``,
since it could not tell the two apart.

It ends within ``--seconds`` plus ``--timeout``: ten seconds at the defaults, even when the
service hangs. Each request opens a new connection, which is what a burst of clients does.

What it is not: a load generator. Threads in one Python process share one interpreter lock, so
the client saturates at a few thousand requests a second on a quiet machine, and the latency
it prints describes that client as much as the service. Never take a capacity number from
it, and never compare two runs. The tier 2 ``load`` question (arrival rate, percentile
latency, error rate against the binding's Limits, soak and breaking point) needs a real
tool the adopter chooses, such as k6, Locust, wrk, oha, Gatling or vegeta, on a host built
for it.
"""

from __future__ import annotations

import argparse
import collections
import http.client
import os
import re
import sys
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

import gatelib

METHODS = ("GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS")
SPEC = re.compile(rf"(?:({'|'.join(METHODS)}) )?(\S+)(?: (\d{{3}})(?: (.+))?)?", re.DOTALL)
FAILED = 400
LISTED = 5


@dataclass(frozen=True)
class Target:
    """One request the workers send, and the status that counts as correct."""

    method: str
    url: str
    status: int | None
    body: bytes | None

    def label(self) -> str:
        """The request as an error line names it."""
        return f"{self.method} {self.url}"

    def accepts(self, status: int) -> bool:
        """Whether ``status`` is the one wanted, or any success when none was named."""
        return status == self.status if self.status else status < FAILED


def target(text: str) -> Target:
    """Parse a ``--url`` value; ``argparse.ArgumentTypeError`` says what is wrong with it."""
    match = SPEC.fullmatch(text.strip())
    if not match:
        message = f"{text!r} is not [METHOD] URL [STATUS [BODY]]"
        raise argparse.ArgumentTypeError(message)
    method, url, status, body = match.groups()
    if not url.startswith(("http://", "https://")):
        message = f"{url!r} is not an http or https URL"
        raise argparse.ArgumentTypeError(message)
    return Target(
        method or "GET", url, int(status) if status else None, body.encode() if body else None
    )


def same_place(url: str, ready: str) -> bool:
    """Whether ``url`` is the readiness URL: the same host, port, path and query."""
    places = [(part.netloc, part.path or "/", part.query) for part in map(urlsplit, (url, ready))]
    return places[0] == places[1]


def targets_of(parser: argparse.ArgumentParser, options: argparse.Namespace) -> list[Target] | str:
    """The requests to send, or why the smoke would overlap nothing a user requests."""
    try:
        ready = target(options.ready or "")
    except argparse.ArgumentTypeError as error:
        parser.error(f"--ready: {error}; with_service.py exports it as {gatelib.READY_ENV}")
    targets: list[Target] = options.url or ([ready] if options.health_only else [])
    if not targets:
        parser.error("name a --url per route, or --health-only while readiness is the only route")
    if any(not same_place(goal.url, ready.url) for goal in targets):
        return targets
    if options.health_only:
        print(
            "load-smoke: health only: readiness is the one route requested (--health-only); "
            "name the first real route here when it lands"
        )
        return targets
    return (
        f"every --url is the readiness URL {ready.url}: the smoke overlaps no request a user "
        "makes; add a --url per route worth overlapping, or pass --health-only while readiness "
        "is the service's only route"
    )


class Closed(Exception):  # noqa: N818 # -- not a fault: the service stopped taking requests
    """A request whose connection was made after the stop line met a closed listener."""


def signal_line(stopping: Path | None) -> float | None:
    """The ``time.monotonic()`` after which a connection may meet a closed listener, if signalled.

    ``with_service.py`` writes it whole before the signal; the clock is the host's, shared by
    every process on it.
    """
    try:
        return float(stopping.read_text(encoding="ascii")) if stopping else None
    except FileNotFoundError:
        return None


def attempt(
    goal: Target, timeout: float, stopping: Path | None = None
) -> tuple[float | None, str | None]:
    """One request: its latency in seconds when an answer came, and what was wrong if anything.

    With ``stopping`` set, a connection error raises ``Closed`` instead of being counted once
    that path exists, when the connection was made at or after the time it holds, or never made:
    a refused connect after the signal. The line is the connection and not the start of the
    attempt, since a client can stall between the two.
    """
    connected_at: list[float] = []
    started = time.perf_counter()
    try:
        status = gatelib.http_status(
            goal.method,
            goal.url,
            body=goal.body,
            timeout=timeout,
            connected=lambda: connected_at.append(time.monotonic()),
        )
    except TimeoutError:
        return None, f"{goal.label()}: timed out after {timeout:g}s"
    except (OSError, http.client.HTTPException) as error:
        line = signal_line(stopping)
        after_signal = line is not None and (not connected_at or connected_at[0] >= line)
        if after_signal and isinstance(error, ConnectionError):
            raise Closed from error
        return None, f"{goal.label()}: {type(error).__name__}"
    wrong = None if goal.accepts(status) else f"{goal.label()}: HTTP {status}"
    return time.perf_counter() - started, wrong


def work(
    targets: list[Target], first: int, deadline: float, *, timeout: float, stopping: Path | None
) -> tuple[list[float], collections.Counter[str]]:
    """One worker's latencies and errors: at least one request, then until the deadline.

    It starts at URL ``first`` and takes them in turn, and stops early on ``Closed``.
    """
    latencies: list[float] = []
    errors: collections.Counter[str] = collections.Counter()
    turn = first
    while True:
        try:
            latency, wrong = attempt(targets[turn % len(targets)], timeout, stopping)
        except Closed:
            break
        if latency is not None:
            latencies.append(latency)
        if wrong:
            errors[wrong] += 1
        turn += 1
        if time.monotonic() >= deadline:
            break
    return latencies, errors


def percentile(sorted_values: list[float], fraction: float) -> float:
    return sorted_values[min(len(sorted_values) - 1, int(fraction * len(sorted_values)))]


def hammer(
    targets: list[Target],
    concurrency: int,
    seconds: float,
    *,
    timeout: float,
    stopping: Path | None = None,
) -> tuple[list[float], collections.Counter[str]]:
    """Run ``concurrency`` workers for ``seconds``; every latency and every error counted."""
    deadline = time.monotonic() + seconds
    results: list[tuple[list[float], collections.Counter[str]]] = []
    threads = [
        threading.Thread(
            target=lambda n=n: results.append(
                work(targets, n, deadline, timeout=timeout, stopping=stopping)
            ),
            daemon=True,
        )
        for n in range(concurrency)
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    latencies = sorted(latency for part, _ in results for latency in part)
    errors: collections.Counter[str] = collections.Counter()
    for _, part_errors in results:
        errors.update(part_errors)
    return latencies, errors


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="load_smoke.py")
    parser.add_argument("--url", action="append", type=target, default=[])
    parser.add_argument("--ready", default=os.environ.get(gatelib.READY_ENV))
    parser.add_argument("--health-only", action="store_true")
    parser.add_argument("--until-stopped", action="store_true")
    parser.add_argument("--concurrency", type=int, default=8)
    parser.add_argument("--seconds", type=float, default=5.0)
    parser.add_argument("--min-requests", type=int, default=20)
    parser.add_argument("--timeout", type=float, default=5.0, help="per request, in seconds")
    options = parser.parse_args(argv[1:])
    if options.concurrency < 1 or options.seconds <= 0 or options.timeout <= 0:
        parser.error("--concurrency, --seconds and --timeout must be positive")
    stopping = os.environ.get(gatelib.STOPPING_ENV)
    if options.until_stopped and not stopping:
        parser.error(
            f"--until-stopped needs {gatelib.STOPPING_ENV}, set by with_service.py --graceful"
        )
    targets = targets_of(parser, options)
    if isinstance(targets, str):
        return gatelib.report("load-smoke", [targets])
    latencies, errors = hammer(
        targets,
        options.concurrency,
        options.seconds,
        timeout=options.timeout,
        stopping=Path(stopping) if options.until_stopped and stopping else None,
    )
    if latencies:
        p50, p95, p99, worst = (percentile(latencies, f) * 1000 for f in (0.5, 0.95, 0.99, 1.0))
        print(
            f"load-smoke: {len(latencies)} answered; latency ms p50 {p50:.0f} p95 {p95:.0f} "
            f"p99 {p99:.0f} max {worst:.0f} (printed, not judged)"
        )
    problems = [f"{count} x {what}" for what, count in errors.most_common(LISTED)]
    if len(errors) > LISTED:
        problems.append(f"and {len(errors) - LISTED} more kinds of error")
    if len(latencies) < options.min_requests:
        problems.append(
            f"{len(latencies)} requests answered, under the floor of {options.min_requests}: "
            "the service hangs or is too slow for this window"
        )
    return gatelib.report("load-smoke", problems)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
