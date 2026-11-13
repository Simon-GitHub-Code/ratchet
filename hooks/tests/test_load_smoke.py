"""``load_smoke``: concurrent requests judged on errors only, against an in-test server."""

import argparse
import http.client
import http.server
import socket
import ssl
import threading
from collections.abc import Callable
from pathlib import Path
from urllib.parse import urlsplit

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


def held(url: str, *taken: gatelib.Connection) -> gatelib.Stopping:
    """What the gate says, once asked, while it holds the listener ``url`` goes to."""
    port = urlsplit(url).port or 80
    return gatelib.Stopping({port: None}, listeners.ELSEWHERE, frozenset(taken), answered=True)


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
    url = closed_port()
    args = ["--until-stopped", "--url", url, "--min-requests", "0", "--seconds", "0.3"]
    before = smoke(*args, capsys=capsys)
    gatelib.write_stopping(marker, held(url))
    after = smoke(*args, capsys=capsys)
    assert before[0] == 1
    assert "ConnectionRefusedError" in before[1]
    assert after == (0, "load-smoke: green\n")


def tried(client_port: int) -> gatelib.Connection:
    return gatelib.Connection("127.0.0.1", 443, "127.0.0.1", client_port)


def test_a_failure_after_the_signal_is_closed_when_refused_and_cut_otherwise(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    marker = tmp_path / "stopping"
    goal = load_smoke.Target("GET", "https://127.0.0.1/", None, None)
    named = gatelib.Stopping({443: None}, listeners.ELSEWHERE, frozenset({tried(40000)}))

    def fail(error: OSError, *clients: int, naming: gatelib.Stopping = named) -> None:
        """``http_status`` failing once the signal landed, after trying ``clients`` ports."""

        def http_status(
            *_: object, connecting: Callable[[gatelib.Connection], None], **__: object
        ) -> int:
            for client in clients:
                connecting(tried(client))
            gatelib.write_stopping(marker, naming)  # the signal lands while the request is out
            raise error

        monkeypatch.setattr(gatelib, "http_status", http_status)

    # Why: a refused connect never reached the service, so it dropped nothing.
    fail(ConnectionRefusedError(), 40000)
    with pytest.raises(load_smoke.Closed):
        load_smoke.attempt(goal, 1, marker)
    # Any other failure may be one the service accepted: whose it was is judged at the end, by
    # whether the gate had named the last connection tried when the client saw it fail.
    fail(ConnectionResetError(), 39999, 40000)
    with pytest.raises(load_smoke.Cutoff) as cutoff:
        load_smoke.attempt(goal, 1, marker)
    assert cutoff.value.cut == load_smoke.Cut(
        "GET https://127.0.0.1/", "ConnectionResetError", 443, named=True, late=True, lost=True
    )
    # With no connect tried there is no connection to match, so it counts.
    fail(OSError("no address"))
    assert load_smoke.attempt(goal, 1, marker) == (None, "GET https://127.0.0.1/: OSError")


def test_a_name_the_gate_writes_only_after_the_client_saw_the_reset_is_not_the_gates_reset(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Why: the gate names a connection before it resets it. A service that resets a request,
    # waits for the client to let its port go and then connects from that same address and
    # port gets the gate to name a connection exactly like the one it dropped, but too late.
    marker = tmp_path / "stopping"
    gatelib.write_stopping(marker, gatelib.Stopping({443: None}, listeners.ELSEWHERE, frozenset()))
    goal = load_smoke.Target("GET", "https://127.0.0.1/", None, None)

    def http_status(
        *_: object, connecting: Callable[[gatelib.Connection], None], **__: object
    ) -> int:
        connecting(tried(40000))
        raise ConnectionResetError

    monkeypatch.setattr(gatelib, "http_status", http_status)
    with pytest.raises(load_smoke.Cutoff) as cutoff:
        load_smoke.attempt(goal, 1, marker)
    forged = gatelib.Stopping({443: None}, listeners.ELSEWHERE, frozenset({tried(40000)}))
    assert load_smoke.judge([cutoff.value.cut], forged, [443])[0] == {
        "GET https://127.0.0.1/: ConnectionResetError": 1
    }


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
    taken: set[gatelib.Connection] = set()
    lock = threading.Lock()

    def handle(handler: http.server.BaseHTTPRequestHandler) -> None:
        if as_gate:
            with lock:
                taken.add(
                    gatelib.connection(handler.connection.getsockname(), handler.client_address)
                )
                gatelib.write_stopping(marker, held(url, *taken))
        handler.connection.setsockopt(socket.SOL_SOCKET, socket.SO_LINGER, listeners.RESET)
        handler.connection.close()

    with servers.server(handle) as url:
        gatelib.write_stopping(marker, held(url))
        args = ["--seconds", "0.3", "--concurrency", "4", "--min-requests", "0"]
        return smoke("--until-stopped", "--url", f"{url}/", *args, capsys=capsys)


def test_a_connection_the_gate_took_and_reset_is_not_counted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    # Why: the gate takes what waited when the service stopped listening and what arrived after,
    # so the note says both; "still waiting" alone was untrue of a connection that came later.
    code, out = cut_after_signal(tmp_path, monkeypatch, capsys, as_gate=True)
    assert out == (
        "load-smoke: 4 connection(s) reset after the stop signal are not counted, since the "
        "service never accepted them. The gate held the service's listener and, once no process "
        "of the service held it, took each connection waiting there and reset it. Each was "
        "waiting when the service stopped listening, or arrived after.\n"
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


def test_a_green_run_judged_the_older_way_says_so_too(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    # Why: the less exact rule can be green on a service that dropped a request sent just after
    # the signal, so a green verdict reached by it says so as a red one does.
    marker, _ = until_stopped(tmp_path, monkeypatch)
    told = gatelib.Stopping({}, "the host is not Linux", frozenset(), answered=True)
    gatelib.write_stopping(marker, told)
    with servers.server(handle) as url:
        code, out = smoke("--until-stopped", "--url", f"{url}/ok", *SHORT, capsys=capsys)
    port = urlsplit(url).port
    assert (
        f"port {port} is judged the older, less exact way, because the host is not Linux. " in out
    )
    assert code == 0


def test_where_the_gate_holds_no_listener_the_smoke_says_it_judged_the_older_way() -> None:
    early, late = (
        load_smoke.Cut("GET u", "ConnectionResetError", 80, named=False, late=late, lost=True)
        for late in (False, True)
    )
    told = gatelib.Stopping({}, "no Linux", frozenset())
    errors, notes = load_smoke.judge([early, late], told, [80, 8080, 80])
    assert errors == {"GET u: ConnectionResetError": 1}, "only the request sent before the signal"
    assert notes == [
        (
            "load-smoke: ports 80, 8080 are judged the older, less exact way, because no Linux. A "
            "reset after the stop signal then counts as a dropped request only when its request "
            "started before the signal. So a service that stops correctly can be red here, and one "
            "that drops a request sent just after the signal can be green."
        )
    ]


def test_each_port_is_judged_by_what_the_gate_knows_of_it() -> None:
    # Why: one listener shut down, or a port no process of the service listened on before the
    # signal, says nothing of the listener the gate still holds; only those ports lose exactness.
    told = gatelib.Stopping({80: None, 81: listeners.SHUT}, "none there", frozenset())
    cuts = [
        load_smoke.Cut(
            f"GET {port}", "ConnectionResetError", port, named=False, late=True, lost=True
        )
        for port in (80, 81, 82)
    ]
    errors, notes = load_smoke.judge(cuts, told, [80, 81, 82])
    assert errors == {"GET 80: ConnectionResetError": 1}, "the held port alone is judged exactly"
    assert [note.split(". ")[0] for note in notes] == [
        f"load-smoke: port 81 is judged the older, less exact way, because {listeners.SHUT}",
        "load-smoke: port 82 is judged the older, less exact way, because none there",
    ]


def mains_verdict(
    goal: load_smoke.Target, error: Exception, *, at_start: bool, at_failure: bool
) -> str | None:
    """What main's load smoke said of one failed request, or ``None`` when it did not count it.

    Its ``attempt`` before the gate held listeners, verbatim but for the marker: ``at_start`` and
    ``at_failure`` are whether the stop marker existed when the request started and failed.
    """
    after_signal = at_start
    try:
        raise error
    except TimeoutError:
        return f"{goal.label()}: timed out after 1s"
    except (OSError, http.client.HTTPException) as caught:
        raced = isinstance(caught, ConnectionRefusedError) and at_failure
        if (after_signal and isinstance(caught, ConnectionError)) or raced:
            return None
        return f"{goal.label()}: {type(caught).__name__}"


FAILURES: list[Exception] = [
    TimeoutError(),
    ConnectionRefusedError(),
    ConnectionResetError(),
    ConnectionAbortedError(),
    BrokenPipeError(),
    http.client.RemoteDisconnected(),
    http.client.IncompleteRead(b""),
    http.client.BadStatusLine("x"),
    ssl.SSLEOFError(),
    OSError("no route"),
]


@pytest.mark.parametrize("error", FAILURES, ids=lambda error: type(error).__name__)
@pytest.mark.parametrize(("at_start", "at_failure"), [(False, False), (False, True), (True, True)])
def test_where_the_gate_holds_no_listener_a_failure_is_judged_exactly_as_main_judged_it(
    error: Exception,
    *,
    at_start: bool,
    at_failure: bool,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Why: without a copy of the listener the gate cannot tell a connection the service accepted
    # from one it never did, so the stop is judged by main's rule, never stricter and never looser.
    marker = tmp_path / "stopping"
    fallback = gatelib.Stopping({}, "the gate holds no listener", frozenset())
    goal = load_smoke.Target("GET", "http://127.0.0.1:8000/work", None, None)

    def http_status(
        *_: object, connecting: Callable[[gatelib.Connection], None], **__: object
    ) -> int:
        connecting(gatelib.Connection("127.0.0.1", 8000, "127.0.0.1", 40000))
        if at_failure:
            gatelib.write_stopping(marker, fallback)
        raise error

    monkeypatch.setattr(gatelib, "http_status", http_status)
    if at_start:
        gatelib.write_stopping(marker, fallback)
    try:
        said = load_smoke.attempt(goal, 1, marker)[1]
    except load_smoke.Closed:
        said = None
    except load_smoke.Cutoff as cutoff:
        said = next(iter(load_smoke.judge([cutoff.cut], fallback, [8000])[0]), None)
    assert said == mains_verdict(goal, error, at_start=at_start, at_failure=at_failure)


def test_an_error_status_or_a_timeout_after_the_signal_is_still_red(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    marker, _ = until_stopped(tmp_path, monkeypatch)
    with servers.server(handle) as url:
        gatelib.write_stopping(marker, held(url))
        code, out = smoke("--until-stopped", "--url", f"{url}/boom", *SHORT, capsys=capsys)
    assert code == 1
    assert "x GET " in out
    assert "HTTP 500" in out
