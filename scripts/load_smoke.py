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
through the run. The smoke creates the path ``RATCHET_SENDING`` once its workers start, and the
signal comes ``--signal-after`` seconds later; ``RATCHET_STOPPING`` is a file that exists from
the signal on. Every request is judged as always, with two exceptions once the signal is sent,
each of which ends that worker. A refused connect meets a service that no longer listens, so it
is expected, not counted. A connection reset or closed with no answer is not counted when the
gate took it: the service never accepted it, it was still waiting on the listener when the
service stopped listening, and the file names its ports. Any other reset is a request the
service accepted and dropped, and is red, and so is a timeout or an error status. Where the gate
holds no copy of the listener it cannot tell the two apart, and the file says why. A reset is
then judged as the smoke judged it before the gate held listeners: not counted when its request
started after the signal, counted when it started before. The smoke says so. Without the flag
a refused connection is always an error. The flag is red without the two paths, since it
could neither say it is sending nor tell a reset the gate made from one the service made.

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
from dataclasses import dataclass, field
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
    """A request after the stop signal whose connect was refused: nothing listened any more."""


@dataclass(frozen=True)
class Cut:
    """A connection that failed after the stop signal, judged once the run is over.

    ``port`` is the service's. ``named`` is whether the gate had named the connection, as one it
    took and reset, by the time the client saw it fail: the gate names it before the reset, so a
    name written later is not the gate's reset. ``late`` is whether the request started after
    the signal, and ``lost`` whether the connection was reset, aborted or broken: what the
    start-time rule needs where the gate holds no listener.
    """

    label: str
    error: str
    port: int
    named: bool
    late: bool
    lost: bool


class Cutoff(Exception):  # noqa: N818 # -- carries a failure to judge, not a fault yet
    """A request after the stop signal that failed otherwise than by a refused connect."""

    def __init__(self, cut: Cut) -> None:
        """Carry ``cut`` to the worker, which stops and keeps it for judging."""
        super().__init__(cut)
        self.cut = cut


def attempt(
    goal: Target, timeout: float, stopping: Path | None = None
) -> tuple[float | None, str | None]:
    """One request: its latency in seconds when an answer came, and what was wrong if anything.

    With ``stopping`` set and existing when a request fails, the signal has been sent: a refused
    connect raises ``Closed``, and any other failure raises ``Cutoff``, judged later against the
    connections the gate took. A timeout always counts, and so does a failure before a connect
    was even tried.
    """
    clients: list[gatelib.Connection] = []
    late = stopping is not None and stopping.exists()
    started = time.perf_counter()
    try:
        status = gatelib.http_status(
            goal.method,
            goal.url,
            body=goal.body,
            timeout=timeout,
            connecting=clients.append,
        )
    except TimeoutError:
        return None, f"{goal.label()}: timed out after {timeout:g}s"
    except (OSError, http.client.HTTPException) as error:
        if stopping is None or not stopping.exists() or not clients:
            return None, f"{goal.label()}: {type(error).__name__}"
        if isinstance(error, ConnectionRefusedError):
            raise Closed from error
        named = clients[-1] in gatelib.read_stopping(stopping).taken
        lost = isinstance(error, ConnectionError)
        cut = Cut(goal.label(), type(error).__name__, clients[-1].port, named, late, lost)
        raise Cutoff(cut) from error
    wrong = None if goal.accepts(status) else f"{goal.label()}: HTTP {status}"
    return time.perf_counter() - started, wrong


@dataclass
class Worked:
    """What the workers saw: every latency, every error, and the cut connections to judge."""

    latencies: list[float] = field(default_factory=list)
    errors: collections.Counter[str] = field(default_factory=collections.Counter)
    cuts: list[Cut] = field(default_factory=list)


def work(
    targets: list[Target], first: int, deadline: float, *, timeout: float, stopping: Path | None
) -> Worked:
    """One worker's run: at least one request, then until the deadline.

    It starts at URL ``first`` and takes them in turn, and stops early at its first request after
    the stop signal that fails to connect or is cut.
    """
    worked = Worked()
    turn = first
    while True:
        try:
            latency, wrong = attempt(targets[turn % len(targets)], timeout, stopping)
        except Closed:
            break
        except Cutoff as cutoff:
            worked.cuts.append(cutoff.cut)
            break
        if latency is not None:
            worked.latencies.append(latency)
        if wrong:
            worked.errors[wrong] += 1
        turn += 1
        if time.monotonic() >= deadline:
            break
    return worked


def counts(cut: Cut, stopping: gatelib.Stopping) -> bool:
    """Whether ``cut`` is a request the service accepted and dropped, as far as can be told.

    With the listener held, a connection the gate took was never accepted by the service, and any
    other was. Without it, the start-time rule the smoke used before the gate held listeners: a
    lost connection is not counted when its request started after the signal.
    """
    if stopping.unread:
        return not (cut.late and cut.lost)
    return not cut.named


def judge(
    cuts: list[Cut], stopping: gatelib.Stopping
) -> tuple[collections.Counter[str], list[str]]:
    """The cut connections that count as errors, and what to say of the rest."""
    errors = collections.Counter(
        f"{cut.label}: {cut.error}" for cut in cuts if counts(cut, stopping)
    )
    notes = []
    excused = len(cuts) - errors.total()
    if excused and not stopping.unread:
        notes.append(
            f"load-smoke: {excused} connection(s) reset after the stop signal are not counted: "
            "the service never accepted them, they were still waiting on its listener when it "
            "stopped listening"
        )
    if stopping.unread:
        notes.append(
            "load-smoke: this stop is judged the older, less exact way, because "
            f"{stopping.unread}. A reset after the stop signal then counts as a dropped request "
            "only when its request started before the signal. So a service that stops correctly "
            "can be red here, and one that drops a request sent just after the signal can be "
            "green."
        )
    return errors, notes


def percentile(sorted_values: list[float], fraction: float) -> float:
    return sorted_values[min(len(sorted_values) - 1, int(fraction * len(sorted_values)))]


def hammer(
    targets: list[Target],
    concurrency: int,
    seconds: float,
    *,
    timeout: float,
    stopping: Path | None = None,
) -> Worked:
    """Run ``concurrency`` workers for ``seconds``; everything they saw, together."""
    deadline = time.monotonic() + seconds
    results: list[Worked] = []
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
    together = Worked()
    for part in results:
        together.latencies += part.latencies
        together.errors.update(part.errors)
        together.cuts += part.cuts
    together.latencies.sort()
    return together


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
    stopping, sending = (
        os.environ.get(name) for name in (gatelib.STOPPING_ENV, gatelib.SENDING_ENV)
    )
    if options.until_stopped and not (stopping and sending):
        parser.error(
            f"--until-stopped needs {gatelib.STOPPING_ENV} and {gatelib.SENDING_ENV}, set by "
            "with_service.py --graceful"
        )
    targets = targets_of(parser, options)
    if isinstance(targets, str):
        return gatelib.report("load-smoke", [targets])
    if options.until_stopped and sending:
        Path(sending).touch()  # Why: the workers start now, and the signal waits for them.
    marker = Path(stopping) if options.until_stopped and stopping else None
    worked = hammer(
        targets, options.concurrency, options.seconds, timeout=options.timeout, stopping=marker
    )
    latencies, errors = worked.latencies, worked.errors
    if marker and worked.cuts:
        cut, notes = judge(worked.cuts, gatelib.read_stopping(marker))
        errors.update(cut)
        for note in notes:
            print(note)
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
