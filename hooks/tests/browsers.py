"""A stand-in browser that speaks the debugging pipe, and the real one when this machine has it.

The stand-in answers on descriptor 4 what it reads on descriptor 3, as Chromium does with
``--remote-debugging-pipe``, and runs no page. Words in what it is asked choose what it does, so
each way a browser can fail is a real process on a real pipe:

* every script is ``false`` the first time it is asked and ``true`` after, so a step waits once;
  one that says ``never`` is never ``true``;
* a script that says ``throw`` throws, ``quietly`` throws with no exception object,
  ``novalue`` has no value;
* a click (the script starting ``(text =>``) that is ``true`` navigates the tab, ``Elsewhere``
  navigates another frame, ``Stall`` never finishes loading;
* opening an address that says ``unreachable`` fails, ``stuck`` never loads, ``crash`` closes
  the browser;
* a window 999 pixels wide is refused;
* ``FAKE_BROWSER=die`` exits at once and ``mute`` never answers.
"""

import os
import shutil
import sys
from pathlib import Path

import pytest

import cdp

FAKE = r"""
import json, os, sys, time
MODE = os.environ.get("FAKE_BROWSER", "")
if MODE == "die":
    sys.exit("the stand-in browser gives up")
if MODE == "mute":
    time.sleep(60)
asked = set()

def send(message):
    os.write(4, json.dumps(message).encode() + b"\0")

def evaluate(ident, expression):
    if "throw" in expression:
        return {"exceptionDetails": {"exception": {"description": "Error: boom"}}}
    if "quietly" in expression:
        return {"exceptionDetails": {"text": "Uncaught"}}
    if "novalue" in expression:
        return {"result": "not an object"}
    value = expression in asked and "never" not in expression
    asked.add(expression)
    send({"id": ident, "result": {"result": {"type": "boolean", "value": value}}})
    if value and expression.startswith("(text =>"):
        frame = "T2" if "Elsewhere" in expression else "T1"
        send({"method": "Page.frameStartedLoading", "params": {"frameId": frame}})
        if "Stall" not in expression:
            send({"method": "Page.loadEventFired", "params": {}})
    return None

def navigate(ident, url):
    send({"method": "Page.frameNavigated", "params": "not an object"})
    if "unreachable" in url:
        send({"id": ident, "result": {"errorText": "net::ERR_NAME_NOT_RESOLVED"}})
        return
    send({"id": ident, "result": {"frameId": "T1"}})
    if "crash" in url:
        sys.exit(0)
    if "stuck" not in url:
        send({"method": "Page.loadEventFired", "params": {}})

send([1])
pending = b""
while chunk := os.read(3, 65536):
    pending += chunk
    *whole, pending = pending.split(b"\0")
    for raw in whole:
        asked_for = json.loads(raw)
        ident, method, params = asked_for["id"], asked_for["method"], asked_for["params"]
        if method == "Page.navigate":
            navigate(ident, params["url"])
            continue
        if method == "Runtime.evaluate":
            answer = evaluate(ident, params["expression"])
            if answer is not None:
                send({"id": ident, "result": answer})
            continue
        if method == "Emulation.setDeviceMetricsOverride" and params["width"] == 999:
            send({"id": ident, "error": {"message": "the width is refused"}})
            continue
        results = {"Target.createTarget": {"targetId": "T1"}}
        results["Target.attachToTarget"] = {"sessionId": "S1"}
        send({"id": ident, **({"result": results[method]} if method in results else {})})
"""


def fake(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mode: str = "") -> Path:
    """Make the stand-in the browser ``cdp`` drives; ``mode`` is ``die``, ``mute`` or neither."""
    program = tmp_path / "fake-browser"
    program.write_text(f"#!{sys.executable}\n{FAKE}", encoding="utf-8")
    program.chmod(0o755)
    monkeypatch.setenv(cdp.ENV, str(program))
    monkeypatch.setenv("FAKE_BROWSER", mode)
    return program


def real() -> str | None:
    """A real Chromium or Chrome, when this machine has one and the stand-in is not chosen."""
    named = os.environ.get(cdp.ENV)
    if named and Path(named).name != "fake-browser":
        return named
    return next((found for name in cdp.NAMES if (found := shutil.which(name))), None)
