import contextlib
import itertools
import os
import shutil
import subprocess
import sys
from collections.abc import Callable, Iterator
from datetime import timedelta
from pathlib import Path
from unittest import mock

import pytest

import drain
import gatelib
import route
from tests.repos import commit, git, write
from tests.test_drain import (
    ALPHA,
    BRANCH_A,
    BRANCH_C,
    GAMMA,
    HALVES,
    STAMP,
    WHOLE,
    Out,
    plan_file,
    project,
    run,
    scratch,  # noqa: F401 # -- an autouse fixture of the drain's tests
)

MARKER = "made-up-value-7f3a91c0"
GATE = """import subprocess
import sys

listed = subprocess.run(["git", "rev-list", "--all", "--reflog"], capture_output=True, text=True)
held = [
    sha
    for sha in listed.stdout.split()
    if subprocess.run(["git", "grep", "-q", "-F", sys.argv[1], sha]).returncode == 0
]
print(" ".join(held))
raise SystemExit(1 if held else 0)
"""


class Killed(BaseException): ...


def reaches(root: Path, text: str) -> bool:
    listed = git(root, "rev-list", "--objects", "--all", "--reflog").splitlines()
    ids = "".join(f"{line.split()[0]}\n" for line in listed)
    shown = subprocess.run(  # noqa: S603 # -- a fixed argv, no shell
        ["git", "-C", str(root), "cat-file", "--batch"],  # noqa: S607 # -- git from PATH on purpose
        input=ids.encode(),
        capture_output=True,
        check=True,
    )
    return text.encode() in shown.stdout


def stored(root: Path, text: str) -> bool:
    shown = subprocess.run(  # noqa: S603 # -- a fixed argv, no shell
        ["git", "-C", str(root), "cat-file", "--batch-all-objects", "--batch"],  # noqa: S607 # -- git from PATH on purpose
        capture_output=True,
        check=True,
    )
    return text.encode() in shown.stdout


LOOSE = (f"A = '{MARKER}'", f"UNTRACKED = '{MARKER}'")


def kept_files(root: Path, branch: str) -> list[Path]:
    return sorted(drain.kept_home(root, branch).glob("*.kept"))


def snapshot(tree: Path) -> tuple[str, str, dict[str, tuple[str, bytes]]]:
    files = {
        str(path.relative_to(tree)): (
            ("link", str(path.readlink()).encode())
            if path.is_symlink()
            else (oct(path.stat().st_mode & 0o111), path.read_bytes())
        )
        for path in tree.rglob("*")
        if path.relative_to(tree).parts[0] not in {".git", ".ratchet"}
        and (path.is_symlink() or not path.is_dir())
    }
    return git(tree, "rev-parse", "HEAD"), git(tree, "ls-files", "--stage", "-z"), files


def unfinished_work(tree: Path) -> None:
    write(tree, "src/staged.py", f"STAGED = '{MARKER}'\n")
    git(tree, "add", "src/staged.py")
    write(tree, "src/a.py", f"A = '{MARKER}'\n")
    write(tree, "src/untracked.py", f"UNTRACKED = '{MARKER}'\n")


def close_parked(root: Path) -> int:
    return run(root, "close", GAMMA, "--outcome", "parked", "--reason", "x")


def restored(root: Path, branch: str) -> Path:
    assert run(root, "restore", branch) == 0
    return root


def by_outcome(root: Path, tmp_path: Path, outcome: str) -> str:
    if outcome == "interrupted":
        assert run(root, "clean", "--all") == 0
        return BRANCH_C
    if outcome == "split":
        tree = drain.tree_of(root, BRANCH_A)
        unfinished_work(tree)
        assert run(root, "split", ALPHA, "--plan", str(plan_file(tmp_path, HALVES))) == 0
        return BRANCH_A
    assert run(root, "close", GAMMA, "--outcome", outcome, "--reason", "x") == 0
    return BRANCH_C


@pytest.mark.parametrize("outcome", ["merged", "waits", "parked", "split", "interrupted"])
def test_no_ref_reaches_the_work_the_drain_sets_aside_with_any_outcome(
    tmp_path: Path, outcome: str
) -> None:
    root = project(tmp_path, **WHOLE)
    for ref in (ALPHA, GAMMA):
        run(root, "open", ref)
    unfinished_work(drain.tree_of(root, BRANCH_C))
    branch = by_outcome(root, tmp_path, outcome)
    assert not drain.tree_of(root, branch).exists()
    assert not reaches(root, MARKER), "no ref of the repository reaches the set-aside work"
    assert stored(root, f"STAGED = '{MARKER}'"), "the agent's own `git add` stored what it staged"
    assert not any(stored(root, text) for text in LOOSE), "the drain stores nothing of the work"
    assert git(root, "stash", "list") == ""
    assert len(kept_files(root, branch)) == 1
    assert MARKER in (restored(root, branch) / "src/untracked.py").read_text(encoding="utf-8")


def test_a_gate_that_reads_every_commit_stays_green_for_the_next_ticket(tmp_path: Path) -> None:
    root = project(tmp_path)
    gate = tmp_path / "gate.py"
    gate.write_text(GATE, encoding="utf-8")
    run(root, "open", GAMMA)
    unfinished_work(drain.tree_of(root, BRANCH_C))
    assert close_parked(root) == 0
    run(root, "open", ALPHA)
    judged = subprocess.run(  # noqa: S603 # -- a fixed argv, no shell
        [sys.executable, str(gate), MARKER],
        cwd=drain.tree_of(root, BRANCH_A),
        capture_output=True,
        text=True,
        check=False,
    )
    assert judged.returncode == 0, f"the gate read the parked ticket's work in {judged.stdout}"


ODD = {
    "src/a.py": "A = 'unstaged'\n",
    "src/staged.py": "staged\n",
    "src/both.py": "staged first\n",
    "src/untracked.py": "untracked\n",
    "src/with space.py": "a space\n",
    "src/new\nline.py": "a newline\n",
    "bin/run.sh": "#!/bin/sh\n",
}


def everything(tree: Path) -> tuple[str, dict[str, tuple[bool, bytes]]]:
    files = {
        str(path.relative_to(tree)): (os.access(path, os.X_OK), path.read_bytes())
        for path in tree.rglob("*")
        if path.is_file() and path.relative_to(tree).parts[0] not in {".git", ".ratchet"}
    }
    return git(tree, "write-tree"), files


def test_restore_gives_back_exactly_the_tree_that_was_set_aside(tmp_path: Path) -> None:
    root = project(tmp_path, **{"src/gone.py": "tracked\n", "src/blob.bin": "old\n"})
    run(root, "open", GAMMA)
    tree = drain.tree_of(root, BRANCH_C)
    for path, text in ODD.items():
        write(tree, path, text)
    (tree / "bin/run.sh").chmod(0o755)
    (tree / "src/blob.bin").write_bytes(bytes(range(256)) * 4)
    (tree / "src/pic.bin").write_bytes(b"\x89PNG\x00\x01\x02" * 50)
    git(tree, "add", "src/staged.py", "src/both.py", "src/with space.py", "src/pic.bin")
    write(tree, "src/both.py", "staged first, then changed\n")
    (tree / "src/gone.py").unlink()
    base = git(tree, "rev-parse", "HEAD")
    was = everything(tree)
    assert close_parked(root) == 0
    assert not tree.exists()
    assert everything(restored(root, BRANCH_C)) == was
    assert git(root, "rev-parse", "HEAD") == base
    assert git(root, "branch", "--show-current") == BRANCH_C
    assert kept_files(root, BRANCH_C) == [], "a restore takes the work out of the drain's keeping"


def test_restore_refuses_a_checkout_with_work_of_its_own_and_says_when_nothing_is_kept(
    tmp_path: Path, capsys: Out
) -> None:
    root = project(tmp_path)
    run(root, "open", GAMMA)
    unfinished_work(drain.tree_of(root, BRANCH_C))
    assert close_parked(root) == 0
    write(root, "src/mine.py", "the owner's own\n")
    capsys.readouterr()
    assert run(root, "restore", BRANCH_C) == 1
    assert capsys.readouterr().out == (
        "restore: the checkout holds uncommitted work of its own: commit it or set it aside,"
        f" then run `drain.py restore {BRANCH_C}` again\n"
    )
    assert len(kept_files(root, BRANCH_C)) == 1
    (root / "src/mine.py").unlink()
    restored(root, BRANCH_C)
    assert run(root, "restore", BRANCH_C) == 1
    assert capsys.readouterr().out.endswith(f"restore: nothing of `{BRANCH_C}` is set aside\n")


def test_the_line_about_set_aside_work_names_where_it_is_and_the_restore_command(
    tmp_path: Path, capsys: Out
) -> None:
    root = project(tmp_path)
    run(root, "open", GAMMA)
    unfinished_work(drain.tree_of(root, BRANCH_C))
    capsys.readouterr()
    assert close_parked(root) == 0
    [kept] = kept_files(root, BRANCH_C)
    assert capsys.readouterr().out == (
        f"kept: what the work tree of `{BRANCH_C}` held uncommitted is set aside at {kept}:"
        f" `drain.py restore {BRANCH_C}` puts it back in the checkout\n"
        f"closed: {GAMMA}: parked\n"
    )
    assert kept.is_relative_to(Path(git(root, "rev-parse", "--absolute-git-dir")))


def unwritable(*_args: object) -> None:
    raise PermissionError(13, "Permission denied")


def unreadable(_path: Path) -> bytes:
    raise PermissionError(13, "Permission denied")


def wrong(real: Callable[..., list[bytes]]) -> Callable[..., list[bytes]]:
    def patched(*args: object) -> list[bytes]:
        return [real(*args)[0], b""]

    return patched


REAL_WRITE_TEXT = Path.write_text


def unnamed(path: Path, *args: str, **named: str) -> int:
    if path.name == drain.KEPT:
        raise PermissionError(13, "Permission denied")
    return REAL_WRITE_TEXT(path, *args, **named)


def full(path: Path, data: bytes) -> None:
    with path.open("wb") as written:
        written.write(data[: len(data) // 2])
    raise OSError(28, "No space left on device")


NEVER_LOST = {
    "unwritable": (lambda: mock.patch.object(Path, "write_bytes", unwritable), "Permission denied"),
    "full": (lambda: mock.patch.object(Path, "write_bytes", full), "No space left on device"),
    "unnamed": (lambda: mock.patch.object(Path, "write_text", unnamed), "Permission denied"),
    "unreadable": (lambda: mock.patch.object(Path, "read_bytes", unreadable), "Permission denied"),
    "unproven": (
        lambda: mock.patch.object(drain, "patches", wrong(drain.patches)),
        "does not give back what the tree holds",
    ),
}


@pytest.mark.parametrize("fault", list(NEVER_LOST))
def test_work_the_drain_cannot_keep_and_prove_stays_in_its_work_tree(
    tmp_path: Path, capsys: Out, fault: str
) -> None:
    root = project(tmp_path)
    run(root, "open", GAMMA)
    tree = drain.tree_of(root, BRANCH_C)
    unfinished_work(tree)
    patch, said = NEVER_LOST[fault]
    capsys.readouterr()
    with patch():
        assert close_parked(root) == 0
    out = capsys.readouterr().out
    assert f"kept: the work tree stays at {tree}: " in out
    assert said in out
    assert MARKER in (tree / "src/untracked.py").read_text(encoding="utf-8")
    assert git(tree, "diff", "--cached", "--name-only") == "src/staged.py"
    assert kept_files(root, BRANCH_C) == [], "a file that is not proven is not kept"
    assert not any(drain.kept_home(root, BRANCH_C).glob(".*")), "no partial file is left"
    assert git(root, "stash", "list") == ""
    assert run(root, "clean", "--all") == 0
    assert not tree.exists()
    assert MARKER in (restored(root, BRANCH_C) / "src/a.py").read_text(encoding="utf-8")


@contextlib.contextmanager
def cut(
    target: object, name: str, match: Callable[..., bool], *, before: bool = False, nth: int = 1
) -> Iterator[None]:
    real, matched = getattr(target, name), []

    def patched(*args: object, **named: object) -> object:
        hit = match(*args) and len(matched) == nth - 1
        matched.extend([args] if match(*args) else [])
        if hit and before:
            raise Killed
        done = real(*args, **named)
        if hit:
            raise Killed
        return done

    with mock.patch.object(target, name, patched):
        yield


def running_git(*stop: str) -> Callable[..., bool]:
    return lambda _where, *args: args[: len(stop)] == stop


def ending(suffix: str, place: int = 0) -> Callable[..., bool]:
    return lambda *args: str(args[place]).endswith(suffix)


SEAMS: dict[str, tuple[bool, Callable[[], contextlib.AbstractContextManager[None]]]] = {
    "after the unit is named": (False, lambda: cut(Path, "write_text", ending(drain.KEPT))),
    "after the file is written": (False, lambda: cut(Path, "write_bytes", ending(".partial"))),
    "after the proof": (False, lambda: cut(Path, "replace", ending(".kept", -1), before=True)),
    "after the rename": (False, lambda: cut(Path, "replace", ending(".kept", -1))),
    "between the reset and the clean": (
        False,
        lambda: cut(gatelib, "git", running_git("clean"), before=True),
    ),
    "after the clean": (False, lambda: cut(gatelib, "end_running_from", bool, before=True)),
    "before the tree is removed": (
        False,
        lambda: cut(gatelib, "git", running_git("worktree", "remove"), before=True),
    ),
    "after a second pass is renamed": (
        True,
        lambda: cut(Path, "replace", ending(".kept", -1), nth=2),
    ),
    "between the second reset and clean": (
        True,
        lambda: cut(gatelib, "git", running_git("clean"), before=True, nth=2),
    ),
    "after a second pass": (
        True,
        lambda: cut(gatelib, "git", running_git("worktree", "remove"), before=True),
    ),
}
LATE = "src/late.py", f"LATE = '{MARKER}'\n"


def written_late(tree: Path) -> list[int]:
    write(tree, *LATE)
    return []


def nothing_running(_tree: Path) -> list[int]:
    return []


@pytest.mark.parametrize("seam", list(SEAMS))
def test_a_close_killed_between_any_two_effects_reruns_to_one_unit_that_restores_exactly(
    tmp_path: Path, capsys: Out, seam: str
) -> None:
    root = project(tmp_path, **{"src/gone.py": "tracked\n", "bin/run.sh": "#!/bin/sh\n"})
    run(root, "open", GAMMA)
    tree = drain.tree_of(root, BRANCH_C)
    unfinished_work(tree)
    (tree / "src/gone.py").unlink()
    (tree / "bin/run.sh").chmod(0o755)
    late, cutting = SEAMS[seam]
    head, index, files = snapshot(tree)
    wanted = (head, index, {**files, LATE[0]: ("0o0", LATE[1].encode())} if late else files)
    ended = mock.patch.object(
        gatelib, "end_running_from", written_late if late else nothing_running
    )
    capsys.readouterr()
    with ended, cutting(), pytest.raises(Killed):
        close_parked(root)
    assert run(root, "clean", "--all") == 0
    assert run(root, "clean", "--all") == 0
    [unit] = kept_files(root, BRANCH_C)
    said = [line for line in capsys.readouterr().out.splitlines() if " is set aside at " in line]
    assert set(said) == {drain.kept_line(BRANCH_C, unit)}, "every line names the one unit"
    assert not tree.exists()
    assert not any(drain.kept_home(root, BRANCH_C).glob(".*")), "no partial file is left"
    assert not reaches(root, MARKER)
    assert snapshot(restored(root, BRANCH_C)) == wanted
    assert run(root, "restore", BRANCH_C) == 1
    assert capsys.readouterr().out.endswith(f"restore: nothing of `{BRANCH_C}` is set aside\n")
    assert snapshot(root) == wanted, "a second restore changes nothing"


CHILD = """import os, signal, sys
sys.path[:0] = sys.argv[1].split(os.pathsep)
import drain
drain.proven = lambda *_args: os.kill(os.getpid(), signal.SIGKILL)
drain.main(["drain", "close", sys.argv[3], "--outcome", "parked", "--reason", "x",
            "--root", sys.argv[2], "--offline"])
"""


def test_a_close_killed_outright_while_it_proves_leaves_nothing_a_rerun_waits_on(
    tmp_path: Path,
) -> None:
    root = project(tmp_path)
    run(root, "open", GAMMA)
    tree = drain.tree_of(root, BRANCH_C)
    unfinished_work(tree)
    child = tmp_path / "child.py"
    child.write_text(CHILD, encoding="utf-8")
    home = Path(drain.__file__).resolve().parents[1]
    paths = os.pathsep.join(str(home / part) for part in ("scripts", "hooks"))
    killed = subprocess.run(  # noqa: S603 # -- a fixed argv, no shell
        [sys.executable, str(child), paths, str(root), GAMMA], capture_output=True, check=False
    )
    assert killed.returncode == -9, killed.stderr
    assert run(root, "clean", "--all") == 0
    assert not tree.exists(), "the rerun finishes without the owner"
    assert MARKER in (restored(root, BRANCH_C) / "src/untracked.py").read_text(encoding="utf-8")


def ticking() -> contextlib.AbstractContextManager[object]:
    ticks = (STAMP + timedelta(seconds=tick) for tick in itertools.count())
    return mock.patch.object(drain, "now", side_effect=ticks)


REAL_WRITE_BYTES = Path.write_bytes


def cut_short(path: Path, data: bytes) -> int:
    if not path.name.endswith(".partial"):
        return REAL_WRITE_BYTES(path, data)
    REAL_WRITE_BYTES(path, data[: len(data) // 2])
    raise Killed


def killed(*_args: object) -> None:
    raise Killed


PARTIAL_KILLS: dict[str, Callable[[], contextlib.AbstractContextManager[object]]] = {
    "while it writes": lambda: mock.patch.object(Path, "write_bytes", cut_short),
    "once it is written": lambda: cut(Path, "write_bytes", ending(".partial")),
    "while it proves": lambda: mock.patch.object(drain, "proven", killed),
}


@pytest.mark.parametrize("kill", list(PARTIAL_KILLS))
def test_a_partial_a_kill_leaves_is_taken_in_by_the_rerun_whatever_the_clock_says(
    tmp_path: Path, kill: str
) -> None:
    root = project(tmp_path)
    run(root, "open", GAMMA)
    tree = drain.tree_of(root, BRANCH_C)
    unfinished_work(tree)
    wanted = snapshot(tree)
    home = drain.kept_home(root, BRANCH_C)
    with ticking():
        with PARTIAL_KILLS[kill](), pytest.raises(Killed):
            close_parked(root)
        assert any(home.glob(".*.partial")), "the kill left a partial"
        assert run(root, "clean", "--all") == 0
        assert run(root, "clean", "--all") == 0
    [unit] = kept_files(root, BRANCH_C)
    assert list(home.iterdir()) == [unit], "no partial stays beside the unit"
    assert not tree.exists()
    assert snapshot(restored(root, BRANCH_C)) == wanted


def test_a_partial_holding_work_the_tree_no_longer_holds_gives_it_to_the_unit(
    tmp_path: Path,
) -> None:
    root = project(tmp_path)
    run(root, "open", GAMMA)
    tree = drain.tree_of(root, BRANCH_C)
    unfinished_work(tree)
    with ticking():
        with mock.patch.object(drain, "proven", killed), pytest.raises(Killed):
            close_parked(root)
        (tree / "src/untracked.py").unlink()
        assert run(root, "clean", "--all") == 0
    [unit] = kept_files(root, BRANCH_C)
    assert list(drain.kept_home(root, BRANCH_C).iterdir()) == [unit]
    back = restored(root, BRANCH_C)
    assert (back / "src/untracked.py").read_text(encoding="utf-8") == f"UNTRACKED = '{MARKER}'\n"
    assert MARKER in (back / "src/a.py").read_text(encoding="utf-8")


def no_diff(real: Callable[..., bytes | None]) -> Callable[..., bytes | None]:
    def patched(store: drain.Scratch, *args: str, given: bytes = b"") -> bytes | None:
        return None if args[0] == "diff-tree" else real(store, *args, given=given)

    return patched


LATER_FAULTS = {
    "unwritable": "it could not be set aside under {home}: Permission denied",
    "unreadable": "{unit} cannot be read: Permission denied",
    "not laid over": "{unit} does not take what the work tree holds",
}


@pytest.mark.parametrize("fault", list(LATER_FAULTS))
def test_a_second_pass_that_cannot_be_kept_leaves_the_tree_with_what_came_late(
    tmp_path: Path, capsys: Out, fault: str
) -> None:
    root = project(tmp_path)
    run(root, "open", GAMMA)
    tree = drain.tree_of(root, BRANCH_C)
    unfinished_work(tree)
    home = drain.kept_home(root, BRANCH_C)
    faults = contextlib.ExitStack()

    def late_then_fault(where: Path) -> list[int]:
        [held] = kept_files(root, BRANCH_C)
        if fault == "unwritable":
            home.chmod(0o500)
        elif fault == "unreadable":
            held.chmod(0)
        else:
            undiffed = no_diff(drain.Scratch.git)
            faults.enter_context(mock.patch.object(drain.Scratch, "git", undiffed))
        return written_late(where)

    capsys.readouterr()
    try:
        with faults, mock.patch.object(gatelib, "end_running_from", late_then_fault):
            assert close_parked(root) == 0
    finally:
        home.chmod(0o700)
        for held in kept_files(root, BRANCH_C):
            held.chmod(0o600)
    [unit] = kept_files(root, BRANCH_C)
    out = capsys.readouterr().out.splitlines()
    assert drain.kept_line(BRANCH_C, unit) in out
    why = LATER_FAULTS[fault].format(home=home, unit=unit)
    assert f"kept: the work tree stays at {tree}: {why}" in out
    assert (tree / LATE[0]).read_text(encoding="utf-8") == LATE[1], "what came late stays"
    assert run(root, "clean", "--all") == 0
    assert not tree.exists()
    assert kept_files(root, BRANCH_C) == [unit]
    back = restored(root, BRANCH_C)
    assert (back / LATE[0]).read_text(encoding="utf-8") == LATE[1]
    assert MARKER in (back / "src/staged.py").read_text(encoding="utf-8")


def test_each_closing_is_its_own_unit_and_restore_says_which_it_took_and_what_stays(
    tmp_path: Path, capsys: Out
) -> None:
    root = project(tmp_path)
    for name in ("first", "second"):
        run(root, "open", GAMMA)
        write(drain.tree_of(root, BRANCH_C), f"src/{name}.py", f"{name.upper()} = 1\n")
        assert run(root, "close", GAMMA, "--outcome", "waits", "--reason", "behind: x") == 0
    first, second = kept_files(root, BRANCH_C)
    capsys.readouterr()
    restored(root, BRANCH_C)
    assert capsys.readouterr().out.splitlines()[-2:] == [
        f"restored: what the work tree of `{BRANCH_C}` held uncommitted, from {first}",
        (
            f"restore: 1 more of `{BRANCH_C}` from other closings stay set aside, the next at"
            f" {second}: commit or set aside this one, then run `drain.py restore {BRANCH_C}`"
            " again"
        ),
    ]
    assert (root / "src/first.py").is_file()
    assert not (root / "src/second.py").exists()
    assert run(root, "restore", BRANCH_C) == 1, "the checkout holds the first closing's work"
    commit(root, "feat(core): the first closing")
    restored(root, BRANCH_C)
    assert (root / "src/second.py").is_file()
    assert kept_files(root, BRANCH_C) == []


@pytest.mark.parametrize("fault", ["unreadable", "cut short"])
def test_a_unit_restore_cannot_read_gets_one_line_that_names_it(
    tmp_path: Path, capsys: Out, fault: str
) -> None:
    root = project(tmp_path)
    run(root, "open", GAMMA)
    unfinished_work(drain.tree_of(root, BRANCH_C))
    assert close_parked(root) == 0
    [unit] = kept_files(root, BRANCH_C)
    if fault == "unreadable":
        unit.chmod(0)
    else:
        unit.write_bytes(unit.read_bytes()[:-1])
    capsys.readouterr()
    try:
        assert run(root, "restore", BRANCH_C) == 1
    finally:
        unit.chmod(0o600)
    why = "cannot be read: Permission denied" if fault == "unreadable" else "is not whole"
    [line] = capsys.readouterr().out.splitlines()
    assert line.startswith(f"restore: {unit} {why}")
    assert line.endswith(f"once it can be read, run `drain.py restore {BRANCH_C}` again")
    assert kept_files(root, BRANCH_C) == [unit]
    assert git(root, "status", "--porcelain") == ""


def old_drain_stash(tree: Path, label: str) -> None:
    unfinished_work(tree)
    git(tree, "stash", "push", "-q", "--include-untracked", "-m", label)


def test_the_drains_old_stashes_move_to_kept_files_and_no_other_stash_is_touched(
    tmp_path: Path, capsys: Out
) -> None:
    root = project(tmp_path)
    run(root, "open", GAMMA)
    tree = drain.tree_of(root, BRANCH_C)
    old_drain_stash(tree, f"{BRANCH_C}: unapproved run")
    assert run(root, "close", GAMMA, "--outcome", "waits", "--reason", "x") == 0
    write(root, "src/a.py", "A = 'mine'\n")
    git(root, "stash", "push", "-q", "-m", f"{BRANCH_C}: unapproved run")
    write(root, "src/a.py", "A = 'mine too'\n")
    git(root, "stash", "push", "-q", "-m", "feature/9-nine: interrupted run")
    write(root, "src/a.py", "A = 'wip'\n")
    git(root, "stash", "push", "-q", "-m", "wip")
    theirs = git(root, "stash", "list", "--format=%H %gs").splitlines()[:3]
    unproven = [
        f"kept: stash {sha} (`{subject}`) stays: nothing the drain recorded proves the drain made"
        f" it; to move it by hand, run `git stash pop --index stash@{{{at}}}` in a clean checkout"
        f" of `{subject.split(': ')[1]}`"
        for at, (sha, _, subject) in reversed(
            list(enumerate(line.partition(" ") for line in theirs))
        )
        if at
    ]
    capsys.readouterr()
    assert run(root, "clean") == 0
    out = capsys.readouterr().out.splitlines()
    [kept] = kept_files(root, BRANCH_C)
    assert git(root, "stash", "list", "--format=%H %gs").splitlines() == theirs
    assert out[: len(unproven) + 1] == [drain.kept_line(BRANCH_C, kept), *unproven]
    assert not reaches(root, MARKER)
    assert run(root, "clean") == 0
    assert git(root, "stash", "list", "--format=%H %gs").splitlines() == theirs
    assert kept_files(root, BRANCH_C) == [kept], "a second run moves nothing"
    assert MARKER in (restored(root, BRANCH_C) / "src/untracked.py").read_text(encoding="utf-8")
    assert git(root, "diff", "--cached", "--name-only") == "src/staged.py"


def detached_in_another_tree(root: Path) -> str:
    run(root, "open", ALPHA)
    tree = drain.tree_of(root, BRANCH_C)
    git(tree, "switch", "-q", "--detach")
    old_drain_stash(tree, f"{BRANCH_A}: interrupted run")
    return BRANCH_A


def users_own(root: Path, label: str, *, committed: bool, outcome: str = "waits") -> str:
    assert run(root, "close", GAMMA, "--outcome", outcome, "--reason", "x") == 0
    git(root, "switch", "-q", BRANCH_C)
    if committed:
        write(root, "src/mine.py", "MINE = 1\n")
        commit(root, "feat(core): the owner's own commit")
    write(root, "src/a.py", "A = 'mine'\n")
    git(root, "stash", "push", "-q", "-m", f"{BRANCH_C}: {label} run")
    git(root, "switch", "-q", "main")
    return BRANCH_C


UNPROVEN: dict[str, Callable[[Path], str]] = {
    "made detached in another ticket's tree": detached_in_another_tree,
    "the owner's, with the word of a run the note does not record": lambda root: users_own(
        root, "interrupted", committed=False
    ),
    "the owner's, on a commit the note does not record": lambda root: users_own(
        root, "unapproved", committed=True
    ),
    "a blocked run, on a ticket the drain parked": lambda root: users_own(
        root, "blocked", committed=False, outcome="parked"
    ),
}


@pytest.mark.parametrize("stash", list(UNPROVEN))
def test_a_stash_with_the_drains_words_the_note_does_not_prove_stays_and_is_named(
    tmp_path: Path, capsys: Out, stash: str
) -> None:
    root = project(tmp_path)
    run(root, "open", GAMMA)
    branch = UNPROVEN[stash](root)
    sha, _, subject = git(root, "stash", "list", "--format=%H %gs").partition(" ")
    capsys.readouterr()
    assert run(root, "clean") == 0
    assert (
        f"kept: stash {sha} (`{subject}`) stays: nothing the drain recorded proves the drain made"
        f" it; to move it by hand, run `git stash pop --index stash@{{0}}` in a clean checkout of"
        f" `{branch}`"
    ) in capsys.readouterr().out.splitlines()
    assert git(root, "rev-parse", "refs/stash") == sha
    assert kept_files(root, branch) == []


def test_a_rerun_after_the_tree_went_between_two_passes_names_the_unit(
    tmp_path: Path, capsys: Out
) -> None:
    root = project(tmp_path)
    run(root, "open", GAMMA)
    tree = drain.tree_of(root, BRANCH_C)
    unfinished_work(tree)
    wanted = snapshot(tree)
    with mock.patch.object(gatelib, "end_running_from", killed), pytest.raises(Killed):
        close_parked(root)
    shutil.rmtree(tree)
    git(root, "worktree", "prune")
    capsys.readouterr()
    assert run(root, "clean", "--all") == 0
    [unit] = kept_files(root, BRANCH_C)
    assert drain.kept_line(BRANCH_C, unit) in capsys.readouterr().out.splitlines()
    assert snapshot(restored(root, BRANCH_C)) == wanted


def test_a_kept_file_that_does_not_apply_stays_and_the_line_says_so(
    tmp_path: Path, capsys: Out
) -> None:
    root = project(tmp_path)
    run(root, "open", GAMMA)
    unfinished_work(drain.tree_of(root, BRANCH_C))
    assert close_parked(root) == 0
    [kept] = kept_files(root, BRANCH_C)
    base = git(root, "rev-parse", BRANCH_C)
    kept.write_bytes(kept.read_bytes().replace(base.encode(), b"0" * len(base), 1))
    capsys.readouterr()
    assert run(root, "restore", BRANCH_C) == 1
    assert capsys.readouterr().out == not_taken(
        kept, f"does not apply on {'0' * len(base)} in the checkout"
    )
    assert kept_files(root, BRANCH_C) == [kept]
    assert git(root, "branch", "--show-current") == "main"


def not_taken(unit: Path, why: str) -> str:
    return (
        f"restore: {unit} {why}, so it stays and the checkout is as it was: once the checkout can"
        f" take it, run `drain.py restore {BRANCH_C}` again\n"
    )


def test_a_restore_a_read_only_folder_refuses_keeps_the_unit_and_the_checkout_as_it_was(
    tmp_path: Path, capsys: Out
) -> None:
    root = project(tmp_path)
    run(root, "open", GAMMA)
    unfinished_work(drain.tree_of(root, BRANCH_C))
    assert close_parked(root) == 0
    [unit] = kept_files(root, BRANCH_C)
    git(root, "switch", "-q", BRANCH_C)
    was = snapshot(root)
    (root / "src").chmod(0o555)
    capsys.readouterr()
    try:
        assert run(root, "restore", BRANCH_C) == 1
    finally:
        (root / "src").chmod(0o755)
    assert capsys.readouterr().out == not_taken(unit, "could not be written into the checkout")
    assert kept_files(root, BRANCH_C) == [unit]
    assert snapshot(root) == was
    assert git(root, "branch", "--show-current") == BRANCH_C
    assert MARKER in (restored(root, BRANCH_C) / "src/untracked.py").read_text(encoding="utf-8")


APPLIED = {
    "refused": (None, "could not be written into the checkout"),
    "said yes and wrote nothing": (
        b"",
        "was applied, yet the checkout does not hold what it set aside",
    ),
}


@pytest.mark.parametrize("answer", list(APPLIED))
def test_a_restore_whose_apply_to_the_files_fails_keeps_the_unit_and_the_checkout_as_it_was(
    tmp_path: Path, capsys: Out, answer: str
) -> None:
    root = project(tmp_path)
    run(root, "open", GAMMA)
    unfinished_work(drain.tree_of(root, BRANCH_C))
    assert close_parked(root) == 0
    [unit] = kept_files(root, BRANCH_C)
    was = snapshot(root)
    answered, why = APPLIED[answer]
    real = drain.plumb

    def plumb(
        where: Path, *args: str, env: dict[str, str] | None = None, given: bytes = b""
    ) -> bytes | None:
        return answered if args == (*drain.APPLY, "-") else real(where, *args, env=env, given=given)

    capsys.readouterr()
    with mock.patch.object(drain, "plumb", plumb):
        assert run(root, "restore", BRANCH_C) == 1
    assert capsys.readouterr().out == not_taken(unit, why)
    assert kept_files(root, BRANCH_C) == [unit]
    assert snapshot(root) == was
    assert git(root, "branch", "--show-current") == "main"
    assert MARKER in (restored(root, BRANCH_C) / "src/a.py").read_text(encoding="utf-8")


def test_a_unit_that_meets_a_file_in_the_checkout_restores_nothing(
    tmp_path: Path, capsys: Out
) -> None:
    root = project(tmp_path)
    run(root, "open", GAMMA)
    unfinished_work(drain.tree_of(root, BRANCH_C))
    assert close_parked(root) == 0
    (root / git(root, "rev-parse", "--git-path", "info/exclude")).write_text(
        "src/untracked.py\n", encoding="utf-8"
    )
    write(root, "src/untracked.py", "ignored\n")
    capsys.readouterr()
    assert run(root, "restore", BRANCH_C) == 1
    [unit] = kept_files(root, BRANCH_C)
    applies = f"does not apply on {git(root, 'rev-parse', BRANCH_C)} in the checkout"
    assert capsys.readouterr().out.endswith(not_taken(unit, applies))
    assert git(root, "branch", "--show-current") == "main"
    assert git(root, "status", "--porcelain") == ""
    assert git(root, "diff", "--cached", "--name-only") == "", "the index is as it was"
    assert (root / "src/untracked.py").read_text(encoding="utf-8") == "ignored\n"


def test_an_old_drain_stash_whose_file_cannot_be_written_stays_and_says_why(
    tmp_path: Path, capsys: Out
) -> None:
    root = project(tmp_path)
    run(root, "open", GAMMA)
    old_drain_stash(drain.tree_of(root, BRANCH_C), f"{BRANCH_C}: interrupted run")
    sha = git(root, "rev-parse", "refs/stash")
    capsys.readouterr()
    with mock.patch.object(Path, "write_bytes", unwritable):
        assert run(root, "clean") == 0
    assert capsys.readouterr().out.splitlines()[0] == (
        f"kept: stash {sha} (`On {BRANCH_C}: {BRANCH_C}: interrupted run`) stays: it could not be"
        f" set aside under {drain.kept_home(root, BRANCH_C)}: Permission denied"
    )
    assert git(root, "rev-parse", "refs/stash") == sha


def test_the_drains_old_stashes_of_one_branch_come_back_in_the_order_they_were_made(
    tmp_path: Path,
) -> None:
    root = project(tmp_path)
    run(root, "open", GAMMA)
    tree = drain.tree_of(root, BRANCH_C)
    for word in ("older", "newer"):
        write(tree, "src/a.py", f"A = '{word}'\n")
        git(tree, "stash", "push", "-q", "-m", f"{BRANCH_C}: interrupted run")
    assert run(root, "clean") == 0
    assert git(root, "stash", "list") == ""
    assert (restored(root, BRANCH_C) / "src/a.py").read_text(encoding="utf-8") == "A = 'older'\n"
    commit(root, "feat(core): the older run")
    assert (restored(root, BRANCH_C) / "src/a.py").read_text(encoding="utf-8") == "A = 'newer'\n"


@pytest.mark.parametrize("command", [("clean",), ("clean", "--all"), ("restore", BRANCH_C)])
def test_a_stash_the_feature_skill_made_stays_and_the_route_reads_the_same_blocked_runs(
    tmp_path: Path, capsys: Out, command: tuple[str, ...]
) -> None:
    root = project(tmp_path)
    run(root, "open", GAMMA)
    assert run(root, "close", GAMMA, "--outcome", "waits", "--reason", "x") == 0
    git(root, "switch", "-q", BRANCH_C)
    write(root, "src/a.py", "A = 'blocked'\n")
    git(root, "stash", "push", "-q", "--include-untracked", "-m", f"{BRANCH_C}: blocked run")
    git(root, "switch", "-q", "main")
    sha = git(root, "rev-parse", "refs/stash")
    before = route.blocked_runs(root, "main")
    assert before == {BRANCH_C: f"{BRANCH_C}: blocked run"}
    capsys.readouterr()
    run(root, *command)
    assert git(root, "rev-parse", "refs/stash") == sha, "the feature skill's stash stays"
    assert route.blocked_runs(root, "main") == before
    assert (
        f"kept: stash {sha} (`On {BRANCH_C}: {BRANCH_C}: blocked run`) stays: nothing the drain"
        " recorded proves the drain made it; to move it by hand, run `git stash pop --index"
        f" stash@{{0}}` in a clean checkout of `{BRANCH_C}`"
    ) in capsys.readouterr().out.splitlines()
