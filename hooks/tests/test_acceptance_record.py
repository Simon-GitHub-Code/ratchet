"""The acceptance record: each rule a careless or dishonest record breaks is one plain sentence."""

import copy
import dataclasses
import hashlib
import json
from collections.abc import Callable

import pytest

import acceptance_record as ar
from tests import products
from tests.products import PROMISES

SESSION = "20261113T000000000000Z-abcd1234"
HEAD = "a" * 40
IDS = ("abcd1234-1", "abcd1234-2", "abcd1234-3")
EMPTY = b"HTTP/1.1 200 OK\r\nContent-Type: text/html\r\n\r\n<p>There is nothing left to read.</p>"
SAVED = b"HTTP/1.1 303 See Other\r\nLocation: /\r\n\r\n"
READS = "a file of the product's code; the tester judges by use and never by reading"
LISTED = b'HTTP/1.1 200 OK\r\n\r\n<a href="https://example.com/a">https://example.com/a</a>'


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


LOG = {
    IDS[0]: ar.Entry(IDS[0], "GET", "/", 200, sha(EMPTY)),
    IDS[1]: ar.Entry(IDS[1], "POST", "/items", 303, sha(SAVED)),
    IDS[2]: ar.Entry(IDS[2], "GET", "/", 200, sha(LISTED)),
}
FACTS = ar.Facts(
    session=SESSION,
    head=HEAD,
    web_ui=True,
    promises=PROMISES,
    log=LOG,
    answers={IDS[0]: EMPTY, IDS[1]: SAVED, IDS[2]: LISTED},
    screenshots=frozenset({"shots/list.png"}),
    code=("src/settings.py", "docs/process/binding.md"),
    served=True,
)


def good() -> dict[str, object]:
    return products.record(SESSION, HEAD, IDS)


def judged(raw: object, **facts: object) -> list[str]:
    record, problems = ar.parse_record(raw)
    assert record is not None, problems
    return ar.judge(record, dataclasses.replace(FACTS, **facts))


def criterion(raw: dict[str, object], n: int) -> dict[str, object]:
    return raw["criteria"][n]


def finding(raw: dict[str, object]) -> dict[str, object]:
    return raw["findings"][0]


def test_a_record_that_cites_what_was_logged_is_accepted() -> None:
    assert judged(good()) == []


def test_the_ticket_promises_are_its_checklist_lines_with_whitespace_collapsed() -> None:
    ticket = "# T\n\nSome prose.\n- [ ] One  thing.\n  * [X] Another\tthing.  \n- not a box\n"
    assert ar.promises(ticket) == ["One thing.", "Another thing."]
    assert ar.promises(products.TICKET) == list(PROMISES)


Mutate = Callable[[dict[str, object]], object]
NO_STEPS: Mutate = lambda r: criterion(r, 2).update(verdict="fail")  # noqa: E731
REFUSED: list[tuple[str, Mutate, dict[str, object], str]] = [
    # The prototype's six seeded records, in this record's terms.
    (
        "missing evidence",
        lambda r: criterion(r, 0)["requests"][0].update(request="abcd1234-99"),
        {},
        "criterion C1 cites request abcd1234-99, which this session never logged",
    ),
    (
        "empty evidence",
        lambda _: None,
        {"answers": {IDS[0]: EMPTY, IDS[1]: SAVED}},
        "criterion C1 cites request abcd1234-3, whose stored answer was changed or lost",
    ),
    (
        "evidence that only shows it started",
        lambda r: criterion(r, 0).update(
            requests=[{**criterion(r, 1)["requests"][0], "contains": ["https://example.com/a"]}]
        ),
        {},
        (
            "criterion C1 says request abcd1234-1 showed 'https://example.com/a',"
            " but its answer does not"
        ),
    ),
    (
        "a pass when nothing started",
        lambda _: None,
        {"served": False, "log": {}},
        "criterion C1 passes, but the product never answered in this session",
    ),
    (
        "a pass with nothing kept",
        lambda r: r.update(kept=[]),
        {},
        "criterion C1 passes with no kept check and no reason it cannot be kept",
    ),
    (
        "evidence outside the session",
        lambda r: criterion(r, 0).update(screenshots=["/etc/hostname"]),
        {},
        (
            "criterion C1 cites screenshot /etc/hostname, which is not a PNG file in the tester's"
            " directory"
        ),
    ),
    # The brief's.
    (
        "another run's request",
        lambda r: criterion(r, 0)["requests"][0].update(request="ffff0000-2"),
        {},
        "criterion C1 cites request ffff0000-2, from another session, not this one",
    ),
    (
        "a logged id with another path",
        lambda r: criterion(r, 0)["requests"][0].update(method="GET", path="/", status=200),
        {},
        (
            "criterion C1 cites request abcd1234-2 as GET / answered 200,"
            " but the log has POST /items answered 303"
        ),
    ),
    (
        "a record for another commit",
        lambda r: r.update(head="b" * 40),
        {},
        f"the record is for commit {'b' * 40}, but {HEAD} was tried",
    ),
    # Ours.
    (
        "a record for another session",
        lambda r: r.update(session="20261112T000000000000Z-ffff0000"),
        {},
        f"the record is for session 20261112T000000000000Z-ffff0000, not {SESSION}",
    ),
    (
        "a line judged twice",
        lambda r: r["criteria"].append({**criterion(r, 2), "id": "C4"}),
        {},
        f"the record gives 2 verdicts on the ticket's line {PROMISES[2]!r}",
    ),
    (
        "a line left out",
        lambda r: r["criteria"].pop(),
        {},
        f"the record gives no verdict on the ticket's line {PROMISES[2]!r}",
    ),
    (
        "a line the ticket does not have",
        lambda r: criterion(r, 2).update(text="It is fast."),
        {},
        "criterion C3 is not a line of the ticket: 'It is fast.'",
    ),
    (
        "two criteria with one id",
        lambda r: criterion(r, 2).update(id="C1"),
        {},
        "two criterions are called C1",
    ),
    (
        "a verdict outside the set",
        lambda r: criterion(r, 2).update(verdict="partial"),
        {},
        "criterion C3 has verdict 'partial', not one of pass, fail, unsure, not-observable",
    ),
    ("a fail with no steps", NO_STEPS, {}, "criterion C3 fails with no steps to reproduce it"),
    (
        "an unsure that does not say why",
        lambda r: criterion(r, 2).update(verdict="unsure"),
        {},
        "criterion C3 is unsure and does not say why",
    ),
    (
        "a not-observable nobody can judge",
        lambda r: criterion(r, 2).pop("judge"),
        {},
        "criterion C3 is not observable and does not say who can judge it",
    ),
    (
        "a pass that cites no request",
        lambda r: criterion(r, 1).update(requests=[]),
        {},
        "criterion C2 passes without citing a request this session logged",
    ),
    (
        "a web-ui pass with no screenshot",
        lambda r: criterion(r, 1).update(screenshots=[]),
        {},
        "criterion C2 passes on a web-ui without a screenshot",
    ),
    (
        "a missing screenshot",
        lambda r: criterion(r, 1).update(screenshots=["shots/none.png"]),
        {},
        (
            "criterion C2 cites screenshot shots/none.png, which is not a PNG file in the tester's"
            " directory"
        ),
    ),
    (
        "a blocker that saw nothing",
        lambda r: finding(r).update(severity="blocker", requests=[]),
        {},
        "finding F1 is a blocker that cites nothing the tester saw",
    ),
    (
        "a severity outside the set",
        lambda r: finding(r).update(severity="huge"),
        {},
        "finding F1 has severity 'huge', not one of blocker, major, minor, note",
    ),
    (
        "a scope with no ticket",
        lambda r: finding(r).update(scope="later: "),
        {},
        "finding F1 has scope 'later: ', not `this` or `later: <ticket>`",
    ),
    (
        "a finding with no steps",
        lambda r: finding(r).update(steps=[]),
        {},
        "finding F1 has no steps to reproduce it",
    ),
    (
        "a finding that cites another session",
        lambda r: finding(r)["requests"][0].update(request="ffff0000-1"),
        {},
        "finding F1 cites request ffff0000-1, from another session, not this one",
    ),
    (
        "a check that keeps what did not pass",
        lambda r: r["kept"][0].update(criterion="C3"),
        {},
        "kept check K1 keeps C3, which did not pass",
    ),
    (
        "two checks with one id",
        lambda r: r["kept"].append(copy.deepcopy(r["kept"][0])),
        {},
        "two kept checks are called K1",
    ),
    (
        "a record that read the code",
        lambda r: criterion(r, 0).update(how="Read src/settings.py, then saved a link."),
        {},
        f"criterion C1 names src/settings.py, {READS}",
    ),
    (
        "a finding that read the code",
        lambda r: finding(r).update(actual="docs/process/binding.md says otherwise."),
        {},
        f"finding F1 names docs/process/binding.md, {READS}",
    ),
    (
        "a note on what was not checked that read the code",
        lambda r: r.update(notChecked=[{"what": "src/settings.py", "why": "It is code."}]),
        {},
        f"notChecked names src/settings.py, {READS}",
    ),
    (
        "a way to try it that reads the code",
        lambda r: r.update(tryIt=["Open src/settings.py."]),
        {},
        f"tryIt names src/settings.py, {READS}",
    ),
    (
        "a tester that signs as the harness",
        lambda r: r.update(author="harness"),
        {},
        "the record says the harness wrote it, but the product did start",
    ),
    (
        "an author nobody is",
        lambda r: r.update(author="owner"),
        {},
        "the record's author is 'owner', not tester or harness",
    ),
]


@pytest.mark.parametrize(
    ("mutate", "facts", "sentence"),
    [pytest.param(m, f, s, id=name) for name, m, f, s in REFUSED],
)
def test_each_broken_rule_is_one_sentence(
    mutate: Mutate, facts: dict[str, object], sentence: str
) -> None:
    raw = good()
    mutate(raw)
    assert sentence in judged(raw, **facts)


def test_a_path_on_the_site_is_not_a_file_of_the_code() -> None:
    raw = good()
    criterion(raw, 0).update(how="Opened /src/settings.py on the site; it answered 404.")
    assert judged(raw) == []
    assert judged(raw, code=()) == []


def test_a_finding_that_is_a_note_needs_no_evidence_and_a_later_scope_names_a_ticket() -> None:
    raw = good()
    finding(raw).update(severity="note", requests=[], scope="this")
    assert judged(raw) == []


def test_a_service_needs_no_screenshot() -> None:
    raw = good()
    criterion(raw, 0).pop("screenshots")
    criterion(raw, 1).pop("screenshots")
    assert judged(raw, web_ui=False) == []


@pytest.mark.parametrize(
    ("raw", "sentence"),
    [
        ("a list", "the record is not an object"),
        ({}, "the record needs session as a non-empty text"),
        ({**good(), "criteria": {}}, "the record needs criteria as a list"),
        ({**good(), "kept": {}}, "the record needs kept as a list"),
        ({**good(), "kept": [7]}, "kept item 1 is not an object"),
        ({**good(), "criteria": [{}]}, "criteria item 1 needs id as a non-empty text"),
        (
            {**good(), "criteria": [{"id": "C1", "requests": {}}]},
            "criterion C1 needs requests as a list",
        ),
        (
            {**good(), "criteria": [{"id": "C1", "requests": [{"status": "200"}]}]},
            "criterion C1 request 1 needs status as the number the log holds",
        ),
        (
            {**good(), "criteria": [{"id": "C1", "requests": [{"status": True}]}]},
            "criterion C1 request 1 needs status as the number the log holds",
        ),
        (
            {**good(), "findings": [{"id": "F1", "steps": "do it"}]},
            "finding F1 needs steps as a list of non-empty texts",
        ),
        ({**good(), "findings": [{}]}, "findings item 1 needs id as a non-empty text"),
        (
            {**good(), "notChecked": [{"what": "x"}]},
            "notChecked item 1 needs why as a non-empty text",
        ),
        ({**good(), "author": ""}, "the record needs author as a non-empty text"),
    ],
)
def test_a_record_not_in_the_shape_is_refused_before_it_is_judged(
    raw: object, sentence: str
) -> None:
    record, problems = ar.parse_record(raw)
    assert record is None
    assert sentence in problems


def test_the_log_reads_each_line_and_names_each_that_is_not_a_request() -> None:
    line = json.dumps(
        {"id": "x-1", "method": "GET", "path": "/", "status": 200, "responseSha256": "d"}
    )
    data = f"{line}\nnot json\n{{}}\n".encode()
    entries, problems = ar.read_log(data)
    assert entries == {"x-1": ar.Entry("x-1", "GET", "/", 200, "d")}
    assert problems == [
        "line 2 of the request log is not a logged request",
        "line 3 of the request log is not a logged request",
    ]


TICKET, SESSION_FILE = b"- [ ] One thing.\n", b'{"session": "s"}\n'
SEALED = {
    "requests": 2,
    "sha256": sha(b'{"a": 1}\n{"b": 2}\n'),
    "stopped": "2026-11-13T00:00:01.000Z",
    "ended": "2026-11-13T00:00:01.100Z",
    "ticketSha256": sha(TICKET),
    "sessionSha256": sha(SESSION_FILE),
    "down": {"at": "2026-11-13T00:00:00.900Z", "endedEarly": False},
}


def sealed(data: bytes, raw: object = SEALED, **files: bytes) -> list[str]:
    given = {"ticket": TICKET, "session": SESSION_FILE} | files
    return ar.seal_problems(data, ar.parse_seal(raw), **given)


def test_a_log_is_held_to_its_seal() -> None:
    data = b'{"a": 1}\n{"b": 2}\n'
    assert sealed(data) == []
    assert sealed(data[:9]) == [
        "the request log was cut short: the harness logged 2 requests and the file holds 1"
    ]
    assert sealed(data + b'{"c": 3}\n') == [
        "the request log holds 3 lines but the harness logged 2 requests: lines were added after it"
    ]
    assert sealed(data.replace(b"1", b"7")) == [
        "the request log was changed after the harness wrote it"
    ]
    assert sealed(data, None) == [
        (
            "the request log was never sealed: the proxy was stopped before it could seal it, so"
            " its requests cannot be trusted"
        )
    ]


def test_a_seal_covers_the_session_files_and_says_down_ended_it_in_time() -> None:
    data = b'{"a": 1}\n{"b": 2}\n'
    assert sealed(data, ticket=b"- [ ] Another.\n", session=b"{}") == [
        "the session's copy of the ticket was changed after `up`",
        "session.json was changed after `up`",
    ]
    assert sealed(data, {**SEALED, "down": None}) == [
        "the session was not ended by `down`: run `down`, which says how it ended"
    ]
    early = {**SEALED, "down": {"at": "2026-11-13T00:00:02.000Z", "endedEarly": True}}
    assert sealed(data, early) == [
        (
            "the session ended at 2026-11-13T00:00:01.000Z, before `down` came: something stopped"
            " it early"
        )
    ]
    for broken in ([], {**SEALED, "requests": "two"}, {**SEALED, "down": {}}):
        assert ar.parse_seal(broken) is None


def test_the_harness_record_for_a_product_that_cannot_start_is_accepted_and_blocks() -> None:
    said = "It was started with `node src/server.js` and did not answer: Error: no dist"
    raw = ar.cannot_start(SESSION, HEAD, ticket=list(PROMISES), said=said)
    assert judged(raw, served=False, log={}, answers={}) == [], "it may quote the boot command"
    record, _ = ar.parse_record(raw)
    assert record is not None
    assert ar.effects(record) == (["F1"], ["C1", "C2", "C3"])
    assert record.findings[0].actual == said


def test_what_blocks_the_merge_and_what_the_owner_decides() -> None:
    raw = good()
    criterion(raw, 1).update(verdict="fail", steps=["Open it."])
    finding(raw).update(severity="major", scope="this")
    blocker = {**finding(raw), "id": "F2", "severity": "blocker", "scope": "later: 02"}
    raw["findings"].append(blocker)
    criterion(raw, 2).update(verdict="unsure", why="It is about the code.")
    record, problems = ar.parse_record(raw)
    assert record is not None, problems
    assert ar.effects(record) == (["C2", "F1"], ["C3", "F2"])


def test_the_summary_is_the_records_facts_and_the_replays_of_these_very_checks() -> None:
    record, _ = ar.parse_record(good())
    assert record is not None
    seal = ar.parse_seal(
        {**SEALED, "requests": 3, "sha256": "f" * 64, "ended": "2026-11-13T00:01:00.000Z"}
    )
    assert seal is not None
    session = ar.Session("save-link", "t" * 40, "2026-11-13T00:00:00Z", seal)
    checks = ar.digest(list(record.kept_raw))
    replay = {"checks": checks, "results": [{"holds": True}, {"holds": False}, "x"]}
    control = {"checks": checks, "results": [{"holds": False}]}
    data = ar.summary(record, session, (replay, control))
    assert data == ar.summary(record, session, (replay, control)), "the same bytes every run"
    assert (data["replay"], data["control"]) == ({"passed": 1, "of": 2}, {"failed": 1, "of": 1})
    assert (data["requests"], data["log"], data["ended"]) == (3, "f" * 64, seal.ended)
    assert data["unkept"] == [
        {"criterion": "C2", "why": criterion(good(), 1)["unkept"]},
    ]
    assert data["criteria"][2] == {
        "id": "C3",
        "text": PROMISES[2],
        "verdict": "not-observable",
        "how": "This is about how the code is built.",
        "judge": "code review",
    }
    stale = {"checks": "another digest", "results": [{"holds": True}]}
    later = ar.summary(record, dataclasses.replace(session, seal=None), (stale, {"checks": checks}))
    assert (later["replay"], later["control"]) == (None, {"failed": 0, "of": 0})
    assert (later["requests"], later["log"], later["ended"]) == (0, None, None)
    unsure = good()
    criterion(unsure, 2).update(verdict="unsure", why="Nobody said.")
    record, _ = ar.parse_record(unsure)
    assert record is not None
    assert ar.summary(record, session, (None, None))["criteria"][2]["why"] == "Nobody said."
