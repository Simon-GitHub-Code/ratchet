import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest

import acceptance
import gatelib
from tests import products
from tests.servers import Popen, Spawn, gone, pid_of

pytestmark = pytest.mark.usefixtures("sessions_ended")
SLUG = "save-link"
SCRIPT = Path(acceptance.__file__)


def started(spawn: Spawn, root: Path, tmp_path: Path, *args: str) -> Popen:
    scratch = tmp_path / "scratch"
    scratch.mkdir(exist_ok=True)
    return spawn(
        [sys.executable, str(SCRIPT), *args, "--root", str(root)],
        env={**os.environ, "TMPDIR": str(scratch)},
        stdout=subprocess.PIPE,
        text=True,
    )


def slow(tmp_path: Path) -> Path:
    return products.make(tmp_path / "product", env=f"BOOTING={tmp_path / 'booting'} DELAY=60")


def left(tmp_path: Path) -> list[str]:
    return products.processes_naming(str(tmp_path))


@pytest.mark.parametrize("signum", [signal.SIGINT, signal.SIGTERM, signal.SIGHUP])
def test_an_interrupted_up_stops_the_boot_and_removes_the_session(
    tmp_path: Path, signum: signal.Signals, spawn: Spawn
) -> None:
    root = slow(tmp_path)
    ticket = str(root / "ticket.md")
    up = started(spawn, root, tmp_path, "up", "--slug", SLUG, "--ticket", ticket)
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
    tmp_path: Path, signum: signal.Signals, spawn: Spawn
) -> None:
    root = slow(tmp_path)
    checks = tmp_path / "checks.json"
    kept = {"id": "K1", "criterion": "C1", "via": "http", "steps": [{"request": "GET /"}]}
    kept["steps"][0]["expect"] = {"status": 200}  # type: ignore[index]
    checks.write_text(json.dumps({"schema": 1, "checks": [kept]}), encoding="utf-8")
    replay = started(spawn, root, tmp_path, "replay", "--checks", str(checks))
    products.wait_for(tmp_path / "booting")
    replay.send_signal(signum)
    out, _ = replay.communicate(timeout=120)
    assert replay.returncode == 128 + signum
    assert "everything it started was stopped" in out
    assert gone(pid_of(tmp_path / "booting"))
    assert left(tmp_path) == []
    assert list((tmp_path / "scratch").iterdir()) == []


KILLED = 60.0
MANY = 10000


def replay_of_many_steps(tmp_path: Path, root: Path, spawn: Spawn, *more: str) -> Popen:
    checks = tmp_path / "checks.json"
    kept = {"id": "K1", "criterion": "C1", "via": "http", "steps": []}
    kept["steps"] = [{"request": "GET /", "expect": {"status": 200}}] * MANY
    checks.write_text(json.dumps({"schema": 1, "checks": [kept]}), encoding="utf-8")
    return started(spawn, root, tmp_path, "replay", "--checks", str(checks), *more)


def served(tmp_path: Path) -> dict[str, object]:
    deadline = time.monotonic() + KILLED
    while not (found := sorted((tmp_path / "scratch").glob("*/K1/proxy.json"))):
        assert time.monotonic() < deadline, "the replay's proxy never served"
        time.sleep(0.02)
    return json.loads(found[0].read_text(encoding="utf-8"))


def answers_within(url: str, limit: float) -> bool:
    deadline = time.monotonic() + limit
    while gatelib.listening(url):
        if time.monotonic() > deadline:
            return True
        time.sleep(0.05)
    return False


def test_a_replay_killed_outright_while_the_product_boots_serves_nothing_and_stops_it(
    tmp_path: Path, spawn: Spawn
) -> None:
    root = products.make(tmp_path / "product", env=f"BOOTING={tmp_path / 'booting'} DELAY=2")
    replay = replay_of_many_steps(tmp_path, root, spawn)
    products.wait_for(tmp_path / "booting")
    replay.kill()
    replay.wait()
    assert gone(pid_of(tmp_path / "booting"), wait=KILLED), "the product outlived its replay"
    assert sorted((tmp_path / "scratch").glob("*/K1/proxy.json")) == [], "a proxy served"


@pytest.mark.parametrize("control", [False, True], ids=["product", "nothing"])
def test_a_replay_killed_outright_while_it_checks_stops_all_it_started(
    tmp_path: Path, spawn: Spawn, *, control: bool
) -> None:
    root = products.make(tmp_path / "product")
    replay = replay_of_many_steps(tmp_path, root, spawn, *(["--control"] if control else []))
    proxy = served(tmp_path)
    replay.kill()
    replay.wait()
    assert gone(int(str(proxy["pid"])), wait=KILLED), "the proxy outlived its replay"
    assert not answers_within(str(proxy["upstream"]), KILLED), "the product outlived its replay"


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
