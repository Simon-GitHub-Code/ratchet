from __future__ import annotations

import os
import signal
import subprocess
import sys
import time
from typing import TYPE_CHECKING

import pytest

import timed

if TYPE_CHECKING:
    from pathlib import Path


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True


def test_run_timed_returns_child_exit_code_and_signal_code() -> None:
    assert timed.main(["timed.py", "5", "--", sys.executable, "-c", "print('ok')"]) == 0
    assert timed.main(["timed.py", "5", sys.executable, "-c", "import sys; sys.exit(7)"]) == 7
    kill_self = "import os, signal; os.kill(os.getpid(), signal.SIGKILL)"
    code = timed.main(["timed.py", "5", "--", sys.executable, "-c", kill_self])
    assert code == 128 + signal.SIGKILL


def test_run_timed_kills_entire_process_group_on_timeout(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    pid_file = tmp_path / "grandchild.pid"
    script = (
        "import pathlib, subprocess, sys, time\n"
        "child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'])\n"
        "pathlib.Path(sys.argv[1]).write_text(str(child.pid))\n"
        "time.sleep(60)\n"
    )
    code = timed.main(
        ["timed.py", "0.4", "--grace", "0.2", "--", sys.executable, "-c", script, str(pid_file)]
    )
    assert code == timed.TIMEOUT_EXIT
    assert "timed out after 0.4s" in capsys.readouterr().err
    grandchild = int(pid_file.read_text(encoding="utf-8"))
    deadline = time.monotonic() + 2.0
    while _pid_alive(grandchild) and time.monotonic() < deadline:
        time.sleep(0.05)
    assert not _pid_alive(grandchild)


def test_run_timed_handles_missing_executable_and_external_signal(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    code = timed.run_timed(["/nonexistent/ratchet-timed-binary"], seconds=1.0)
    assert code == timed.NOT_FOUND_EXIT
    assert "cannot run /nonexistent/ratchet-timed-binary" in capsys.readouterr().err

    def send_sigterm_while_waiting() -> None:
        os.kill(os.getpid(), signal.SIGTERM)

    original_wait = subprocess.Popen.wait
    fired = False

    def interrupt_wait(self: subprocess.Popen[bytes], timeout: float | None = None) -> int:
        nonlocal fired
        if timeout is not None and not fired:
            fired = True
            send_sigterm_while_waiting()
        return original_wait(self, timeout=timeout)

    monkeypatch.setattr(subprocess.Popen, "wait", interrupt_wait)
    with pytest.raises(SystemExit) as caught:
        timed.run_timed(
            [sys.executable, "-c", "import time; time.sleep(30)"], seconds=5.0, grace=0.1
        )
    assert caught.value.code == 128 + signal.SIGTERM

    def raise_before_spawn(*_args: object, **_kwargs: object) -> object:
        raise KeyboardInterrupt

    monkeypatch.setattr(subprocess, "Popen", raise_before_spawn)
    with pytest.raises(KeyboardInterrupt):
        timed.run_timed([sys.executable, "-c", "pass"], seconds=5.0)


@pytest.mark.parametrize(
    "argv",
    [
        ["timed.py", "0", "--", "true"],
        ["timed.py", "nan", "--", "true"],
        ["timed.py", "5", "--grace", "-1", "--", "true"],
        ["timed.py", "5", "--grace", "inf", "--", "true"],
        ["timed.py", "5", "--"],
    ],
)
def test_main_rejects_invalid_arguments(argv: list[str]) -> None:
    with pytest.raises(SystemExit) as caught:
        timed.main(argv)
    assert caught.value.code == 2


def test_unsafe_justfile_timeouts_flags_bare_timeout_and_allows_safe_forms() -> None:
    justfile = (
        'booted := "scripts/with_service.py --timeout 30"\n'
        "\n"
        "bare:\n"
        '    HYPOTHESIS_PROFILE=fuzz timeout "600" tool run --x; \\\n'
        "    timeout -s TERM --kill-after 10 30 tool2\n"
        "\n"
        "missing_kill:\n"
        "    @env -u FOO -- VAR=1 command exec /usr/bin/timeout --foreground -v 30 tool\n"
        "\n"
        "missing_fg:\n"
        "    -timeout -k 30 600 tool && env -i timeout --kill-after=10 60 tool2 || true\n"
        "\n"
        "safe_forms:\n"
        "    timeout --foreground --kill-after=10 600 tool\n"
        "    timeout --foreground -k10 -- 600 tool\n"
        "    timeout --foreground -k 10 -s INT 600 tool\n"
        '    echo "timeout 30" | cat && just booted --timeout 30\n'
        "    FOO=1\n"
        "    python3 -c 'unclosed quote\n"
        "    echo \"a\\;b\" && echo 'plain' && echo x\\;y\n"
        "    env -C /tmp -i FOO=1 tool\n"
        "    env -i\n"
        "    timeout --foreground --kill-after=5 - 10\n"
        "    timeout --foreground -k 5\n"
        "    line_continuation \\\n"
    )
    findings = timed.unsafe_justfile_timeouts(justfile)
    assert findings == [
        ("bare", ("--foreground", "--kill-after")),
        ("missing_kill", ("--kill-after",)),
        ("missing_fg", ("--foreground",)),
    ]
