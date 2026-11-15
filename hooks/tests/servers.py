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
# graceful-shutdown check is a real process meeting a real signal. ``/work`` is held until the stop,
# and the stop waits for one, so a request is in flight at the signal however late a busy machine
# delivers either. The stop runs on a thread started at boot: a signal handler that started one
# could deadlock on the lock the server takes to start each request's thread. Modes: ``drain``
# answers the requests in flight and exits 0; ``ignore`` never stops; ``drop`` exits 0 at once and
# cuts them off; ``late`` drains but exits 1; ``slow`` drains after a 3 s pause; ``die`` exits
# before it is signalled. ``queue`` serves one request at a time, so the connections behind the held
# one wait in the kernel's accept queue, and its stop waits until three do; it answers the held one
# and exits 0, and the kernel resets the connections it never accepted, as any correct server's
# listener close does. ``abandon`` is ``queue`` exiting at once instead, so it drops the held one,
# which it accepted, as well. ``shut`` is ``queue`` shutting its listener down, which resets the
# connections waiting on it at once, and closing it only a second after it answers the held one.
# ``HIDDEN`` makes the service non-dumpable, so no other process may look at its open files.
STOPS = (
    "import ctypes, os, signal, socket, struct, sys, threading, time, http.server as h\n"
    "if os.environ.get('HIDDEN'): ctypes.CDLL(None).prctl(4, 0, 0, 0, 0)\n"
    "MODE = os.environ['MODE']\n"
    "QUEUES = MODE in ('queue', 'abandon', 'shut')\n"
    "signalled, held, stopping = threading.Event(), threading.Event(), threading.Event()\n"
    "class Server(h.HTTPServer if QUEUES else h.ThreadingHTTPServer):\n"
    "    daemon_threads = False\n"
    "    block_on_close = True\n"
    "    def handle_error(self, request, client_address): pass\n"
    "class Handler(h.BaseHTTPRequestHandler):\n"
    "    def log_message(self, *args): pass\n"
    "    def do_GET(self):\n"
    "        if self.path == '/work': held.set(); stopping.wait()\n"
    "        self.send_response(200)\n"
    "        self.send_header('Content-Length', '2')\n"
    "        self.end_headers()\n"
    "        self.wfile.write(b'ok')\n"
    "server = Server(('127.0.0.1', int(os.environ['PORT'])), Handler)\n"
    "def waiting():\n"
    "    info = server.socket.getsockopt(socket.IPPROTO_TCP, socket.TCP_INFO, 32)\n"
    "    return struct.unpack_from('I', info, 24)[0]\n"
    "def stop():\n"
    "    signalled.wait()\n"
    "    held.wait()\n"
    "    while QUEUES and waiting() < 3: time.sleep(0.01)\n"
    "    if MODE in ('drop', 'abandon'): os._exit(0)\n"
    "    if MODE == 'slow': time.sleep(3)\n"
    "    if MODE == 'shut': server.socket.shutdown(socket.SHUT_RDWR)\n"
    "    stopping.set()\n"
    "    server.shutdown()\n"
    "if MODE == 'die': threading.Timer(0.3, os._exit, [5]).start()\n"
    "threading.Thread(target=stop, daemon=True).start()\n"
    "signal.signal(signal.SIGTERM, lambda *_: signalled.set())\n"
    "if MODE == 'ignore': signal.signal(signal.SIGTERM, signal.SIG_IGN)\n"
    "server.serve_forever()\n"
    "if MODE == 'shut': time.sleep(1)\n"
    "server.server_close()\n"
    "sys.exit(1 if MODE == 'late' else 0)"
)

# Why: a broken service that forges the gate's excuse. It holds each ``/work`` until it is
# stopped, and on SIGTERM goes on accepting until it holds one. It then stops accepting and, for
# each request in hand, connects to its own listener from 127.0.0.2 with that request's client
# port, so the gate takes a connection with the same ports. It closes the listener, waits until
# the gate has taken and reset each of those, and resets the requests it holds: those it dropped.
FORGE = (
    "import os, signal, socket, struct, threading\n"
    "PORT = int(os.environ['PORT'])\n"
    "listener = socket.create_server(('127.0.0.1', PORT))\n"
    "listener.settimeout(0.01)\n"
    "held, stopping = {}, threading.Event()\n"
    "def serve(connection, client):\n"
    "    data = b''\n"
    "    while b'\\r\\n\\r\\n' not in data and (chunk := connection.recv(4096)):\n"
    "        data += chunk\n"
    "    if data.startswith(b'GET /work'):\n"
    "        held[client] = connection\n"
    "        return\n"
    "    connection.sendall(b'HTTP/1.1 200 OK\\r\\nContent-Length: 2\\r\\n\\r\\nok')\n"
    "    connection.close()\n"
    "signal.signal(signal.SIGTERM, lambda *_: stopping.set())\n"
    "while not (stopping.is_set() and held):\n"
    "    try: connection, (_, client) = listener.accept()\n"
    "    except TimeoutError: continue\n"
    "    threading.Thread(target=serve, args=(connection, client)).start()\n"
    "forged = []\n"
    "for client in list(held):\n"
    "    forged.append(socket.socket())\n"
    "    forged[-1].bind(('127.0.0.2', client))\n"
    "    forged[-1].connect(('127.0.0.1', PORT))\n"
    "listener.close()\n"
    "for each in forged:\n"
    "    try: each.recv(1)\n"
    "    except ConnectionResetError: pass\n"
    "for connection in list(held.values()):\n"
    "    connection.setsockopt(socket.SOL_SOCKET, socket.SO_LINGER, struct.pack('ii', 1, 0))\n"
    "    connection.close()\n"
    "os._exit(0)\n"
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
