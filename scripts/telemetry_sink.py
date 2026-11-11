#!/usr/bin/env python3
"""The telemetry gate: does a service's telemetry arrive? Answered with no collector to install.

It listens on a local port as an OTLP/HTTP receiver, runs a command with the exporter pointed at
itself, and is green only when every required signal arrived non-empty and names the expected
``service.name``. Python 3.11 standard library only.

Usage::

    telemetry_sink.py --service NAME [--require traces,metrics,logs] [--wait S] [--port N] -- cmd...

The command is typically a service booted by ``with_service.py`` and a request against it, in
one line: ``telemetry_sink.py --service api -- with_service.py --boot ... --ready ... -- curl ...``.

The command runs with ``OTEL_EXPORTER_OTLP_ENDPOINT`` set to the sink and the per-signal endpoint
variables removed, so nothing in the project's environment redirects the export. When unset,
``OTEL_EXPORTER_OTLP_PROTOCOL`` becomes ``http/protobuf`` and the batch, log and metric export
intervals drop to a fraction of a second, so a short run exports before it ends. After the
command the sink waits up to ``--wait`` seconds (10) for what is still missing, then judges. The
SDK must flush on a clean shutdown, as the OpenTelemetry SDKs do.

``/v1/traces``, ``/v1/metrics`` and ``/v1/logs`` take protobuf or JSON, gzip or plain, with a
length or chunked. The protobuf is walked by hand for the two facts asked: the ``service.name``
resource attribute and the number of spans, metrics or log records under it. A red line says
which failed: nothing arrived, only empty exports arrived, or the signal named another service
(``unknown_service:python`` is an SDK never told its name).

What it does not check: it is not a schema validator. It does not read span names, attributes,
metric values, trace ids or the link from a log to its trace; the tier 1 telemetry tests do. It
runs no collector, so nothing is forwarded, retried or sampled. An exporter that speaks gRPC only
reaches nothing here: set ``OTEL_EXPORTER_OTLP_PROTOCOL`` to ``http/protobuf`` or ``http/json``.
It listens on loopback, so a service in a container needs a network that reaches the host.
"""

from __future__ import annotations

import argparse
import gzip
import json
import os
import sys
import threading
import time
import zlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import TYPE_CHECKING

import gatelib

if TYPE_CHECKING:
    from collections.abc import Iterator

SIGNALS = ("traces", "metrics", "logs")
PATHS = {f"/v1/{name}": name for name in SIGNALS}
JSON_KEYS = {
    "traces": ("resourceSpans", "scopeSpans", "spans"),
    "metrics": ("resourceMetrics", "scopeMetrics", "metrics"),
    "logs": ("resourceLogs", "scopeLogs", "logRecords"),
}
FAST_EXPORT = {
    "OTEL_BSP_SCHEDULE_DELAY": "200",
    "OTEL_BLRP_SCHEDULE_DELAY": "200",
    "OTEL_METRIC_EXPORT_INTERVAL": "500",
}
Block = tuple[str | None, int]


def varint(data: bytes, pos: int) -> tuple[int, int]:
    """The integer at ``pos`` and the position after it."""
    value = shift = 0
    while pos < len(data):
        byte = data[pos]
        value |= (byte & 0x7F) << shift
        pos += 1
        if byte < 0x80:  # noqa: PLR2004 # -- the continuation bit
            return value, pos
        shift += 7
    message = "truncated varint"
    raise ValueError(message)


def fields(data: bytes) -> Iterator[tuple[int, bytes]]:
    """Each length-delimited field of a protobuf message as (number, bytes); scalars are skipped."""
    pos = 0
    while pos < len(data):
        key, pos = varint(data, pos)
        number, wire = key >> 3, key & 7
        if wire == 2:  # noqa: PLR2004 # -- length-delimited
            size, pos = varint(data, pos)
            if pos + size > len(data):
                message = "truncated field"
                raise ValueError(message)
            yield number, data[pos : pos + size]
            pos += size
        elif wire == 0:
            pos = varint(data, pos)[1]
        elif wire in (1, 5):
            pos += 8 if wire == 1 else 4
        else:
            message = f"unknown wire type {wire}"
            raise ValueError(message)


def proto_service(resource: bytes) -> str | None:
    """``service.name`` of a Resource message: KeyValue.key is field 1, its AnyValue field 2."""
    for number, pair in fields(resource):
        if number != 1:
            continue
        parts = dict(fields(pair))
        if parts.get(1) == b"service.name":
            return dict(fields(parts.get(2, b""))).get(1, b"").decode("utf-8", errors="replace")
    return None


def proto_blocks(data: bytes) -> list[Block]:
    """The (service.name, record count) of each resource block.

    The three Export*ServiceRequest messages share a shape: field 1 repeats the resource block,
    whose field 1 is the Resource and whose field 2 repeats a scope block, whose field 2 repeats
    the spans, metrics or log records.
    """
    blocks: list[Block] = []
    for number, block in fields(data):
        if number != 1:
            continue
        name, count = None, 0
        for inner, payload in fields(block):
            if inner == 1:
                name = proto_service(payload)
            elif inner == 2:  # noqa: PLR2004 # -- the repeated scope block
                count += sum(1 for field, _ in fields(payload) if field == 2)  # noqa: PLR2004
        blocks.append((name, count))
    return blocks


def items(value: object) -> list[object]:
    return value if isinstance(value, list) else []


def mapping(value: object) -> dict[str, object]:
    return value if isinstance(value, dict) else {}


def json_blocks(signal: str, data: bytes) -> list[Block]:
    """The same blocks from an OTLP/JSON body."""
    top, scope, record = JSON_KEYS[signal]
    blocks: list[Block] = []
    for block in items(mapping(json.loads(data)).get(top)):
        name = None
        for attribute in items(mapping(mapping(block).get("resource")).get("attributes")):
            if mapping(attribute).get("key") == "service.name":
                value = mapping(mapping(attribute).get("value")).get("stringValue")
                name = value if isinstance(value, str) else None
        count = sum(
            len(items(mapping(part).get(record))) for part in items(mapping(block).get(scope))
        )
        blocks.append((name, count))
    return blocks


def read_body(handler: BaseHTTPRequestHandler) -> bytes:
    """The request body, de-chunked and un-gzipped."""
    if "chunked" in handler.headers.get("Transfer-Encoding", "").lower():
        chunks = []
        while size := int(handler.rfile.readline().split(b";")[0], 16):
            chunks.append(handler.rfile.read(size))
            handler.rfile.readline()
        handler.rfile.readline()
        body = b"".join(chunks)
    else:
        body = handler.rfile.read(int(handler.headers.get("Content-Length", "0")))
    return gzip.decompress(body) if "gzip" in handler.headers.get("Content-Encoding", "") else body


class Handler(BaseHTTPRequestHandler):
    """Takes an OTLP export, files its blocks under the signal, answers an empty success."""

    server: Receiver

    def do_POST(self) -> None:
        """File one OTLP export under its signal."""
        signal = PATHS.get(self.path.split("?")[0])
        if signal is None:
            self.send_error(404)
            return
        kind = self.headers.get("Content-Type", "")
        try:
            body = read_body(self)
            blocks = json_blocks(signal, body) if "json" in kind else proto_blocks(body)
        except (ValueError, OSError, EOFError, zlib.error) as error:
            self.server.rejected.append(f"{signal}: an export could not be read: {error}")
            self.send_error(400)
            return
        with self.server.lock:
            self.server.blocks[signal].extend(blocks)
        reply = b"{}" if "json" in kind else b""
        self.send_response(200)
        self.send_header("Content-Type", kind or "application/x-protobuf")
        self.send_header("Content-Length", str(len(reply)))
        self.end_headers()
        self.wfile.write(reply)

    def log_message(self, format: str, *args: object) -> None:  # noqa: A002 # -- the override
        """Say nothing: the verdict is the output."""


class Receiver(ThreadingHTTPServer):
    """The OTLP/HTTP endpoint and what it was sent."""

    def __init__(self, port: int) -> None:
        """Bind the loopback interface; port 0 lets the kernel pick."""
        super().__init__(("127.0.0.1", port), Handler)
        self.lock = threading.Lock()
        self.blocks: dict[str, list[Block]] = {name: [] for name in SIGNALS}
        self.rejected: list[str] = []

    def judge(self, required: list[str], service: str) -> list[str]:
        """What is still missing: one line per required signal that has not arrived as asked."""
        with self.lock:
            seen = {name: list(self.blocks[name]) for name in required}
        problems: list[str] = []
        for name, blocks in seen.items():
            if not blocks:
                problems.append(f"{name}: nothing arrived at the sink")
            elif not any(count for _, count in blocks):
                problems.append(f"{name}: only empty exports arrived")
            elif not any(who == service and count for who, count in blocks):
                named = sorted({who or "(no service.name)" for who, _ in blocks})
                problems.append(f"{name}: arrived for service.name {named}, expected {service!r}")
        return [*problems, *self.rejected] if problems else []


def exporter_env(port: int) -> dict[str, str]:
    """The environment that points an OpenTelemetry SDK at the sink."""
    own = {f"OTEL_EXPORTER_OTLP_{name.upper()}_ENDPOINT" for name in SIGNALS}
    env = {key: value for key, value in os.environ.items() if key not in own}
    env["OTEL_EXPORTER_OTLP_ENDPOINT"] = f"http://127.0.0.1:{port}"
    env.setdefault("OTEL_EXPORTER_OTLP_PROTOCOL", "http/protobuf")
    for key, value in FAST_EXPORT.items():
        env.setdefault(key, value)
    return env


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="telemetry_sink.py", usage="%(prog)s [options] -- cmd...")
    parser.add_argument("--service", required=True, help="the service.name every signal must carry")
    parser.add_argument("--require", default=",".join(SIGNALS), help="comma-separated signals")
    parser.add_argument(
        "--wait", type=float, default=10.0, help="seconds to wait after the command"
    )
    parser.add_argument("--port", type=int, default=0)
    rest = argv[1:]
    split = rest.index("--") if "--" in rest else len(rest)
    options = parser.parse_args(rest[:split])
    command = rest[split + 1 :]
    required = [name.strip() for name in options.require.split(",") if name.strip()]
    unknown = [name for name in required if name not in SIGNALS]
    if unknown or not required or not command:
        parser.error("needs signals from traces,metrics,logs and a command after --")
    interrupted = 0
    with Receiver(options.port) as receiver, gatelib.interrupts_raise():
        threading.Thread(target=receiver.serve_forever, args=(0.02,), daemon=True).start()
        try:
            _, problems = gatelib.run_command(command, exporter_env(receiver.server_address[1]))
            deadline = time.monotonic() + options.wait
            while receiver.judge(required, options.service) and time.monotonic() < deadline:
                time.sleep(0.05)
        except gatelib.Interrupted as stop:
            interrupted, problems = 128 + stop.signum, [str(stop)]
        finally:
            receiver.shutdown()
    problems += receiver.judge(required, options.service)
    if not problems:
        print(f"telemetry_sink: {', '.join(required)} arrived for service.name {options.service!r}")
    status = gatelib.report("telemetry_sink", problems)
    return interrupted or status


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
