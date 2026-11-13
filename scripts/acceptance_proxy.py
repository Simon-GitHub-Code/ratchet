#!/usr/bin/env python3
"""The acceptance harness's proxy: it stands between a tester and the product and logs each request.

Usage::

    with_service.py --boot "<boot>" --ready <url> -- acceptance_proxy.py SESSION_DIR
    acceptance_proxy.py --null            # booted by with_service.py: a product that does nothing

Run as the command inside ``with_service.py``, it forwards every request on a loopback port of
its own to the booted product, at the origin of ``RATCHET_READY``, and back, changing nothing:
the ``Host`` header, the method, the path, the body and the answer pass as they are, less the
headers that describe one connection. Each request gets an id, ``<session suffix>-<n>``, and one
line in ``requests.jsonl``. ``n`` counts the requests in the order they arrived, and a line is
written once its answer has gone out, so a slow request's line can follow one with a later id:
a reader that wants the order of use sorts by ``n``. The request and the answer are stored as
they were sent under ``probes/<id>.request`` and ``probes/<id>.response``, each cut at 1 MB.
The tester receives only the address; it never writes the log, so a request it cites is one it
made through here.

The proxy is the log's only writer, and it keeps a running SHA-256 of every byte it wrote. On
SIGTERM, SIGINT or SIGHUP, or when the process that started it is gone, it stops listening and
writes ``seal.json``: how many lines it wrote and their digest. A log that does not match its
seal was changed, cut short or added to after the proxy wrote it, and a log with no seal is from
a session that never ended. Nothing in the log is written after the seal.

``SESSION_DIR/proxy.json`` appears once the proxy listens: its address, the product's, and its
process id. ``--null`` is the control a kept check must fail against: every ``GET`` and ``HEAD``
is an empty page, everything else a ``303`` to ``/``.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import http.client
import http.server
import json
import os
import signal
import sys
import threading
import time
from pathlib import Path
from typing import TYPE_CHECKING
from urllib.parse import urlsplit

import gatelib

if TYPE_CHECKING:
    from collections.abc import Callable
    from types import FrameType

CAP = 1 << 20
CHUNK = 1 << 16
UPSTREAM_TIMEOUT = 120.0
WATCH = 0.2
# Why 2: a request answered just before the stop is still being logged, and the seal waits for
# it; `with_service.py` kills the proxy five seconds after asking it to stop, so not longer.
SETTLE = 2.0
# Why: these describe one connection, not the request or the answer; RFC 9110 section 7.6.1.
HOP = frozenset(
    {
        "connection",
        "keep-alive",
        "proxy-authenticate",
        "proxy-authorization",
        "proxy-connection",
        "te",
        "trailer",
        "transfer-encoding",
        "upgrade",
        "content-length",
    }
)
# Why module-level: the handler is installed before anything else runs, and a stop asked for then
# must still be honoured once the server is up.
STOP = threading.Event()
NOTHING = b"<!doctype html><html lang=en><head><title>x</title></head><body></body></html>"
NO_BODY = frozenset({204, 304})


def now() -> str:
    return dt.datetime.now(dt.UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def write_json(path: Path, data: dict[str, object]) -> None:
    """Write ``data`` whole or not at all: a reader never sees half a file."""
    partial = path.with_name(path.name + ".partial")
    partial.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    partial.replace(path)


class Log:
    """``requests.jsonl`` and the probes; the seal says what was written, so nothing else can be."""

    def __init__(self, directory: Path, prefix: str) -> None:
        """Open the log of ``directory``; each id starts with ``prefix``."""
        self.directory, self.prefix = directory, prefix
        (directory / "probes").mkdir(parents=True, exist_ok=True)
        self.file = (directory / "requests.jsonl").open("ab")
        self.lock = threading.Lock()
        self.idle = threading.Condition(self.lock)
        self.digest = hashlib.sha256()
        self.issued = self.written = self.open = 0
        self.sealed = False

    def issue(self) -> str:
        """A new request id; the request is open until ``finish``."""
        with self.lock:
            self.issued += 1
            self.open += 1
            return f"{self.prefix}-{self.issued}"

    def store(self, name: str, data: bytes) -> tuple[str, bool]:
        """Keep a probe, cut at ``CAP``; its digest and whether it was cut."""
        kept = data[:CAP]
        (self.directory / "probes" / name).write_bytes(kept)
        return hashlib.sha256(kept).hexdigest(), len(data) > CAP

    def finish(self, entry: dict[str, object] | None) -> None:
        """Close an open request: its line, unless it broke first or the log is sealed."""
        line = json.dumps(entry).encode() + b"\n" if entry else b""
        with self.lock:
            self.open -= 1
            self.idle.notify_all()
            if self.sealed or not line:
                return
            self.file.write(line)
            self.file.flush()
            self.digest.update(line)
            self.written += 1

    def seal(self, facts: dict[str, object]) -> None:
        """Wait up to ``SETTLE`` for the open requests, then write nothing more and say what was.

        ``facts`` are what the seal says besides the log: the session, when the stop was asked
        for, and the digests of the session's files as they were when the proxy started.
        """
        with self.lock:
            self.idle.wait_for(lambda: self.open == 0, timeout=SETTLE)
            self.sealed = True
            self.file.close()
            seal = facts | {"requests": self.written, "sha256": self.digest.hexdigest()}
            write_json(self.directory / "seal.json", seal | {"ended": now()})


def message(start: str, headers: list[tuple[str, str]], body: bytes) -> bytes:
    """An HTTP message as it went over the wire, for a probe."""
    head = "".join(f"{name}: {value}\r\n" for name, value in headers)
    return f"{start}\r\n{head}\r\n".encode("latin-1") + body


class Proxy(http.server.ThreadingHTTPServer):
    """The listener: where the product is and the log every handler writes to."""

    daemon_threads = True
    log: Log
    upstream: tuple[str, int]

    def handle_error(self, request: object, client_address: object) -> None:
        """A client that went away mid-answer is not the proxy's failure."""


class Forward(http.server.BaseHTTPRequestHandler):
    """Every method: forward to the product, answer the client, log the exchange."""

    protocol_version = "HTTP/1.1"
    server: Proxy

    def __getattr__(self, name: str) -> Callable[[], None]:
        """``do_<METHOD>`` for any method, so nothing is answered by the proxy itself."""
        if name.startswith("do_"):
            return self.forward
        raise AttributeError(name)

    def log_message(self, format: str, *args: object) -> None:  # noqa: A002 # -- the override
        """The log is ``requests.jsonl``, not stderr."""

    def body(self) -> bytes:
        """The request body, de-chunked when it came in chunks."""
        if "chunked" in self.headers.get("Transfer-Encoding", "").lower():
            data = b""
            while size := int(self.rfile.readline().split(b";")[0].strip() or b"0", 16):
                data += self.rfile.read(size)
                self.rfile.readline()
            while self.rfile.readline() not in (b"\r\n", b"\n", b""):
                pass
            return data
        return self.rfile.read(int(self.headers.get("Content-Length") or 0))

    def forward(self) -> None:
        """Relay one request and log it."""
        log = self.server.log
        ident = log.issue()
        entry = None
        try:
            entry = self.exchange(ident)
        finally:
            log.finish(entry)

    def exchange(self, ident: str) -> dict[str, object]:
        """Relay one request; its line for the log."""
        log = self.server.log
        at, started = now(), time.monotonic()
        body = self.body()
        sent = [(k, v) for k, v in self.headers.items() if k.lower() not in HOP]
        if body or "Content-Length" in self.headers or "Transfer-Encoding" in self.headers:
            sent.append(("Content-Length", str(len(body))))
        start = f"{self.command} {self.path} HTTP/1.1"
        request_digest, request_cut = log.store(f"{ident}.request", message(start, sent, body))
        status, answer, error = self.relay(sent, body)
        response_digest, response_cut = log.store(f"{ident}.response", answer)
        entry: dict[str, object] = {"id": ident, "at": at, "method": self.command}
        entry |= {"target": self.path, "path": urlsplit(self.path).path, "status": status}
        entry |= {"ms": round((time.monotonic() - started) * 1000)}
        entry |= {"requestSha256": request_digest, "responseSha256": response_digest}
        entry |= {"cut": request_cut or response_cut}
        if error:
            entry["error"] = error
        return entry

    def relay(self, sent: list[tuple[str, str]], body: bytes) -> tuple[int, bytes, str | None]:
        """Send the request on and the answer back; the status, the answer as kept, any error."""
        host, port = self.server.upstream
        connection = http.client.HTTPConnection(host, port, timeout=UPSTREAM_TIMEOUT)
        answer, began = b"", False
        try:
            connection.putrequest(
                self.command, self.path, skip_host=True, skip_accept_encoding=True
            )
            for name, value in sent:
                connection.putheader(name, value)
            connection.endheaders(body or None)
            response = connection.getresponse()
            headers = [(k, v) for k, v in response.getheaders() if k.lower() not in HOP]
            bodiless = self.command == "HEAD" or response.status in NO_BODY
            length = response.getheader("Content-Length")
            if length is not None and response.status not in NO_BODY:
                headers.append(("Content-Length", length))
            elif not bodiless:
                headers.append(("Connection", "close"))
            start = f"HTTP/1.1 {response.status} {response.reason}"
            answer, began = message(start, headers, b""), True
            self.send_response_only(response.status, response.reason)
            for name, value in headers:
                self.send_header(name, value)
            self.end_headers()
            while chunk := response.read(CHUNK):
                self.wfile.write(chunk)
                answer += chunk[: max(0, CAP + 1 - len(answer))]
            if response.length:
                # Why: `read` returns what came when the product closes early, short of its length.
                none_more = b""
                raise http.client.IncompleteRead(none_more, response.length)
        except (OSError, http.client.HTTPException) as failed:
            return self.failed(answer, failed, began=began)
        finally:
            connection.close()
        return response.status, answer, None

    def failed(self, answer: bytes, error: Exception, *, began: bool) -> tuple[int, bytes, str]:
        """Answer 502 when nothing was sent yet; say what broke either way."""
        said = f"{type(error).__name__}: {error}"
        if began:
            self.close_connection = True
            return int(answer.split(b" ", 2)[1]), answer, f"the answer broke off: {said}"
        text = f"the product did not answer: {said}".encode()
        headers = [
            ("Content-Type", "text/plain; charset=utf-8"),
            ("Content-Length", str(len(text))),
        ]
        self.send_response_only(502, "Bad Gateway")
        for name, value in headers:
            self.send_header(name, value)
        self.end_headers()
        self.wfile.write(text)
        return 502, message("HTTP/1.1 502 Bad Gateway", headers, text), said


class Nothing(http.server.BaseHTTPRequestHandler):
    """A product that does nothing: an empty page for ``GET``, ``303`` to ``/`` for the rest."""

    protocol_version = "HTTP/1.1"

    def __getattr__(self, name: str) -> Callable[[], None]:
        """``do_<METHOD>`` for any method."""
        if name.startswith("do_"):
            return self.answer
        raise AttributeError(name)

    def log_message(self, format: str, *args: object) -> None:  # noqa: A002 # -- the override
        """Nothing to say."""

    def answer(self) -> None:
        """Answer as a product with nothing in it would."""
        self.rfile.read(int(self.headers.get("Content-Length") or 0))
        page = self.command in ("GET", "HEAD")
        self.send_response_only(200 if page else 303)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(NOTHING) if page else 0))
        if not page:
            self.send_header("Location", "/")
        self.end_headers()
        if self.command == "GET":
            self.wfile.write(NOTHING)


def stopping(signum: int, frame: FrameType | None) -> None:
    del signum, frame
    STOP.set()


def until_stopped(server: http.server.ThreadingHTTPServer, parent: int) -> str:
    """Serve until a signal, or until ``parent``, which started this process, is gone.

    Returns when the stop was asked for.
    """
    thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.05})
    thread.start()
    while not STOP.wait(WATCH):
        if os.getppid() != parent:
            break
    stopped = now()
    server.shutdown()
    thread.join()
    server.server_close()
    return stopped


def digest_of(path: Path) -> str | None:
    """The SHA-256 of a file, or ``None`` when there is none."""
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else None


def serve(directory: Path, parent: int) -> int:
    session = str(json.loads((directory / "session.json").read_text(encoding="utf-8"))["session"])
    facts: dict[str, object] = {"session": session}
    facts |= {"ticketSha256": digest_of(directory / "ticket.md")}
    facts |= {"sessionSha256": digest_of(directory / "session.json")}
    ready = urlsplit(os.environ[gatelib.READY_ENV])
    server = Proxy(("127.0.0.1", 0), Forward)
    server.log = Log(directory, session.rsplit("-", 1)[-1])
    server.upstream = (ready.hostname or "127.0.0.1", ready.port or 80)
    port = server.server_address[1]
    origin = f"{ready.scheme}://{ready.netloc}"
    write_json(
        directory / "proxy.json",
        {"url": f"http://127.0.0.1:{port}/", "upstream": origin, "pid": os.getpid()},
    )
    server.log.seal(facts | {"stopped": until_stopped(server, parent)})
    return 0


def null(parent: int) -> int:
    port = int(os.environ["PORT"])
    until_stopped(http.server.ThreadingHTTPServer(("127.0.0.1", port), Nothing), parent)
    return 0


def main(argv: list[str]) -> int:
    for interrupt in gatelib.INTERRUPTS:
        signal.signal(interrupt, stopping)
    parent = os.getppid()
    if argv[1:] == ["--null"]:
        return null(parent)
    return serve(Path(argv[1]), parent)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
