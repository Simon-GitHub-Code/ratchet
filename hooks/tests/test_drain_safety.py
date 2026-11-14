"""The queue drain and the merge keep work through a stopped run, a rival and a lock.

Each test is a replay of a defect found by running it: work removed that no stash held, a second
drain that cleaned the first, a cap that stopped counting, and runs killed part-way. Each runs on
a real repository, and a kill is a real SIGKILL or the state one leaves.
"""

import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest

import drain
import gatelib
import merge
from tests import servers
from tests.repos import commit, git, hook_script, write
from tests.test_drain import (
    ALPHA,
    BRANCH_A,
    BRANCH_C,
    GAMMA,
    SCRIPTS,
    Out,
    approval,
    binding,
    build,
    note,
    offline,
    project,
    run,
    scratch,  # noqa: F401 # -- an autouse fixture of the drain's tests
    ticket,
)


def gitdir(tree: Path) -> Path:
    return Path(git(tree, "rev-parse", "--absolute-git-dir"))


def stashes(root: Path) -> str:
    return git(root, "stash", "list", "--format=%gs")


def registered(root: Path) -> list[str]:
    listing = git(root, "worktree", "list", "--porcelain")
    return [
        line.removeprefix("worktree ")
        for line in listing.splitlines()
        if line.startswith("worktree ")
    ]


# --- S1: a work tree is removed only once its work is saved ----------------------------------


def test_a_stale_index_lock_keeps_the_tree_and_its_work(tmp_path: Path, capsys: Out) -> None:
    root = project(tmp_path)
    run(root, "open", ALPHA)
    tree = drain.tree_of(root, BRANCH_A)
    write(tree, "src/work.py", "an hour of uncommitted work\n")
    lock = gitdir(tree) / "index.lock"
    lock.write_text("")
    capsys.readouterr()
    assert run(root, "clean", "--all") == 0
    assert capsys.readouterr().out.startswith(
        f"left: {tree}: the drain that made it was interrupted"
    )
    assert (tree / "src/work.py").read_text(encoding="utf-8") == "an hour of uncommitted work\n"
    assert tree in drain.worktrees(root)
    lock.unlink()
    assert run(root, "clean", "--all") == 0
    assert not tree.exists()
    assert stashes(root) == f"On {BRANCH_A}: {BRANCH_A}: interrupted run"
    assert git(root, "show", "stash@{0}^3:src/work.py") == "an hour of uncommitted work"


def test_a_tree_left_mid_merge_is_kept_when_it_is_closed(tmp_path: Path, capsys: Out) -> None:
    root = project(tmp_path)
    run(root, "open", GAMMA)
    tree = drain.tree_of(root, BRANCH_C)
    git(root, "branch", "other")
    git(root, "worktree", "add", "-q", str(tmp_path / "other"), "other")
    write(tmp_path / "other", "src/a.py", "A = 'other'\n")
    commit(tmp_path / "other", "other side")
    write(tree, "src/a.py", "A = 'mine'\n")
    commit(tree, "mine")
    gatelib.git(tree, "merge", "other")
    write(tree, "src/draft.py", "uncommitted draft\n")
    capsys.readouterr()
    assert run(root, "close", GAMMA, "--outcome", "parked", "--reason", "x") == 0
    out = capsys.readouterr().out
    assert f"kept: the work tree stays at {tree}" in out
    assert (tree / "src/draft.py").read_text(encoding="utf-8") == "uncommitted draft\n"
    assert tree in drain.worktrees(root)
    reasons = note(root, BRANCH_C)["reasons"]
    assert isinstance(reasons, list)
    assert reasons[-1].startswith(f"the work tree stays at {tree}")


def test_a_damaged_index_is_not_read_as_clean(tmp_path: Path, capsys: Out) -> None:
    root = project(tmp_path)
    run(root, "open", ALPHA)
    tree = drain.tree_of(root, BRANCH_A)
    write(tree, "src/work.py", "an hour of uncommitted work\n")
    (gitdir(tree) / "index").write_bytes(b"garbage, not an index")
    assert drain.dirty(tree), "a status git cannot read is not a clean tree"
    capsys.readouterr()
    assert run(root, "clean", "--all") == 0
    assert capsys.readouterr().out.startswith(f"left: {tree}")
    assert (tree / "src/work.py").is_file()


def test_nothing_is_ended_while_the_work_cannot_be_saved(tmp_path: Path) -> None:
    root = project(tmp_path)
    run(root, "open", ALPHA)
    tree = drain.tree_of(root, BRANCH_A)
    write(tree, "src/work.py", "work\n")
    (gitdir(tree) / "index.lock").write_text("")
    service = servers.sleeper(tree)
    try:
        assert run(root, "clean", "--all") == 0
        assert service.poll() is None, "the program of a tree that stays is left running"
    finally:
        service.kill()
        service.wait()


# --- S2, S4: one drain at a time --------------------------------------------------------------


REAL_ANCESTRY = drain.ancestry


def other_session(monkeypatch: pytest.MonkeyPatch) -> subprocess.Popen[bytes]:
    """A live process standing for the session of another drain: the lease names it."""
    holder = subprocess.Popen(["sleep", "60"])  # noqa: S607 # -- a sleep, no shell
    row = drain.process_row(holder.pid)
    assert row is not None
    monkeypatch.setattr(drain, "ancestry", lambda: [f"{holder.pid}:{row[1]}"])
    return holder


def own_session(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(drain, "ancestry", REAL_ANCESTRY)


def test_a_second_drain_does_not_clean_the_first(
    tmp_path: Path, capsys: Out, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = project(tmp_path)
    first = other_session(monkeypatch)
    try:
        assert run(root, "clean", "--all") == 0
        assert run(root, "open", ALPHA) == 0
        tree = drain.tree_of(root, BRANCH_A)
        write(tree, "src/b.py", "drain 1's agent is mid-build\n")
        service = servers.sleeper(tree)
        own_session(monkeypatch)
        capsys.readouterr()
        try:
            assert run(root, "clean", "--all") == 2
            assert "another drain is running on this repository" in capsys.readouterr().out
            assert service.poll() is None
            assert (tree / "src/b.py").is_file()
            assert tree in drain.worktrees(root)
            assert stashes(root) == ""
            first.kill()
            first.wait()
            assert run(root, "clean", "--all") == 2, "a program of its trees still runs"
            service.kill()
            service.wait()
            assert run(root, "clean", "--all") == 0, "a drain whose session is gone is not held"
            assert not tree.exists()
            assert stashes(root) == f"On {BRANCH_A}: {BRANCH_A}: interrupted run"
        finally:
            service.kill()
            service.wait()
    finally:
        first.kill()
        first.wait()


def test_a_drain_run_again_from_its_own_session_is_not_a_rival(tmp_path: Path) -> None:
    root = project(tmp_path)
    assert run(root, "clean", "--all") == 0
    assert run(root, "clean", "--all") == 0


def test_the_report_ends_the_drain_and_take_over_ends_a_lost_one(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = project(tmp_path)
    sessions = [other_session(monkeypatch)]
    try:
        assert run(root, "clean", "--all") == 0
        own_session(monkeypatch)
        assert run(root, "clean", "--all") == 2
        assert run(root, "clean", "--all", "--take-over") == 0
        assert run(root, "report") == 0
        sessions.append(other_session(monkeypatch))
        assert run(root, "clean", "--all") == 0, "the report released the lease"
    finally:
        for session in sessions:
            session.kill()
            session.wait()


DRIVER = Path(__file__).with_name("drain_driver.py")


def start(root: Path, *argv: str, **env: str) -> subprocess.Popen[str]:
    """``drain.py argv`` as a process of its own, as a session's command is."""
    return subprocess.Popen(  # noqa: S603 # -- this interpreter, no shell
        [sys.executable, str(DRIVER), *argv, "--root", str(root), "--offline"],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        env={**os.environ, **env},
    )


def two_opens(tmp_path: Path, *, wait: str, outlast: bool) -> tuple[Path, list[object]]:
    """Two ``open`` processes of one ticket: the first stops after it read the plan.

    Why: no step is timed. The first goes on once the second says it waits for the lock, or,
    with ``outlast``, once the second has given up, so a slow start on a busy machine changes
    nothing.
    """
    root = project(tmp_path)
    gate = tmp_path / "gate"
    gate.mkdir()
    first = start(root, "open", ALPHA, DRV_PAUSE=str(gate))
    while not (gate / "paused").exists():
        assert first.poll() is None, first.stdout.read() if first.stdout else ""
        time.sleep(0.02)
    second = start(root, "open", ALPHA, DRV_LOCK_WAIT=wait, DRV_WAITING=str(gate / "waiting"))
    while not (gate / "waiting").exists():
        assert second.poll() is None, second.stdout.read() if second.stdout else ""
        time.sleep(0.02)
    waiting = first.poll() is None
    out2 = None
    if outlast:
        out2, _ = second.communicate(timeout=60)
    (gate / "go").touch()
    out1, _ = first.communicate(timeout=60)
    if out2 is None:
        out2, _ = second.communicate(timeout=60)
    return root, [first.returncode, second.returncode, waiting, out1, out2]


def test_a_second_open_of_the_ticket_waits_and_is_then_not_started(tmp_path: Path) -> None:
    root, (rc1, rc2, waiting, _out1, out2) = two_opens(tmp_path, wait="30", outlast=False)
    tree = drain.tree_of(root, BRANCH_A)
    assert (rc1, rc2, waiting) == (0, 1, True), "the second waited while the first ran"
    assert "is not started: it is building" in str(out2)
    assert tree in drain.worktrees(root), "the tree the first reported is still there"
    assert stashes(root) == ""


def test_a_second_open_is_refused_when_its_wait_runs_out(tmp_path: Path) -> None:
    root, (rc1, rc2, waiting, _out1, out2) = two_opens(tmp_path, wait="1", outlast=True)
    assert (rc1, rc2, waiting) == (0, 1, True)
    assert "another drain command is running on this repository" in str(out2)
    assert drain.tree_of(root, BRANCH_A) in drain.worktrees(root)
    assert stashes(root) == ""


SESSION = """
import re, shutil, subprocess, sys, time
from pathlib import Path

def run(*argv):
    done = subprocess.run([sys.executable, {driver!r}, *argv, "--root", {root!r}, "--offline"],
                          capture_output=True, text=True)
    return done.returncode, done.stdout + done.stderr

rc, out = run("clean", "--all")
token = re.search(r"drain: (\\w+)", out).group(1)
print("d1", rc, run("open", {ticket!r})[0], flush=True)
tree = Path({tree!r})
(tree / "src").mkdir(exist_ok=True)
(tree / "src/b.py").write_text("drain 1's agent is mid-build\\n")
(tree / "target").mkdir()
shutil.copy(shutil.which("sleep"), tree / "target/sleep")
service = subprocess.Popen([str(tree / "target/sleep"), "300"])
rc2, _ = run("clean", "--all")
time.sleep(0.2)
print("d2", rc2, "service", service.poll(), "tree", tree.exists(), flush=True)
rc3, _ = run("clean", "--all", "--drain", token)
time.sleep(0.2)
print("d1 again", rc3, "service", service.poll(), flush=True)
service.kill()
service.wait()
"""


def test_two_drains_under_one_long_lived_process_are_told_apart(tmp_path: Path) -> None:
    root = project(tmp_path)
    tree = drain.tree_of(root, BRANCH_A)
    session = SESSION.format(driver=str(DRIVER), root=str(root), ticket=ALPHA, tree=str(tree))
    done = subprocess.run(  # noqa: S603 # -- this interpreter, no shell
        [sys.executable, "-c", session], capture_output=True, text=True, check=False, timeout=120
    )
    lines = done.stdout.splitlines()
    assert done.returncode == 0, done.stderr
    assert lines[0] == "d1 0 0"
    assert lines[1] == "d2 2 service None tree True", "the second drain is refused; nothing ended"
    assert lines[2].startswith("d1 again 0 service "), "the first drain, by its token, resumes"
    assert not tree.exists()


def test_a_process_above_both_commands_is_no_evidence_of_a_drain(tmp_path: Path) -> None:
    root = project(tmp_path)
    held: dict[str, object] = {"chain": drain.ancestry()}
    assert drain.rival(root, held) is None, "every process of the chain is above this command"


def test_a_program_running_from_a_queue_tree_is_a_drain_though_its_chain_is_gone(
    tmp_path: Path,
) -> None:
    root = project(tmp_path)
    run(root, "open", ALPHA)
    tree = drain.tree_of(root, BRANCH_A)
    service = servers.sleeper(tree)
    try:
        why = drain.rival(root, {"chain": ["4194300:1"]})
        assert why == f"process {service.pid} runs in its work trees"
    finally:
        service.kill()
        service.wait()
    assert drain.rival(root, {"chain": ["4194300:1"]}) is None, "nothing left runs in it"


DEAD = """
echo $$ > {state}/pid
{clean} > {state}/clean.out 2>&1
{open} > {state}/open.out 2>&1
mkdir -p {tree}/src && echo "drain 1's agent is mid-build" > {tree}/src/b.py
touch {state}/ready
sleep 300
"""


def test_a_drain_whose_processes_are_all_gone_does_not_block_the_next(tmp_path: Path) -> None:
    root = project(tmp_path)
    tree = drain.tree_of(root, BRANCH_A)
    state = tmp_path / "d1"
    state.mkdir()

    def command(*argv: str) -> str:
        return " ".join([sys.executable, str(DRIVER), *argv, "--root", str(root), "--offline"])

    script = DEAD.format(
        state=state, clean=command("clean", "--all"), open=command("open", ALPHA), tree=tree
    )
    subprocess.run(["setsid", "-f", "bash", "-c", script], check=True)  # noqa: S603, S607
    while not (state / "ready").exists():
        time.sleep(0.05)
    shell = int((state / "pid").read_text())
    os.kill(shell, signal.SIGKILL)
    while (
        drain.process_row(shell) is not None and "Z" not in Path(f"/proc/{shell}/stat").read_text()
    ):
        time.sleep(0.05)
    new = subprocess.run(  # noqa: S603 # -- this interpreter, no shell
        [sys.executable, str(DRIVER), "clean", "--all", "--root", str(root), "--offline"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert new.returncode == 0, new.stdout
    assert not tree.exists()
    assert stashes(root) == f"On {BRANCH_A}: {BRANCH_A}: interrupted run"


def test_a_rebase_git_cannot_abort_stops_the_update(tmp_path: Path, capsys: Out) -> None:
    root = project(tmp_path)
    run(root, "open", ALPHA)
    tree = build(root, BRANCH_A, {"src/b.py": "B = 1\n"})
    (gitdir(tree) / "rebase-merge").mkdir()
    capsys.readouterr()
    assert update(root).returncode == 1
    assert drain.main(["drain", "update", ALPHA, "--root", str(root), "--offline"]) == 1
    assert "an earlier rebase stopped, git cannot abort it" in capsys.readouterr().out


def test_a_merged_branch_whose_tree_cannot_be_saved_is_recorded_and_kept(
    tmp_path: Path, capsys: Out
) -> None:
    root = project(tmp_path)
    run(root, "open", ALPHA)
    tree = build(root, BRANCH_A, {"src/b.py": "B = 1\n"})
    git(root, "merge", "-q", "--no-ff", BRANCH_A, "-m", f"Merge branch '{BRANCH_A}'")
    write(tree, "src/draft.py", "a draft\n")
    (gitdir(tree) / "index.lock").write_text("")
    capsys.readouterr()
    assert run(root, "clean") == 0
    out = capsys.readouterr().out
    assert f"kept: the work tree stays at {tree}" in out
    assert f"left: {tree}: its branch is merged, and" in out
    assert note(root, BRANCH_A)["outcome"] == "merged"
    assert (tree / "src/draft.py").is_file()


# --- S3: the cap counts a ticket until its tree is gone ---------------------------------------


def test_a_built_ticket_still_in_its_tree_holds_its_place_under_the_cap(
    tmp_path: Path, capsys: Out
) -> None:
    root = project(tmp_path)
    assert run(root, "open", ALPHA, "--concurrency", "1") == 0
    build(root, BRANCH_A, {"src/b.py": "B = 1\n"})
    plan = drain.plan(offline(root, 1))
    assert next(e.state for e in plan.tickets if e.branch == BRANCH_A) == "built"
    assert plan.start == ()
    capsys.readouterr()
    assert run(root, "open", GAMMA, "--concurrency", "1") == 1
    assert "1 tickets are building: the cap" in capsys.readouterr().out
    assert len(drain.worktrees(root)) == 1


# --- S8: a merge killed after the default branch moved ----------------------------------------

KILLER = """\
import os
import signal
import sys

sys.path.insert(0, {scripts!r})
import merge

real_land = merge.land


def land_then_die(*args, **kwargs):
    real_land(*args, **kwargs)
    os.kill(os.getpid(), signal.SIGKILL)


merge.land = land_then_die
raise SystemExit(merge.main(sys.argv))
"""


def test_a_merge_killed_after_it_landed_is_recorded_and_leaves_no_work_tree(
    tmp_path: Path, capsys: Out
) -> None:
    killer = tmp_path / "killing_merge.py"
    killer.write_text(KILLER.format(scripts=str(SCRIPTS)), encoding="utf-8")
    killing = binding(f"{sys.executable} {killer}")
    root = project(tmp_path, **{"docs/process/binding.md": killing})
    run(root, "open", ALPHA)
    tree = build(
        root,
        BRANCH_A,
        {"src/b.py": "B = 1\n", ALPHA: ticket(1, "Alpha", status="resolved")},
        record=approval("alpha"),
    )
    before = git(root, "rev-parse", "main")
    assert run(root, "land", ALPHA) == 2
    assert git(root, "rev-parse", "main") != before, "the default branch moved"
    assert "outcome" not in note(root, BRANCH_A)
    assert any("ratchet-merge-" in path for path in registered(root))
    capsys.readouterr()
    assert run(root, "land", ALPHA) == 0
    assert "closed: " in capsys.readouterr().out
    assert note(root, BRANCH_A)["outcome"] == "merged"
    assert not tree.exists()
    assert not any("ratchet-merge-" in path for path in registered(root))
    run(root, "report")
    assert "Alpha" in capsys.readouterr().out.split("## Waits for you")[0]


def test_clean_records_a_merge_no_note_said_and_sweeps_the_merge_tree(
    tmp_path: Path, capsys: Out
) -> None:
    killer = tmp_path / "killing_merge.py"
    killer.write_text(KILLER.format(scripts=str(SCRIPTS)), encoding="utf-8")
    root = project(tmp_path, **{"docs/process/binding.md": binding(f"{sys.executable} {killer}")})
    run(root, "open", ALPHA)
    tree = build(
        root,
        BRANCH_A,
        {"src/b.py": "B = 1\n", ALPHA: ticket(1, "Alpha", status="resolved")},
        record=approval("alpha"),
    )
    assert run(root, "land", ALPHA) == 2
    capsys.readouterr()
    assert run(root, "clean", "--all") == 0
    assert note(root, BRANCH_A)["outcome"] == "merged"
    assert not tree.exists()
    assert registered(root) == [str(root)]


def test_a_live_merge_keeps_its_work_tree_from_the_sweep(tmp_path: Path) -> None:
    root = project(tmp_path)
    scratch_dir = Path(os.environ["TMPDIR"]) / f"{merge.SCRATCH}live"
    scratch_dir.mkdir()
    git(root, "worktree", "add", "-q", "--detach", str(scratch_dir / "tree"))
    with merge.locked(scratch_dir / merge.HELD) as held:
        assert held is not None
        assert merge.sweep(root) == []
        assert (scratch_dir / "tree").is_dir()
    assert len(merge.sweep(root)) == 1
    assert registered(root) == [str(root)]


# --- S10: a fast-forward killed half-way ------------------------------------------------------


def half_landed(tmp_path: Path, *, adds: bool = True) -> tuple[Path, str, str]:
    """A root whose fast-forward was killed: some files of ``new`` written, the index locked."""
    root = project(tmp_path)
    git(root, "switch", "-q", "-c", "big")
    write(root, "src/a.py", "A = 'new'\n")
    if adds:
        write(root, "src/added.py", "ADDED = 1\n")
        write(root, "src/also.py", "ALSO = 1\n")
    new = commit(root, "feat(core): the merge result")
    git(root, "switch", "-q", "main")
    old = git(root, "rev-parse", "main")
    write(root, "src/a.py", "A = 'new'\n")
    if adds:
        write(root, "src/added.py", "ADDED = 1\n")
    (gitdir(root) / "index.lock").write_text("")
    intent = {"default": "main", "old": old, "new": new, "holder": str(root)}
    (gitdir(root) / merge.LANDING).write_text(json.dumps(intent), encoding="utf-8")
    return root, old, new


def test_a_fast_forward_killed_half_way_is_put_back(tmp_path: Path) -> None:
    root, old, _new = half_landed(tmp_path)
    assert drain.plan(offline(root)).blocked, "the checkout is not fit to build on"
    assert run(root, "clean") == 0
    assert git(root, "rev-parse", "main") == old
    assert git(root, "status", "--porcelain") == ""
    assert not (gitdir(root) / "index.lock").exists()
    assert not (root / "src/added.py").exists()
    assert (root / "src/a.py").read_text(encoding="utf-8") == "A = 1\n"
    assert drain.plan(offline(root)).blocked == ()
    assert (gitdir(root) / merge.LANDING).read_text(encoding="utf-8") == ""


def owned(root: Path, *paths: str) -> None:
    """Say the fast-forward began with ``paths`` already changed or untracked by the owner."""
    landing = gitdir(root) / merge.LANDING
    intent = json.loads(landing.read_text(encoding="utf-8"))
    landing.write_text(json.dumps({**intent, "owned": list(paths)}), encoding="utf-8")


def test_a_fast_forward_put_back_leaves_what_the_owner_had_changed_before(tmp_path: Path) -> None:
    root, old, _new = half_landed(tmp_path)
    write(root, "src/a.py", "A = 'the owner, meanwhile'\n")
    owned(root, "src/a.py")
    lines = merge.recover(root)
    assert "merge: src/a.py was changed since, and stays as it is" in lines
    assert (root / "src/a.py").read_text(encoding="utf-8") == "A = 'the owner, meanwhile'\n"
    assert not (root / "src/added.py").exists()
    assert git(root, "rev-parse", "main") == old


def test_a_fast_forward_that_only_changed_files_is_put_back(tmp_path: Path) -> None:
    root, old, _new = half_landed(tmp_path, adds=False)
    assert merge.recover(root)[0].startswith("merge: a run that stopped was moving `main`")
    assert (root / "src/a.py").read_text(encoding="utf-8") == "A = 1\n"
    assert git(root, "status", "--porcelain") == ""
    assert git(root, "rev-parse", "main") == old


def test_a_fast_forward_is_left_while_a_git_command_still_runs_in_the_tree(tmp_path: Path) -> None:
    root, _old, _new = half_landed(tmp_path)
    running = subprocess.Popen(
        ["git", "cat-file", "--batch"],  # noqa: S607
        cwd=root,
        stdin=subprocess.PIPE,
        stdout=subprocess.DEVNULL,
    )
    try:
        assert merge.recover(root) == [
            f"merge: a git command still runs in {root}; run this again once it ends"
        ]
        assert (root / "src/added.py").exists()
    finally:
        running.kill()
        assert running.stdin is not None
        running.stdin.close()
        running.wait()
    assert merge.recover(root)
    assert not (root / "src/added.py").exists()


def test_a_merge_cannot_land_while_another_is_landing(tmp_path: Path) -> None:
    root = project(tmp_path)
    old = git(root, "rev-parse", "main")
    with merge.locked(gitdir(root) / merge.LANDING) as held:
        assert held is not None
        reason = merge.land(root, "main", old=old, new=old)
    assert reason is not None
    assert reason.code == "moved"


def test_merge_puts_back_what_a_stopped_merge_left_before_it_runs(
    tmp_path: Path, capsys: Out
) -> None:
    root, _old, _new = half_landed(tmp_path)
    capsys.readouterr()
    merge.main(["merge", "big", "--root", str(root)])
    assert "merge: a run that stopped was moving `main`" in capsys.readouterr().out


def test_a_fast_forward_is_not_touched_while_a_merge_is_landing(tmp_path: Path) -> None:
    root, _old, _new = half_landed(tmp_path)
    with merge.locked(gitdir(root) / merge.LANDING) as held:
        assert held is not None
        assert merge.recover(root) == []
        assert (root / "src/added.py").exists()
    assert merge.recover(root)


def test_a_landing_that_moved_the_branch_needs_nothing_put_back(tmp_path: Path) -> None:
    root, _old, new = half_landed(tmp_path)
    git(root, "update-ref", "refs/heads/main", new)
    (gitdir(root) / "index.lock").unlink()
    git(root, "reset", "-q", "--hard", "main")
    assert merge.recover(root) == []
    assert git(root, "rev-parse", "main") == new
    assert (root / "src/also.py").exists()


def test_land_writes_its_intent_while_the_branch_moves_and_clears_it(tmp_path: Path) -> None:
    root = project(tmp_path)
    git(root, "switch", "-q", "-c", "big")
    write(root, "src/b.py", "B = 1\n")
    new = commit(root, "feat(core): the merge result")
    git(root, "switch", "-q", "main")
    old = git(root, "rev-parse", "main")
    seen = tmp_path / "seen.json"
    hook_script(tmp_path / "hooks", "post-merge", f'cp "{gitdir(root) / merge.LANDING}" "{seen}"')
    git(root, "config", "core.hooksPath", str(tmp_path / "hooks"))
    assert merge.land(root, "main", old=old, new=new) is None
    assert json.loads(seen.read_text(encoding="utf-8")) == {
        "default": "main",
        "old": old,
        "new": new,
        "holder": str(root),
        "owned": [],
    }
    assert (gitdir(root) / merge.LANDING).read_text(encoding="utf-8") == ""


# --- S11: an update killed mid-rebase ---------------------------------------------------------


def update(root: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603 # -- this interpreter, no shell
        [
            sys.executable,
            str(SCRIPTS / "drain.py"),
            "update",
            ALPHA,
            "--root",
            str(root),
            "--offline",
        ],
        capture_output=True,
        text=True,
        check=False,
    )


def test_an_update_killed_mid_rebase_is_started_again(tmp_path: Path) -> None:
    root = project(tmp_path)
    run(root, "open", ALPHA)
    tree = build(root, BRANCH_A, {"src/b.py": "B = 1\n"})
    write(root, "src/m.py", "M = 1\n")
    base = commit(root, "feat(core): merged meanwhile")
    hooks = tmp_path / "killhooks"
    # kills git rebase and the drain.py that ran it, once, at the rebase's first checkout
    hook_script(
        hooks,
        "post-checkout",
        f"[ -e {hooks}/fired ] && exit 0; touch {hooks}/fired; kill -9 $(ps -o ppid= $PPID) $PPID",
    )
    git(root, "config", "core.hooksPath", str(hooks))
    first = update(root)
    git(root, "config", "--unset", "core.hooksPath")
    assert first.returncode == -signal.SIGKILL
    assert git(tree, "rev-parse", "HEAD") == base, "the tree is detached at the default branch"
    second = update(root)
    assert (second.returncode, json.loads(second.stdout)) == (0, {"moved": True, "conflicts": []})
    assert git(tree, "rev-parse", "--abbrev-ref", "HEAD") == BRANCH_A
    assert git(tree, "merge-base", "--is-ancestor", base, "HEAD") == ""
    assert (tree / "src/b.py").is_file()
    assert git(tree, "log", "-1", "--format=%s") == "feat(core): the change"
    assert "rebasing" not in note(root, BRANCH_A)
    third = update(root)
    assert json.loads(third.stdout) == {"moved": False, "conflicts": []}


def test_an_update_killed_after_the_rebase_still_says_it_moved(tmp_path: Path, capsys: Out) -> None:
    root = project(tmp_path)
    run(root, "open", ALPHA)
    tree = build(root, BRANCH_A, {"src/b.py": "B = 1\n"})
    tip = git(root, "rev-parse", BRANCH_A)
    write(root, "src/m.py", "M = 1\n")
    commit(root, "feat(core): merged meanwhile")
    path = drain.queue_notes.note_path(root, BRANCH_A)
    drain.queue_notes.write_note(path, {**note(root, BRANCH_A), "rebasing": tip})
    git(tree, "rebase", "--quiet", "main")
    capsys.readouterr()
    assert update(root).stdout.strip() == json.dumps({"moved": True, "conflicts": []})
    assert json.loads(update(root).stdout) == {"moved": False, "conflicts": []}


def test_an_update_killed_before_it_dropped_the_record_drops_it_on_the_rerun(
    tmp_path: Path, capsys: Out
) -> None:
    root = project(tmp_path)
    run(root, "open", ALPHA)
    tree = build(root, BRANCH_A, {"src/b.py": "B = 1\n"}, record=approval("alpha"))
    tip = git(root, "rev-parse", BRANCH_A)
    write(root, "src/m.py", "M = 1\n")
    commit(root, "feat(core): merged meanwhile")
    path = drain.queue_notes.note_path(root, BRANCH_A)
    drain.queue_notes.write_note(path, {**note(root, BRANCH_A), "rebasing": tip})
    git(tree, "rebase", "--quiet", "main")  # the run stopped here: the record is still on top
    assert "verdict.json" in git(tree, "show", "--name-only", "--format=", "HEAD")
    capsys.readouterr()
    assert update(root).stdout.strip() == json.dumps({"moved": True, "conflicts": []})
    assert "verdict.json" not in git(tree, "show", "--name-only", "--format=", "HEAD")
    assert json.loads(update(root).stdout) == {"moved": False, "conflicts": []}


def test_a_parked_close_of_a_kept_tree_resets_the_retry_count_and_says_it_stays(
    tmp_path: Path,
) -> None:
    root = project(tmp_path)
    run(root, "open", ALPHA)
    tree = drain.tree_of(root, BRANCH_A)
    write(tree, "src/work.py", "work\n")
    (gitdir(tree) / "index.lock").write_text("")
    path = drain.queue_notes.note_path(root, BRANCH_A)
    drain.queue_notes.write_note(path, {**note(root, BRANCH_A), "attempts": 2})
    assert run(root, "close", ALPHA, "--outcome", "parked") == 0
    closed = note(root, BRANCH_A)
    assert closed["attempts"] == 0
    assert closed["hold"] is True
    assert any(
        "the work tree stays" in reason for reason in drain.queue_notes.texts(closed["reasons"])
    )


def test_a_marker_for_a_rebase_that_changed_nothing_is_cleared(tmp_path: Path) -> None:
    root = project(tmp_path)
    run(root, "open", ALPHA)
    tree = build(root, BRANCH_A, {"src/b.py": "B = 1\n"})
    path = drain.queue_notes.note_path(root, BRANCH_A)
    drain.queue_notes.write_note(
        path, {**note(root, BRANCH_A), "rebasing": git(tree, "rev-parse", "HEAD")}
    )
    assert json.loads(update(root).stdout) == {"moved": False, "conflicts": []}
    assert "rebasing" not in note(root, BRANCH_A)


def test_a_process_that_cannot_be_read_is_not_a_git_in_the_tree(tmp_path: Path) -> None:
    (tmp_path / "proc" / "4242").mkdir(parents=True)
    assert not merge.git_running_in(tmp_path, proc=tmp_path / "proc")


BIG = "bulk/a_big.bin"
CONTENT = b"0123456789abcdef" * 256


class MergeKilledError(Exception):
    """The merge run was killed."""


@pytest.mark.parametrize("written", [0, 100])
def test_a_fast_forward_killed_inside_one_files_write_is_put_back(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, written: int
) -> None:
    """The state a kill leaves: the intent is on disk, ``BIG`` is cut short, the index is locked."""
    root = project(tmp_path)
    git(root, "switch", "-q", "-c", "big")
    (root / BIG).parent.mkdir()
    (root / BIG).write_bytes(CONTENT)
    write(root, "src/b.py", "B = 1\n")
    new = commit(root, "feat(core): the merge result")
    git(root, "switch", "-q", "main")
    write(root, "owner.txt", "the owner's untracked notes\n")
    old = git(root, "rev-parse", "main")
    real = gatelib.git

    def die_in_the_write(where: Path, *args: str) -> str | None:
        if args[:2] != ("merge", "--ff-only"):
            return real(where, *args)
        (where / BIG).parent.mkdir(exist_ok=True)
        (where / BIG).write_bytes(CONTENT[:written])
        (gitdir(where) / "index.lock").write_text("")
        raise MergeKilledError

    monkeypatch.setattr(gatelib, "git", die_in_the_write)
    with pytest.raises(MergeKilledError):
        merge.land(root, "main", old=old, new=new)
    monkeypatch.setattr(gatelib, "git", real)
    assert (root / BIG).stat().st_size == written
    lines = merge.recover(root)
    assert lines[0].startswith("merge: a run that stopped was moving `main`")
    assert not any("stays as it is" in line for line in lines), lines
    assert not (root / BIG).exists(), "git's part-written file is not the owner's edit"
    assert git(root, "rev-parse", "main") == old
    assert git(root, "status", "--porcelain") == "?? owner.txt"
    assert (root / "owner.txt").read_text(encoding="utf-8") == "the owner's untracked notes\n"
    assert not (gitdir(root) / "index.lock").exists()


def test_an_update_killed_after_the_rebase_by_a_real_kill_drops_the_record_on_the_rerun(
    tmp_path: Path,
) -> None:
    root = project(tmp_path)
    run(root, "open", ALPHA)
    tree = build(root, BRANCH_A, {"src/b.py": "B = 1\n"}, record=approval("alpha"))
    write(root, "src/m.py", "M = 1\n")
    base = commit(root, "feat(core): merged meanwhile")
    killed = start(root, "update", ALPHA, DRV_KILL_AT="drop_record")
    killed.communicate(timeout=60)
    assert killed.returncode == -signal.SIGKILL
    assert git(tree, "merge-base", "--is-ancestor", base, "HEAD") == "", "the rebase finished"
    assert "verdict.json" in git(tree, "show", "--name-only", "--format=", "HEAD"), (
        "record is on top"
    )
    again = start(root, "update", ALPHA)
    out, _ = again.communicate(timeout=60)
    assert json.loads(out) == {"moved": True, "conflicts": []}
    assert "verdict.json" not in git(tree, "show", "--name-only", "--format=", "HEAD")


# --- findings of the drain's model, each replayed on a real repository ------------------------


def judged(root: Path, *, resolved: bool = True) -> Path:
    """Open ticket A and commit its change and an approving verdict record, as a run would."""
    run(root, "open", ALPHA)
    done = {ALPHA: ticket(1, "Alpha", status="resolved")} if resolved else {}
    return build(root, BRANCH_A, {"src/b.py": "B = 1\n", **done}, record=approval("alpha"))


def test_a_held_ticket_is_not_landed(tmp_path: Path, capsys: Out) -> None:
    root = project(tmp_path)
    judged(root)
    assert run(root, "close", ALPHA, "--outcome", "parked", "--reason", "the owner decides") == 0
    before = git(root, "rev-parse", "main")
    capsys.readouterr()
    assert run(root, "land", ALPHA) == 2
    out = capsys.readouterr().out
    assert "is held" in out
    assert "the owner decides" in out
    assert git(root, "rev-parse", "main") == before, "nothing landed"
    assert note(root, BRANCH_A)["outcome"] == "parked"


def test_a_close_after_the_landing_does_not_unsay_the_merge(tmp_path: Path, capsys: Out) -> None:
    root = project(tmp_path)
    judged(root)
    assert run(root, "land", ALPHA) == 0
    merged = note(root, BRANCH_A)
    assert merged["outcome"] == "merged"
    capsys.readouterr()
    assert run(root, "close", ALPHA, "--outcome", "parked") != 0
    assert "merged" in capsys.readouterr().out
    assert note(root, BRANCH_A) == merged
    assert run(root, "report") == 0
    report = capsys.readouterr().out
    assert "## Parked\n\n- none" in report
    assert "## Merged\n\n- none" not in report


def test_an_update_killed_after_its_rebase_and_a_clean_all_drops_the_record_on_the_rerun(
    tmp_path: Path,
) -> None:
    root = project(tmp_path)
    tree = judged(root, resolved=False)
    write(root, "src/m.py", "M = 1\n")
    base = commit(root, "feat(core): merged meanwhile")
    killed = start(root, "update", ALPHA, DRV_KILL_AT="drop_record")
    killed.communicate(timeout=60)
    assert killed.returncode == -signal.SIGKILL
    assert run(root, "clean", "--all") == 0
    assert not tree.exists(), "the sweep took the tree"
    again = start(root, "update", ALPHA)
    out, _ = again.communicate(timeout=60)
    assert json.loads(out) == {"moved": True, "conflicts": []}
    assert git(root, "merge-base", "--is-ancestor", base, BRANCH_A) == ""
    assert "verdict.json" not in git(root, "show", "--name-only", "--format=", BRANCH_A)
    assert json.loads(update(root).stdout) == {"moved": False, "conflicts": []}


def test_update_does_not_make_a_tree_beyond_the_cap(tmp_path: Path, capsys: Out) -> None:
    root = project(tmp_path)
    assert run(root, "open", ALPHA, "--concurrency", "1") == 0
    build(root, BRANCH_A, {"src/b.py": "B = 1\n"})
    assert run(root, "close", ALPHA, "--outcome", "parked", "--reason", "x") == 0
    assert run(root, "open", GAMMA, "--concurrency", "1") == 0
    write(root, ALPHA, ticket(1, "Alpha", "the owner answered\n\n"))
    commit(root, "docs: the owner edits ticket 1")
    capsys.readouterr()
    assert run(root, "update", ALPHA, "--concurrency", "1") == 1
    assert "1 tickets are building: the cap" in capsys.readouterr().out
    assert len(drain.worktrees(root)) == 1


def test_open_keeps_a_parked_outcome_the_report_has_not_listed(tmp_path: Path) -> None:
    root = project(tmp_path)
    run(root, "open", ALPHA)
    assert run(root, "close", ALPHA, "--outcome", "parked", "--reason", "needs a decision") == 0
    write(root, ALPHA, ticket(1, "Alpha", "the owner answered\n\n"))
    commit(root, "docs: the owner edits ticket 1")
    assert run(root, "open", ALPHA) == 0
    kept = note(root, BRANCH_A)
    assert (kept["outcome"], kept["reported"]) == ("parked", False)
    assert kept["reasons"] == ["needs a decision"]


def test_split_waits_for_the_drain_lock(
    tmp_path: Path, capsys: Out, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = project(tmp_path)
    run(root, "open", ALPHA)
    parts = tmp_path / "plan.json"
    parts.write_text(json.dumps({"parts": []}), encoding="utf-8")
    monkeypatch.setattr(drain, "LOCK_WAIT", 0.2)
    capsys.readouterr()
    with drain.exclusive(root):
        assert run(root, "split", ALPHA, "--plan", str(parts)) == 2
    assert "another drain command is running" in capsys.readouterr().out
    assert "outcome" not in note(root, BRANCH_A)
