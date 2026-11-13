"""The acceptance harness under signals: whatever is interrupted, nothing it started is left behind.

Each test signals a real ``acceptance.py`` while it boots a product that holds its start, then
looks for what is left: a process naming the test's directory, a session directory, a
temporary directory, an address that still answers.
"""

import json
import os
import signal
import subprocess
import sys
from pathlib import Path

import pytest

import acceptance
import gatelib
from tests import products
from tests.servers import gone, pid_of

SLUG = "save-link"
SCRIPT = Path(acceptance.__file__)


def started(root: Path, tmp_path: Path, *args: str) -> subprocess.Popen[str]:
    """``acceptance.py`` run as the owner runs it, with a temporary directory of its own."""
    scratch = tmp_path / "scratch"
    scratch.mkdir(exist_ok=True)
    return subprocess.Popen(  # noqa: S603 # -- this interpreter and the harness
        [sys.executable, str(SCRIPT), *args, "--root", str(root)],
        env={**os.environ, "TMPDIR": str(scratch)},
        stdout=subprocess.PIPE,
        text=True,
    )


def slow(tmp_path: Path) -> Path:
    """A product that writes its process id as it starts and then waits a minute to listen."""
    return products.make(tmp_path / "product", env=f"BOOTING={tmp_path / 'booting'} DELAY=60")


def left(tmp_path: Path) -> list[str]:
    return products.processes_naming(str(tmp_path))


@pytest.mark.parametrize("signum", [signal.SIGINT, signal.SIGTERM, signal.SIGHUP])
def test_an_interrupted_up_stops_the_boot_and_removes_the_session(
    tmp_path: Path, signum: signal.Signals
) -> None:
    root = slow(tmp_path)
    up = started(root, tmp_path, "up", "--slug", SLUG, "--ticket", str(root / "ticket.md"))
    products.wait_for(tmp_path / "booting")
    up.send_signal(signum)
    out, _ = up.communicate(timeout=120)
    assert up.returncode == 128 + signum
    assert f"interrupted by signal {signum}; everything it started was stopped" in out
    assert gone(pid_of(tmp_path / "booting"))
    assert left(tmp_path) == []
    assert acceptance.sessions(acceptance.store(root), 3) == []
    assert list((tmp_path / "scratch").iterdir()) == []


@pytest.mark.parametrize("signum", [signal.SIGINT, signal.SIGTERM, signal.SIGHUP])
def test_an_interrupted_session_ends_sealed_with_nothing_left(
    tmp_path: Path, signum: signal.Signals, capsys: pytest.CaptureFixture[str]
) -> None:
    root = products.make(tmp_path / "product")
    ticket = str(root / "ticket.md")
    assert (
        acceptance.main(["x", "up", "--slug", SLUG, "--ticket", ticket, "--root", str(root)]) == 0
    )
    url = capsys.readouterr().out.splitlines()[0]
    assert products.request(url)[0] == 200
    directory = acceptance.latest(root, SLUG)
    proxy = json.loads((directory / "proxy.json").read_text(encoding="utf-8"))
    os.kill(pid_of(directory / "live.lock"), signum)
    assert acceptance.wait_unlocked(directory, acceptance.STOP_GRACE)
    assert gone(proxy["pid"])
    assert not gatelib.listening(proxy["url"])
    assert not gatelib.listening(proxy["upstream"])
    assert left(tmp_path) == []
    assert json.loads((directory / "seal.json").read_text(encoding="utf-8"))["requests"] == 1
    assert acceptance.state(directory) is acceptance.State.ENDED


@pytest.mark.parametrize("signum", [signal.SIGINT, signal.SIGTERM])
def test_an_interrupted_replay_stops_its_boot_and_removes_its_scratch(
    tmp_path: Path, signum: signal.Signals
) -> None:
    root = slow(tmp_path)
    checks = tmp_path / "checks.json"
    kept = {"id": "K1", "criterion": "C1", "via": "http", "steps": [{"request": "GET /"}]}
    kept["steps"][0]["expect"] = {"status": 200}  # type: ignore[index]
    checks.write_text(json.dumps({"checks": [kept]}), encoding="utf-8")
    replay = started(root, tmp_path, "replay", "--checks", str(checks))
    products.wait_for(tmp_path / "booting")
    replay.send_signal(signum)
    out, _ = replay.communicate(timeout=120)
    assert replay.returncode == 128 + signum
    assert "everything it started was stopped" in out
    assert gone(pid_of(tmp_path / "booting"))
    assert left(tmp_path) == []
    assert list((tmp_path / "scratch").iterdir()) == []


def test_an_interrupt_while_the_session_is_written_removes_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    root = products.make(tmp_path / "product")

    def interrupted(path: Path, data: dict[str, object]) -> None:
        del path, data
        raise gatelib.Interrupted(signal.SIGINT)

    monkeypatch.setattr(acceptance.acceptance_proxy, "write_json", interrupted)
    ticket = str(root / "ticket.md")
    code = acceptance.main(["x", "up", "--slug", SLUG, "--ticket", ticket, "--root", str(root)])
    assert code == 128 + signal.SIGINT
    assert "interrupted by signal 2" in capsys.readouterr().out
    assert acceptance.sessions(acceptance.store(root), 3) == []
