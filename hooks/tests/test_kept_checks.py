"""Kept checks: read as data, run against a fresh product over HTTP and in a browser."""

import io
from http.client import parse_headers
from pathlib import Path

import pytest

import gatelib
import kept_checks
from tests import browsers, products

SAVES = {
    "id": "K1",
    "criterion": "C1",
    "via": "http",
    "steps": [
        {
            "request": "POST /items",
            "form": {"link": "https://example.com/{unique}"},
            "headers": {"Origin": "{BASE_URL}"},
            "expect": {"status": 303, "headers": {"location": "/"}},
        },
        {
            "request": "GET /",
            "expect": {
                "status": 200,
                "contains": ["Link to save", "https://example.com/{unique}"],
                "lacks": ["There is nothing left to read."],
                "count": {"https://example.com/{unique}": 2},
            },
        },
    ],
}
IN_A_BROWSER = {
    "id": "K2",
    "criterion": "C1",
    "via": "browser",
    "width": 375,
    "steps": [
        {"open": "/"},
        {"fill": "Link to save", "with": "https://example.com/{unique}"},
        {"click": "Save"},
        {"see": "https://example.com/{unique}"},
        {"lacks": "There is nothing left to read."},
        {"true": "document.documentElement.scrollWidth <= innerWidth"},
    ],
}


def parsed(raw: object) -> kept_checks.Check:
    check, problems = kept_checks.parse(raw)
    assert check is not None, problems
    return check


def with_step(n: int, **step: object) -> dict[str, object]:
    raw = {**SAVES, "steps": [dict(s) for s in SAVES["steps"]]}  # type: ignore[attr-defined]
    raw["steps"][n] = {**raw["steps"][n], **step}  # type: ignore[index]
    return raw


@pytest.mark.parametrize(
    ("raw", "said"),
    [
        ([], "a kept check is not an object"),
        ({**SAVES, "via": "carrier pigeon"}, "kept check K1 needs via as http or browser"),
        (
            {**SAVES, "width": 10},
            "kept check K1 needs width as a number of pixels from 200 to 4000",
        ),
        ({**SAVES, "steps": []}, "kept check K1 needs steps as a non-empty list"),
        ({**SAVES, "id": ""}, "a kept check needs id as a non-empty text"),
        (
            with_step(0, request="POST https://elsewhere.example/items"),
            (
                "kept check K1 step 1 needs request as METHOD /path, a path on BASE_URL and never"
                " a host"
            ),
        ),
        (
            with_step(0, body="x"),
            "kept check K1 step 1 has both form and body; a request sends one",
        ),
        (with_step(0, form=["link"]), "kept check K1 step 1 needs form as an object"),
        (
            with_step(0, form={"link": 1}),
            "kept check K1 step 1 needs form with a text for each name",
        ),
        (
            with_step(0, expect={"status": 99}),
            "kept check K1 step 1's expect needs status as a number from 100 to 599",
        ),
        (
            with_step(0, expect={"count": {"x": -1}}),
            "kept check K1 step 1's expect needs count with a whole number for each text",
        ),
        (
            with_step(0, expect={"status": 303, "screenshot": True}),
            "kept check K1 step 1's expect names screenshot, which a check cannot expect",
        ),
        (with_step(1, expect=[]), "kept check K1 step 2's expect is not an object"),
        (
            with_step(1, expect={"contains": "x"}),
            "kept check K1 step 2's expect needs contains as a list of non-empty texts",
        ),
        (
            {**SAVES, "steps": [{"request": "GET /"}]},
            "kept check K1 expects nothing, so it could never fail",
        ),
        (
            {**IN_A_BROWSER, "steps": [{"open": "/"}]},
            "kept check K2 expects nothing, so it could never fail",
        ),
        (
            {**IN_A_BROWSER, "steps": [{"open": "https://example.com/"}]},
            "kept check K2 step 1 needs open as a path on BASE_URL, never a host",
        ),
        (
            {**IN_A_BROWSER, "steps": [{"open": "/", "see": "x"}]},
            "kept check K2 step 1 needs exactly one of open, fill, click, see, lacks, true",
        ),
        (
            {**IN_A_BROWSER, "steps": [{"fill": "Link to save"}]},
            "kept check K2 step 1 needs with as a non-empty text",
        ),
    ],
)
def test_a_check_that_does_not_read_says_why(raw: object, said: str) -> None:
    check, problems = kept_checks.parse(raw)
    assert check is None
    assert said in problems


def test_a_check_names_paths_and_values_never_the_machine_it_was_written_on() -> None:
    check = parsed(SAVES)
    assert isinstance(check, kept_checks.HttpCheck)
    assert check.steps[0].path == "/items"
    assert check.steps[0].headers[-1] == ("Content-Type", "application/x-www-form-urlencoded")
    browser = parsed(IN_A_BROWSER)
    assert isinstance(browser, kept_checks.BrowserCheck)
    assert (browser.width, browser.steps[1]) == (
        375,
        kept_checks.Action("fill", "Link to save", "https://example.com/{unique}"),
    )


def answer(status: int, body: bytes, headers: bytes = b"") -> tuple[int, object, bytes]:
    return status, parse_headers(io.BytesIO(headers + b"\r\n")), body


@pytest.mark.parametrize(
    ("expect", "said"),
    [
        (kept_checks.Expect(status=303), "answered 200, expected 303"),
        (
            kept_checks.Expect(headers=(("Location", "/"),)),
            "sent Location: '/elsewhere', expected '/'",
        ),
        (kept_checks.Expect(contains=("b", "b")), "does not show 'b' after what came before it"),
        (kept_checks.Expect(contains=("z",)), "does not show 'z'"),
        (kept_checks.Expect(lacks=("b",)), "shows 'b'"),
        (kept_checks.Expect(count=(("a", 1),)), "shows 'a' 2 times, expected 1"),
        (kept_checks.Expect(status=200, contains=("a", "b"), count=(("a", 2),)), None),
    ],
)
def test_what_a_response_must_show(expect: kept_checks.Expect, said: str | None) -> None:
    got = answer(200, b"a b a", b"Location: /elsewhere\r\n")
    assert kept_checks.unmet(expect, got) == said  # type: ignore[arg-type]


def test_an_http_check_holds_on_the_product_with_a_new_value_each_run(tmp_path: Path) -> None:
    check = parsed(SAVES)
    with products.serving(tmp_path) as url:
        assert kept_checks.run(check, url, tmp_path) is None
        assert kept_checks.run(check, url, tmp_path) is None, "{unique} is new on every run"
        stale = with_step(1, expect={"count": {"https://example.com/{unique}": 4}})
        reason = kept_checks.run(parsed(stale), url, tmp_path)
        assert reason is not None
        assert reason.startswith("step 2, GET /, shows 'https://example.com/")
        assert reason.endswith("' 2 times, expected 4")
        sent = {**SAVES, "steps": [{"request": "PUT /x", "body": "{unique}", "expect": {}}]}
        sent["steps"][0]["expect"] = {"contains": ["PUT {unique}"]}  # type: ignore[index]
        assert kept_checks.run(parsed(sent), url, tmp_path) is None
    nothing = f"http://127.0.0.1:{gatelib.free_port()}"
    reason = kept_checks.run(check, nothing, tmp_path)
    assert reason is not None
    assert reason.startswith("step 1, POST /items, got no answer: [Errno 111]")


def test_a_browser_check_waits_for_each_step_and_says_which_did_not_hold(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    browsers.fake(tmp_path, monkeypatch)
    monkeypatch.setattr(kept_checks, "WAIT", 1.0)
    assert kept_checks.run(parsed(IN_A_BROWSER), "http://127.0.0.1:9", tmp_path) is None
    never = {**IN_A_BROWSER, "steps": [{"open": "/"}, {"see": "never"}]}
    assert kept_checks.run(parsed(never), "http://127.0.0.1:9", tmp_path) == (
        'step 2, see, does not show "never"'
    )
    for kind, said in (
        ("fill", 'found no field labelled "never"'),
        ("click", 'found nothing to click that says "never"'),
        ("lacks", 'shows "never"'),
        ("true", "found `never` not true"),
    ):
        step = {kind: "never", "with": "x"} if kind == "fill" else {kind: "never"}
        failing = {**IN_A_BROWSER, "steps": [step]}
        assert kept_checks.run(parsed(failing), "http://127.0.0.1:9", tmp_path) == (
            f"step 1, {kind}, {said}"
        )
    crashes = {**IN_A_BROWSER, "steps": [{"open": "/crash"}, {"see": "x"}]}
    reason = kept_checks.run(parsed(crashes), "http://127.0.0.1:9", tmp_path)
    assert reason == "the browser failed: the browser closed while it was asked Page.loadEventFired"
    assert products.processes_naming(str(tmp_path)) == []


def test_a_browser_check_with_no_browser_says_how_to_name_one(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("RATCHET_BROWSER", raising=False)
    monkeypatch.setenv("PATH", str(tmp_path))
    reason = kept_checks.run(parsed(IN_A_BROWSER), "http://127.0.0.1:9", tmp_path)
    assert reason == "needs a browser: set RATCHET_BROWSER to a Chromium or Chrome executable"


@pytest.mark.skipif(browsers.real() is None, reason="no Chromium or Chrome on this machine")
def test_a_browser_check_holds_in_a_real_browser_and_fails_when_the_page_does_not_show_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("RATCHET_BROWSER", browsers.real() or "")
    for profile in ("one", "two", "three"):
        (tmp_path / profile).mkdir()
    with products.serving(tmp_path) as url:
        assert kept_checks.run(parsed(IN_A_BROWSER), url, tmp_path / "one") is None
        wrong = {**IN_A_BROWSER, "steps": [*IN_A_BROWSER["steps"][:3], {"see": "Saved!"}]}  # type: ignore[misc]
        monkeypatch.setattr(kept_checks, "WAIT", 1.0)
        assert kept_checks.run(parsed(wrong), url, tmp_path / "two") == (
            'step 4, see, does not show "Saved!"'
        )
        reach = "fetch('https://example.com/').then(() => true, () => false)"
        outside = {**IN_A_BROWSER, "steps": [{"open": "/"}, {"true": reach}]}
        assert kept_checks.run(parsed(outside), url, tmp_path / "three") == (
            f"step 2, true, found `{reach}` not true"
        ), "nothing outside this machine is reachable from the page"
    assert products.processes_naming(str(tmp_path)) == []
