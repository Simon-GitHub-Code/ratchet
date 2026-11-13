"""``load_smoke``: concurrent requests judged on errors only, against an in-test server."""

import argparse
import http.server
import socket
import threading
from collections.abc import Callable
from pathlib import Path
from types import SimpleNamespace

import pytest

import gatelib
import listeners
import load_smoke
from tests import servers

SHORT = ["--seconds", "0.3", "--concurrency", "4", "--min-requests", "4"]


def handle(handler: http.server.BaseHTTPRequestHandler) -> None:
    path = handler.path
    if path == "/slow":
        RELEASE.wait(30)
    if path.startswith(("/boom", "/e")):
        servers.reply(handler, 500)
    elif path == "/missing":
        servers.reply(handler, 404)
    elif path == "/created":
        body = getattr(handler, "body", b"")
        servers.reply(handler, 201 if handler.command == "POST" and body == b'{"a": 1}' else 400)
    else:
        servers.reply(handler, 200)


RELEASE = threading.Event()
HEALTH_ONLY = "load-smoke: health only: readiness is the one route requested (--health-only)"


@pytest.fixture(autouse=True)
def inside_with_service(monkeypatch: pytest.MonkeyPatch) -> None:
    """The readiness URL as ``with_service.py`` exports it; no test's route is that one."""
    monkeypatch.setenv(gatelib.READY_ENV, "http://127.0.0.1:1/ready")


def smoke(*args: str, capsys: pytest.CaptureFixture[str]) -> tuple[int, str]:
    code = load_smoke.main(["load_smoke.py", *args])
    return code, capsys.readouterr().out


def test_green_on_a_healthy_service_and_the_latency_is_printed(
    capsys: pytest.CaptureFixture[str],
) -> None:
    with servers.server(handle) as url:
        code, out = smoke(
            "--url", f"{url}/ok", "--url", f"GET {url}/other 200", *SHORT, capsys=capsys
        )
    lines = out.splitlines()
    assert code == 0
    assert lines[0].startswith("load-smoke: ")
    assert "answered; latency ms p50 " in lines[0]
    assert lines[0].endswith("(printed, not judged)")
    assert lines[1] == "load-smoke: green"


@pytest.mark.parametrize("spec", ["{url}/health", "POST {url}/health 200", "{url}/health 200"])
def test_a_smoke_that_requests_nothing_but_readiness_is_red_before_it_sends_anything(
    capsys: pytest.CaptureFixture[str], spec: str
) -> None:
    dead = f"http://127.0.0.1:{gatelib.free_port()}"
    code, out = smoke(
        "--url", spec.format(url=dead), "--ready", f"{dead}/health", *SHORT, capsys=capsys
    )
    assert code == 1
    assert out == (
        f"every --url is the readiness URL {dead}/health: the smoke overlaps no request a user"
        " makes; add a --url per route worth overlapping, or pass --health-only while readiness"
        " is the service's only route\nload-smoke: red\n"
    )


def test_a_skeleton_with_one_route_says_health_only_and_is_green(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    with servers.server(handle) as url:
        monkeypatch.setenv(gatelib.READY_ENV, f"{url}/health")
        code, out = smoke("--health-only", *SHORT, capsys=capsys)
        assert code == 0
        assert out.startswith(HEALTH_ONLY)
        assert out.endswith("load-smoke: green\n")
        code, out = smoke("--health-only", "--url", f"{url}/health", *SHORT, capsys=capsys)
        assert (code, out.startswith(HEALTH_ONLY)) == (0, True)
        code, out = smoke("--health-only", "--url", f"{url}/ok", *SHORT, capsys=capsys)
        assert code == 0
        assert HEALTH_ONLY not in out, "a real route is requested, so the flag says nothing"
        code, out = smoke("--url", f"{url}/health", "--url", f"{url}/ok", *SHORT, capsys=capsys)
        assert code == 0, "readiness beside a real route is fine"


def test_the_readiness_url_is_one_place_however_it_is_written() -> None:
    assert load_smoke.same_place("http://h:1", "http://h:1/")
    assert load_smoke.same_place("http://h:1/health?x=1", "http://h:1/health?x=1")
    assert not load_smoke.same_place("http://h:1/health?x=1", "http://h:1/health")
    assert not load_smoke.same_place("http://h:1/links", "http://h:1/health")
    assert not load_smoke.same_place("http://h:2/health", "http://h:1/health")


@pytest.mark.parametrize(
    ("args", "said"),
    [
        (["--url", "http://127.0.0.1:9/"], "with_service.py exports it as RATCHET_READY"),
        (["--url", "http://127.0.0.1:9/", "--ready", "ftp://h/"], "not an http or https URL"),
        (["--ready", "http://127.0.0.1:9/"], "name a --url per route, or --health-only"),
    ],
)
def test_a_smoke_that_cannot_tell_readiness_from_a_route_is_a_usage_error(
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
    *,
    args: list[str],
    said: str,
) -> None:
    monkeypatch.delenv(gatelib.READY_ENV)
    with pytest.raises(SystemExit) as caught:
        load_smoke.main(["load_smoke.py", *args])
    assert caught.value.code == 2
    assert said in capsys.readouterr().err


def test_a_5xx_is_red_and_counted(capsys: pytest.CaptureFixture[str]) -> None:
    with servers.server(handle) as url:
        code, out = smoke("--url", f"{url}/ok", "--url", f"{url}/boom", *SHORT, capsys=capsys)
    assert code == 1
    assert f" x GET {url}/boom: HTTP 500" in out
    assert out.endswith("load-smoke: red\n")


def test_the_status_wanted_is_exact_and_the_default_is_any_success(
    capsys: pytest.CaptureFixture[str],
) -> None:
    with servers.server(handle) as url:
        code, out = smoke("--url", f"{url}/missing", *SHORT, capsys=capsys)
        assert code == 1
        assert f"GET {url}/missing: HTTP 404" in out
        code, out = smoke("--url", f"{url}/missing 404", *SHORT, capsys=capsys)
        assert (code, out.splitlines()[-1]) == (0, "load-smoke: green"), out
        code, out = smoke("--url", f"{url}/ok 201", *SHORT, capsys=capsys)
        assert code == 1
        assert f"GET {url}/ok: HTTP 200" in out


def test_a_method_and_a_json_body_are_sent(capsys: pytest.CaptureFixture[str]) -> None:
    with servers.server(handle) as url:
        right = f'POST {url}/created 201 {{"a": 1}}'
        assert smoke("--url", right, *SHORT, capsys=capsys)[0] == 0
        assert smoke("--url", f"{url}/created", *SHORT, capsys=capsys)[0] == 1, "a GET is refused"


def test_a_refused_connection_is_red_and_so_is_the_floor(
    capsys: pytest.CaptureFixture[str],
) -> None:
    dead = f"http://127.0.0.1:{gatelib.free_port()}/"
    code, out = smoke("--url", dead, *SHORT, capsys=capsys)
    assert code == 1
    assert f"GET {dead}: ConnectionRefusedError" in out
    assert "0 requests answered, under the floor of 4" in out
    assert "answered; latency" not in out


def test_a_request_that_times_out_is_red(capsys: pytest.CaptureFixture[str]) -> None:
    RELEASE.clear()
    try:
        with servers.server(handle) as url:
            args = ["--url", f"{url}/slow", "--seconds", "0.1", "--concurrency", "2"]
            code, out = smoke(*args, "--timeout", "0.2", "--min-requests", "0", capsys=capsys)
            RELEASE.set()
    finally:
        RELEASE.set()
    assert code == 1
    assert f"x GET {url}/slow: timed out after 0.2s" in out


def test_a_floor_the_service_cannot_reach_is_red(capsys: pytest.CaptureFixture[str]) -> None:
    with servers.server(handle) as url:
        code, out = smoke(
            "--url",
            f"{url}/ok",
            "--seconds",
            "0.1",
            "--concurrency",
            "1",
            "--min-requests",
            "1000000",
            capsys=capsys,
        )
    assert code == 1
    assert "under the floor of 1000000: the service hangs or is too slow" in out


def test_only_the_commonest_errors_are_listed(capsys: pytest.CaptureFixture[str]) -> None:
    with servers.server(handle) as url:
        urls = [arg for n in range(7) for arg in ("--url", f"{url}/e{n}")]
        code, out = smoke(
            *urls, "--seconds", "0.4", "--concurrency", "7", "--min-requests", "0", capsys=capsys
        )
    assert code == 1
    assert out.count(" x GET ") == 5
    assert "and 2 more kinds of error" in out


def test_a_url_spec_is_parsed_or_refused() -> None:
    assert load_smoke.target("http://h/p") == load_smoke.Target("GET", "http://h/p", None, None)
    assert load_smoke.target(" DELETE https://h/p 204 ") == load_smoke.Target(
        "DELETE", "https://h/p", 204, None
    )
    assert load_smoke.target('PUT http://h/p 200 {"a": 1} x') == load_smoke.Target(
        "PUT", "http://h/p", 200, b'{"a": 1} x'
    )
    for bad in ("", "GET", "ftp://h/p", "GET  http://h/p", "http://h/p 20"):
        with pytest.raises(argparse.ArgumentTypeError, match=r"is not|not an http"):
            load_smoke.target(bad)


def test_percentiles_never_run_off_the_end() -> None:
    values = [1.0, 2.0, 3.0, 4.0]
    assert [load_smoke.percentile(values, f) for f in (0.5, 0.95, 0.99, 1.0)] == [
        3.0,
        4.0,
        4.0,
        4.0,
    ]
    assert load_smoke.percentile([7.0], 0.99) == 7.0


@pytest.mark.parametrize("flag", ["--concurrency", "--seconds", "--timeout"])
def test_a_non_positive_option_is_a_usage_error(flag: str) -> None:
    with pytest.raises(SystemExit) as caught:
        load_smoke.main(["load_smoke.py", "--url", "http://127.0.0.1:9/", flag, "0"])
    assert caught.value.code == 2


def test_a_live_deployment_is_probed_with_no_service_booted(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.delenv(gatelib.READY_ENV)
    with servers.server(handle) as url:
        green = smoke("--ready", f"{url}/health", "--url", f"{url}/ok", *SHORT, capsys=capsys)
        red = smoke("--ready", f"{url}/health", "--url", f"{url}/boom", *SHORT, capsys=capsys)
    assert green[0] == 0
    assert (red[0], "x GET " in red[1], red[1].endswith("load-smoke: red\n")) == (1, True, True)


def closed_port() -> str:
    return f"http://127.0.0.1:{gatelib.free_port()}/gone"


def until_stopped(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, Path]:
    """The stop marker and the sending path, exported as ``with_service.py --graceful`` does."""
    marker, sending = tmp_path / "stopping", tmp_path / "sending"
    monkeypatch.setenv(gatelib.STOPPING_ENV, str(marker))
    monkeypatch.setenv(gatelib.SENDING_ENV, str(sending))
    return marker, sending


@pytest.mark.parametrize("missing", [gatelib.STOPPING_ENV, gatelib.SENDING_ENV])
def test_until_stopped_needs_both_paths_with_service_exports(
    missing: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    until_stopped(tmp_path, monkeypatch)
    monkeypatch.delenv(missing)
    with pytest.raises(SystemExit) as exit_:
        load_smoke.main(["load_smoke.py", "--until-stopped", "--url", closed_port()])
    assert exit_.value.code == 2


def test_the_smoke_says_it_is_sending_so_the_signal_can_wait_for_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _, sending = until_stopped(tmp_path, monkeypatch)
    with servers.server(handle) as url:
        plain = smoke("--url", f"{url}/ok", *SHORT, capsys=capsys)
        assert plain[0] == 0
        assert not sending.exists(), "only --until-stopped runs inside the graceful check"
        code, _ = smoke("--until-stopped", "--url", f"{url}/ok", *SHORT, capsys=capsys)
    assert code == 0
    assert sending.exists()


def test_a_refusal_after_the_signal_is_expected_and_before_it_is_an_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    marker, _ = until_stopped(tmp_path, monkeypatch)
    args = ["--until-stopped", "--url", closed_port(), "--min-requests", "0", "--seconds", "0.3"]
    before = smoke(*args, capsys=capsys)
    marker.touch()
    after = smoke(*args, capsys=capsys)
    assert before[0] == 1
    assert "ConnectionRefusedError" in before[1]
    assert after == (0, "load-smoke: green\n")


def test_a_failure_after_the_signal_is_closed_when_refused_and_cut_otherwise(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    marker = tmp_path / "stopping"
    goal = load_smoke.Target("GET", "https://127.0.0.1/", None, None)

    def fail(error: OSError, *clients: int) -> None:
        """``http_status`` failing once the signal landed, after trying ``clients`` ports."""

        def http_status(*_: object, connecting: Callable[[object], None], **__: object) -> int:
            for client in clients:
                connecting(SimpleNamespace(getsockname=lambda port=client: ("127.0.0.1", port)))
            marker.touch()  # the signal lands while the request is in flight
            raise error

        monkeypatch.setattr(gatelib, "http_status", http_status)

    # Why: a refused connect never reached the service, so it dropped nothing.
    fail(ConnectionRefusedError(), 40000)
    with pytest.raises(load_smoke.Closed):
        load_smoke.attempt(goal, 1, marker)
    # Any other failure may be one the service accepted: whose it was is judged at the end, by
    # the port of the last connect tried.
    fail(ConnectionResetError(), 39999, 40000)
    with pytest.raises(load_smoke.Cutoff) as cutoff:
        load_smoke.attempt(goal, 1, marker)
    assert cutoff.value.cut == load_smoke.Cut(
        "GET https://127.0.0.1/", "ConnectionResetError", 443, 40000
    )
    # With no connect tried there is no connection to match, so it counts.
    fail(OSError("no address"))
    assert load_smoke.attempt(goal, 1, marker) == (None, "GET https://127.0.0.1/: OSError")


def cut_after_signal(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    *,
    as_gate: bool,
) -> tuple[int, str]:
    """The smoke after the signal against a server that resets every connection.

    With ``as_gate`` it first writes the connection's ports to the marker, as the gate does with
    a connection it took from a listener the service let go of; without, it is a service
    dropping requests it accepted.
    """
    marker, _ = until_stopped(tmp_path, monkeypatch)
    gatelib.write_stopping(marker, gatelib.Stopping(frozenset(), None))
    taken: set[tuple[int, int]] = set()
    lock = threading.Lock()

    def handle(handler: http.server.BaseHTTPRequestHandler) -> None:
        if as_gate:
            with lock:
                taken.add((handler.connection.getsockname()[1], handler.client_address[1]))
                gatelib.write_stopping(marker, gatelib.Stopping(frozenset(taken), None))
        handler.connection.setsockopt(socket.SOL_SOCKET, socket.SO_LINGER, listeners.RESET)
        handler.connection.close()

    with servers.server(handle) as url:
        args = ["--seconds", "0.3", "--concurrency", "4", "--min-requests", "0"]
        return smoke("--until-stopped", "--url", f"{url}/", *args, capsys=capsys)


def test_a_connection_the_gate_took_and_reset_is_not_counted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    code, out = cut_after_signal(tmp_path, monkeypatch, capsys, as_gate=True)
    assert out == (
        "load-smoke: 4 connection(s) reset after the stop signal are not counted: the service "
        "never accepted them, they were still waiting on its listener when it stopped listening\n"
        "load-smoke: green\n"
    )
    assert code == 0


def test_a_connection_the_service_accepted_and_reset_after_the_signal_is_red(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    code, out = cut_after_signal(tmp_path, monkeypatch, capsys, as_gate=False)
    assert out.startswith("4 x GET http://127.0.0.1:"), out
    assert "not counted" not in out
    assert code == 1


def test_resets_count_with_the_reason_when_the_gate_could_not_take_the_listener() -> None:
    cuts = [load_smoke.Cut("GET u", "ConnectionResetError", 80, client) for client in (1, 2)]
    errors, notes = load_smoke.judge(cuts, gatelib.Stopping(frozenset({(80, 1)}), "no Linux"))
    assert errors == {"GET u: ConnectionResetError": 1}
    assert notes[0].startswith("load-smoke: 1 connection(s) reset after the stop signal are not")
    assert notes[1] == (
        "load-smoke: every reset after the stop signal counts as a dropped request here, since "
        "the gate could not tell which connections the service had accepted: no Linux. A service "
        "that stops correctly still resets connections that were waiting on its listener."
    )
    assert load_smoke.judge(cuts[:1], gatelib.Stopping(frozenset({(80, 1)}), "no Linux"))[1] == [
        notes[0]
    ], "with nothing counted there is nothing to explain"


def test_an_error_status_or_a_timeout_after_the_signal_is_still_red(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    marker, _ = until_stopped(tmp_path, monkeypatch)
    marker.touch()
    with servers.server(handle) as url:
        code, out = smoke("--until-stopped", "--url", f"{url}/boom", *SHORT, capsys=capsys)
    assert code == 1
    assert "x GET " in out
    assert "HTTP 500" in out
