"""The debugging-pipe driver, against a stand-in browser on a real pipe; see ``browsers.py``."""

import re
from pathlib import Path

import pytest

import cdp
from tests import browsers, products

CLICK = "(text => true)('Save')"


def tab(tmp_path: Path, width: int = 375) -> object:
    profile = tmp_path / "profile"
    profile.mkdir(exist_ok=True)
    return cdp.page(cdp.executable() or "", profile, width=width, height=800)


def test_a_page_opens_runs_scripts_and_waits_for_the_navigation_a_click_starts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    browsers.fake(tmp_path, monkeypatch)
    with tab(tmp_path) as page:  # type: ignore[attr-defined]
        page.open("http://127.0.0.1:9/")
        assert page.evaluate("document.title") is False
        assert page.evaluate("document.title") is True
        assert page.evaluate("novalue") is None
        assert page.act(CLICK, 0.3) is False, "not found yet: nothing navigates"
        assert page.act(CLICK, 0.3) is True, "it navigated, and the load was waited for"
        assert page.act("(text => true)('Elsewhere')", 0.1) is False
        assert page.act("(text => true)('Elsewhere')", 0.1) is True, "another frame is not waited"
    assert products.processes_naming(str(tmp_path)) == []


@pytest.mark.parametrize(
    ("expression", "said"),
    [("throw", "the page threw: Error: boom"), ("quietly", "the page threw: Uncaught")],
)
def test_a_script_that_throws_is_a_browser_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *, expression: str, said: str
) -> None:
    browsers.fake(tmp_path, monkeypatch)
    with tab(tmp_path) as page, pytest.raises(cdp.BrowserError, match=re.escape(said)):  # type: ignore[attr-defined]
        page.evaluate(expression)


@pytest.mark.parametrize(
    ("url", "said"),
    [
        ("http://unreachable/", "did not open: net::ERR_NAME_NOT_RESOLVED"),
        ("http://stuck/", "did not finish loading within 2s"),
        ("http://crash/", "the browser closed while it was asked Page.loadEventFired"),
    ],
)
def test_a_page_that_does_not_open_is_a_browser_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *, url: str, said: str
) -> None:
    browsers.fake(tmp_path, monkeypatch)
    monkeypatch.setattr(cdp, "ANSWER", 2.0)
    with tab(tmp_path) as page, pytest.raises(cdp.BrowserError, match=re.escape(said)):  # type: ignore[attr-defined]
        page.open(url)
    assert products.processes_naming(str(tmp_path)) == []


def test_a_click_whose_page_never_loads_is_a_browser_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    browsers.fake(tmp_path, monkeypatch)
    monkeypatch.setattr(cdp, "ANSWER", 2.0)
    stall = "(text => true)('Stall')"
    with tab(tmp_path) as page:  # type: ignore[attr-defined]
        page.act(stall, 0.1)
        with pytest.raises(cdp.BrowserError, match="the page did not finish loading within 2s"):
            page.act(stall, 0.1)


@pytest.mark.parametrize(
    ("mode", "width", "said"),
    [
        ("", 999, "Emulation.setDeviceMetricsOverride failed: the width is refused"),
        (
            "die",
            375,
            "the browser closed while it was asked Target.createTarget; the stand-in browser gives",
        ),
        ("mute", 375, "the browser did not answer Target.createTarget in time; it printed nothing"),
    ],
)
def test_a_browser_that_fails_says_how(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *, mode: str, width: int, said: str
) -> None:
    browsers.fake(tmp_path, monkeypatch, mode)
    monkeypatch.setattr(cdp, "ANSWER", 2.0)
    with pytest.raises(cdp.BrowserError, match=re.escape(said)), tab(tmp_path, width):
        pass
    assert products.processes_naming(str(tmp_path)) == []


def test_a_browser_that_cannot_be_started_is_a_browser_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    browsers.fake(tmp_path, monkeypatch)
    monkeypatch.setenv("PATH", str(tmp_path / "nothing"))
    with pytest.raises(cdp.BrowserError, match="could not be started"), tab(tmp_path):
        pass


def test_the_browser_is_the_one_named_or_the_first_usual_name_on_the_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(cdp.ENV, "/opt/some/chrome")
    assert cdp.executable() == "/opt/some/chrome"
    monkeypatch.delenv(cdp.ENV)
    monkeypatch.setenv("PATH", str(tmp_path))
    assert cdp.executable() is None
    found = tmp_path / "google-chrome"
    found.write_text("#!/bin/sh\n", encoding="utf-8")
    found.chmod(0o755)
    assert cdp.executable() == str(found)
