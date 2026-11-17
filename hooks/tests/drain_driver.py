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

if os.environ.get("DRV_KILL_AT") == "discard":
    drain.discard = lambda *_args: os.kill(os.getpid(), signal.SIGKILL)

raise SystemExit(drain.main(["drain", *sys.argv[1:]]))
