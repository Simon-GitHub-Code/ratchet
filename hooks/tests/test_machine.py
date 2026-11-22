import contextlib
import ctypes
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

import machine


def write_proc(
    tmp_path: Path,
    *,
    busy_ticks: int = 153,
    cores: int = 2,
    cgroup_line: str = "0::/test\n",
    extra_procs: dict[int, tuple[int, int]] | None = None,
) -> tuple[Path, Path]:
    proc = tmp_path / "proc"
    cgroup = tmp_path / "cgroup"
    (proc / "self").mkdir(parents=True)
    (cgroup / "test").mkdir(parents=True)
    lines = [
        f"cpu  {busy_ticks * cores} 0 0 1000 0 0 0 0 0 0\n",
        *[f"cpu{i} {busy_ticks} 0 0 1000 0 0 0 0 0 0\n" for i in range(cores)],
    ]
    (proc / "stat").write_text("".join(lines), encoding="utf-8")
    (proc / "self/cgroup").write_text(cgroup_line, encoding="utf-8")
    (cgroup / "test/cpu.max").write_text("max 100000\n", encoding="utf-8")
    for pid, (ppid, ticks) in (extra_procs or {}).items():
        (proc / str(pid)).mkdir(parents=True)
        (proc / str(pid) / "stat").write_text(
            f"{pid} (worker) S {ppid} 1 1 0 -1 0 0 0 0 0 {ticks} 0 0 0 20 0 1 0 0 0 0\n",
            encoding="utf-8",
        )
    return proc, cgroup


def fake_env(
    tmp_path: Path,
    samples: list[machine.Sample],
    *,
    busy_ticks: int = 100,
    rusage: tuple[float, float] = (1.0, 5.0),
    cores: int = 2,
) -> tuple[machine.ProbeEnv, Path]:
    proc, cgroup = write_proc(tmp_path, busy_ticks=busy_ticks, cores=cores)
    it = iter(samples)
    usages = iter(rusage)

    def probe(_steps: int) -> machine.Sample:
        return next(it, samples[-1])

    def set_subreaper(*, enabled: bool) -> bool:
        del enabled
        return True

    def next_rusage() -> float:
        return next(usages, rusage[-1])

    env = machine.ProbeEnv(
        proc=proc,
        cgroup_root=cgroup,
        probe=probe,
        calibrate=lambda _target: 10,
        set_subreaper=set_subreaper,
        rusage=next_rusage,
        cpus=lambda count: frozenset(range(count or 0)) if count else None,
        host=lambda: (cores, 0.5),
    )
    return env, proc


def test_free_machine_has_factor_one(tmp_path: Path) -> None:
    env, proc = fake_env(
        tmp_path,
        [machine.Sample(0.02, 0.02), machine.Sample(0.0204, 0.02)],
        busy_ticks=100,
        rusage=(1.0, 5.0),
        cores=2,
    )
    with machine.Monitor(interval=0.005, env=env) as monitor:
        assert monitor.result().machine_read is False
        time.sleep(0.03)
        (proc / "stat").write_text(
            "cpu0 300 0 0 1000 0 0 0 0 0 0\ncpu1 300 0 0 1000 0 0 0 0 0 0\n",
            encoding="utf-8",
        )
    result = monitor.result()
    assert result.machine_read is True
    assert result.cores == 2
    assert result.load == 0.5
    assert result.m == 1.0
    assert result.share == 1.0
    assert result.k < 1.1
    assert result.fields() == {
        "cores": 2,
        "load": 0.5,
        "k": result.k,
        "share": 1.0,
        "m": 1.0,
        "machine_read": True,
    }


def test_real_quick_run_reads_machine() -> None:
    with machine.Monitor(interval=0.01) as monitor:
        time.sleep(0.03)
    result = monitor.result()
    assert result.machine_read is True
    assert result.cores == os.cpu_count()
    assert result.load is not None
    assert result.load >= 0.0
    assert result.k > 0.0
    assert result.share >= 1.0
    assert result.m >= 1.0
    assert monitor.stop() == result


def test_self_made_contention_leaves_factor_one(tmp_path: Path) -> None:
    env, proc = fake_env(
        tmp_path,
        [machine.Sample(0.048, 0.02), machine.Sample(0.052, 0.02)],
        busy_ticks=100,
        rusage=(1.0, 11.0),
        cores=2,
    )
    with machine.Monitor(interval=0.005, env=env) as monitor:
        time.sleep(0.02)
        (proc / "stat").write_text(
            "cpu0 620 0 0 1000 0 0 0 0 0 0\ncpu1 620 0 0 1000 0 0 0 0 0 0\n",
            encoding="utf-8",
        )
    result = monitor.result()
    assert result.machine_read is True
    assert result.k > 2.0
    assert result.share < 1.1
    assert result.m == 1.0


def test_external_contention_sets_factor_to_other_tenant_factor(tmp_path: Path) -> None:
    env, proc = fake_env(
        tmp_path,
        [machine.Sample(0.036, 0.02), machine.Sample(0.036, 0.02)],
        busy_ticks=100,
        rusage=(1.0, 3.0),
        cores=2,
    )
    with machine.Monitor(interval=0.005, env=env) as monitor:
        time.sleep(0.02)
        (proc / "stat").write_text(
            "cpu0 600 0 0 1000 0 0 0 0 0 0\ncpu1 600 0 0 1000 0 0 0 0 0 0\n",
            encoding="utf-8",
        )
    result = monitor.result()
    assert result.machine_read is True
    assert result.k == 1.8
    assert result.share == 5.0
    assert result.m == 1.8


@pytest.mark.parametrize(
    ("samples", "own", "busy", "expected_m"),
    [
        ([machine.Sample(0.019, 0.02)], 2.0, 6.0, 1.0),
        ([machine.Sample(0.0218, 0.02)], 2.0, 6.0, 1.0),
        ([machine.Sample(1.1, 1.0)], 2.0, 6.0, 1.1),
        ([machine.Sample(0.04, 0.02)], 2.0, 2.18, 1.0),
        ([machine.Sample(0.02, 0.0)], 0.0, 2.0, 1.0),
        ([], 2.0, 4.0, 1.0),
    ],
)
def test_noise_floor_clamps_below_one_point_one_to_one(
    *, samples: list[machine.Sample], own: float, busy: float, expected_m: float
) -> None:
    _, _, m = machine.factor(samples, own, busy)
    assert m == expected_m


def test_affinity_subset_disables_machine_factor(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    proc, cgroup = write_proc(tmp_path)
    monkeypatch.setattr(os, "cpu_count", lambda: 4)
    monkeypatch.setattr(os, "sched_getaffinity", lambda _pid: {0, 1})
    called: list[bool] = []
    env = machine.ProbeEnv(
        proc=proc,
        cgroup_root=cgroup,
        set_subreaper=lambda *, enabled: called.append(enabled) or True,
    )
    with machine.Monitor(interval=0.01, env=env) as monitor:
        pass
    result = monitor.result()
    assert result.machine_read is False
    assert result.m == 1.0
    assert called == []


@pytest.mark.parametrize(
    ("cpu_max", "parent_cpu_max", "expected_ok"),
    [
        ("max 100000\n", "max 100000\n", True),
        ("50000 100000\n", "max 100000\n", False),
        ("max 100000\n", "50000 100000\n", False),
        ("", "max 100000\n", False),
    ],
)
def test_cgroup_v2_quota_disables_machine_factor(
    tmp_path: Path, *, cpu_max: str, parent_cpu_max: str, expected_ok: bool
) -> None:
    proc, cgroup = write_proc(tmp_path, cgroup_line="0::/parent/test\n")
    (cgroup / "parent/test").mkdir(parents=True)
    (cgroup / "parent/cpu.max").write_text(parent_cpu_max, encoding="utf-8")
    (cgroup / "parent/test/cpu.max").write_text(cpu_max, encoding="utf-8")
    assert machine.no_cgroup_quota(proc, cgroup) is expected_ok


@pytest.mark.parametrize(
    ("quota", "expected_ok"),
    [("-1\n", True), ("50000\n", False), ("bad\n", False)],
)
def test_cgroup_v1_quota_disables_machine_factor(
    tmp_path: Path, *, quota: str, expected_ok: bool
) -> None:
    proc, cgroup = write_proc(tmp_path, cgroup_line="4:cpu,cpuacct:/docker/abc\n")
    v1 = cgroup / "cpu/docker/abc"
    v1.mkdir(parents=True)
    (v1 / "cpu.cfs_quota_us").write_text(quota, encoding="utf-8")
    assert machine.no_cgroup_quota(proc, cgroup) is expected_ok


def test_cgroup_unreadable_and_malformed_lines(tmp_path: Path) -> None:
    proc, cgroup = write_proc(tmp_path, cgroup_line="badline\n0::/test\n")
    (cgroup / "test/cpu.max").unlink()
    (cgroup / "test/cpu.max").mkdir()
    assert machine.no_cgroup_quota(proc, cgroup) is False

    proc2, cgroup2 = write_proc(tmp_path / "v1", cgroup_line="4:cpu,cpuacct:/docker/abc\n")
    v1 = cgroup2 / "cpu/docker/abc"
    v1.mkdir(parents=True)
    (v1 / "cpu.cfs_quota_us").mkdir()
    assert machine.no_cgroup_quota(proc2, cgroup2) is False

    proc3, cgroup3 = write_proc(tmp_path / "v1ok", cgroup_line="4:cpu:/docker/abc\n")
    (cgroup3 / "cpu/docker/abc").mkdir(parents=True)
    assert machine.no_cgroup_quota(proc3, cgroup3) is True

    proc4, cgroup4 = write_proc(tmp_path / "empty", cgroup_line="not-a-cgroup-line\n")
    assert machine.no_cgroup_quota(proc4, cgroup4) is False


def test_missing_or_unreadable_proc_disables_machine_factor(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    missing = tmp_path / "no-proc"
    env = machine.ProbeEnv(proc=missing, cgroup_root=tmp_path / "no-cg")
    with machine.Monitor(interval=0.01, env=env) as monitor:
        pass
    assert monitor.result().machine_read is False
    assert monitor.result().m == 1.0

    proc, cgroup = write_proc(tmp_path)
    env_end = machine.ProbeEnv(
        proc=proc,
        cgroup_root=cgroup,
        probe=lambda _s: machine.Sample(0.02, 0.02),
        calibrate=lambda _t: 10,
        set_subreaper=lambda *, enabled: enabled is not None,
        cpus=lambda count: frozenset(range(count or 0)),
        host=lambda: (2, 0.2),
    )
    with machine.Monitor(interval=0.01, env=env_end) as monitor_end:
        (proc / "stat").unlink()
        monitor_end.reap_orphans()
    assert monitor_end.result().machine_read is False
    assert monitor_end.result().m == 1.0

    monkeypatch.setattr(sys, "platform", "darwin")
    assert machine.set_child_subreaper(enabled=True) is False
    with machine.Monitor(interval=0.01) as mac_mon:
        pass
    assert mac_mon.result().machine_read is False
    assert mac_mon.result().m == 1.0


def test_proc_parsers_and_host_edge_cases(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    assert machine.cpu_busy_ticks(["1", "2"]) is None
    assert machine.cpu_busy_ticks(["1", "x", "3", "4"]) is None
    assert machine.parse_proc_stat("bad") is None
    assert machine.parse_proc_stat("123 (x) R 1") is None
    assert machine.parse_proc_stat("123 (x) R bad " + " ".join(["0"] * 15)) is None
    assert machine.parse_proc_stat("123 (a (b)) R 1 " + " ".join(["0"] * 13)) == (123, 1, 0)

    proc, _ = write_proc(tmp_path)
    (proc / "stat").write_text("intr 1\ncpu 10 0 0 0\ncpu99 1 2 3 4\ncpu0 1 2\n", encoding="utf-8")
    assert machine.read_busy_cpu(frozenset({0}), proc) is None
    (proc / "stat").write_text("cpu0 1 2 3 4\n", encoding="utf-8")
    assert machine.read_busy_cpu(frozenset({0, 1}), proc) is None

    (proc / "999").mkdir()
    (proc / "999/stat").mkdir()
    (proc / "998").mkdir()
    (proc / "998/stat").write_text("bad", encoding="utf-8")
    assert machine.proc_table(proc) == []
    assert machine.process_cpu(999, proc) is None
    assert machine.process_cpu(os.getpid()) is not None
    desc = machine.descendants_cpu([(10, 1, 5), (11, 10, 7), (10, 11, 5)], 1)
    assert desc == 12 / machine.CLK_TCK

    monkeypatch.setattr(os, "getloadavg", lambda: (_ for _ in ()).throw(OSError("no")))
    assert machine.host_info()[1] is None
    monkeypatch.delattr(os, "getloadavg", raising=False)
    assert machine.host_info()[1] is None
    assert machine.usable_cpus(None) is None
    monkeypatch.setattr(os, "sched_getaffinity", lambda _p: (_ for _ in ()).throw(OSError("no")))
    assert machine.usable_cpus(2) is None

    monkeypatch.setattr(machine.time, "thread_time", lambda: 0.0)
    assert machine.calibrate(0.02) >= machine.MAX_CALIBRATE_STEPS


def test_reap_orphans_when_proc_disappears(tmp_path: Path) -> None:
    proc, cgroup = write_proc(tmp_path)
    env = machine.ProbeEnv(proc=proc, cgroup_root=cgroup)
    mon = machine.Monitor(interval=0.01, env=env)
    proc.rename(tmp_path / "proc.gone")
    mon.reap_orphans()


def test_subreaper_reaps_orphan_and_counts_cpu_without_stealing_exit3(tmp_path: Path) -> None:
    orphan_pid_file = tmp_path / "orphan.pid"
    orphan_done = tmp_path / "orphan.done"
    script = (
        "import hashlib, os, pathlib, sys\n"
        "pid = os.fork()\n"
        "if pid == 0:\n"
        "    os.setsid()\n"
        "    pathlib.Path(sys.argv[1]).write_text(str(os.getpid()))\n"
        "    d = b'x'\n"
        "    for _ in range(40000):\n"
        "        d = hashlib.sha256(d).digest()\n"
        "    pathlib.Path(sys.argv[2]).write_text('done')\n"
        "    os._exit(0)\n"
        "sys.exit(3)\n"
    )
    with machine.Monitor(interval=0.01) as monitor:
        with monitor.spawning():
            proc = subprocess.Popen(  # noqa: S603 # -- this interpreter and test script
                [sys.executable, "-c", script, str(orphan_pid_file), str(orphan_done)],
            )
            monitor.register(proc.pid)
        try:
            code = proc.wait()
        finally:
            monitor.unregister(proc.pid)
        assert code == 3
        deadline = time.monotonic() + 5.0
        while not orphan_done.exists() and time.monotonic() < deadline:
            time.sleep(0.01)
        time.sleep(0.05)
    result = monitor.result()
    assert result.machine_read is True
    orphan_pid = int(orphan_pid_file.read_text(encoding="utf-8"))
    with pytest.raises(ChildProcessError):
        os.waitpid(orphan_pid, os.WNOHANG)


def test_live_detached_descendant_cpu_is_counted_at_stop(tmp_path: Path) -> None:
    pid_file = tmp_path / "live.pid"
    ready = tmp_path / "ready"
    script = (
        "import hashlib, os, pathlib, sys, time\n"
        "if os.fork() == 0:\n"
        "    os.setsid()\n"
        "    pathlib.Path(sys.argv[1]).write_text(str(os.getpid()))\n"
        "    d = b'x'\n"
        "    for _ in range(40000):\n"
        "        d = hashlib.sha256(d).digest()\n"
        "    pathlib.Path(sys.argv[2]).write_text('ready')\n"
        "    time.sleep(30)\n"
        "    os._exit(0)\n"
        "sys.exit(3)\n"
    )
    live_pid: int | None = None
    try:
        with machine.Monitor(interval=0.01) as monitor:
            with monitor.spawning():
                proc = subprocess.Popen(  # noqa: S603 # -- this interpreter and test script
                    [sys.executable, "-c", script, str(pid_file), str(ready)],
                )
                monitor.register(proc.pid)
            try:
                assert proc.wait() == 3
            finally:
                monitor.unregister(proc.pid)
            deadline = time.monotonic() + 5.0
            while not ready.exists() and time.monotonic() < deadline:
                time.sleep(0.01)
            live_pid = int(pid_file.read_text(encoding="utf-8"))
        result = monitor.result()
        assert result.machine_read is True
    finally:
        if live_pid is not None:
            with contextlib.suppress(ProcessLookupError):
                os.kill(live_pid, 9)
            with contextlib.suppress(ChildProcessError):
                os.waitpid(live_pid, 0)


def test_prctl_failure_and_oserror_keep_reaper_off(monkeypatch: pytest.MonkeyPatch) -> None:
    class BadLib:
        def prctl(self, *_args: object) -> int:
            return -1

    monkeypatch.setattr(ctypes, "CDLL", lambda *_a, **_kw: BadLib())
    assert machine.set_child_subreaper(enabled=True) is False
    monkeypatch.setattr(ctypes, "CDLL", lambda *_a, **_kw: (_ for _ in ()).throw(OSError("no")))
    assert machine.set_child_subreaper(enabled=True) is False
