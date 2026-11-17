#!/usr/bin/env python3
from __future__ import annotations

import datetime as dt
import fcntl
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
SETTLE = 2.0
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
ENCODING = "accept-encoding"
STOP = threading.Event()
NOTHING = b"<!doctype html><html lang=en><head><title>x</title></head><body></body></html>"
NO_BODY = frozenset({204, 304})


def now() -> str:
    return dt.datetime.now(dt.UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def write_json(path: Path, data: dict[str, object]) -> None:
    partial = path.with_name(path.name + ".partial")
    partial.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    partial.replace(path)


class Log:
    def __init__(self, directory: Path, prefix: str) -> None:
        self.directory, self.prefix = directory, prefix
        (directory / "probes").mkdir(parents=True, exist_ok=True)
        self.file = (directory / "requests.jsonl").open("ab", buffering=0)
        self.lock = threading.Lock()
        self.idle = threading.Condition(self.lock)
        self.digest = hashlib.sha256()
        self.issued = self.written = self.open = self.answering = self.unlogged = 0
        self.sealed = False

    def issue(self) -> str:
        with self.lock:
            self.issued += 1
            self.open += 1
            return f"{self.prefix}-{self.issued}"

    def store(self, name: str, data: bytes) -> tuple[str, bool]:
        kept = data[:CAP]
        (self.directory / "probes" / name).write_bytes(kept)
        return hashlib.sha256(kept).hexdigest(), len(data) > CAP

    def answer(self) -> bool:
        with self.lock:
            self.answering += not self.sealed
            return not self.sealed

    def finish(self, entry: dict[str, object] | None, *, answered: bool) -> None:
        line = json.dumps(entry).encode() + b"\n" if entry else b""
        with self.lock:
            self.open -= 1
            self.answering -= answered
            self.idle.notify_all()
            if self.sealed:
                return
            try:
                done = self.file.write(line) if line else 0
            except OSError:
                done = 0
            if line and done == len(line):
                self.digest.update(line)
                self.written += 1
            else:
                self.unlogged += answered

    def seal(self, facts: dict[str, object]) -> None:
        with self.lock:
            self.idle.wait_for(lambda: self.open == 0, timeout=SETTLE)
            self.sealed = True
            self.file.close()
            seal = facts | {"requests": self.written, "sha256": self.digest.hexdigest()}
            seal |= {"unlogged": self.unlogged + self.answering}
            write_json(self.directory / "seal.json", seal | {"ended": now()})


def message(start: str, headers: list[tuple[str, str]], body: bytes) -> bytes:
    head = "".join(f"{name}: {value}\r\n" for name, value in headers)
    return f"{start}\r\n{head}\r\n".encode("latin-1") + body


class Proxy(http.server.ThreadingHTTPServer):
    daemon_threads = True
    log: Log
    upstream: tuple[str, int]

    def handle_error(self, request: object, client_address: object) -> None: ...


class Sealed(Exception):  # noqa: N818 # -- a state the request ran into, not a fault
    ...


class Forward(http.server.BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server: Proxy
    answered = False

    def __getattr__(self, name: str) -> Callable[[], None]:
        if name.startswith("do_"):
            return self.forward
        raise AttributeError(name)

    def log_message(self, format: str, *args: object) -> None:  # noqa: A002 # -- the override
        ...

    def body(self) -> bytes:
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
        log = self.server.log
        ident = log.issue()
        entry, self.answered = None, False
        try:
            entry = self.exchange(ident)
        finally:
            log.finish(entry, answered=self.answered)

    def send_response_only(self, code: int, message: str | None = None) -> None:
        self.answered = self.server.log.answer()
        if not self.answered:
            raise Sealed
        super().send_response_only(code, message)

    def exchange(self, ident: str) -> dict[str, object]:
        log = self.server.log
        at, started = now(), time.monotonic()
        body = self.body()
        sent = [(k, v) for k, v in self.headers.items() if k.lower() not in HOP | {ENCODING}]
        sent.append(("Accept-Encoding", "identity"))
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
                none_more = b""
                raise http.client.IncompleteRead(none_more, response.length)
        except (OSError, http.client.HTTPException) as failed:
            return self.failed(answer, failed, began=began)
        finally:
            connection.close()
        return response.status, answer, None

    def failed(self, answer: bytes, error: Exception, *, began: bool) -> tuple[int, bytes, str]:
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
    protocol_version = "HTTP/1.1"

    def __getattr__(self, name: str) -> Callable[[], None]:
        if name.startswith("do_"):
            return self.answer
        raise AttributeError(name)

    def log_message(self, format: str, *args: object) -> None:  # noqa: A002 # -- the override
        ...

    def answer(self) -> None:
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


def locked(directory: Path) -> bool:
    path = directory / "live.lock"
    if not path.is_file():
        return False
    with path.open("rb") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return True
    return False


def abandoned(directory: Path) -> bool:
    return (directory / "live.lock").is_file() and not locked(directory)


def until_stopped(server: http.server.ThreadingHTTPServer, going: Callable[[], bool]) -> str:
    thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.05})
    thread.start()
    while not STOP.wait(WATCH):
        if not going():
            break
    stopped = now()
    server.shutdown()
    thread.join()
    server.server_close()
    return stopped


def digest_of(path: Path) -> str | None:
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else None


def serve(directory: Path, parent: int) -> int:
    def going() -> bool:
        return os.getppid() == parent and directory.is_dir() and not abandoned(directory)

    if not going():
        print("acceptance_proxy: the supervisor is gone; nothing was served", file=sys.stderr)
        return 1
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
    stopped = until_stopped(server, going)
    if not directory.is_dir():
        print(
            "acceptance_proxy: the session's directory was removed; nothing is sealed",
            file=sys.stderr,
        )
        return 1
    server.log.seal(facts | {"stopped": stopped})
    return 0


def null(parent: int) -> int:
    port = int(os.environ["PORT"])
    server = http.server.ThreadingHTTPServer(("127.0.0.1", port), Nothing)
    until_stopped(server, lambda: os.getppid() == parent)
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
