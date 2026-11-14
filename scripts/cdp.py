"""Just enough of the Chrome DevTools Protocol to drive one page: open, run a script, wait for load.

The browser is any Chromium or Chrome. It is started headless with ``--remote-debugging-pipe``,
so commands go in on its file descriptor 3 and answers come out on 4, each a JSON text ended by
a NUL byte; no WebSocket and no library is needed. Every host name but ``127.0.0.1`` fails to
resolve, so a page under test reaches nothing outside this machine. The browser runs in a
session of its own and is stopped with its whole process group, whatever happened, and its
``TMPDIR`` is a directory of its own under ``/tmp`` that is removed with it.

``RATCHET_BROWSER`` names the executable; without it the first of the usual names on ``PATH``
is used. Nothing here knows where a browser is installed on any one machine.
"""

from __future__ import annotations

import contextlib
import fcntl
import io
import json
import os
import queue
import shutil
import subprocess
import tempfile
import threading
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

import gatelib

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path

ENV = "RATCHET_BROWSER"
NAMES = ("chromium", "chromium-browser", "google-chrome", "google-chrome-stable", "chrome")
# Why `--no-sandbox`: Ubuntu 23.10 and later refuse the user namespaces Chromium's sandbox needs
# unless the browser came with its own AppArmor profile, and a browser a tool downloaded did not.
# Browser automation libraries drive it the same way by default. The page under test is the
# project's own, and the resolver rule below keeps every other site unreachable.
FLAGS = (
    "--headless=new",
    "--no-sandbox",
    "--remote-debugging-pipe",
    "--no-first-run",
    "--no-default-browser-check",
    "--disable-extensions",
    "--disable-background-networking",
    "--disable-component-update",
    "--disable-sync",
    "--mute-audio",
    "--host-resolver-rules=MAP * ~NOTFOUND , EXCLUDE 127.0.0.1",
)
ANSWER = 30.0
# Why: the first answer comes after the browser has started, and a browser read cold from disk
# (a CI runner's Chromium took 33 s the first time and 7 s the next) can take longer than any
# later command does. Only that first wait is this long; a browser that dies still ends it at
# once, as the pipe closes.
STARTUP = 120.0
# Why: a pipe end the shell maps to 3 or 4 must not itself be 3 or 4, or one redirection would
# close the other's file.
HIGH_FD = 10
# Why not the inherited TMPDIR: Chromium makes its sockets under it, a socket's path holds at
# most 107 bytes, and an agent's sandbox can set a TMPDIR long enough that the browser dies
# before its first answer. The browser gets a directory of its own here, removed after.
SHORT_TMP = "/tmp"  # noqa: S108 # -- a fresh directory of mkdtemp's, not a fixed file
STDERR_TAIL = 5


class BrowserError(Exception):
    """The browser could not be started, stopped answering, or a command it ran failed."""


class PageError(BrowserError):
    """A script threw in the page: the page's doing, not the browser's."""


def executable() -> str | None:
    """The browser to drive: ``RATCHET_BROWSER``, else the first usual name on ``PATH``."""
    named = os.environ.get(ENV)
    if named:
        return named
    return next((found for name in NAMES if (found := shutil.which(name))), None)


@dataclass
class Pipe:
    """The two ends of the debugging pipe this side holds, and what the reader has seen.

    Answers are read on a thread of their own from the start, so a command can wait with a
    deadline.
    """

    commands: io.BufferedWriter
    answers: io.BufferedReader
    inbox: queue.Queue[dict[str, object] | None] = field(default_factory=queue.Queue)
    events: list[dict[str, object]] = field(default_factory=list)
    sent: int = 0
    reader: threading.Thread = field(init=False)

    def __post_init__(self) -> None:
        """Start reading."""
        self.reader = threading.Thread(target=self.read, daemon=True)
        self.reader.start()

    def close(self) -> None:
        """Once the browser is gone: let the reader see the end, then close both ends.

        A command the browser never read is dropped: closing would otherwise try to send it.
        """
        self.reader.join(timeout=ANSWER)
        with contextlib.suppress(BrokenPipeError):
            self.commands.close()
        self.answers.close()

    def read(self) -> None:
        """Split what the browser writes into messages until it closes the pipe."""
        pending = b""
        while chunk := self.answers.read1(65536):
            pending += chunk
            *whole, pending = pending.split(b"\0")
            for message in whole:
                parsed = json.loads(message)
                if isinstance(parsed, dict):
                    self.inbox.put(parsed)
        self.inbox.put(None)

    def next(self, deadline: float, waiting_for: str) -> dict[str, object]:
        """The next message from the browser, by ``deadline``."""
        try:
            message = self.inbox.get(timeout=max(0.0, deadline - time.monotonic()))
        except queue.Empty:
            said = f"the browser did not answer {waiting_for} in time"
            raise BrowserError(said) from None
        if message is None:
            said = f"the browser closed while it was asked {waiting_for}"
            raise BrowserError(said)
        return message

    def call(
        self,
        method: str,
        params: dict[str, object],
        session: str | None = None,
        *,
        within: float | None = None,
    ) -> dict[str, object]:
        """Send one command and return its result; events that arrive first are kept.

        The answer is due in ``within`` seconds, ``ANSWER`` when not given.
        """
        self.sent += 1
        message: dict[str, object] = {"id": self.sent, "method": method, "params": params}
        if session:
            message["sessionId"] = session
        try:
            self.commands.write(json.dumps(message).encode() + b"\0")
            self.commands.flush()
        except BrokenPipeError:
            said = f"the browser closed while it was asked {method}"
            raise BrowserError(said) from None
        deadline = time.monotonic() + (ANSWER if within is None else within)
        while True:
            answer = self.next(deadline, method)
            if answer.get("id") != self.sent:
                self.events.append(answer)
                continue
            error = answer.get("error")
            if isinstance(error, dict):
                said = f"{method} failed: {error.get('message')}"
                raise BrowserError(said)
            result = answer.get("result")
            return result if isinstance(result, dict) else {}

    def event(self, method: str, timeout: float, *, frame: str | None = None) -> bool:
        """Whether ``method`` arrives (or already did) within ``timeout``; it is consumed."""
        deadline = time.monotonic() + timeout
        while True:
            for index, seen in enumerate(self.events):
                params = seen.get("params")
                frame_of = params.get("frameId") if isinstance(params, dict) else None
                if seen.get("method") == method and frame in (None, frame_of):
                    del self.events[index]
                    return True
            try:
                self.events.append(self.next(deadline, method))
            except BrowserError:
                if time.monotonic() >= deadline:
                    return False
                raise


@dataclass
class Page:
    """One tab of the browser, sized as the check asks."""

    pipe: Pipe
    session: str
    frame: str

    def call(self, method: str, params: dict[str, object]) -> dict[str, object]:
        """One command to this tab."""
        return self.pipe.call(method, params, self.session)

    def open(self, url: str) -> None:
        """Navigate and wait for the page's load event."""
        self.pipe.events.clear()
        navigated = self.call("Page.navigate", {"url": url})
        if navigated.get("errorText"):
            said = f"{url} did not open: {navigated['errorText']}"
            raise BrowserError(said)
        if not self.pipe.event("Page.loadEventFired", ANSWER):
            said = f"{url} did not finish loading within {ANSWER:g}s"
            raise BrowserError(said)

    def evaluate(self, expression: str) -> object:
        """The value of ``expression`` in the page; a thrown error is a ``PageError``."""
        result = self.call(
            "Runtime.evaluate",
            {"expression": expression, "returnByValue": True, "awaitPromise": True},
        )
        thrown = result.get("exceptionDetails")
        if isinstance(thrown, dict):
            detail = thrown.get("exception")
            text = detail.get("description") if isinstance(detail, dict) else thrown.get("text")
            said = f"the page threw: {text}"
            raise PageError(said)
        value = result.get("result")
        return value.get("value") if isinstance(value, dict) else None

    def act(self, expression: str, settle: float) -> object:
        """Run ``expression``; when it starts a navigation of this tab, wait for that load."""
        self.pipe.events.clear()
        value = self.evaluate(expression)
        if self.pipe.event("Page.frameStartedLoading", settle, frame=self.frame) and not (
            self.pipe.event("Page.loadEventFired", ANSWER)
        ):
            said = f"the page did not finish loading within {ANSWER:g}s"
            raise BrowserError(said)
        return value


def launch(browser: str, profile: Path, scratch: str) -> tuple[subprocess.Popen[bytes], Pipe]:
    """Start the browser on a debugging pipe, in a session of its own, writing to ``profile``.

    ``scratch`` is its ``TMPDIR``.
    """
    to_browser, commands = os.pipe()
    answers, from_browser = os.pipe()
    ends = [fcntl.fcntl(end, fcntl.F_DUPFD_CLOEXEC, HIGH_FD) for end in (to_browser, from_browser)]
    for end in (to_browser, from_browser):
        os.close(end)
    give, take = ends
    shell = f'exec "$@" 3<&{give} 4>&{take} {give}<&- {take}>&-'
    try:
        with (profile / "stderr.log").open("wb") as stderr:
            process = subprocess.Popen(
                ["bash", "-c", shell, "browser", browser, *FLAGS, f"--user-data-dir={profile}"],
                pass_fds=ends,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=stderr,
                start_new_session=True,
                env={**os.environ, "TMPDIR": scratch},
            )
    except OSError as error:
        for end in (*ends, commands, answers):
            os.close(end)
        said = f"{browser} could not be started: {error}"
        raise BrowserError(said) from None
    for end in ends:
        os.close(end)
    return process, Pipe(
        io.BufferedWriter(io.FileIO(commands, "w")), io.BufferedReader(io.FileIO(answers, "r"))
    )


def stderr_tail(profile: Path) -> str:
    lines = (profile / "stderr.log").read_text(encoding="utf-8", errors="replace").splitlines()
    return " / ".join(lines[-STDERR_TAIL:]) or "it printed nothing"


@contextlib.contextmanager
def page(browser: str, profile: Path, *, width: int, height: int) -> Iterator[Page]:
    """A blank tab of a fresh headless browser; the browser is stopped when the block ends.

    Its temporary directory goes with it.
    """
    with contextlib.ExitStack() as stack:
        with gatelib.signals_held():
            scratch = tempfile.mkdtemp(prefix="ratchet-browser-", dir=SHORT_TMP)
            stack.callback(shutil.rmtree, scratch, ignore_errors=True)
            process, pipe = launch(browser, profile, scratch)
            stack.callback(pipe.close)
            stack.callback(gatelib.stop_group, process)
        try:
            target = pipe.call("Target.createTarget", {"url": "about:blank"}, within=STARTUP)
        except BrowserError as error:
            said = f"{error}; {stderr_tail(profile)}"
            raise BrowserError(said) from None
        frame = str(target.get("targetId"))
        attached = pipe.call("Target.attachToTarget", {"targetId": frame, "flatten": True})
        tab = Page(pipe, str(attached.get("sessionId")), frame)
        tab.call("Page.enable", {})
        metrics: dict[str, object] = {"width": width, "height": height, "mobile": False}
        metrics["deviceScaleFactor"] = 1
        tab.call("Emulation.setDeviceMetricsOverride", metrics)
        yield tab
