"""``load_smoke``: concurrent requests judged on errors only, against an in-test server."""

import argparse
import http.server
import threading

import pytest

import gatelib
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
