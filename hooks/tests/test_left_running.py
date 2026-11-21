from pathlib import Path

import pytest

from tests.servers import gone, pid_of

CONFTEST = Path(__file__).resolve().parents[2] / "conftest.py"
SLEEPER = '[sys.executable, "-c", "import time; time.sleep(60)", "{name}"]'
PLANTED = f"""
import subprocess, sys

def test_forgets_a_sleeper(spawn):
    subprocess.Popen({SLEEPER.format(name="forgotten-sleeper")})

def test_forgets_a_daemon(spawn):
    subprocess.run(["sh", "-c", 'setsid "$0" "$@" &', *{SLEEPER.format(name="forgotten-daemon")}])

def test_ends_the_sleeper_it_starts(spawn):
    sleeper = subprocess.Popen({SLEEPER.format(name="ended-sleeper")})
    sleeper.kill()
    sleeper.wait()

def test_fails_beside_a_sleeper_the_shared_fixture_started(spawn):
    spawn({SLEEPER.format(name="spawned-sleeper")})
    assert False, "this test failed"
"""
INTERRUPTED = f"""
import os, signal, sys

def test_is_interrupted_beside_a_sleeper_the_shared_fixture_started(spawn):
    sleeper = spawn({SLEEPER.format(name="interrupted-sleeper")})
    open("sleeper.pid", "w").write(str(sleeper.pid))
    os.kill(os.getpid(), signal.SIGINT)
"""


@pytest.fixture
def inner(pytester: pytest.Pytester, monkeypatch: pytest.MonkeyPatch) -> pytest.Pytester:
    monkeypatch.delenv("SHARD", raising=False)
    monkeypatch.delenv("SHARDS", raising=False)
    pytester.makeconftest(CONFTEST.read_text(encoding="utf-8"))
    return pytester


def test_a_test_that_leaves_a_process_running_fails_and_names_its_command_line(
    inner: pytest.Pytester,
) -> None:
    inner.makepyfile(PLANTED)
    result = inner.runpytest_subprocess("-p", "no:randomly", "-p", "no:cacheprovider")
    said = result.stdout.str()
    result.assert_outcomes(passed=3, failed=1, errors=2)
    assert "ERROR at teardown of test_forgets_a_sleeper" in said, said
    assert "ERROR at teardown of test_forgets_a_daemon" in said, said
    assert "import time; time.sleep(60) forgotten-sleeper" in said, said
    assert "import time; time.sleep(60) forgotten-daemon" in said, said


def test_a_process_the_shared_fixture_started_ends_when_its_test_is_interrupted(
    inner: pytest.Pytester,
) -> None:
    inner.makepyfile(INTERRUPTED)
    result = inner.runpytest_subprocess("-p", "no:randomly", "-p", "no:cacheprovider")
    assert result.ret == pytest.ExitCode.INTERRUPTED, result.stdout.str()
    assert gone(pid_of(inner.path / "sleeper.pid"), wait=0), "it outlived its interrupted test"
