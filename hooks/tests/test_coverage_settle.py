import os
import subprocess
import sys
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import pytest

import coverage_settle


@contextmanager
def live_process() -> Iterator[int]:
    child = subprocess.Popen([sys.executable, "-S", "-c", "import time; time.sleep(60)"])
    try:
        yield child.pid
    finally:
        child.kill()
        child.wait()


def data_file(directory: Path, pid: int, tail: str = "") -> Path:
    path = directory / f".coverage.host.pid{pid}.XyZ12345{tail}"
    path.write_bytes(b"")
    return path


def test_a_file_with_its_final_name_is_not_waited_for(tmp_path: Path) -> None:
    with live_process() as pid:
        data_file(tmp_path, pid, ".HNQIp837rWOh")
        assert coverage_settle.writing(tmp_path, os.getpid()) == []


def test_a_file_whose_writer_is_gone_is_not_waited_for(tmp_path: Path) -> None:
    with live_process() as pid:
        pass
    data_file(tmp_path, pid)
    assert coverage_settle.writing(tmp_path, os.getpid()) == []


def test_the_callers_own_file_is_not_waited_for(tmp_path: Path) -> None:
    data_file(tmp_path, os.getpid())
    assert coverage_settle.writing(tmp_path, os.getpid()) == []


def test_a_file_a_live_process_has_not_renamed_is_waited_for(tmp_path: Path) -> None:
    with live_process() as pid:
        unfinished = data_file(tmp_path, pid)
        assert coverage_settle.writing(tmp_path, os.getpid()) == [unfinished]


def test_settle_returns_once_the_writer_has_renamed_its_file(tmp_path: Path) -> None:
    with live_process() as pid:
        unfinished = data_file(tmp_path, pid)

        def finish() -> None:
            time.sleep(0.3)
            unfinished.rename(unfinished.with_name(unfinished.name + ".HNQIp837rWOh"))

        renamer = threading.Thread(target=finish)
        started = time.monotonic()
        renamer.start()
        assert coverage_settle.settle(tmp_path, timeout=20) == []
        renamer.join()
        assert time.monotonic() - started >= 0.3


def test_settle_gives_up_at_the_timeout_and_names_the_file(tmp_path: Path) -> None:
    with live_process() as pid:
        unfinished = data_file(tmp_path, pid)
        assert coverage_settle.settle(tmp_path, timeout=0.2) == [unfinished]


def test_settle_over_a_directory_without_data_files_returns_at_once(tmp_path: Path) -> None:
    assert coverage_settle.settle(tmp_path, timeout=20) == []


@pytest.mark.parametrize("name", [".coverage", ".coverage.host", "other.pid1.x"])
def test_names_that_are_not_a_writers_data_file_are_ignored(tmp_path: Path, name: str) -> None:
    (tmp_path / name).write_bytes(b"")
    assert coverage_settle.writing(tmp_path, os.getpid()) == []
