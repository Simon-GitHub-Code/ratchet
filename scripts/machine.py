from __future__ import annotations

import contextlib
import ctypes
import hashlib
import os
import resource
import select
import signal
import statistics
import sys
import threading
import time
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import TYPE_CHECKING, Protocol, Self

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator, Sequence
    from collections.abc import Set as AbstractSet
    from types import FrameType

PROC = Path("/proc")
CGROUP = Path("/sys/fs/cgroup")
NOISE_FLOOR = 1.1
DEFAULT_INTERVAL = 2.0
DEFAULT_PROBE_CPU = 0.02
PR_SET_CHILD_SUBREAPER = 36
CGROUP_FIELDS = 3
MIN_STAT_FIELDS = 4
MIN_PROC_FIELDS = 15
MAX_CALIBRATE_STEPS = 1_000_000
MIN_CALIBRATE_CPU = 0.002
WAKE_CHUNK = 4096
CLK_TCK = getattr(os, "sysconf", lambda _name: 100)("SC_CLK_TCK")


class Subreaper(Protocol):
    def __call__(self, *, enabled: bool) -> bool: ...


@dataclass(frozen=True, slots=True)
class Sample:
    wall: float
    cpu: float


@dataclass(frozen=True, slots=True)
class Snapshot:
    busy: float
    rusage: float
    live: float


@dataclass(frozen=True, slots=True)
class Machine:
    cores: int | None
    load: float | None
    k: float
    share: float
    m: float
    machine_read: bool

    def fields(self) -> dict[str, object]:
        return {
            "cores": self.cores,
            "load": self.load,
            "k": self.k,
            "share": self.share,
            "m": self.m,
            "machine_read": self.machine_read,
        }


def host_info() -> tuple[int | None, float | None]:
    cores = os.cpu_count()
    getloadavg = getattr(os, "getloadavg", None)
    load: float | None = None
    if getloadavg is not None:
        with contextlib.suppress(OSError):
            load = round(float(getloadavg()[0]), 2)
    return cores, load


def unavailable(cores: int | None = None, load: float | None = None) -> Machine:
    return Machine(cores=cores, load=load, k=1.0, share=1.0, m=1.0, machine_read=False)


def usable_cpus(cores: int | None) -> frozenset[int] | None:
    getaffinity = getattr(os, "sched_getaffinity", None)
    if cores is None or cores < 1 or getaffinity is None:
        return None
    try:
        cpus = frozenset(getaffinity(0))
    except OSError:
        return None
    return cpus if len(cpus) >= cores else None


def cgroup_walk(base: Path, rel_path: str) -> list[Path]:
    dirs = [base]
    current = base
    for part in PurePosixPath(rel_path.lstrip("/")).parts:
        current = current / part
        dirs.append(current)
    return dirs


def no_v2_quota(rel_path: str, cgroup_root: Path) -> bool:
    for directory in cgroup_walk(cgroup_root, rel_path):
        try:
            text = (directory / "cpu.max").read_text(encoding="utf-8")
        except FileNotFoundError:
            continue
        except OSError:
            return False
        tokens = text.split()
        if not tokens or tokens[0] != "max":
            return False
    return True


def no_v1_quota(controllers: str, rel_path: str, cgroup_root: Path) -> bool:
    for name in dict.fromkeys(("cpu", controllers)):
        base = cgroup_root / name
        if not base.is_dir():
            continue
        for directory in cgroup_walk(base, rel_path):
            try:
                text = (directory / "cpu.cfs_quota_us").read_text(encoding="utf-8")
            except FileNotFoundError:
                continue
            except OSError:
                return False
            try:
                quota = int(text.strip())
            except ValueError:
                return False
            if quota > 0:
                return False
    return True


def no_cgroup_quota(proc: Path = PROC, cgroup_root: Path = CGROUP) -> bool:
    try:
        lines = (proc / "self/cgroup").read_text(encoding="utf-8").splitlines()
    except OSError:
        return False
    saw_entry = False
    for line in lines:
        parts = line.split(":", 2)
        if len(parts) != CGROUP_FIELDS:
            continue
        saw_entry = True
        hid, controllers, rel_path = parts
        if hid == "0" and not controllers and not no_v2_quota(rel_path, cgroup_root):
            return False
        if "cpu" in controllers.split(",") and not no_v1_quota(controllers, rel_path, cgroup_root):
            return False
    return saw_entry


def cpu_busy_ticks(fields: list[str]) -> int | None:
    if len(fields) < MIN_STAT_FIELDS:
        return None
    try:
        nums = [int(item) for item in fields]
    except ValueError:
        return None
    padded = [*nums, 0, 0, 0, 0]
    return padded[0] + padded[1] + padded[2] + padded[5] + padded[6] + padded[7]


def read_busy_cpu(cpus: frozenset[int], proc: Path = PROC, clk_tck: int = CLK_TCK) -> float | None:
    try:
        lines = (proc / "stat").read_text(encoding="utf-8").splitlines()
    except OSError:
        return None
    seen: set[int] = set()
    total = 0
    for line in lines:
        parts = line.split()
        if not parts or not parts[0].startswith("cpu") or parts[0] == "cpu":
            continue
        suffix = parts[0][3:]
        if not suffix.isdigit() or (idx := int(suffix)) not in cpus:
            continue
        ticks = cpu_busy_ticks(parts[1:])
        if ticks is None:
            return None
        seen.add(idx)
        total += ticks
    return total / clk_tck if seen == cpus else None


def parse_proc_stat(text: str) -> tuple[int, int, int] | None:
    rparen = text.rfind(")")
    lparen = text.find("(")
    if rparen < 0 or lparen < 0 or rparen < lparen:
        return None
    parts = text[rparen + 2 :].split()
    if len(parts) < MIN_PROC_FIELDS:
        return None
    try:
        pid = int(text[:lparen].strip())
        ppid = int(parts[1])
        ticks = int(parts[11]) + int(parts[12]) + int(parts[13]) + int(parts[14])
    except ValueError:
        return None
    return pid, ppid, ticks


def proc_table(proc: Path = PROC) -> list[tuple[int, int, int]] | None:
    try:
        entries = list(os.scandir(proc))
    except OSError:
        return None
    rows: list[tuple[int, int, int]] = []
    for entry in entries:
        if not entry.name.isdigit():
            continue
        try:
            text = (proc / entry.name / "stat").read_text(encoding="utf-8")
        except OSError:
            continue
        parsed = parse_proc_stat(text)
        if parsed is not None:
            rows.append(parsed)
    return rows


def descendants_cpu(
    rows: list[tuple[int, int, int]], root_pid: int, clk_tck: int = CLK_TCK
) -> float:
    children: dict[int, list[tuple[int, int]]] = {}
    for pid, ppid, ticks in rows:
        children.setdefault(ppid, []).append((pid, ticks))
    stack = [root_pid]
    seen = {root_pid}
    total = 0
    while stack:
        current = stack.pop()
        for pid, ticks in children.get(current, ()):
            if pid not in seen:
                seen.add(pid)
                total += ticks
                stack.append(pid)
    return total / clk_tck


def process_cpu(pid: int, proc: Path = PROC, clk_tck: int = CLK_TCK) -> float | None:
    try:
        text = (proc / str(pid) / "stat").read_text(encoding="utf-8")
    except OSError:
        return None
    parsed = parse_proc_stat(text)
    return parsed[2] / clk_tck if parsed is not None else None


def orphan_pids(
    rows: list[tuple[int, int, int]], my_pid: int, tracked: AbstractSet[int]
) -> list[int]:
    return [pid for pid, ppid, _ticks in rows if ppid == my_pid and pid not in tracked]


def direct_children(proc: Path, my_pid: int) -> list[int] | None:
    task_dir = proc / str(my_pid) / "task"
    try:
        entries = list(os.scandir(task_dir))
        pids: list[int] = []
        for entry in entries:
            text = (task_dir / entry.name / "children").read_text(encoding="utf-8")
            pids.extend(int(token) for token in text.split())
    except OSError:
        return None
    return pids


def set_child_subreaper(*, enabled: bool) -> bool:
    if sys.platform != "linux":
        return False
    try:
        libc = ctypes.CDLL(None, use_errno=True)
        return int(libc.prctl(PR_SET_CHILD_SUBREAPER, int(enabled), 0, 0, 0)) == 0
    except OSError:
        return False


def reap_pid(pid: int) -> None:
    with contextlib.suppress(ChildProcessError, OSError):
        os.waitpid(pid, os.WNOHANG)


def read_rusage() -> float:
    self_ru = resource.getrusage(resource.RUSAGE_SELF)
    child_ru = resource.getrusage(resource.RUSAGE_CHILDREN)
    return self_ru.ru_utime + self_ru.ru_stime + child_ru.ru_utime + child_ru.ru_stime


def hash_chain(steps: int) -> None:
    digest = b"ratchet-probe"
    for _ in range(steps):
        digest = hashlib.sha256(digest).digest()


def calibrate(target_cpu: float = DEFAULT_PROBE_CPU) -> int:
    steps = 1000
    elapsed = 0.0
    while steps < MAX_CALIBRATE_STEPS:
        start = time.thread_time()
        hash_chain(steps)
        elapsed = time.thread_time() - start
        if elapsed >= MIN_CALIBRATE_CPU:
            break
        steps *= 2
    return max(1, int(steps * (target_cpu / max(elapsed, 1e-6))))


def measure_probe(steps: int) -> Sample:
    wall_start = time.monotonic()
    cpu_start = time.thread_time()
    hash_chain(steps)
    cpu = time.thread_time() - cpu_start
    return Sample(wall=time.monotonic() - wall_start, cpu=cpu)


def factor(samples: Sequence[Sample], own: float, busy: float) -> tuple[float, float, float]:
    ratios = [sample.wall / sample.cpu for sample in samples if sample.cpu > 0 and sample.wall >= 0]
    k = statistics.median(ratios) if ratios else 1.0
    others = max(0.0, busy - own)
    share = (own + others) / own if own > 0 else 1.0
    raw = min(k, share)
    m = 1.0 if raw < NOISE_FLOOR else raw
    return round(k, 4), round(share, 4), round(m, 4)


@dataclass(frozen=True, slots=True)
class ProbeEnv:
    proc: Path = PROC
    cgroup_root: Path = CGROUP
    probe: Callable[[int], Sample] = measure_probe
    calibrate: Callable[[float], int] = calibrate
    set_subreaper: Subreaper = set_child_subreaper
    reap: Callable[[int], None] = reap_pid
    rusage: Callable[[], float] = read_rusage
    cpus: Callable[[int | None], frozenset[int] | None] = usable_cpus
    host: Callable[[], tuple[int | None, float | None]] = host_info


class Monitor:
    def __init__(
        self,
        interval: float = DEFAULT_INTERVAL,
        *,
        probe_cpu: float = DEFAULT_PROBE_CPU,
        env: ProbeEnv | None = None,
    ) -> None:
        self._interval = interval
        self._probe_cpu = probe_cpu
        self._env = env or ProbeEnv()
        self._pid = os.getpid()
        self._lock = threading.Lock()
        self._tracked: set[int] = set()
        self._stop_event = threading.Event()
        self._samples: list[Sample] = []
        self._cpus: frozenset[int] = frozenset()
        self._start_snap: Snapshot | None = None
        self._thread: threading.Thread | None = None
        self._result: Machine | None = None
        self._wake_fds: list[int] = []
        self._prev_sigchld: Callable[[int, FrameType | None], object] | int | None = None

    @contextlib.contextmanager
    def spawning(self) -> Iterator[None]:
        with self._lock:
            yield

    def register(self, pid: int) -> None:
        self._tracked.add(pid)

    def unregister(self, pid: int) -> None:
        with self._lock:
            self._tracked.discard(pid)

    def reap_orphans(self) -> None:
        with self._lock:
            children = direct_children(self._env.proc, self._pid)
            if children is None:
                rows = proc_table(self._env.proc)
                if rows is None:
                    return
                to_reap = orphan_pids(rows, self._pid, self._tracked)
            else:
                to_reap = [pid for pid in children if pid not in self._tracked]
            for pid in to_reap:
                self._env.reap(pid)

    def _install_sigchld(self, wake_w: int) -> None:
        if threading.current_thread() is not threading.main_thread() or not hasattr(
            signal, "SIGCHLD"
        ):
            return
        previous: Callable[[int, FrameType | None], object] | int | None = None

        def on_sigchld(signum: int, frame: FrameType | None) -> None:
            with contextlib.suppress(OSError):
                os.write(wake_w, b"\x00")
            if callable(previous):
                previous(signum, frame)

        previous = signal.signal(signal.SIGCHLD, on_sigchld)
        self._prev_sigchld = previous

    def _restore_sigchld(self) -> None:
        if self._prev_sigchld is not None:
            signal.signal(signal.SIGCHLD, self._prev_sigchld)
            self._prev_sigchld = None

    def _loop(self, wake_r: int) -> None:
        steps: int | None = None
        next_probe = time.monotonic() + self._interval
        while True:
            timeout = max(0.0, next_probe - time.monotonic())
            readable, _, _ = select.select([wake_r], [], [], timeout)
            if self._stop_event.is_set():
                return
            if readable:
                with contextlib.suppress(OSError):
                    os.read(wake_r, WAKE_CHUNK)
                self.reap_orphans()
            if time.monotonic() >= next_probe:
                if steps is None:
                    steps = self._env.calibrate(self._probe_cpu)
                self._samples.append(self._env.probe(steps))
                self.reap_orphans()
                next_probe = time.monotonic() + self._interval

    def start(self) -> None:
        cores, _ = self._env.host()
        cpus = self._env.cpus(cores)
        if cpus is None or not no_cgroup_quota(self._env.proc, self._env.cgroup_root):
            return
        busy = read_busy_cpu(cpus, self._env.proc)
        rows = proc_table(self._env.proc)
        if busy is None or rows is None or not self._env.set_subreaper(enabled=True):
            return
        wake_r, wake_w = os.pipe()
        os.set_blocking(wake_r, False)
        os.set_blocking(wake_w, False)
        self._wake_fds = [wake_r, wake_w]
        self._install_sigchld(wake_w)
        self._cpus = cpus
        self._start_snap = Snapshot(busy, self._env.rusage(), descendants_cpu(rows, self._pid))
        self._thread = threading.Thread(target=self._loop, args=(wake_r,), daemon=True)
        self._thread.start()

    def stop(self) -> Machine:
        if self._result is not None:
            return self._result
        cores, load = self._env.host()
        if self._thread is None or self._start_snap is None:
            self._result = unavailable(cores, load)
            return self._result
        try:
            self._stop_event.set()
            with contextlib.suppress(OSError):
                os.write(self._wake_fds[1], b"\x00")
            self._thread.join()
        finally:
            try:
                self._restore_sigchld()
            finally:
                for fd in self._wake_fds:
                    with contextlib.suppress(OSError):
                        os.close(fd)
                self._wake_fds.clear()
        self.reap_orphans()
        busy = read_busy_cpu(self._cpus, self._env.proc)
        rows = proc_table(self._env.proc)
        rusage = self._env.rusage()
        self._env.set_subreaper(enabled=False)
        if busy is None or rows is None:
            self._result = unavailable(cores, load)
            return self._result
        end_live = descendants_cpu(rows, self._pid)
        own = max(0.0, (rusage + end_live) - (self._start_snap.rusage + self._start_snap.live))
        k, share, m = factor(self._samples, own, max(0.0, busy - self._start_snap.busy))
        self._result = Machine(cores, load, k, share, m, machine_read=True)
        return self._result

    def result(self) -> Machine:
        if self._result is not None:
            return self._result
        return unavailable(*self._env.host())

    def __enter__(self) -> Self:
        self.start()
        return self

    def __exit__(self, *_exc: object) -> None:
        self.stop()
