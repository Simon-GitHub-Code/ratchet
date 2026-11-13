"""The debugging-pipe driver, against a stand-in browser on a real pipe; see ``browsers.py``."""

import re
import tempfile
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
def test_a_script_that_throws_is_the_pages_error_not_the_browsers(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *, expression: str, said: str
) -> None:
    browsers.fake(tmp_path, monkeypatch)
    with tab(tmp_path) as page, pytest.raises(cdp.PageError, match=re.escape(said)):  # type: ignore[attr-defined]
        page.evaluate(expression)


@pytest.mark.parametrize(
    ("url", "said"),
    [
        ("http://unreachable/", "did not open: net::ERR_NAME_NOT_RESOLVED"),
        ("http://stuck/", "did not finish loading within 0.5s"),
        ("http://crash/", "the browser closed while it was asked Page.loadEventFired"),
    ],
)
def test_a_page_that_does_not_open_is_a_browser_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *, url: str, said: str
) -> None:
    browsers.fake(tmp_path, monkeypatch)
    with tab(tmp_path) as page:  # type: ignore[attr-defined]
        monkeypatch.setattr(cdp, "ANSWER", 0.5)
        with pytest.raises(cdp.BrowserError, match=re.escape(said)):
            page.open(url)
    assert products.processes_naming(str(tmp_path)) == []


def test_a_click_whose_page_never_loads_is_a_browser_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    browsers.fake(tmp_path, monkeypatch)
    stall = "(text => true)('Stall')"
    with tab(tmp_path) as page:  # type: ignore[attr-defined]
        monkeypatch.setattr(cdp, "ANSWER", 0.5)
        page.act(stall, 0.1)
        said = "the page did not finish loading within 0.5s"
        with pytest.raises(cdp.BrowserError, match=re.escape(said)):
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
        ("deaf", 375, "the browser closed while it was asked Page.enable"),
    ],
)
def test_a_browser_that_fails_says_how(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *, mode: str, width: int, said: str
) -> None:
    browsers.fake(tmp_path, monkeypatch, mode)
    with pytest.raises(cdp.BrowserError, match=re.escape(said)), tab(tmp_path, width):
        pass
    assert products.processes_naming(str(tmp_path)) == []


def test_a_browser_that_never_answers_is_given_up_on(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    browsers.fake(tmp_path, monkeypatch, "mute")
    monkeypatch.setattr(cdp, "ANSWER", 0.5)
    said = "the browser did not answer Target.createTarget in time; it printed nothing"
    with pytest.raises(cdp.BrowserError, match=re.escape(said)), tab(tmp_path):
        pass
    assert products.processes_naming(str(tmp_path)) == []


def test_the_browser_keeps_its_temporary_files_in_a_short_directory_that_is_removed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    browsers.fake(tmp_path, monkeypatch)
    long = tmp_path / ("a-long-name" * 12)
    long.mkdir()
    monkeypatch.setenv("TMPDIR", str(long))
    # Why: Python reads TMPDIR once and keeps it; this makes it read the long one, as a process
    # started under it would.
    monkeypatch.setattr(tempfile, "tempdir", None)
    with tab(tmp_path):
        used = Path((tmp_path / "profile" / "tmpdir").read_text(encoding="utf-8"))
        socket = used / ".org.chromium.Chromium.XXXXXX" / "SingletonSocket"
        assert len(str(socket)) < 108, "a socket's path has room for 107 bytes"
        assert (used / "org.chromium.Chromium.fake").is_file()
    assert not used.exists()


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
