"""Run ``drain.py`` as a process of its own, as a session's command is, for the tests that need two.

The one stand-in the test fixture also makes is kept: the repository's commit hooks count as
installed. Optional environment:

- ``DRV_LOCK_WAIT`` sets how long a command waits for the drain lock.
- ``DRV_WAITING=<file>`` touches ``<file>`` when the command first finds the drain lock held and
  starts to wait for it.
- ``DRV_PAUSE=<dir>`` stops ``plan`` after it has read the repository, touching ``<dir>/paused``
  and waiting for ``<dir>/go``: a command caught between reading and acting.
- ``DRV_KILL_AT=drop_record`` ends this process with SIGKILL where ``update`` is about to drop
  the verdict record the rebase outdated: a run killed after its rebase.
"""

import os
import signal
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(HERE / "scripts"), str(HERE / "hooks")]

import drain  # noqa: E402
import hooks_installed  # noqa: E402

hooks_installed.check = lambda _root: None
if "DRV_LOCK_WAIT" in os.environ:
    drain.LOCK_WAIT = float(os.environ["DRV_LOCK_WAIT"])
if "DRV_WAITING" in os.environ:
    waiting = Path(os.environ["DRV_WAITING"])
    real_sleep = time.sleep

    def announced_sleep(seconds: float) -> None:
        if seconds == drain.LOCK_POLL:
            waiting.touch()
        real_sleep(seconds)

    drain.time.sleep = announced_sleep
if "DRV_PAUSE" in os.environ:
    gate = Path(os.environ["DRV_PAUSE"])
    real_plan = drain.plan

    def paused_plan(d: drain.Drain) -> drain.Plan:
        drafted = real_plan(d)
        (gate / "paused").write_text(str(time.time()))
        while not (gate / "go").exists():
            time.sleep(0.02)
        return drafted

    drain.plan = paused_plan
if os.environ.get("DRV_KILL_AT") == "drop_record":
    drain.drop_record = lambda _tree: os.kill(os.getpid(), signal.SIGKILL)

raise SystemExit(drain.main(["drain", *sys.argv[1:]]))
