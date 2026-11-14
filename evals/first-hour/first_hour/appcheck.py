"""Start the app the way its repository says to, request its page, and exercise its first slice.

The start command is what the repository tells a person: a run command in the README first, then
a justfile recipe, then a ``package.json`` script. With none, the finding is "no way to start
it", not a crash. The app gets a free port through ``PORT``; one that ignores it is found by the
URL it prints. Each slice kind in ``SLICES`` knows how to exercise one kind of first slice.
"""

from __future__ import annotations

import contextlib
import json
import os
import re
import signal
import socket
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import asdict, dataclass
from enum import StrEnum
from html.parser import HTMLParser
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping, Sequence
    from pathlib import Path

    from first_hour.persona import Slice

NO_WAY = "no way to start it: no run command in the README, justfile or package.json"
START_WORDS = re.compile(r"\b(start|dev|serve|server|run|up|app|main|bootRun|rackup)\b|\brails s\b")
NOT_START = re.compile(
    r"\b(install|sync|test|pytest|vitest|jest|check|lint|build|fmt|format|typecheck|ci)\b"
)
README_LINE = re.compile(
    r"^\s*\$?\s*((?:just|npm|pnpm|yarn|bun|node|python3?|uv|make|deno"
    r"|go|cargo|dotnet|bundle|ruby|rails|rackup|bin/rails|java|gradle|mvn|sbt"
    r"|\./gradlew|\./mvnw)\s.+)$"
)
RECIPE = re.compile(r"^([A-Za-z][\w-]*)(?:\s+[^:\n]*)?:(?!=)", re.MULTILINE)
URL = re.compile(r"https?://(?:localhost|127\.0\.0\.1|0\.0\.0\.0|\[::1?\]):(\d+)")
RECIPE_NAMES = ("start", "dev", "serve", "run", "app")
SCRIPT_NAMES = ("start", "dev", "serve")
CLI_KINDS = frozenset({"cli"})
PLACEHOLDER = re.compile(r"^(?:<.*|\[.*|\$.*|\{.*|\.\.\.)$")
FIELD_TYPES = frozenset({"", "text", "url", "search"})
TIMEOUT_S = 120.0
REQUEST_S = 15.0
STOP_GRACE_S = 10.0


class Verdict(StrEnum):
    """What the slice check found: it works, it does not, or this check cannot tell."""

    WORKS = "works"
    FAILS = "fails"
    UNCHECKED = "unchecked"


UNCHECKED = (
    "the page builds itself in the browser (it has a script and no form that works without it);"
    " this check runs no browser, so the slice is not checked"
)


@dataclass(frozen=True)
class Start:
    """How the repository says to start the app, and where it says so."""

    command: str
    source: str


@dataclass(frozen=True)
class Result:
    """What the app check found."""

    start_command: str | None
    source: str | None
    started: bool
    page_ok: bool
    slice: Verdict
    finding: str
    url: str | None = None

    @property
    def usable(self) -> bool:
        """Whether a person can use the first slice: only a slice seen to work counts."""
        return self.slice is Verdict.WORKS

    def event(self) -> dict[str, object]:
        """The result as one event of the run's log."""
        return {"kind": "app", **asdict(self), "slice_ok": self.usable}


def _readme(root: Path) -> Start | None:
    for name in ("README.md", "README", "readme.md"):
        path = root / name
        if not path.is_file():
            continue
        fenced = False
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            if line.lstrip().startswith("```"):
                fenced = not fenced
                continue
            match = README_LINE.match(line) if fenced else None
            if match and START_WORDS.search(match.group(1)) and not NOT_START.search(line):
                return Start(match.group(1).strip(), name)
    return None


def _justfile(root: Path) -> Start | None:
    path = next(
        (root / n for n in ("justfile", "Justfile", ".justfile") if (root / n).is_file()), None
    )
    if path is None:
        return None
    recipes = set(RECIPE.findall(path.read_text(encoding="utf-8", errors="replace")))
    name = next((n for n in RECIPE_NAMES if n in recipes), None)
    return Start(f"just {name}", path.name) if name else None


def _package(root: Path) -> Start | None:
    path = root / "package.json"
    if not path.is_file():
        return None
    try:
        scripts = json.loads(path.read_text(encoding="utf-8")).get("scripts") or {}
    except (ValueError, AttributeError):
        return None
    name = next((n for n in SCRIPT_NAMES if n in scripts), None)
    if name is None:
        return None
    runner = "pnpm" if (root / "pnpm-lock.yaml").is_file() else "npm"
    runner = "yarn" if (root / "yarn.lock").is_file() else runner
    return Start(f"{runner} run {name}", "package.json")


def cli_base(command: str, verb: str) -> str:
    """A README's example run of a command-line tool, cut back to the part before its arguments.

    ``go run . add <title>`` with the slice's first word ``add`` is ``go run .``: the words from
    the first placeholder or the slice's own verb on are the arguments the check supplies itself.
    """
    kept: list[str] = []
    for word in command.split():
        if PLACEHOLDER.match(word) or (kept and word == verb):
            break
        kept.append(word)
    return " ".join(kept)


def start_command(root: Path) -> Start | None:
    """The command the repository gives a person to start the app, or ``None``."""
    return _readme(root) or _justfile(root) or _package(root)


class _Forms(HTMLParser):
    """The forms of a page: action, method, the text field to fill and the hidden fields."""

    def __init__(self) -> None:
        super().__init__()
        self.forms: list[dict[str, object]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        a = {k: v or "" for k, v in attrs}
        if tag == "form":
            self.forms.append(
                {"action": a.get("action", ""), "method": a.get("method", "get"), "fields": {}}
            )
        elif tag in ("input", "textarea") and self.forms and a.get("name"):
            fields = self.forms[-1]["fields"]
            assert isinstance(fields, dict)  # noqa: S101
            kind = a.get("type", "").lower() if tag == "input" else "text"
            fields[a["name"]] = (kind, a.get("value", ""))


def _get(url: str) -> tuple[int, str]:
    try:
        with urllib.request.urlopen(url, timeout=REQUEST_S) as response:  # noqa: S310
            return response.status, response.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as error:
        error.close()
        return error.code, ""


def html_form(base: str, page: str, piece: Slice) -> tuple[Verdict, str]:
    """Fill the page's first text field with the slice's value, send it, look for the result.

    A page with a script may do all of this in the browser, which this check does not run: when
    the plain form does not work there, the slice is unchecked rather than failed.
    """
    scripted = "<script" in page.lower()
    verdict, finding = _plain_form(base, page, piece)
    if verdict is Verdict.FAILS and scripted:
        return Verdict.UNCHECKED, f"{UNCHECKED} ({finding})"
    return verdict, finding


def _plain_form(base: str, page: str, piece: Slice) -> tuple[Verdict, str]:
    parser = _Forms()
    parser.feed(page)
    for form in parser.forms:
        fields = form["fields"]
        assert isinstance(fields, dict)  # noqa: S101
        target = next((n for n, (kind, _) in fields.items() if kind in FIELD_TYPES), None)
        if target is None:
            continue
        data = {n: v for n, (kind, v) in fields.items() if kind == "hidden"}
        data[target] = piece.value
        body = urllib.parse.urlencode(data)
        url = urllib.parse.urljoin(base, str(form["action"]))
        if str(form["method"]).lower() == "post":
            request = urllib.request.Request(url, data=body.encode(), method="POST")  # noqa: S310
            try:
                with urllib.request.urlopen(request, timeout=REQUEST_S) as response:  # noqa: S310
                    response.read()
            except urllib.error.HTTPError as error:
                error.close()
                return Verdict.FAILS, f"the form answered {error.code} to a valid link"
        else:
            _get(f"{url}?{body}")
        _status, after = _get(base)
        if piece.expect in after:
            return Verdict.WORKS, "the saved link shows on the page"
        return (
            Verdict.FAILS,
            f"sent the form's `{target}` field; `{piece.expect}` is not on the page after",
        )
    return Verdict.FAILS, "the page has no form with a text field to paste a link into"


SLICES: dict[str, Callable[[str, str, Slice], tuple[Verdict, str]]] = {"html-form": html_form}


def free_port() -> int:
    """A TCP port nothing listens on now."""
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


def _listening(port: int) -> bool:
    with socket.socket() as probe:
        probe.settimeout(0.5)
        return probe.connect_ex(("127.0.0.1", port)) == 0


def _wait(process: subprocess.Popen[bytes], log: Path, *, port: int, timeout: float) -> int | None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if _listening(port):
            return port
        printed = URL.findall(log.read_text(encoding="utf-8", errors="replace"))
        if printed and _listening(int(printed[-1])):
            return int(printed[-1])
        if process.poll() is not None:
            return None
        time.sleep(0.5)
    return None


def stop(process: subprocess.Popen[bytes]) -> None:
    """Stop the app's whole process group: a polite signal, then a kill."""
    with contextlib.suppress(ProcessLookupError):
        os.killpg(process.pid, signal.SIGTERM)
    try:
        process.wait(timeout=STOP_GRACE_S)
    except subprocess.TimeoutExpired:
        with contextlib.suppress(ProcessLookupError):
            os.killpg(process.pid, signal.SIGKILL)
        process.wait()


def _direct(command: Sequence[str]) -> list[str]:
    return list(command)


@dataclass(frozen=True)
class Launch:
    """How the check starts the app.

    ``wrap`` turns the start command into what runs (the run's sandbox, in a run), ``env`` is its
    environment, and ``on_start`` is told of the process so that a run that is stopped can end it.
    """

    wrap: Callable[[Sequence[str]], list[str]] = _direct
    env: Mapping[str, str] | None = None
    on_start: Callable[[subprocess.Popen[bytes]], None] | None = None


DIRECT = Launch()


@dataclass(frozen=True)
class Where:
    """Where a command-line check runs: the checkout, a command's time, and how it starts."""

    root: Path
    timeout: float
    launch: Launch


def _cli_once(command: str, where: Where) -> tuple[int, str]:
    env = {**(os.environ if where.launch.env is None else where.launch.env), "CI": "1"}
    timeout = where.timeout
    try:
        done = subprocess.run(
            where.launch.wrap(["sh", "-c", command.strip()]),
            cwd=where.root,
            env=env,
            capture_output=True,
            text=True,
            stdin=subprocess.DEVNULL,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return -1, f"no answer within {timeout:g}s"
    return done.returncode, (done.stdout + done.stderr).strip()


def check_cli(start: Start, piece: Slice, log: Path, *, where: Where) -> Result:
    """Run the tool the way the repository says: the slice's first command, then its second.

    The slice works when the last command succeeds and its output holds what the slice expects.
    """
    base = cli_base(start.command, (piece.value.split() or [""])[0])
    steps = [s for s in (piece.value, piece.then) if s]
    lines: list[str] = []
    code, out = 0, ""
    for args in steps:
        code, out = _cli_once(f"{base} {args}", where)
        lines.append(f"$ {base} {args}\n[{code}] {out}")
        if code != 0:
            break
    log.write_text("\n".join(lines) + "\n", encoding="utf-8")
    ran = code == 0
    works = ran and piece.expect in out
    if works:
        finding = f"`{base} {steps[-1]}` printed what the slice expects"
    elif ran:
        finding = f"`{base} {steps[-1]}` ran, and `{piece.expect}` is not in its output"
    else:
        finding = f"`{base} {steps[-1]}` failed ({code}): {out[-300:]}"
    return Result(
        base,
        start.source,
        started=ran,
        page_ok=ran,
        slice=Verdict.WORKS if works else Verdict.FAILS,
        finding=finding,
    )


def check(
    root: Path, piece: Slice, log: Path, *, timeout: float = TIMEOUT_S, launch: Launch = DIRECT
) -> Result:
    """Start the app at ``root``, request its page, and exercise ``piece``; stop it again."""
    start = start_command(root)
    if start is None:
        return Result(None, None, started=False, page_ok=False, slice=Verdict.FAILS, finding=NO_WAY)
    if piece.kind in CLI_KINDS:
        return check_cli(start, piece, log, where=Where(root, timeout, launch))
    port = free_port()
    base_env = os.environ if launch.env is None else launch.env
    child_env = {**base_env, "PORT": str(port), "HOST": "127.0.0.1", "CI": "1"}
    with log.open("wb") as out:
        process = subprocess.Popen(
            launch.wrap(["sh", "-c", start.command]),
            cwd=root,
            env=child_env,
            stdout=out,
            stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL,
            start_new_session=True,
        )
    if launch.on_start is not None:
        launch.on_start(process)
    try:
        bound = _wait(process, log, port=port, timeout=timeout)
        if bound is None:
            tail = log.read_text(encoding="utf-8", errors="replace")[-400:].strip()
            return Result(
                start.command,
                start.source,
                started=False,
                page_ok=False,
                slice=Verdict.FAILS,
                finding=f"`{start.command}` did not listen within {timeout:g}s: {tail}",
            )
        base = f"http://127.0.0.1:{bound}/"
        status, page = _get(base)
        if status != 200:  # noqa: PLR2004
            return Result(
                start.command,
                start.source,
                started=True,
                page_ok=False,
                slice=Verdict.FAILS,
                finding=f"GET / answered {status}",
                url=base,
            )
        verdict, finding = SLICES[piece.kind](base, page, piece)
        return Result(
            start.command,
            start.source,
            started=True,
            page_ok=True,
            slice=verdict,
            finding=finding,
            url=base,
        )
    except (OSError, urllib.error.URLError) as error:
        return Result(
            start.command,
            start.source,
            started=True,
            page_ok=False,
            slice=Verdict.FAILS,
            finding=f"request failed: {error}",
        )
    finally:
        stop(process)
