import http.server
import shlex
import shutil
import subprocess
import sys
import threading
import time
import uuid
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path

PYTHON = f"{shlex.quote(sys.executable)} -S"
SERVE = (
    "import os, http.server as h\n"
    "class SilentWhenAClientHangsUp(h.ThreadingHTTPServer):\n"
    "    def handle_error(self, request, client_address): pass\n"
    "class Handler(h.SimpleHTTPRequestHandler):\n"
    "    def log_message(self, *args): pass\n"
    "SilentWhenAClientHangsUp(('127.0.0.1', int(os.environ['PORT'])), Handler).serve_forever()"
)


def python(code: str) -> str:
    return f"{PYTHON} -c {shlex.quote(code)}"


def alive(pid: int) -> bool:
    state = subprocess.run(  # noqa: S603 # -- a fixed argv, no shell
        ["ps", "-o", "stat=", "-p", str(pid)],  # noqa: S607 # -- ps is resolved from PATH
        capture_output=True,
        text=True,
        check=False,
    ).stdout.strip()
    return bool(state) and not state.startswith("Z")


def gone(pid: int, wait: float = 10.0) -> bool:
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
    class Handler(http.server.BaseHTTPRequestHandler):
        body = b""

        def do_GET(self) -> None:
            self.body = self.rfile.read(int(self.headers.get("Content-Length", "0")))
            handle(self)

        do_POST = do_GET  # noqa: N815 # -- the name http.server dispatches on

        def log_message(self, format: str, *args: object) -> None:  # noqa: A002 # -- the override
            pass

    class SilentWhenAClientHangsUp(http.server.ThreadingHTTPServer):
        daemon_threads = False

        def handle_error(self, request: object, client_address: object) -> None: ...

    httpd = SilentWhenAClientHangsUp(("127.0.0.1", 0), Handler)
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


HOLDS_FOR_OTHERS = (
    "import contextlib, socket, sys, threading\n"
    "def answer(listener):\n"
    "    while True:\n"
    "        try:\n"
    "            connection, _ = listener.accept()\n"
    "        except OSError:\n"
    "            return\n"
    "        connection.recv(65536)\n"
    "        connection.sendall(b'HTTP/1.0 200 OK\\r\\nContent-Length: 0\\r\\n\\r\\n')\n"
    "        connection.close()\n"
    "def hold(client):\n"
    "    address, port = client.recv(64).decode().split()\n"
    "    family = socket.AF_INET6 if ':' in address else socket.AF_INET\n"
    "    listener = socket.create_server((address, int(port)), family=family)\n"
    "    threading.Thread(target=answer, args=(listener,), daemon=True).start()\n"
    "    client.sendall(b'up')\n"
    "    client.recv(64)\n"
    "    listener.shutdown(socket.SHUT_RDWR)\n"
    "    listener.close()\n"
    "    with contextlib.suppress(OSError):\n"
    "        client.sendall(b'down')\n"
    "hub = socket.socket(socket.AF_UNIX)\n"
    "hub.bind('\\0' + sys.argv[1])\n"
    "hub.listen()\n"
    "print('up', flush=True)\n"
    "while True:\n"
    "    threading.Thread(target=hold, args=(hub.accept()[0],), daemon=True).start()\n"
)

ASKS_ANOTHER_TO_LISTEN = (
    "import os, signal, socket, sys\n"
    "hub = socket.socket(socket.AF_UNIX)\n"
    "hub.connect('\\0' + sys.argv[1])\n"
    "hub.sendall(f\"{sys.argv[2]} {os.environ['PORT']}\".encode())\n"
    "hub.recv(2)\n"
    "def stop(*_):\n"
    "    hub.sendall(b'close')\n"
    "    hub.recv(4)\n"
    "    os._exit(0)\n"
    "signal.signal(signal.SIGTERM, stop)\n"
    "while True:\n"
    "    signal.pause()\n"
)


@contextmanager
def held_outside_the_group() -> Iterator[Callable[[str], str]]:
    name = f"ratchet-test-{uuid.uuid4().hex}"
    with subprocess.Popen(  # noqa: S603 # -- this interpreter and a fixed script
        [sys.executable, "-S", "-c", HOLDS_FOR_OTHERS, name],
        stdout=subprocess.PIPE,
        start_new_session=True,
    ) as holder:
        try:
            assert holder.stdout is not None
            assert holder.stdout.readline() == b"up\n", "the holder did not start"
            yield lambda address: f"exec {python(ASKS_ANOTHER_TO_LISTEN)} {name} {address}"
        finally:
            holder.kill()


def sleeper(tree: Path) -> subprocess.Popen[bytes]:
    program = tree / "target" / "sleep"
    program.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy(shutil.which("sleep") or "", program)
    return subprocess.Popen([str(program), "60"])  # noqa: S603 # -- a copy of sleep, no shell


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
    "def stop_off_the_signal_handler():\n"
    "    signalled.wait()\n"
    "    held.wait()\n"
    "    while QUEUES and waiting() < 3: time.sleep(0.01)\n"
    "    if MODE in ('drop', 'abandon'): os._exit(0)\n"
    "    if MODE == 'slow': time.sleep(3)\n"
    "    if MODE == 'shut': server.socket.shutdown(socket.SHUT_RDWR)\n"
    "    stopping.set()\n"
    "    server.shutdown()\n"
    "if MODE == 'die': threading.Timer(0.3, os._exit, [5]).start()\n"
    "threading.Thread(target=stop_off_the_signal_handler, daemon=True).start()\n"
    "signal.signal(signal.SIGTERM, lambda *_: signalled.set())\n"
    "if MODE == 'ignore': signal.signal(signal.SIGTERM, signal.SIG_IGN)\n"
    "server.serve_forever()\n"
    "if MODE == 'shut': time.sleep(1)\n"
    "server.server_close()\n"
    "sys.exit(1 if MODE == 'late' else 0)"
)

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

LISTENS = (
    "import os, socket, sys\n"
    "def listen(address, port):\n"
    "    v6 = ':' in address\n"
    "    s = socket.socket(socket.AF_INET6 if v6 else socket.AF_INET)\n"
    "    if v6: s.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, 0)\n"
    "    s.bind((address, port))\n"
    "    s.listen()\n"
    "    return s\n"
    "served = listen(sys.argv[1], int(os.environ['PORT']))\n"
    "others = [listen(address, 0) for address in sys.argv[2:]]\n"
    "while True:\n"
    "    connection, _ = served.accept()\n"
    "    connection.recv(65536)\n"
    "    connection.sendall(b'HTTP/1.0 200 OK\\r\\nContent-Length: 0\\r\\n\\r\\n')\n"
    "    connection.close()\n"
)

NAMES_ITSELF = (
    "import os, socket\n"
    "served = socket.create_server(('127.0.0.1', int(os.environ['PORT'])))\n"
    "name = os.environ.get('HOST', 'localhost')\n"
    "while True:\n"
    "    connection, _ = served.accept()\n"
    "    lines = connection.recv(65536).decode().split('\\r\\n')\n"
    "    asked = [line[5:].strip() for line in lines if line.lower().startswith('host:')]\n"
    "    status = b'200 OK' if asked[0].rpartition(':')[0] == name else b'400 Bad Request'\n"
    "    connection.sendall(b'HTTP/1.0 ' + status + b'\\r\\nContent-Length: 0\\r\\n\\r\\n')\n"
    "    connection.close()\n"
)

HIDES = (
    "import ctypes, socket, sys, time\n"
    "held = socket.create_server(('127.0.0.1', 0))\n"
    "ctypes.CDLL(None).prctl(4, 0, 0, 0, 0)\n"
    "open(sys.argv[1], 'w').close()\n"
    "time.sleep(60)\n"
)

HELPS = (
    "import socket, sys, time\n"
    "held = socket.create_server(('127.0.0.1', 0))\n"
    "open(sys.argv[1], 'w').close()\n"
    "time.sleep(60)\n"
)


def helped_by(marker: Path, boot: str) -> str:
    return f"{python(HELPS)} {marker} & while [ ! -e {marker} ]; do sleep 0.05; done; {boot}"


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
