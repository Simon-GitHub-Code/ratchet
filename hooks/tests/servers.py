"""Small HTTP servers and process probes for the service-gate tests; localhost only."""

import http.server
import shlex
import shutil
import subprocess
import sys
import threading
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path

PYTHON = f"{shlex.quote(sys.executable)} -S"
# Why: a service that is slow to answer makes the probe give up and the server print a traceback
# for each broken connection, which would push the boot's own lines out of the gate's output tail.
SERVE = (
    "import os, http.server as h\n"
    "class Server(h.ThreadingHTTPServer):\n"
    "    def handle_error(self, request, client_address): pass\n"
    "class Handler(h.SimpleHTTPRequestHandler):\n"
    "    def log_message(self, *args): pass\n"
    "Server(('127.0.0.1', int(os.environ['PORT'])), Handler).serve_forever()"
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


def gone(pid: int, wait: float = 10.0) -> bool:
    """Whether the process is gone within ``wait`` seconds.

    A killed process takes a moment to die, and longer when the machine is busy; the tests that
    kill one run beside many others.
    """
    deadline = time.monotonic() + wait
    while alive(pid):
        if time.monotonic() > deadline:
            return False
        time.sleep(0.02)
    return True


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


def sleeper(tree: Path) -> subprocess.Popen[bytes]:
    """Start a copy of ``sleep`` that is a file of ``tree``: a service somebody built and left."""
    program = tree / "target" / "sleep"
    program.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy(shutil.which("sleep") or "", program)
    return subprocess.Popen([str(program), "60"])  # noqa: S603 # -- a copy of sleep, no shell


# Why: one service whose behaviour on SIGTERM is chosen by ``MODE``, so each way of failing the
# graceful-shutdown check is a real process meeting a real signal. ``/work`` takes 0.3 s, so a
# request is in flight at any moment while a load smoke runs. Modes: ``drain`` finishes the
# requests in flight and exits 0; ``ignore`` never stops; ``drop`` exits 0 at once and cuts them
# off; ``late`` drains but exits 1; ``slow`` drains after a 3 s pause; ``die`` exits before it
# is signalled. ``queue`` serves one request at a time, answers the first four ``/work`` at once
# and holds the fifth until the signal, so the connections behind it wait in the kernel's accept
# queue; on the signal it answers the fifth and exits 0, and the kernel resets the connections it
# never accepted, as any correct server's listener close does. ``abandon`` is ``queue`` exiting at
# once on the signal instead, so it drops the fifth, which it accepted, as well. ``HIDDEN`` makes
# the service non-dumpable, so no other process may look at its open files.
STOPS = (
    "import ctypes, os, signal, sys, threading, time, http.server as h\n"
    "if os.environ.get('HIDDEN'): ctypes.CDLL(None).prctl(4, 0, 0, 0, 0)\n"
    "MODE = os.environ['MODE']\n"
    "QUEUES = MODE in ('queue', 'abandon')\n"
    "held, stopping = [], threading.Event()\n"
    "class Server(h.HTTPServer if QUEUES else h.ThreadingHTTPServer):\n"
    "    daemon_threads = False\n"
    "    block_on_close = True\n"
    "    def handle_error(self, request, client_address): pass\n"
    "class Handler(h.BaseHTTPRequestHandler):\n"
    "    def log_message(self, *args): pass\n"
    "    def do_GET(self):\n"
    "        if self.path == '/work' and not QUEUES: time.sleep(0.3)\n"
    "        if self.path == '/work' and QUEUES:\n"
    "            held.append(self.connection)\n"
    "            if len(held) == 5: stopping.wait()\n"
    "        self.send_response(200)\n"
    "        self.send_header('Content-Length', '2')\n"
    "        self.end_headers()\n"
    "        self.wfile.write(b'ok')\n"
    "server = Server(('127.0.0.1', int(os.environ['PORT'])), Handler)\n"
    "def stop(*args):\n"
    "    if MODE in ('drop', 'abandon'): os._exit(0)\n"
    "    if MODE == 'slow': time.sleep(3)\n"
    "    stopping.set()\n"
    "    threading.Thread(target=server.shutdown).start()\n"
    "if MODE == 'die': threading.Timer(0.3, os._exit, [5]).start()\n"
    "signal.signal(signal.SIGTERM, signal.SIG_IGN if MODE == 'ignore' else stop)\n"
    "server.serve_forever()\n"
    "server.server_close()\n"
    "sys.exit(1 if MODE == 'late' else 0)"
)

# Why: a service that reads ``NAME``, and what it does when it is missing is chosen by ``MODE``:
# ``refuse`` names it and exits 2, ``silent`` exits 2 without a word, ``zero`` names it and exits
# 0, ``hang`` waits, ``boot`` answers anyway. ``ALSO`` is a second setting it needs.
NEEDS = (
    "import os, sys, time\n"
    "MODE = os.environ['MODE']\n"
    "if os.environ['NAME'] not in os.environ:\n"
    "    if MODE == 'refuse': sys.exit(f\"config error: {os.environ['NAME']} is not set\")\n"
    "    if MODE == 'silent': sys.exit(2)\n"
    "    if MODE == 'zero': print(f\"{os.environ['NAME']} is not set\"); sys.exit(0)\n"
    "    if MODE == 'hang': time.sleep(60)\n"
    f"{SERVE}"
)
