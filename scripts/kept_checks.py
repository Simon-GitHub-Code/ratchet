"""Kept checks: what an acceptance tester leaves behind so a promise is tried on every change.

A kept check is data, never a program, so it carries nothing of the machine it was written on:
no path, no port, no browser location. It names a criterion of the ticket and the steps that
show that criterion holding against ``BASE_URL``, the address of a freshly booted product::

    {"id": "K1", "criterion": "C2", "via": "http", "steps": [
      {"request": "POST /items", "form": {"link": "https://example.com/{unique}"},
       "headers": {"Origin": "{BASE_URL}"},
       "expect": {"status": 303, "headers": {"Location": "/"}}},
      {"request": "GET /", "expect": {"contains": ["https://example.com/{unique}"],
                                     "count": {"https://example.com/{unique}": 1}}}]}

    {"id": "K2", "criterion": "C9", "via": "browser", "width": 375, "steps": [
      {"open": "/"}, {"fill": "Link to save", "with": "https://example.com/{unique}"},
      {"click": "Save"}, {"see": "https://example.com/{unique}"},
      {"true": "document.documentElement.scrollWidth <= innerWidth"}]}

``docs/acceptance.md`` lists every field of a check and its steps, and a field it does not list
is refused. An HTTP step sends one request, ``METHOD /path`` (a path, never a host), with optional
``headers`` and either ``form`` (sent URL-encoded) or ``body`` (sent as it is). It follows no
redirect. It holds when each ``expect`` holds: ``status``; ``headers``, exact values with names
in any case; ``contains``, texts the body shows in this order; ``lacks``, texts it never shows;
``count``, how many times a text shows. A browser step is one of: ``open`` a path; ``fill`` the
field with that label ``with`` a value; ``click`` the button or link with that text; ``see`` a
text on the page; ``lacks`` a text; ``true``, a script expression that must be ``true``. A
browser step waits up to five seconds for what it needs. ``{BASE_URL}`` and ``{unique}`` are
replaced in every text: the address, and a value new to each run, so that no check depends on
what an earlier run left in the product's store.

A run ends one of three ways. The check is ``held`` when every step holds. It is ``unmet`` when
the product answered and did not show what a step asks, the first such step being the reason;
a script the page throws counts as not showing it, once the step's five seconds are up. It is
``not-run`` when nothing could try it: a request that got no answer, no browser, or a browser
that would not start, crashed or stopped answering. A check that was not run says nothing about
the product either way, so it is never counted as holding or as failing.
"""

from __future__ import annotations

import http.client
import json
import secrets
import time
from dataclasses import dataclass
from enum import Enum
from typing import TYPE_CHECKING
from urllib.parse import urlencode, urlsplit

import cdp

if TYPE_CHECKING:
    from pathlib import Path

BASE = "{BASE_URL}"
UNIQUE = "{unique}"
WIDTH, HEIGHT = 1280, 800
WAIT = 5.0
POLL = 0.1
SETTLE = 0.3
TIMEOUT = 30.0
# The fields of each object, in the order docs/acceptance.md lists them.
CHECK_FIELDS = ("id", "criterion", "via", "width", "steps")
HTTP_STEP_FIELDS = ("request", "headers", "form", "body", "expect")
EXPECTS = ("status", "headers", "contains", "lacks", "count")
BROWSER_STEP_FIELDS = ("open", "fill", "with", "click", "see", "lacks", "true")
ACTIONS = ("open", "fill", "click", "see", "lacks", "true")
VIA = ("http", "browser")
STATUSES = range(100, 600)
WIDTHS = range(200, 4001)


@dataclass(frozen=True)
class Expect:
    """What a response must show; an empty field asks nothing."""

    status: int | None = None
    headers: tuple[tuple[str, str], ...] = ()
    contains: tuple[str, ...] = ()
    lacks: tuple[str, ...] = ()
    count: tuple[tuple[str, int], ...] = ()

    def empty(self) -> bool:
        """Whether it asks nothing of the response."""
        return self == Expect()


@dataclass(frozen=True)
class Request:
    """One HTTP step: the request, and what its response must show."""

    method: str
    path: str
    headers: tuple[tuple[str, str], ...]
    body: str | None
    form: tuple[tuple[str, str], ...]
    expect: Expect


@dataclass(frozen=True)
class Action:
    """One browser step: ``kind`` is one of ``ACTIONS``, ``value`` only for ``fill``."""

    kind: str
    target: str
    value: str = ""


@dataclass(frozen=True)
class HttpCheck:
    """A kept check made of requests."""

    id: str
    criterion: str
    steps: tuple[Request, ...]

    def expects(self) -> bool:
        """Whether any step can fail: a check that expects nothing proves nothing."""
        return any(not step.expect.empty() for step in self.steps)


@dataclass(frozen=True)
class BrowserCheck:
    """A kept check made of browser steps, in a window ``width`` pixels wide."""

    id: str
    criterion: str
    steps: tuple[Action, ...]
    width: int = WIDTH

    def expects(self) -> bool:
        """Whether any step can fail: every step but ``open`` waits for something."""
        return any(step.kind != "open" for step in self.steps)


Check = HttpCheck | BrowserCheck


class Outcome(Enum):
    """How a run of a check ended; see the module's docstring."""

    HELD = "held"
    UNMET = "unmet"
    NOT_RUN = "not-run"


@dataclass(frozen=True)
class Result:
    """A run's outcome, and why, unless it held."""

    outcome: Outcome
    reason: str | None = None


HELD = Result(Outcome.HELD)


class Shape:
    """Reads one JSON object of a check; each field that is wrong becomes a sentence."""

    def __init__(self, raw: object, where: str, problems: list[str]) -> None:
        """Keep the object, where it sits for the sentences, and the list they go to."""
        self.raw = raw if isinstance(raw, dict) else {}
        self.where, self.problems = where, problems
        if not isinstance(raw, dict):
            problems.append(f"{where} is not an object")

    def say(self, problem: str) -> None:
        """Add a sentence about this object."""
        self.problems.append(f"{self.where} {problem}")

    def known(self, fields: tuple[str, ...], which: str) -> None:
        """Say which fields are not among ``fields``; ``which`` ends the sentence."""
        unknown = sorted(set(self.raw) - set(fields))
        if unknown:
            self.say(f"names {', '.join(unknown)}, {which}")

    def text(self, key: str, *, required: bool = True) -> str:
        """The field as a non-empty text, or ``""``."""
        value = self.raw.get(key)
        if isinstance(value, str) and value.strip():
            return value
        if value is not None or required:
            self.say(f"needs {key} as a non-empty text")
        return ""

    def texts(self, key: str) -> tuple[str, ...]:
        """The field as a list of non-empty texts; absent is empty."""
        value = self.raw.get(key, [])
        if isinstance(value, list) and all(isinstance(item, str) and item for item in value):
            return tuple(str(item) for item in value)
        self.say(f"needs {key} as a list of non-empty texts")
        return ()

    def table(self, key: str) -> tuple[tuple[str, object], ...]:
        """The field as an object's pairs; absent is empty."""
        value = self.raw.get(key, {})
        if isinstance(value, dict):
            return tuple((str(name), item) for name, item in value.items())
        self.say(f"needs {key} as an object")
        return ()

    def text_table(self, key: str) -> tuple[tuple[str, str], ...]:
        """The field as an object of texts; absent is empty."""
        pairs = self.table(key)
        if all(isinstance(value, str) for _, value in pairs):
            return tuple((name, str(value)) for name, value in pairs)
        self.say(f"needs {key} with a text for each name")
        return ()


def parse_expect(shape: Shape) -> Expect:
    raw = shape.raw.get("expect", {})
    expect = Shape(raw, f"{shape.where}'s expect", shape.problems)
    status = expect.raw.get("status")
    if status is not None and not (isinstance(status, int) and status in STATUSES):
        expect.say("needs status as a number from 100 to 599")
        status = None
    counts = expect.table("count")
    if not all(isinstance(n, int) and not isinstance(n, bool) and n >= 0 for _, n in counts):
        expect.say("needs count with a whole number for each text")
        counts = ()
    expect.known(EXPECTS, "which a check cannot expect")
    return Expect(
        status=status if isinstance(status, int) else None,
        headers=expect.text_table("headers"),
        contains=expect.texts("contains"),
        lacks=expect.texts("lacks"),
        count=tuple((text, int(str(n))) for text, n in counts),
    )


def parse_request(shape: Shape) -> Request:
    shape.known(HTTP_STEP_FIELDS, "which an HTTP step does not have")
    method, _, path = shape.text("request").partition(" ")
    if not method.isalpha() or not path.startswith("/"):
        shape.say("needs request as METHOD /path, a path on BASE_URL and never a host")
    form, body = shape.text_table("form"), shape.text("body", required=False)
    headers = shape.text_table("headers")
    if form and body:
        shape.say("has both form and body; a request sends one")
    if form:
        headers = (*headers, ("Content-Type", "application/x-www-form-urlencoded"))
    return Request(method.upper(), path, headers, body or None, form, parse_expect(shape))


def parse_action(shape: Shape) -> Action:
    shape.known(BROWSER_STEP_FIELDS, "which a browser step does not have")
    kinds = [kind for kind in ACTIONS if kind in shape.raw]
    if len(kinds) != 1:
        shape.say(f"needs exactly one of {', '.join(ACTIONS)}")
        return Action("open", "/")
    kind = kinds[0]
    target = shape.text(kind)
    if kind == "open" and not target.startswith("/"):
        shape.say("needs open as a path on BASE_URL, never a host")
    value = shape.text("with") if kind == "fill" else ""
    return Action(kind, target, value)


def parse(raw: object, where: str = "a kept check") -> tuple[Check | None, list[str]]:
    """The check, or ``None`` and why not; ``where`` names it in each sentence."""
    problems: list[str] = []
    shape = Shape(raw, where, problems)
    ident = shape.text("id")
    shape.where = f"kept check {ident}" if ident else where
    shape.known(CHECK_FIELDS, "which a kept check does not have")
    criterion, via = shape.text("criterion"), shape.raw.get("via")
    width = shape.raw.get("width", WIDTH)
    if via not in VIA:
        shape.say("needs via as http or browser")
    if not (isinstance(width, int) and width in WIDTHS):
        shape.say("needs width as a number of pixels from 200 to 4000")
    raw_steps = shape.raw.get("steps")
    if not isinstance(raw_steps, list) or not raw_steps:
        shape.say("needs steps as a non-empty list")
        raw_steps = []
    shapes = [
        Shape(step, f"{shape.where} step {n}", problems) for n, step in enumerate(raw_steps, 1)
    ]
    check: Check = (
        HttpCheck(ident, criterion, tuple(parse_request(step) for step in shapes))
        if via == "http"
        else BrowserCheck(
            ident,
            criterion,
            tuple(parse_action(step) for step in shapes),
            width if isinstance(width, int) else WIDTH,
        )
    )
    if not problems and not check.expects():
        shape.say("expects nothing, so it could never fail")
    return (None if problems else check), problems


@dataclass(frozen=True)
class Target:
    """The product a check runs against, and the value ``{unique}`` stands for in this run."""

    base: str
    unique: str

    def fill(self, text: str) -> str:
        """``text`` with ``{BASE_URL}`` and ``{unique}`` replaced."""
        return text.replace(BASE, self.base).replace(UNIQUE, self.unique)

    def pairs(self, pairs: tuple[tuple[str, str], ...]) -> tuple[tuple[str, str], ...]:
        """Each value filled."""
        return tuple((name, self.fill(value)) for name, value in pairs)

    def expect(self, expect: Expect) -> Expect:
        """Every text the expectation names, filled."""
        return Expect(
            status=expect.status,
            headers=self.pairs(expect.headers),
            contains=tuple(map(self.fill, expect.contains)),
            lacks=tuple(map(self.fill, expect.lacks)),
            count=tuple((self.fill(text), times) for text, times in expect.count),
        )


def unmet(expect: Expect, answer: tuple[int, http.client.HTTPMessage, bytes]) -> str | None:
    """Why the response does not show what ``expect`` asks, or ``None``."""
    status, headers, body = answer
    if expect.status is not None and status != expect.status:
        return f"answered {status}, expected {expect.status}"
    for name, want in expect.headers:
        got = headers.get(name)
        if got != want:
            return f"sent {name}: {got!r}, expected {want!r}"
    at = 0
    for text in expect.contains:
        found = body.find(text.encode(), at)
        if found < 0:
            return f"does not show {text!r}" + (" after what came before it" if at else "")
        at = found + len(text.encode())
    for text in expect.lacks:
        if text.encode() in body:
            return f"shows {text!r}"
    for text, times in expect.count:
        seen = body.count(text.encode())
        if seen != times:
            return f"shows {text!r} {seen} times, expected {times}"
    return None


def send(step: Request, target: Target) -> tuple[int, http.client.HTTPMessage, bytes]:
    parts = urlsplit(target.base)
    connect = http.client.HTTPSConnection if parts.scheme == "https" else http.client.HTTPConnection
    connection = connect(parts.hostname or "", parts.port, timeout=TIMEOUT)
    text = urlencode(target.pairs(step.form)) if step.form else step.body
    body = target.fill(text).encode() if text is not None else None
    headers = dict(target.pairs(step.headers))
    try:
        connection.request(step.method, target.fill(step.path), body=body, headers=headers)
        response = connection.getresponse()
        return response.status, response.msg, response.read()
    finally:
        connection.close()


def run_http(check: HttpCheck, target: Target) -> Result:
    for n, step in enumerate(check.steps, 1):
        said = f"step {n}, {step.method} {target.fill(step.path)},"
        try:
            answer = send(step, target)
        except (OSError, http.client.HTTPException) as error:
            return Result(Outcome.NOT_RUN, f"{said} got no answer: {error}")
        problem = unmet(target.expect(step.expect), answer)
        if problem:
            return Result(Outcome.UNMET, f"{said} {problem}")
    return HELD


NORMAL = "const norm = s => (s || '').replace(/\\s+/g, ' ').trim();"
FILL = (
    "((label, value) => {" + NORMAL + "const labelled = [...document.querySelectorAll('label')]"
    ".filter(l => norm(l.textContent) === label).map(l => l.control).filter(Boolean);"
    "const named = [...document.querySelectorAll('input, textarea, select')]"
    ".filter(f => norm(f.getAttribute('aria-label')) === label);"
    "const field = labelled[0] || named[0]; if (!field) return false;"
    "Object.getOwnPropertyDescriptor(Object.getPrototypeOf(field), 'value').set.call(field, value);"
    "field.dispatchEvent(new Event('input', {bubbles: true}));"
    "field.dispatchEvent(new Event('change', {bubbles: true})); return true;"
    "})(%s, %s)"
)
CLICK = (
    "(text => {" + NORMAL + "const hit = [...document.querySelectorAll("
    "'button, a, [role=button], input[type=submit], input[type=button]')]"
    ".find(el => norm(el.innerText || el.value || el.getAttribute('aria-label')) === text);"
    "if (!hit) return false; hit.click(); return true; })(%s)"
)
SEE = "document.body !== null && document.body.innerText.includes(%s)"


def probe(action: Action, target: Target) -> tuple[str, str]:
    """The expression that is ``true`` once ``action`` is done or holds, and what failing means."""
    said = json.dumps(target.fill(action.target))
    if action.kind == "fill":
        value = json.dumps(target.fill(action.value))
        return FILL % (said, value), f"found no field labelled {said}"
    if action.kind == "click":
        return CLICK % said, f"found nothing to click that says {said}"
    if action.kind == "see":
        return SEE % said, f"does not show {said}"
    if action.kind == "lacks":
        return f"!({SEE % said})", f"shows {said}"
    return f"({target.fill(action.target)}) === true", f"found `{action.target}` not true"


def perform(page: cdp.Page, action: Action, target: Target) -> str | None:
    if action.kind == "open":
        page.open(target.base + target.fill(action.target))
        return None
    expression, failing = probe(action, target)
    deadline = time.monotonic() + WAIT
    settle = SETTLE if action.kind == "click" else 0.0
    while True:
        try:
            if page.act(expression, settle) is True:
                return None
            thrown = ""
        except cdp.PageError as error:
            thrown = f"; {error}"
        if time.monotonic() >= deadline:
            return failing + thrown
        time.sleep(POLL)


def run_browser(check: BrowserCheck, target: Target, profile: Path) -> Result:
    browser = cdp.executable()
    if browser is None:
        said = f"needs a browser: set {cdp.ENV} to a Chromium or Chrome executable"
        return Result(Outcome.NOT_RUN, said)
    try:
        with cdp.page(browser, profile, width=check.width, height=HEIGHT) as page:
            for n, action in enumerate(check.steps, 1):
                problem = perform(page, action, target)
                if problem:
                    return Result(Outcome.UNMET, f"step {n}, {action.kind}, {problem}")
    except cdp.BrowserError as error:
        return Result(Outcome.NOT_RUN, f"the browser failed: {error}")
    return HELD


def run(check: Check, base: str, scratch: Path) -> Result:
    """How ``check`` ran against the product at ``base``.

    ``scratch`` is an empty directory the browser may use; the caller removes it.
    """
    target = Target(base.rstrip("/"), secrets.token_hex(6))
    if isinstance(check, HttpCheck):
        return run_http(check, target)
    return run_browser(check, target, scratch)
