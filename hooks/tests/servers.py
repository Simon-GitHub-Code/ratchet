"""Small HTTP servers and process probes for the service-gate tests; localhost only."""

import http.server
import shlex
import subprocess
import sys
import threading
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path

PYTHON = f"{shlex.quote(sys.executable)} -S"
SERVE = (
    "import os, http.server as h; "
    "h.ThreadingHTTPServer(('127.0.0.1', int(os.environ['PORT'])), "
    "h.SimpleHTTPRequestHandler).serve_forever()"
)


def python(code: str) -> str:
    """A shell line that runs ``code`` with this interpreter."""
    return f"{PYTHON} -c {shlex.quote(code)}"


def alive(pid: int) -> bool:
    """Whether the process exists and is not a zombie."""
    state = subprocess.run(  # noqa: S603 # -- a fixed argv, no shell
        ["ps", "-o", "stat=", "-p", str(pid)],  # noqa: S607 # -- ps is resolved from PATH
        capture_output=True,
        text=True,
        check=False,
    ).stdout.strip()
    return bool(state) and not state.startswith("Z")


def pid_of(path: Path) -> int:
    return int(path.read_text(encoding="utf-8"))


@contextmanager
def server(
    handle: Callable[[http.server.BaseHTTPRequestHandler], None],
) -> Iterator[str]:
    """A server on a free loopback port whose handler calls ``handle`` for GET and POST."""

    class Handler(http.server.BaseHTTPRequestHandler):
        body = b""

        def do_GET(self) -> None:
            self.body = self.rfile.read(int(self.headers.get("Content-Length", "0")))
            handle(self)

        do_POST = do_GET  # noqa: N815 # -- the name http.server dispatches on

        def log_message(self, format: str, *args: object) -> None:  # noqa: A002 # -- the override
            pass

    class Quiet(http.server.ThreadingHTTPServer):
        daemon_threads = False

        def handle_error(self, request: object, client_address: object) -> None:
            """A client that gave up mid-reply is the tests' doing, not a failure."""

    httpd = Quiet(("127.0.0.1", 0), Handler)
    thread = threading.Thread(
        target=httpd.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True
    )
    thread.start()
    try:
        yield f"http://127.0.0.1:{httpd.server_address[1]}"
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join()


def reply(handler: http.server.BaseHTTPRequestHandler, status: int, body: bytes = b"ok") -> None:
    handler.send_response(status)
    handler.send_header("Content-Length", str(len(body)))
    handler.end_headers()
    handler.wfile.write(body)
