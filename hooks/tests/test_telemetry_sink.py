"""``telemetry_sink``: an OTLP/HTTP receiver judged on what a real SDK export contains."""

import gzip
import http.client
import json
import os
import re
import signal
import sys
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import pytest

import telemetry_sink
from tests.servers import gone, pid_of

INTERRUPTS = (signal.SIGINT, signal.SIGTERM, signal.SIGHUP)
FIXTURES = Path(__file__).parent / "fixtures/otlp"
EMIT = """
import json, os, sys, urllib.request
KEYS = {"traces": ("resourceSpans", "scopeSpans", "spans"),
        "metrics": ("resourceMetrics", "scopeMetrics", "metrics"),
        "logs": ("resourceLogs", "scopeLogs", "logRecords")}
service = sys.argv[1]
for spec in sys.argv[2:]:
    if spec == "fail":
        sys.exit(5)
    signal, _, count = spec.partition(":")
    top, scope, record = KEYS[signal]
    attribute = {"key": "service.name", "value": {"stringValue": service}}
    records = [{}] * int(count or 1)
    body = {top: [{"resource": {"attributes": [attribute]}, scope: [{record: records}]}]}
    request = urllib.request.Request(
        os.environ["OTEL_EXPORTER_OTLP_ENDPOINT"] + "/v1/" + signal,
        data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json"},
    )
    urllib.request.urlopen(request).close()
"""


def varint(number: int) -> bytes:
    out = bytearray()
    while number > 0x7F:
        out.append(number & 0x7F | 0x80)
        number >>= 7
    return bytes([*out, number])


def field(number: int, payload: bytes) -> bytes:
    return varint(number << 3 | 2) + varint(len(payload)) + payload


def string_pair(key: bytes, value: bytes) -> bytes:
    return field(1, key) + field(2, field(1, value))


def export(service: bytes | None, records: int, *, scalars: bytes = b"") -> bytes:
    """An Export*ServiceRequest body: one resource block holding ``records`` records."""
    resource = field(1, string_pair(b"host.name", b"h")) + field(3, b"an entity ref")
    if service is not None:
        resource += field(1, string_pair(b"service.name", service))
    scope = scalars + b"".join(field(2, b"\x08\x01") for _ in range(records))
    return field(1, field(1, resource) + field(2, scope) + field(3, b"https://schema"))


def emit(service: str, *specs: str) -> list[str]:
    return [sys.executable, "-c", EMIT, service, *specs]


def sink(*args: str) -> int:
    return telemetry_sink.main(["telemetry_sink.py", *args])


@contextmanager
def receiving() -> Iterator[telemetry_sink.Receiver]:
    with telemetry_sink.Receiver(0) as receiver:
        thread = threading.Thread(target=receiver.serve_forever, args=(0.01,), daemon=True)
        thread.start()
        try:
            yield receiver
        finally:
            receiver.shutdown()
            thread.join()


def post(  # noqa: PLR0917 # -- a helper whose call sites read as one request
    receiver: telemetry_sink.Receiver, path: str, body: object, headers: dict[str, str]
) -> tuple[int, bytes, str]:
    connection = http.client.HTTPConnection("127.0.0.1", receiver.server_address[1], timeout=10)
    try:
        connection.request("POST", path, body=body, headers=headers, encode_chunked=True)
        response = connection.getresponse()
        return response.status, response.read(), response.getheader("Content-Type", "")
    finally:
        connection.close()


PROTOBUF = {"Content-Type": "application/x-protobuf"}
JSON = {"Content-Type": "application/json"}


def test_a_real_sdk_export_names_its_service_and_counts_its_records() -> None:
    for signal_name, records in (("traces", 4), ("metrics", 4), ("logs", 1)):
        data = (FIXTURES / f"{signal_name}.pb").read_bytes()
        assert telemetry_sink.proto_blocks(data) == [("inventory", records)], signal_name


def test_the_protobuf_walker_skips_scalars_and_refuses_what_is_not_protobuf() -> None:
    scalars = varint(3 << 3 | 0) + varint(300) + varint(4 << 3 | 1) + bytes(8)
    scalars += varint(5 << 3 | 5) + bytes(4)
    assert telemetry_sink.proto_blocks(export(b"api", 2, scalars=scalars)) == [("api", 2)]
    assert telemetry_sink.proto_blocks(export(None, 1)) == [(None, 1)]
    assert telemetry_sink.proto_blocks(field(2, b"x") + export(b"api", 0)) == [("api", 0)]
    assert telemetry_sink.proto_blocks(b"") == []
    for broken in (b"\x0a", b"\x0a\x05ab", b"\x0b", b"\x80"):
        with pytest.raises(ValueError, match=r"truncated|unknown wire"):
            telemetry_sink.proto_blocks(broken)


def test_a_resource_without_a_service_name_or_a_value_has_none() -> None:
    bare = field(1, field(1, field(1, field(1, b"service.name"))))
    assert telemetry_sink.proto_blocks(bare) == [("", 0)]
    other = field(1, field(1, field(1, string_pair(b"host.name", b"h"))))
    assert telemetry_sink.proto_blocks(other) == [(None, 0)]


def test_json_exports_are_read_leniently() -> None:
    document = {
        "resourceSpans": [
            {
                "resource": {
                    "attributes": [{"key": "service.name", "value": {"stringValue": "api"}}]
                },
                "scopeSpans": [{"spans": [{}, {}]}, {"spans": [{}]}, "junk"],
            },
            {
                "resource": {
                    "attributes": ["junk", {"key": "service.name", "value": {"intValue": 1}}]
                }
            },
            "junk",
        ]
    }
    assert telemetry_sink.json_blocks("traces", json.dumps(document).encode()) == [
        ("api", 3),
        (None, 0),
        (None, 0),
    ]
    assert telemetry_sink.json_blocks("logs", b'{"resourceLogs": 7}') == []
    assert telemetry_sink.json_blocks("metrics", b"[]") == []
    with pytest.raises(ValueError, match="Expecting"):
        telemetry_sink.json_blocks("traces", b"not json")


def test_the_receiver_takes_protobuf_gzip_chunked_and_json_exports() -> None:
    real = (FIXTURES / "traces.pb").read_bytes()
    with receiving() as receiver:
        assert post(receiver, "/v1/traces", real, PROTOBUF) == (200, b"", "application/x-protobuf")
        zipped = {**PROTOBUF, "Content-Encoding": "gzip"}
        assert post(receiver, "/v1/metrics?x=1", gzip.compress(export(b"api", 2)), zipped)[0] == 200
        chunked = {**PROTOBUF, "Transfer-Encoding": "chunked"}
        parts = iter([export(b"api", 1)[:5], export(b"api", 1)[5:]])
        assert post(receiver, "/v1/logs", parts, chunked)[0] == 200
        body = json.dumps({"resourceLogs": [{"scopeLogs": [{"logRecords": [{}]}]}]}).encode()
        assert post(receiver, "/v1/logs", body, JSON) == (200, b"{}", "application/json")
        assert receiver.blocks == {
            "traces": [("inventory", 4)],
            "metrics": [("api", 2)],
            "logs": [("api", 1), (None, 1)],
        }


def test_the_receiver_refuses_what_it_cannot_read_and_says_so() -> None:
    with receiving() as receiver:
        assert post(receiver, "/v1/other", b"", PROTOBUF)[0] == 404
        assert post(receiver, "/v1/traces", b"\x0b", PROTOBUF)[0] == 400
        assert (
            post(receiver, "/v1/traces", b"nope", {**PROTOBUF, "Content-Encoding": "gzip"})[0]
            == 400
        )
        assert post(receiver, "/v1/logs", b"{", JSON)[0] == 400
        assert (
            post(receiver, "/v1/logs", iter([b"zz"]), {**JSON, "Transfer-Encoding": "chunked"})[0]
            == 400
        )
        assert receiver.blocks == {"traces": [], "metrics": [], "logs": []}
        problems = receiver.judge(["traces"], "api")
        assert problems[0] == "traces: nothing arrived at the sink"
        assert len(problems) == 5
        assert problems[1].startswith("traces: an export could not be read: ")


def test_the_verdict_names_what_is_wrong_with_each_signal() -> None:
    with receiving() as receiver:
        receiver.blocks["traces"] = [("other", 3), (None, 1)]
        receiver.blocks["metrics"] = [("api", 0)]
        receiver.blocks["logs"] = [("other", 1), ("api", 2)]
        assert receiver.judge(["traces", "metrics", "logs"], "api") == [
            "traces: arrived for service.name ['(no service.name)', 'other'], expected 'api'",
            "metrics: only empty exports arrived",
        ]
        assert receiver.judge(["logs"], "api") == []


def test_green_when_every_required_signal_arrives_for_the_service(
    capsys: pytest.CaptureFixture[str],
) -> None:
    code = sink("--service", "api", "--", *emit("api", "traces", "metrics:3", "logs"))
    assert code == 0
    assert capsys.readouterr().out == (
        "telemetry_sink: traces, metrics, logs arrived for service.name 'api'\n"
        "telemetry_sink: green\n"
    )


def test_red_names_the_service_that_arrived_and_the_signal_that_did_not(
    capsys: pytest.CaptureFixture[str],
) -> None:
    code = sink(
        "--service", "api", "--wait", "0.1", "--", *emit("unknown_service:python", "traces")
    )
    out = capsys.readouterr().out
    assert code == 1
    assert "traces: arrived for service.name ['unknown_service:python'], expected 'api'" in out
    assert "metrics: nothing arrived at the sink" in out
    assert out.endswith("telemetry_sink: red\n")


def test_red_when_the_exports_are_empty_and_only_the_required_signals_are_judged(
    capsys: pytest.CaptureFixture[str],
) -> None:
    command = emit("api", "traces:0", "logs")
    assert (
        sink("--service", "api", "--require", " traces ,, logs", "--wait", "0.1", "--", *command)
        == 1
    )
    assert "traces: only empty exports arrived" in capsys.readouterr().out
    assert sink("--service", "api", "--require", "logs", "--", *command) == 0


def test_a_failing_command_is_red_even_when_the_telemetry_arrived(
    capsys: pytest.CaptureFixture[str],
) -> None:
    code = sink("--service", "api", "--require", "logs", "--", *emit("api", "logs", "fail"))
    assert code == 1
    assert "the command exited with code 5" in capsys.readouterr().out


def test_telemetry_that_arrives_after_the_command_ended_is_waited_for() -> None:
    delayed = "import time\ntime.sleep(0.3)" + EMIT
    later = (
        "import subprocess, sys\n"
        f"subprocess.Popen([sys.executable, '-c', {delayed!r}, 'api', 'logs'])"
    )
    command = (sys.executable, "-c", later)
    assert sink("--service", "api", "--require", "logs", "--wait", "20", "--", *command) == 0


def test_a_signal_stops_the_command_and_exits_with_it(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    marker = tmp_path / "command"
    script = (
        "import os, signal, sys, time\n"
        f"open({str(marker)!r}, 'w').write(str(os.getpid()))\n"
        f"os.kill({os.getpid()}, signal.SIGTERM)\ntime.sleep(60)"
    )
    code = sink("--service", "api", "--", sys.executable, "-c", script)
    assert code == 128 + signal.SIGTERM
    assert "interrupted by signal" in capsys.readouterr().out
    assert marker.exists()
    assert gone(pid_of(marker))


def test_the_receiver_thread_leaves_every_interrupt_to_the_main_thread(tmp_path: Path) -> None:
    # Why: the kernel hands a signal sent to the process to any thread that does not block it,
    # and Python runs the handler only on the main thread, which sleeps until the command exits:
    # an interrupt the receiver took would let the command run to its end.
    blocked: set[int] = set()
    seen = tmp_path / "seen"

    def watch_the_receiver() -> None:
        deadline = time.monotonic() + 10
        while not (started := set(threading.enumerate()) - running):
            time.sleep(0.01)
        status = Path(f"/proc/self/task/{started.pop().native_id}/status")
        while blocked != set(INTERRUPTS) and time.monotonic() < deadline:
            mask = int(re.findall(r"^SigBlk:\s*(\w+)$", status.read_text(), re.MULTILINE)[0], 16)
            blocked.update(s for s in INTERRUPTS if mask >> (s - 1) & 1)
            time.sleep(0.01)
        seen.touch()

    watcher = threading.Thread(target=watch_the_receiver, daemon=True)
    running = {*threading.enumerate(), watcher}
    watcher.start()
    wait = "import os, sys, time\nwhile not os.path.exists(sys.argv[1]): time.sleep(0.01)"
    code = sink("--service", "api", "--wait", "0", "--", sys.executable, "-c", wait, str(seen))
    assert code == 1
    assert blocked == set(INTERRUPTS)


def test_the_exporter_environment_points_at_the_sink_and_keeps_what_the_project_set(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for name in ("OTEL_EXPORTER_OTLP_PROTOCOL", "OTEL_BSP_SCHEDULE_DELAY"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_TRACES_ENDPOINT", "http://elsewhere:4318/v1/traces")
    monkeypatch.setenv("OTEL_METRIC_EXPORT_INTERVAL", "999")
    env = telemetry_sink.exporter_env(4321)
    assert env["OTEL_EXPORTER_OTLP_ENDPOINT"] == "http://127.0.0.1:4321"
    assert "OTEL_EXPORTER_OTLP_TRACES_ENDPOINT" not in env
    assert env["OTEL_EXPORTER_OTLP_PROTOCOL"] == "http/protobuf"
    assert env["OTEL_BSP_SCHEDULE_DELAY"] == "200"
    assert env["OTEL_METRIC_EXPORT_INTERVAL"] == "999"
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_PROTOCOL", "http/json")
    assert telemetry_sink.exporter_env(1)["OTEL_EXPORTER_OTLP_PROTOCOL"] == "http/json"


@pytest.mark.parametrize(
    "arguments",
    [
        ["--service", "api"],
        ["--service", "api", "--require", "spans", "--", "true"],
        ["--service", "api", "--require", ",", "--", "true"],
        ["--", "true"],
    ],
)
def test_usage_errors_exit_2(arguments: list[str]) -> None:
    with pytest.raises(SystemExit) as caught:
        sink(*arguments)
    assert caught.value.code == 2
