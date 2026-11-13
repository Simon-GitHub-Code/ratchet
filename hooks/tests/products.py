"""A small product the acceptance harness can boot, in a git work tree of its own."""

import http.client
import json
import os
import shlex
import struct
import subprocess
import sys
import time
import zlib
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import gatelib
from tests import repos

# Why each route: `/` and `/items` are the product a tester tries; `/flag` shows the flag the
# justfile's `flags_on` turns on; the rest are the shapes of answer the proxy must pass through
# unchanged (a large body, one with no length, no content, one that breaks off, the request's
# own headers echoed) and a route that holds a request open. `BOOTING` names a file the app
# writes its process id to as it starts, and `DELAY` holds it that long before it listens.
APP = r"""
import html, json, os, time, urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
if os.environ.get("BOOTING"):
    open(os.environ["BOOTING"], "w").write(str(os.getpid()))
time.sleep(float(os.environ.get("DELAY", "0")))
ITEMS = []
class Server(ThreadingHTTPServer):
    daemon_threads = True
    def handle_error(self, request, client_address): pass
class App(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    def log_message(self, *args): pass
    def send(self, status, body=b"", kind="text/html; charset=utf-8", length=True):
        self.send_response(status)
        self.send_header("Content-Type", kind)
        if length:
            self.send_header("Content-Length", str(len(body)))
        else:
            self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(body)
    def do_GET(self):
        if self.path == "/slow":
            open(os.environ["SLOW"], "w").write("hit")
            time.sleep(30)
        if self.path == "/big":
            return self.send(200, b"x" * (2 * 1024 * 1024), "text/plain")
        if self.path == "/stream":
            self.close_connection = True
            return self.send(200, b"streamed without a length", "text/plain", length=False)
        if self.path == "/empty":
            self.send_response(204)
            self.end_headers()
            return
        if self.path == "/break":
            self.send_response(200)
            self.send_header("Content-Length", "100")
            self.end_headers()
            self.wfile.write(b"0123456789")
            self.wfile.flush()
            self.connection.shutdown(2)
            return
        if self.path == "/flag":
            return self.send(200, os.environ.get("FLAG", "").encode(), "text/plain")
        if self.path == "/echo":
            body = json.dumps(dict(self.headers.items())).encode()
            return self.send(200, body, "application/json")
        rows = "".join(
            f'<li><a href="{html.escape(i)}" target="_blank">{html.escape(i)}</a></li>'
            for i in reversed(ITEMS)
        )
        empty = "" if ITEMS else "<p>There is nothing left to read.</p>"
        page = (
            "<!doctype html><title>Read later</title>"
            "<form method=post action=/items><label for=l>Link to save</label>"
            f"<input id=l name=link><button>Save</button></form><ul>{rows}</ul>{empty}"
        )
        self.send(200, page.encode())
    def do_HEAD(self):
        self.send_response(200)
        self.send_header("Content-Length", "5")
        self.end_headers()
    def do_POST(self):
        n = int(self.headers.get("Content-Length") or 0)
        data = self.rfile.read(n).decode()
        if self.path != "/items":
            return self.send(200, f"{self.command} {data}".encode(), "text/plain")
        ITEMS.append(urllib.parse.parse_qs(data).get("link", [""])[0])
        self.send_response(303)
        self.send_header("Location", "/")
        self.send_header("Content-Length", "0")
        self.end_headers()
    do_PUT = do_PATCH = do_DELETE = do_POST
Server(("127.0.0.1", int(os.environ["PORT"])), App).serve_forever()
"""

TICKET = """# 01: Save a link and see it on the list

- [ ] Saving a link answers `303` to the list, and the new item is at the top.
- [ ] An empty list shows one plain sentence saying nothing is left.
- [x] The app reads its settings in one module.
"""
PROMISES = (
    "Saving a link answers `303` to the list, and the new item is at the top.",
    "An empty list shows one plain sentence saying nothing is left.",
    "The app reads its settings in one module.",
)


def make(
    root: Path,
    *,
    boot: str | None = None,
    env: str = "",
    ready: str = "http://127.0.0.1:{port}/",
    extra: str = "",
) -> Path:
    """A committed product at ``root``: an app, its justfile and binding, and a ticket."""
    root.mkdir(parents=True, exist_ok=True)
    (root / "app.py").write_text(APP, encoding="utf-8")
    (root / "src").mkdir(exist_ok=True)
    (root / "src" / "settings.py").write_text("PORT = 0\n", encoding="utf-8")
    app = f"{env} exec {shlex.quote(sys.executable)} {root / 'app.py'}".strip()
    command = boot if boot is not None else app
    lines = [f"boot := {json.dumps(command)}", f"ready := {json.dumps(ready)}", extra]
    (root / "justfile").write_text("\n".join(lines) + "\n", encoding="utf-8")
    (root / "docs" / "process").mkdir(parents=True, exist_ok=True)
    binding = "# Binding\n\n- Application types: service, web-ui\n"
    (root / "docs" / "process" / "binding.md").write_text(binding, encoding="utf-8")
    (root / "ticket.md").write_text(TICKET, encoding="utf-8")
    return repos.repository(root, {".gitignore": "docs/features/\n"})


@contextmanager
def serving(tmp_path: Path) -> Iterator[str]:
    """The test product on a free port, stopped at the end; its address."""
    (tmp_path / "app.py").write_text(APP, encoding="utf-8")
    port = gatelib.free_port()
    env = {**os.environ, "PORT": str(port), "SLOW": str(tmp_path / "slow")}
    process = subprocess.Popen(  # noqa: S603 # -- this interpreter and a file this test wrote
        [sys.executable, str(tmp_path / "app.py")], env=env, start_new_session=True
    )
    url = f"http://127.0.0.1:{port}/"
    try:
        while not gatelib.listening(url):
            assert process.poll() is None, "the test product did not start"
            time.sleep(0.02)
        yield url
    finally:
        gatelib.stop_group(process)


def request(
    url: str, method: str = "GET", path: str = "/", *, body: bytes | None = None
) -> tuple[int, bytes]:
    """One request through the harness's address; the status and body."""
    host, port = url.split("//")[1].rstrip("/").split(":")
    connection = http.client.HTTPConnection(host, int(port), timeout=30)
    headers = {"Content-Type": "application/x-www-form-urlencoded"} if body is not None else {}
    try:
        connection.request(method, path, body=body, headers=headers)
        response = connection.getresponse()
        return response.status, response.read()
    finally:
        connection.close()


def png(path: Path) -> Path:
    """A one-pixel PNG, as a screenshot would be."""

    def chunk(kind: bytes, data: bytes) -> bytes:
        crc = struct.pack(">I", zlib.crc32(kind + data))
        return struct.pack(">I", len(data)) + kind + data + crc

    header = struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0)
    image = chunk(b"IHDR", header) + chunk(b"IDAT", zlib.compress(b"\0\xff\xff\xff"))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"\x89PNG\r\n\x1a\n" + image + chunk(b"IEND", b""))
    return path


def log(directory: Path) -> list[dict[str, object]]:
    lines = (directory / "requests.jsonl").read_text(encoding="utf-8").splitlines()
    return [json.loads(line) for line in lines]


def wait_for(path: Path, limit: float = 30.0) -> None:
    deadline = time.monotonic() + limit
    while not path.exists():
        assert time.monotonic() < deadline, f"{path} never appeared"
        time.sleep(0.02)


def processes_naming(text: str) -> list[str]:
    """Every process whose command line names ``text``: what a test left running."""
    listed = subprocess.run(
        ["ps", "-eo", "pid=,args="],  # noqa: S607 # -- ps is resolved from PATH
        capture_output=True,
        text=True,
        check=True,
    ).stdout.splitlines()
    return [line for line in listed if text in line and "ps -eo" not in line]


def tried(url: str) -> None:
    """What the good record says was done: the empty list, a link saved, the list again."""
    assert request(url)[0] == 200
    assert request(url, "POST", "/items", body=b"link=https://example.com/a")[0] == 303
    assert request(url)[0] == 200


def good_record(directory: Path) -> dict[str, object]:
    """A record the harness accepts, citing what ``directory``'s session logged after ``tried``."""
    session = json.loads((directory / "session.json").read_text(encoding="utf-8"))
    entries = log(directory)
    png(directory / "tester" / "shots" / "list.png")
    ids = tuple(str(entry["id"]) for entry in entries[:3])
    return record(str(session["session"]), str(session["head"]), ids)


def record(session: str, head: str, ids: tuple[str, ...]) -> dict[str, object]:
    """The good record of ``session``, whose requests ``ids`` were ``tried``."""
    first, saved, after = ({"id": ident} for ident in ids)
    return {
        "session": session,
        "head": head,
        "criteria": [
            {
                "id": "C1",
                "text": PROMISES[0],
                "verdict": "pass",
                "how": "Saved a link: it answered 303 to the list, and the link was on top.",
                "requests": [
                    {"request": saved["id"], "method": "POST", "path": "/items", "status": 303},
                    {
                        "request": after["id"],
                        "method": "GET",
                        "path": "/",
                        "status": 200,
                        "contains": ["https://example.com/a"],
                    },
                ],
                "screenshots": ["shots/list.png"],
            },
            {
                "id": "C2",
                "text": PROMISES[1],
                "verdict": "pass",
                "how": "Opened the page before saving anything.",
                "requests": [
                    {
                        "request": first["id"],
                        "method": "GET",
                        "path": "/",
                        "status": 200,
                        "contains": ["There is nothing left to read."],
                    }
                ],
                "screenshots": ["shots/list.png"],
                "unkept": "It needs an empty list, and replay boots on what the product keeps.",
            },
            {
                "id": "C3",
                "text": PROMISES[2],
                "verdict": "not-observable",
                "how": "This is about how the code is built.",
                "judge": "code review",
            },
        ],
        "findings": [
            {
                "id": "F1",
                "severity": "minor",
                "scope": "later: 04",
                "title": "A link that is not a web link is saved",
                "steps": ["Type words into the field.", "Press Save."],
                "expected": "It is refused.",
                "actual": "It is saved.",
                "requests": [
                    {"request": saved["id"], "method": "POST", "path": "/items", "status": 303}
                ],
            }
        ],
        "kept": [
            {
                "id": "K1",
                "criterion": "C1",
                "via": "http",
                "steps": [
                    {
                        "request": "POST /items",
                        "form": {"link": "https://example.com/{unique}"},
                        "headers": {"Origin": "{BASE_URL}"},
                        "expect": {"status": 303, "headers": {"Location": "/"}},
                    },
                    {
                        "request": "GET /",
                        "expect": {
                            "status": 200,
                            "contains": ["https://example.com/{unique}"],
                            "count": {"https://example.com/{unique}": 2},
                        },
                    },
                ],
            }
        ],
        "notChecked": [{"what": "A phone", "why": "Only a narrow window was at hand."}],
        "tryIt": ["Open the address, paste a link and press Save."],
    }
