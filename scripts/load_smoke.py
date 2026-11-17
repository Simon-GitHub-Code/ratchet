#!/usr/bin/env python3
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
    method: str
    url: str
    status: int | None
    body: bytes | None

    def label(self) -> str:
        return f"{self.method} {self.url}"

    def accepts(self, status: int) -> bool:
        return status == self.status if self.status else status < FAILED

    def port(self) -> int:
        parts = urlsplit(self.url)
        return parts.port or (443 if parts.scheme == "https" else 80)


def target(text: str) -> Target:
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
    places = [(part.netloc, part.path or "/", part.query) for part in map(urlsplit, (url, ready))]
    return places[0] == places[1]


def targets_of(parser: argparse.ArgumentParser, options: argparse.Namespace) -> list[Target] | str:
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
    ...


@dataclass(frozen=True)
class Cut:
    label: str
    error: str
    port: int
    named: bool
    late: bool
    lost: bool


class Cutoff(Exception):  # noqa: N818 # -- carries a failure to judge, not a fault yet
    def __init__(self, cut: Cut) -> None:
        super().__init__(cut)
        self.cut = cut


def attempt(
    goal: Target, timeout: float, stopping: Path | None = None
) -> tuple[float | None, str | None]:
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
    latencies: list[float] = field(default_factory=list)
    errors: collections.Counter[str] = field(default_factory=collections.Counter)
    cuts: list[Cut] = field(default_factory=list)


def work(
    targets: list[Target], first: int, deadline: float, *, timeout: float, stopping: Path | None
) -> Worked:
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


def counts(cut: Cut, told: gatelib.Stopping) -> bool:
    if told.inexact(cut.port):
        return not (cut.late and cut.lost)
    return not cut.named


def judge(
    cuts: list[Cut], told: gatelib.Stopping, ports: list[int]
) -> tuple[collections.Counter[str], list[str]]:
    errors = collections.Counter(f"{cut.label}: {cut.error}" for cut in cuts if counts(cut, told))
    notes = []
    taken = sum(cut.named and not told.inexact(cut.port) for cut in cuts)
    if taken:
        notes.append(
            f"load-smoke: {taken} connection(s) reset after the stop signal are not counted, "
            "since the service never accepted them. The gate held the service's listener and, "
            "once no process of the service held it, took each connection waiting there and "
            "reset it. Each was waiting when the service stopped listening, or arrived after."
        )
    older: dict[str, list[str]] = {}
    for port in sorted(set(ports)):
        if why := told.inexact(port):
            older.setdefault(why, []).append(str(port))
    for why, named in older.items():
        which = f"port {named[0]} is" if len(named) == 1 else f"ports {', '.join(named)} are"
        notes.append(
            f"load-smoke: {which} judged the older, less exact way, because {why}. A reset after "
            "the stop signal then counts as a dropped request only when its request started "
            "before the signal. So a service that stops correctly can be red here, and one that "
            "drops a request sent just after the signal can be green."
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
        Path(sending).touch()
    marker = Path(stopping) if options.until_stopped and stopping else None
    worked = hammer(
        targets, options.concurrency, options.seconds, timeout=options.timeout, stopping=marker
    )
    latencies, errors = worked.latencies, worked.errors
    if marker and marker.exists():
        told = gatelib.ask(marker, options.timeout)
        cut, notes = judge(worked.cuts, told, [goal.port() for goal in targets])
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
