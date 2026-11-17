"""Hold the coverage combine until every live process has finished writing its data file.

A process that records coverage writes its data file when it exits: it creates
``.coverage.<host>.pid<N>.<random>``, fills it, then renames it to add a ``.H<hash>h`` tail.
The combine at the end of the run lists the files it finds, reads one, and opens it again; a
process that exits at the same moment renames the file in between, and the second open fails
with ``unable to open database file``. Waiting for every live writer to finish its rename
closes that window without skipping a file, so the coverage floor still sees every line.
"""

from __future__ import annotations

import os
import re
import time
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path

FINISHED = re.compile(r"\.H[A-Za-z0-9]+h$")
WRITER = re.compile(r"\.pid(\d+)\.")


def alive(pid: int) -> bool:
    """Whether a process with this id exists."""
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def writing(directory: Path, own: int) -> list[Path]:
    """The data files in ``directory`` that a live process other than ``own`` has not finished."""
    unfinished = []
    for path in sorted(directory.glob(".coverage.*")):
        found = WRITER.search(path.name)
        if found is None or FINISHED.search(path.name):
            continue
        pid = int(found.group(1))
        if pid != own and alive(pid):
            unfinished.append(path)
    return unfinished


def settle(directory: Path, timeout: float, interval: float = 0.05) -> list[Path]:
    """Wait until no live process is writing a data file; the files still unfinished at the end."""
    own = os.getpid()
    deadline = time.monotonic() + timeout
    left = writing(directory, own)
    while left and time.monotonic() < deadline:
        time.sleep(interval)
        left = writing(directory, own)
    return left
