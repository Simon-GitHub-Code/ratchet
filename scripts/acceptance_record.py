"""The acceptance record and what it is held to: the ticket, the request log and its seal.

A tester who saw only the ticket and an address writes one record per session. This module
reads it and judges it against facts the tester did not write: the ticket's checklist, the log
the proxy kept of every request, the seal over that log, the screenshots on disk and the files
the product's code is made of. Every function here is pure; ``acceptance.py`` reads the files.

The record::

    {"session": "<id>", "head": "<commit>",
     "criteria": [{"id": "C1", "text": "<a checklist line of the ticket, verbatim>",
                   "verdict": "pass" | "fail" | "unsure" | "not-observable",
                   "how": "<what was done, in plain words>",
                   "requests": [{"request": "<logged id>", "method": "GET", "path": "/",
                                 "status": 200, "contains": ["<text the answer showed>"]}],
                   "screenshots": ["<path under the tester's directory>"],
                   "steps": ["<to reproduce a fail>"], "why": "<for unsure>",
                   "judge": "<who can judge a not-observable line>",
                   "unkept": "<why a pass has no kept check>"}],
     "findings": [{"id": "F1", "severity": "blocker" | "major" | "minor" | "note",
                   "scope": "this" | "later: <ticket>", "title": "...", "steps": ["..."],
                   "expected": "...", "actual": "...", "requests": [...], "screenshots": [...]}],
     "kept": [<kept checks, see kept_checks.py>],
     "notChecked": [{"what": "...", "why": "..."}], "tryIt": ["..."]}

A record is accepted only when every rule below holds; each one broken is a sentence. It is for
this session and this commit. Every checklist line of the ticket has exactly one criterion, with
its text. A ``pass`` cites a request this session logged, as the method, path and status the log
holds, and every text it says the answer showed is in the stored answer; for a ``web-ui`` it also
cites a screenshot. A ``pass`` is kept by a check or says why it cannot be. A ``fail`` and every
finding carry steps to reproduce; a blocker or major finding cites what it saw. Verdicts and
severities come from their closed sets. No text names a file of the product's code, since the
tester judges by use and never by reading. The log matches its seal.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from typing import TYPE_CHECKING

import kept_checks
from kept_checks import Shape

if TYPE_CHECKING:
    from collections.abc import Iterator

SCHEMA = 1
VERDICTS = ("pass", "fail", "unsure", "not-observable")
SEVERITIES = ("blocker", "major", "minor", "note")
HARMFUL = ("blocker", "major")
LATER = "later: "
TESTER, HARNESS = "tester", "harness"
PNG = b"\x89PNG\r\n\x1a\n"
CHECKLIST = re.compile(r"^\s*[-*] \[[ xX]\] (.+?)\s*$", re.MULTILINE)
CANNOT_START = "The product could not be started, so nothing in it was tried."


def plain(text: str) -> str:
    return " ".join(text.split())


def promises(ticket: str) -> list[str]:
    """The ticket's checklist lines, each with its whitespace collapsed."""
    return [plain(line) for line in CHECKLIST.findall(ticket)]


@dataclass(frozen=True)
class Entry:
    """One line of ``requests.jsonl``: a request the tester made through the proxy."""

    id: str
    method: str
    path: str
    status: int
    response: str


def read_log(data: bytes) -> tuple[dict[str, Entry], list[str]]:
    """The entries of a request log by id, and a sentence for each line that is not one."""
    entries: dict[str, Entry] = {}
    problems: list[str] = []
    for number, line in enumerate(data.splitlines(), 1):
        try:
            raw = json.loads(line)
            entry = Entry(
                raw["id"], raw["method"], raw["path"], int(raw["status"]), raw["responseSha256"]
            )
        except (ValueError, TypeError, KeyError):
            problems.append(f"line {number} of the request log is not a logged request")
            continue
        entries[entry.id] = entry
    return entries, problems


@dataclass(frozen=True)
class Ending:
    """What ``down`` added to a seal: when it came, and whether the session ended well.

    ``early`` when the proxy had stopped before ``down`` came; ``stale`` when the work tree had
    moved off the tree the session served.
    """

    at: str
    early: bool
    stale: bool


@dataclass(frozen=True)
class Seal:
    """What the proxy said it wrote when it stopped, and what ``down`` said about the stop.

    ``ticket`` and ``session_file`` are the digests of the session's ``ticket.md`` and
    ``session.json`` as they were when the proxy started, before the tester had an address.
    """

    requests: int
    sha256: str
    stopped: str
    ended: str
    ticket: str | None
    session_file: str | None
    ending: Ending | None


def parse_seal(raw: object) -> Seal | None:
    """The seal in ``seal.json``, or ``None`` when it does not read as one."""
    if not isinstance(raw, dict):
        return None
    ending = raw.get("down")
    ticket, session = (raw.get(key) for key in ("ticketSha256", "sessionSha256"))
    try:
        return Seal(
            int(raw["requests"]),
            str(raw["sha256"]),
            str(raw["stopped"]),
            str(raw["ended"]),
            None if ticket is None else str(ticket),
            None if session is None else str(session),
            Ending(str(ending["at"]), ending["endedEarly"] is True, ending.get("stale") is True)
            if isinstance(ending, dict)
            else None,
        )
    except (KeyError, TypeError, ValueError):
        return None


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def seal_problems(data: bytes, seal: Seal | None, *, ticket: bytes, session: bytes) -> list[str]:
    """Why the session on disk is not the one the proxy sealed and ``down`` ended; empty when it is.

    ``data`` is the request log, ``ticket`` and ``session`` the session's copies of the ticket
    and of ``session.json``.
    """
    if seal is None:
        said = "the request log was never sealed: the proxy was stopped before it could seal it"
        return [f"{said}, so its requests cannot be trusted"]
    problems = log_problems(data, seal)
    if sha256(ticket) != seal.ticket:
        problems.append("the session's copy of the ticket was changed after `up`")
    if sha256(session) != seal.session_file:
        problems.append("session.json was changed after `up`")
    if seal.ending is None:
        problems.append("the session was not ended by `down`: run `down`, which says how it ended")
        return problems
    if seal.ending.early:
        problems.append(early(seal))
    if seal.ending.stale:
        problems.append(
            "the work tree changed while the session ran, so it may not be what was served"
        )
    return problems


EARLY = "before `down` came: something stopped it early"
STALE = "the work tree changed while it ran, so what it served is not what is there now"


def early(seal: Seal) -> str:
    return f"the session ended at {seal.stopped}, {EARLY}"


def log_problems(data: bytes, seal: Seal) -> list[str]:
    """Why the log on disk is not the log the proxy sealed; empty when it is."""
    held = data.count(b"\n")
    if sha256(data) == seal.sha256 and held == seal.requests:
        return []
    logged = f"the harness logged {seal.requests} requests"
    if held < seal.requests:
        return [f"the request log was cut short: {logged} and the file holds {held}"]
    if held > seal.requests:
        return [f"the request log holds {held} lines but {logged}: lines were added after it"]
    return ["the request log was changed after the harness wrote it"]


@dataclass(frozen=True)
class Citation:
    """A logged request a verdict rests on, and what the tester says it showed."""

    request: str
    method: str
    path: str
    status: int
    contains: tuple[str, ...]


@dataclass(frozen=True)
class Criterion:
    """The verdict on one checklist line of the ticket."""

    id: str
    text: str
    verdict: str
    how: str
    requests: tuple[Citation, ...]
    screenshots: tuple[str, ...]
    steps: tuple[str, ...]
    why: str
    judge: str
    unkept: str


@dataclass(frozen=True)
class Finding:
    """Something the tester found, with how to see it again."""

    id: str
    severity: str
    scope: str
    title: str
    steps: tuple[str, ...]
    expected: str
    actual: str
    requests: tuple[Citation, ...]
    screenshots: tuple[str, ...]


@dataclass(frozen=True)
class Record:
    """A tester's record of one session, as read."""

    session: str
    tree: str
    author: str
    criteria: tuple[Criterion, ...]
    findings: tuple[Finding, ...]
    kept: tuple[kept_checks.Check, ...]
    kept_raw: tuple[object, ...]
    not_checked: tuple[tuple[str, str], ...]
    try_it: tuple[str, ...]


def citations(shape: Shape) -> tuple[Citation, ...]:
    raw = shape.raw.get("requests", [])
    if not isinstance(raw, list):
        shape.say("needs requests as a list")
        return ()
    found = []
    for n, item in enumerate(raw, 1):
        cited = Shape(item, f"{shape.where} request {n}", shape.problems)
        status = cited.raw.get("status")
        if not isinstance(status, int) or isinstance(status, bool):
            cited.say("needs status as the number the log holds")
        found.append(
            Citation(
                cited.text("request"),
                cited.text("method"),
                cited.text("path"),
                status if isinstance(status, int) else 0,
                cited.texts("contains"),
            )
        )
    return tuple(found)


def criterion(shape: Shape) -> Criterion:
    ident = shape.text("id")
    shape.where = f"criterion {ident}" if ident else shape.where
    return Criterion(
        ident,
        shape.text("text"),
        shape.text("verdict"),
        shape.text("how"),
        citations(shape),
        shape.texts("screenshots"),
        shape.texts("steps"),
        shape.text("why", required=False),
        shape.text("judge", required=False),
        shape.text("unkept", required=False),
    )


def finding(shape: Shape) -> Finding:
    ident = shape.text("id")
    shape.where = f"finding {ident}" if ident else shape.where
    return Finding(
        ident,
        shape.text("severity"),
        shape.text("scope"),
        shape.text("title"),
        shape.texts("steps"),
        shape.text("expected"),
        shape.text("actual"),
        citations(shape),
        shape.texts("screenshots"),
    )


def objects(shape: Shape, key: str) -> list[Shape]:
    raw = shape.raw.get(key, [])
    if not isinstance(raw, list):
        shape.say(f"needs {key} as a list")
        return []
    return [Shape(item, f"{key} item {n}", shape.problems) for n, item in enumerate(raw, 1)]


def parse_record(raw: object) -> tuple[Record | None, list[str]]:
    """The record, or ``None`` and a sentence for each field that is not as the shape says."""
    problems: list[str] = []
    shape = Shape(raw, "the record", problems)
    session, tree = shape.text("session"), shape.text("tree")
    author = shape.text("author", required=False) or TESTER
    criteria = tuple(criterion(item) for item in objects(shape, "criteria"))
    findings = tuple(finding(item) for item in objects(shape, "findings"))
    kept_raw = shape.raw.get("kept", [])
    kept: list[kept_checks.Check] = []
    if not isinstance(kept_raw, list):
        shape.say("needs kept as a list")
        kept_raw = []
    for n, item in enumerate(kept_raw, 1):
        check, wrong = kept_checks.parse(item, f"kept item {n}")
        problems.extend(wrong)
        kept.extend([check] if check else [])
    not_checked = tuple(
        (item.text("what"), item.text("why")) for item in objects(shape, "notChecked")
    )
    record = Record(
        session,
        tree,
        author,
        criteria,
        findings,
        tuple(kept),
        tuple(kept_raw),
        not_checked,
        shape.texts("tryIt"),
    )
    return (None if problems else record), problems


@dataclass(frozen=True)
class Facts:
    """What the record is held to, none of it written by the tester.

    ``answers`` holds the stored answer of each cited request whose probe matches the log;
    ``screenshots`` the cited paths that are PNG files inside the tester's directory;
    ``code`` the repository paths of the product's files; ``served`` whether the product ever
    answered in this session.
    """

    session: str
    tree: str
    web_ui: bool
    promises: tuple[str, ...]
    log: dict[str, Entry]
    answers: dict[str, bytes]
    screenshots: frozenset[str]
    code: tuple[str, ...]
    served: bool


def suffix(session: str) -> str:
    return session.rsplit("-", 1)[-1]


def citation_problems(where: str, cited: Citation, facts: Facts) -> list[str]:
    entry = facts.log.get(cited.request)
    if entry is None:
        prefix = cited.request.rsplit("-", 1)[0]
        if prefix != suffix(facts.session):
            return [f"{where} cites request {cited.request}, from another session, not this one"]
        return [f"{where} cites request {cited.request}, which this session never logged"]
    said = f"{cited.method} {cited.path} answered {cited.status}"
    logged = f"{entry.method} {entry.path} answered {entry.status}"
    if said != logged:
        return [f"{where} cites request {cited.request} as {said}, but the log has {logged}"]
    if not cited.contains:
        return []
    answer = facts.answers.get(cited.request)
    if answer is None:
        return [f"{where} cites request {cited.request}, whose stored answer was changed or lost"]
    body = answer.split(b"\r\n\r\n", 1)[-1]
    return [
        f"{where} says request {cited.request} showed {text!r}, but its answer does not"
        for text in cited.contains
        if text.encode() not in body
    ]


def evidence_problems(where: str, item: Criterion | Finding, facts: Facts) -> list[str]:
    found = [line for cited in item.requests for line in citation_problems(where, cited, facts)]
    return found + [
        f"{where} cites screenshot {path}, which is not a PNG file in the tester's directory"
        for path in item.screenshots
        if path not in facts.screenshots
    ]


def criterion_problems(item: Criterion, facts: Facts, kept: set[str]) -> list[str]:
    where = f"criterion {item.id}"
    problems = evidence_problems(where, item, facts)
    if item.verdict not in VERDICTS:
        problems.append(f"{where} has verdict {item.verdict!r}, not one of {', '.join(VERDICTS)}")
    if item.verdict == "pass":
        problems += pass_problems(item, facts, kept)
    if item.verdict == "fail" and not item.steps:
        problems.append(f"{where} fails with no steps to reproduce it")
    if item.verdict == "unsure" and not item.why:
        problems.append(f"{where} is unsure and does not say why")
    if item.verdict == "not-observable" and not item.judge:
        problems.append(f"{where} is not observable and does not say who can judge it")
    return problems


def pass_problems(item: Criterion, facts: Facts, kept: set[str]) -> list[str]:
    where = f"criterion {item.id}"
    problems = []
    if not facts.served:
        problems.append(f"{where} passes, but the product never answered in this session")
    elif not item.requests:
        problems.append(f"{where} passes without citing a request this session logged")
    if facts.web_ui and not item.screenshots:
        problems.append(f"{where} passes on a web-ui without a screenshot")
    if item.id not in kept and not item.unkept:
        problems.append(f"{where} passes with no kept check and no reason it cannot be kept")
    return problems


def finding_problems(item: Finding, facts: Facts, author: str) -> list[str]:
    where = f"finding {item.id}"
    problems = evidence_problems(where, item, facts)
    if item.severity not in SEVERITIES:
        problems.append(
            f"{where} has severity {item.severity!r}, not one of {', '.join(SEVERITIES)}"
        )
    if item.scope != "this" and not (item.scope.startswith(LATER) and item.scope[len(LATER) :]):
        problems.append(f"{where} has scope {item.scope!r}, not `this` or `later: <ticket>`")
    if not item.steps:
        problems.append(f"{where} has no steps to reproduce it")
    unseen = not item.requests and not item.screenshots
    if item.severity in HARMFUL and unseen and author != HARNESS:
        problems.append(f"{where} is a {item.severity} that cites nothing the tester saw")
    return problems


def promise_problems(record: Record, facts: Facts) -> list[str]:
    given = [plain(item.text) for item in record.criteria]
    problems = [
        f"the record gives no verdict on the ticket's line {text!r}"
        for text in facts.promises
        if text not in given
    ]
    problems += [
        f"criterion {item.id} is not a line of the ticket: {item.text!r}"
        for item in record.criteria
        if plain(item.text) not in facts.promises
    ]
    return problems + [
        f"the record gives {given.count(text)} verdicts on the ticket's line {text!r}"
        for text in dict.fromkeys(given)
        if given.count(text) > 1
    ]


def identity_problems(record: Record, facts: Facts) -> list[str]:
    problems = []
    if record.session != facts.session:
        problems.append(f"the record is for session {record.session}, not {facts.session}")
    if record.tree != facts.tree:
        problems.append(f"the record is for tree {record.tree}, but tree {facts.tree} was served")
    if record.author not in (TESTER, HARNESS):
        problems.append(f"the record's author is {record.author!r}, not tester or harness")
    if record.author == HARNESS and facts.served:
        problems.append("the record says the harness wrote it, but the product did start")
    for kind, ids in (
        ("criterion", [item.id for item in record.criteria]),
        ("finding", [item.id for item in record.findings]),
        ("kept check", [check.id for check in record.kept]),
    ):
        problems += [f"two {kind}s are called {i}" for i in dict.fromkeys(ids) if ids.count(i) > 1]
    return problems


def kept_problems(record: Record) -> list[str]:
    passed = {item.id for item in record.criteria if item.verdict == "pass"}
    return [
        f"kept check {check.id} keeps {check.criterion}, which did not pass"
        for check in record.kept
        if check.criterion not in passed
    ]


def written(record: Record) -> Iterator[tuple[str, str]]:
    """Every text the tester wrote, but the ticket's own lines, with where it is."""
    for item in record.criteria:
        for text in (item.how, *item.steps, item.why, item.judge, item.unkept):
            yield f"criterion {item.id}", text
    for found in record.findings:
        for text in (found.title, *found.steps, found.expected, found.actual):
            yield f"finding {found.id}", text
    for what, why in record.not_checked:
        yield "notChecked", f"{what} {why}"
    for text in record.try_it:
        yield "tryIt", text


def mention_problems(record: Record, code: tuple[str, ...]) -> list[str]:
    """A file of the code named as a file: not after ``/``, which makes it a path on the site."""
    if not code:
        return []
    named = re.compile(
        r"(?<![\w/.-])(" + "|".join(map(re.escape, code)) + r")(?![\w/-])",
    )
    problems = []
    for where, text in written(record):
        problems += [
            f"{where} names {path}, a file of the product's code; the tester judges by use and"
            " never by reading"
            for path in dict.fromkeys(named.findall(text))
        ]
    return problems


def judge(record: Record, facts: Facts) -> list[str]:
    """Every rule the record breaks, as a sentence; empty when it is accepted."""
    kept = {check.criterion for check in record.kept}
    problems = identity_problems(record, facts) + promise_problems(record, facts)
    for item in record.criteria:
        problems += criterion_problems(item, facts, kept)
    for found in record.findings:
        problems += finding_problems(found, facts, record.author)
    # Why the harness is exempt: its record quotes the boot command, which may name a file.
    code = () if record.author == HARNESS else facts.code
    return problems + kept_problems(record) + mention_problems(record, code)


def cannot_start(session: str, tree: str, *, ticket: list[str], said: str) -> dict[str, object]:
    """The record the harness writes itself when the product cannot be started."""
    criteria = [
        {"id": f"C{n}", "text": text, "verdict": "unsure", "how": CANNOT_START, "why": CANNOT_START}
        for n, text in enumerate(ticket, 1)
    ]
    blocker: dict[str, object] = {
        "id": "F1",
        "severity": "blocker",
        "scope": "this",
        "title": "The owner cannot start this",
        "steps": ["Start it the way the project says it starts."],
        "expected": "It starts and answers, so it can be tried.",
        "actual": said,
    }
    return {
        "session": session,
        "tree": tree,
        "author": HARNESS,
        "criteria": criteria,
        "findings": [blocker],
        "kept": [],
        "notChecked": [{"what": "everything on the ticket", "why": CANNOT_START}],
        "tryIt": [],
    }


def digest(value: object) -> str:
    """The SHA-256 of ``value`` as canonical JSON, so the same checks always hash alike."""
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


def tally(results: object, checks: str, *, control: bool) -> dict[str, int] | None:
    """A replay's counts when it ran these very checks; ``None`` when it did not run them."""
    if not isinstance(results, dict) or results.get("checks") != checks:
        return None
    ran = results.get("results")
    held = (
        [item.get("holds") is True for item in ran if isinstance(item, dict)]
        if isinstance(ran, list)
        else []
    )
    key = "failed" if control else "passed"
    return {key: held.count(not control), "of": len(held)}


@dataclass(frozen=True)
class Session:
    """What the harness recorded about a session: the summary's facts that are not the record's."""

    slug: str
    started: str
    seal: Seal | None


def effects(record: Record) -> tuple[list[str], list[str]]:
    """What keeps the change from merging, and what the owner must decide."""
    blocking = [item.id for item in record.criteria if item.verdict == "fail"]
    blocking += [
        found.id for found in record.findings if found.severity in HARMFUL and found.scope == "this"
    ]
    questions = [item.id for item in record.criteria if item.verdict == "unsure"]
    questions += [
        found.id
        for found in record.findings
        if found.severity == "blocker" and found.scope != "this"
    ]
    return blocking, questions


def summary(record: Record, session: Session, replays: tuple[object, object]) -> dict[str, object]:
    """The committed summary of an accepted record: recorded facts only, the same on every run."""
    blocking, questions = effects(record)
    checks = digest(list(record.kept_raw))
    kept_ids = {check.criterion for check in record.kept}
    return {
        "schema": SCHEMA,
        "slug": session.slug,
        "tree": record.tree,
        "session": record.session,
        "started": session.started,
        "ended": session.seal.ended if session.seal else None,
        "requests": session.seal.requests if session.seal else 0,
        "log": session.seal.sha256 if session.seal else None,
        "blocking": blocking,
        "questions": questions,
        "criteria": [
            {"id": item.id, "text": item.text, "verdict": item.verdict, "how": item.how}
            | ({"why": item.why} if item.why else {})
            | ({"judge": item.judge} if item.judge else {})
            for item in record.criteria
        ],
        "findings": [
            {
                "id": found.id,
                "severity": found.severity,
                "scope": found.scope,
                "title": found.title,
                "steps": list(found.steps),
                "expected": found.expected,
                "actual": found.actual,
            }
            for found in record.findings
        ],
        "kept": len(record.kept),
        "unkept": [
            {"criterion": item.id, "why": item.unkept}
            for item in record.criteria
            if item.verdict == "pass" and item.id not in kept_ids
        ],
        "replay": tally(replays[0], checks, control=False),
        "control": tally(replays[1], checks, control=True),
        "notChecked": [{"what": what, "why": why} for what, why in record.not_checked],
        "tryIt": list(record.try_it),
    }
