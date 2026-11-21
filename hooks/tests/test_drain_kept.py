import os
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path
from unittest import mock

import pytest

import drain
import gatelib
from tests.repos import git, write
from tests.test_drain import (
    ALPHA,
    BRANCH_A,
    BRANCH_C,
    GAMMA,
    HALVES,
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


def kept_files(root: Path, branch: str) -> list[Path]:
    return sorted(drain.kept_home(root, branch).glob("*.kept"))


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
        return [*real(*args)[:2], b""]

    return patched


NEVER_LOST = {
    "unwritable": (lambda: mock.patch.object(Path, "write_bytes", unwritable), "Permission denied"),
    "unreadable": (lambda: mock.patch.object(Path, "read_bytes", unreadable), "Permission denied"),
    "unproven": (
        lambda: mock.patch.object(drain, "patches", wrong(drain.patches)),
        "does not give back what the work tree holds",
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
    assert git(root, "stash", "list") == ""
    assert run(root, "clean", "--all") == 0
    assert not tree.exists()
    assert MARKER in (restored(root, BRANCH_C) / "src/a.py").read_text(encoding="utf-8")


def killed_at(stop: tuple[str, ...]) -> Callable[..., str | None]:
    real = gatelib.git

    def patched(root: Path, *args: str, **named: str | None) -> str | None:
        if args[: len(stop)] == stop:
            raise Killed
        return real(root, *args, **named)

    return patched


def kill(*_args: object) -> None:
    raise Killed


SEAMS = {
    "before the work is read": lambda: mock.patch.object(drain, "aside_of", kill),
    "after the file is written": lambda: mock.patch.object(drain, "unproven", kill),
    "after the proof": lambda: mock.patch.object(gatelib, "git", killed_at(("reset",))),
    "after the tree is cleaned": lambda: mock.patch.object(
        gatelib, "git", killed_at(("worktree", "remove"))
    ),
}


@pytest.mark.parametrize("seam", list(SEAMS))
def test_a_close_killed_between_any_two_steps_loses_nothing_and_a_rerun_converges(
    tmp_path: Path, seam: str
) -> None:
    root = project(tmp_path)
    run(root, "open", GAMMA)
    tree = drain.tree_of(root, BRANCH_C)
    unfinished_work(tree)
    with SEAMS[seam](), pytest.raises(Killed):
        close_parked(root)
    held = tree.exists() and MARKER in (tree / "src/a.py").read_text(encoding="utf-8")
    assert held or len(kept_files(root, BRANCH_C)) == 1, "the work is in the tree or in its file"
    assert run(root, "clean", "--all") == 0
    assert run(root, "clean", "--all") == 0
    assert not tree.exists()
    assert len(kept_files(root, BRANCH_C)) == 1
    assert not reaches(root, MARKER)
    assert MARKER in (restored(root, BRANCH_C) / "src/staged.py").read_text(encoding="utf-8")


def old_drain_stash(tree: Path, label: str) -> None:
    unfinished_work(tree)
    git(tree, "stash", "push", "-q", "--include-untracked", "-m", label)


def test_the_drains_old_stashes_move_to_kept_files_and_no_other_stash_is_touched(
    tmp_path: Path, capsys: Out
) -> None:
    root = project(tmp_path)
    run(root, "open", GAMMA)
    tree = drain.tree_of(root, BRANCH_C)
    old_drain_stash(tree, f"{BRANCH_C}: blocked run")
    write(root, "src/a.py", "A = 'mine'\n")
    git(root, "stash", "push", "-q", "-m", f"{BRANCH_C}: blocked run")
    write(root, "src/a.py", "A = 'mine too'\n")
    git(root, "stash", "push", "-q", "-m", "feature/9-nine: interrupted run")
    write(root, "src/a.py", "A = 'wip'\n")
    git(root, "stash", "push", "-q", "-m", "wip")
    theirs = git(root, "stash", "list", "--format=%H %gs").splitlines()[:3]
    capsys.readouterr()
    assert run(root, "clean") == 0
    out = capsys.readouterr().out.splitlines()
    [kept] = kept_files(root, BRANCH_C)
    assert git(root, "stash", "list", "--format=%H %gs").splitlines() == theirs
    unproven = [
        f"kept: stash {sha} (`{subject}`) carries the drain's label, and no note of the drain"
        " proves it is the drain's, so it stays: `git stash show -p --include-untracked"
        f" {sha}` shows it"
        for sha, _, subject in (line.partition(" ") for line in theirs[1:])
    ]
    assert out[: len(unproven) + 1] == [
        *unproven,
        (
            f"kept: what the work tree of `{BRANCH_C}` held uncommitted is set aside at {kept}:"
            f" `drain.py restore {BRANCH_C}` puts it back in the checkout"
        ),
    ]
    assert not reaches(root, MARKER)
    assert run(root, "clean") == 0
    assert git(root, "stash", "list", "--format=%H %gs").splitlines() == theirs
    assert kept_files(root, BRANCH_C) == [kept], "a second run moves nothing"
    assert MARKER in (restored(root, BRANCH_C) / "src/untracked.py").read_text(encoding="utf-8")
    assert git(root, "diff", "--cached", "--name-only") == "src/staged.py"


def test_an_old_drain_stash_on_a_detached_head_moves_too(tmp_path: Path) -> None:
    root = project(tmp_path)
    run(root, "open", GAMMA)
    tree = drain.tree_of(root, BRANCH_C)
    git(tree, "switch", "-q", "--detach")
    old_drain_stash(tree, f"{BRANCH_C}: interrupted run")
    assert run(root, "clean") == 0
    assert git(root, "stash", "list") == ""
    assert len(kept_files(root, BRANCH_C)) == 1


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
    assert capsys.readouterr().out == (
        f"restore: {kept} does not apply on {'0' * len(base)} in the checkout, so it stays, and"
        " what applied of it stays in the checkout\n"
    )
    assert kept_files(root, BRANCH_C) == [kept]


def test_an_old_drain_stash_whose_file_cannot_be_written_stays_and_says_why(
    tmp_path: Path, capsys: Out
) -> None:
    root = project(tmp_path)
    run(root, "open", GAMMA)
    old_drain_stash(drain.tree_of(root, BRANCH_C), f"{BRANCH_C}: blocked run")
    sha = git(root, "rev-parse", "refs/stash")
    capsys.readouterr()
    with mock.patch.object(Path, "write_bytes", unwritable):
        assert run(root, "clean") == 0
    assert capsys.readouterr().out.splitlines()[0] == (
        f"kept: stash {sha} (`On {BRANCH_C}: {BRANCH_C}: blocked run`) stays: it could not be"
        f" set aside under {drain.kept_home(root, BRANCH_C)}: Permission denied"
    )
    assert git(root, "rev-parse", "refs/stash") == sha
