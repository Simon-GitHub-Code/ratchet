"""The acceptance proxy: each request passes through unchanged and is logged; the log is sealed."""

import hashlib
import http.client
import json
import os
import shlex
import signal
import socket
import subprocess
import sys
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

import pytest

import acceptance_proxy
import gatelib
from tests import products
from tests.servers import gone, pid_of

PROXY = Path(acceptance_proxy.__file__)
SESSION = "20261113T000000000000Z-abcd1234"


@contextmanager
def proxied(tmp_path: Path, upstream: str, monkeypatch: pytest.MonkeyPatch) -> Iterator[str]:
    """The proxy in front of ``upstream``, serving on a thread until the block ends; its address."""
    directory = tmp_path / "session"
    directory.mkdir()
    (directory / "session.json").write_text(json.dumps({"session": SESSION}), encoding="utf-8")
    monkeypatch.setenv(gatelib.READY_ENV, upstream)
    serving = threading.Thread(target=acceptance_proxy.serve, args=(directory, os.getppid()))
    serving.start()
    try:
        products.wait_for(directory / "proxy.json")
        yield json.loads((directory / "proxy.json").read_text(encoding="utf-8"))["url"]
    finally:
        acceptance_proxy.STOP.set()
        serving.join()
        acceptance_proxy.STOP.clear()


@dataclass(frozen=True)
class Answer:
    status: int
    headers: http.client.HTTPMessage
    body: bytes


def exchange(url: str, method: str, path: str, **sent: object) -> Answer:
    """One request on a connection of its own, read whole."""
    host, port = url.split("//")[1].rstrip("/").split(":")
    connection = http.client.HTTPConnection(host, int(port), timeout=30)
    try:
        connection.request(method, path, **sent)  # type: ignore[arg-type]
        response = connection.getresponse()
        return Answer(response.status, response.msg, response.read())
    finally:
        connection.close()


def test_each_answer_reaches_the_tester_unchanged_and_each_request_is_logged(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    directory = tmp_path / "session"
    with products.serving(tmp_path) as upstream, proxied(tmp_path, upstream, monkeypatch) as url:
        assert products.request(url) == products.request(upstream)
        assert products.request(url, "POST", "/items", body=b"link=x")[0] == 303
        big = exchange(url, "GET", "/big")
        assert (big.status, len(big.body)) == (200, 2 * 1024 * 1024), "a large answer whole"
        assert exchange(url, "GET", "/stream").body == b"streamed without a length"
        assert exchange(url, "GET", "/empty").status == 204
        head = exchange(url, "HEAD", "/")
        assert (head.status, head.headers["Content-Length"], head.body) == (200, "5", b"")
        chunked = exchange(url, "PUT", "/other", body=iter([b"he", b"llo"]), encode_chunked=True)
        assert chunked.body == b"PUT hello", "a chunked body reaches the product whole"
        assert exchange(url, "OPTIONS", "/").status == 501, "the product's answer, not the proxy's"
    # Why sorted: a line is written once the answer is sent, so a large one can land after the next.
    entries = sorted(products.log(directory), key=lambda e: int(str(e["id"]).split("-")[1]))
    assert [(e["id"], e["method"], e["path"], e["status"]) for e in entries] == [
        ("abcd1234-1", "GET", "/", 200),
        ("abcd1234-2", "POST", "/items", 303),
        ("abcd1234-3", "GET", "/big", 200),
        ("abcd1234-4", "GET", "/stream", 200),
        ("abcd1234-5", "GET", "/empty", 204),
        ("abcd1234-6", "HEAD", "/", 200),
        ("abcd1234-7", "PUT", "/other", 200),
        ("abcd1234-8", "OPTIONS", "/", 501),
    ]
    probes = directory / "probes"
    assert entries[2]["cut"] is True
    assert len((probes / "abcd1234-3.response").read_bytes()) == acceptance_proxy.CAP
    assert entries[1]["cut"] is False
    sent = (probes / "abcd1234-2.request").read_bytes()
    assert sent.startswith(b"POST /items HTTP/1.1\r\n")
    assert sent.endswith(b"\r\n\r\nlink=x")
    answer = (probes / "abcd1234-1.response").read_bytes()
    assert hashlib.sha256(answer).hexdigest() == entries[0]["responseSha256"]
    assert b"There is nothing left to read." in answer


def test_the_host_reaches_the_product_and_headers_about_one_connection_do_not(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    with products.serving(tmp_path) as upstream, proxied(tmp_path, upstream, monkeypatch) as url:
        headers = {"X-Kept": "yes", "Keep-Alive": "5", "Proxy-Authorization": "secret"}
        seen = json.loads(exchange(url, "GET", "/echo", headers=headers).body)
    assert seen["Host"] == url.split("//")[1].rstrip("/"), "the product sees the address it gave"
    assert seen["X-Kept"] == "yes"
    assert "Keep-Alive" not in seen
    assert "Proxy-Authorization" not in seen


def test_an_answer_that_breaks_off_is_logged_as_broken(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    directory = tmp_path / "session"
    with (
        products.serving(tmp_path) as upstream,
        proxied(tmp_path, upstream, monkeypatch) as url,
        pytest.raises(http.client.IncompleteRead),
    ):
        exchange(url, "GET", "/break")
    (entry,) = products.log(directory)
    assert entry["status"] == 200
    assert str(entry["error"]).startswith("the answer broke off: IncompleteRead")


def test_a_product_that_does_not_answer_is_a_502_the_log_names(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    nothing = f"http://127.0.0.1:{gatelib.free_port()}/"
    with proxied(tmp_path, nothing, monkeypatch) as url:
        status, body = products.request(url)
    assert status == 502
    assert body.startswith(b"the product did not answer: ConnectionRefusedError")
    (entry,) = products.log(tmp_path / "session")
    assert (entry["status"], str(entry["error"])[:22]) == (502, "ConnectionRefusedError")


def test_the_seal_says_what_was_written_and_nothing_is_written_after_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(acceptance_proxy, "SETTLE", 0.2)
    directory = tmp_path / "session"
    with products.serving(tmp_path) as upstream, proxied(tmp_path, upstream, monkeypatch) as url:
        products.request(url)
        time.sleep(acceptance_proxy.WATCH * 2)
        assert products.request(url)[0] == 200, "idle while its parent lives, it keeps serving"
    seal = json.loads((directory / "seal.json").read_text(encoding="utf-8"))
    data = (directory / "requests.jsonl").read_bytes()
    assert (seal["session"], seal["requests"]) == (SESSION, 2)
    assert seal["sha256"] == hashlib.sha256(data).hexdigest()
    log = acceptance_proxy.Log(tmp_path / "again", "x")
    log.issue()
    log.seal("again")
    log.finish({"id": "x-1"})
    assert (tmp_path / "again" / "requests.jsonl").read_bytes() == b"", "it waited, then sealed"


def run_proxy(tmp_path: Path, *args: str, env: dict[str, str]) -> subprocess.Popen[bytes]:
    return subprocess.Popen(  # noqa: S603 # -- this interpreter and the proxy script
        [sys.executable, str(PROXY), *args],
        env={**os.environ, **env},
        cwd=tmp_path,
        start_new_session=True,
    )


@pytest.mark.parametrize("signum", gatelib.INTERRUPTS)
def test_the_null_product_answers_like_nothing_and_stops_on_a_signal(
    tmp_path: Path, signum: signal.Signals
) -> None:
    port = gatelib.free_port()
    url = f"http://127.0.0.1:{port}/"
    process = run_proxy(tmp_path, "--null", env={"PORT": str(port)})
    try:
        while not gatelib.listening(url):
            assert process.poll() is None
            time.sleep(0.02)
        page = exchange(url, "GET", "/anything")
        assert (page.status, page.body) == (200, acceptance_proxy.NOTHING)
        saved = exchange(url, "POST", "/items", body=b"link=x")
        assert (saved.status, saved.headers["Location"], saved.body) == (303, "/", b"")
        head = exchange(url, "HEAD", "/")
        assert (head.status, head.body) == (200, b"")
    finally:
        process.send_signal(signum)
        assert process.wait(timeout=30) == 0
    assert not gatelib.listening(url)


def test_the_proxy_stops_and_seals_when_whoever_started_it_is_gone(tmp_path: Path) -> None:
    directory = tmp_path / "session"
    directory.mkdir()
    (directory / "session.json").write_text(json.dumps({"session": SESSION}), encoding="utf-8")
    pid = tmp_path / "pid"
    nothing = f"http://127.0.0.1:{gatelib.free_port()}/"
    started = f"{shlex.quote(sys.executable)} {shlex.quote(str(PROXY))} {directory}"
    parent = subprocess.Popen(  # noqa: S603 # -- a fixed shell line this test wrote
        ["bash", "-c", f"{started} & echo $! > {pid}; read -r"],  # noqa: S607 # -- bash from PATH
        env={**os.environ, gatelib.READY_ENV: nothing},
        stdin=subprocess.PIPE,
        start_new_session=True,
    )
    products.wait_for(directory / "proxy.json")
    url = json.loads((directory / "proxy.json").read_text(encoding="utf-8"))["url"]
    assert products.request(url)[0] == 502
    parent.communicate(b"\n", timeout=30)
    assert gone(pid_of(pid), wait=30), "the proxy outlived the process that started it"
    seal = json.loads((directory / "seal.json").read_text(encoding="utf-8"))
    assert seal["requests"] == 1
    assert not gatelib.listening(url)


def test_a_chunked_body_with_a_trailer_reaches_the_product_whole(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    with products.serving(tmp_path) as upstream, proxied(tmp_path, upstream, monkeypatch) as url:
        host, port = url.split("//")[1].rstrip("/").split(":")
        with socket.create_connection((host, int(port)), timeout=30) as raw:
            raw.sendall(
                b"POST /other HTTP/1.1\r\nHost: x\r\nTransfer-Encoding: chunked\r\n"
                b"Connection: close\r\n\r\n5;note=x\r\nhello\r\n0\r\nX-Sum: 1\r\n\r\n"
            )
            answer = b"".join(iter(lambda: raw.recv(65536), b""))
    assert answer.endswith(b"\r\n\r\nPOST hello")


def test_a_client_that_hangs_up_mid_request_does_not_stop_the_proxy(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    with products.serving(tmp_path) as upstream, proxied(tmp_path, upstream, monkeypatch) as url:
        host, port = url.split("//")[1].rstrip("/").split(":")
        with socket.create_connection((host, int(port))) as hung:
            hung.sendall(b"POST /items HTTP/1.1\r\nTransfer-Encoding: chunked\r\n\r\nzz\r\n")
        assert products.request(url)[0] == 200
