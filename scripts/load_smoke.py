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

A ``--url`` is ``[METHOD] URL [STATUS [BODY]]``, one argument, words separated by spaces:
``http://127.0.0.1:8000/health``, ``POST http://127.0.0.1:8000/links 201 {"url": "http://a.b"}``.
The method defaults to GET. The expected status defaults to any 2xx or 3xx, so a wrong path
(404) is red and not vacuously green; a status you name must match exactly. A body is sent as
JSON. Workers start at different URLs and take them in turn, each sending at least one request.
Run it inside ``with_service.py``.

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


def attempt(goal: Target, timeout: float) -> tuple[float | None, str | None]:
    """One request: its latency in seconds when an answer came, and what was wrong if anything."""
    started = time.perf_counter()
    try:
        status = gatelib.http_status(goal.method, goal.url, body=goal.body, timeout=timeout)
    except TimeoutError:
        return None, f"{goal.label()}: timed out after {timeout:g}s"
    except (OSError, http.client.HTTPException) as error:
        return None, f"{goal.label()}: {type(error).__name__}"
    wrong = None if goal.accepts(status) else f"{goal.label()}: HTTP {status}"
    return time.perf_counter() - started, wrong


def work(
    targets: list[Target], first: int, deadline: float, *, timeout: float
) -> tuple[list[float], collections.Counter[str]]:
    """One worker's latencies and errors: at least one request, then until the deadline.

    It starts at URL ``first`` and takes them in turn.
    """
    latencies: list[float] = []
    errors: collections.Counter[str] = collections.Counter()
    turn = first
    while True:
        latency, wrong = attempt(targets[turn % len(targets)], timeout)
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
    targets: list[Target], concurrency: int, seconds: float, *, timeout: float
) -> tuple[list[float], collections.Counter[str]]:
    """Run ``concurrency`` workers for ``seconds``; every latency and every error counted."""
    deadline = time.monotonic() + seconds
    results: list[tuple[list[float], collections.Counter[str]]] = []
    threads = [
        threading.Thread(
            target=lambda n=n: results.append(work(targets, n, deadline, timeout=timeout)),
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
    parser.add_argument("--concurrency", type=int, default=8)
    parser.add_argument("--seconds", type=float, default=5.0)
    parser.add_argument("--min-requests", type=int, default=20)
    parser.add_argument("--timeout", type=float, default=5.0, help="per request, in seconds")
    options = parser.parse_args(argv[1:])
    if options.concurrency < 1 or options.seconds <= 0 or options.timeout <= 0:
        parser.error("--concurrency, --seconds and --timeout must be positive")
    targets = targets_of(parser, options)
    if isinstance(targets, str):
        return gatelib.report("load-smoke", [targets])
    latencies, errors = hammer(
        targets, options.concurrency, options.seconds, timeout=options.timeout
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
