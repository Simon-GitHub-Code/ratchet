"""``changed.py``: a gate over the change gets its base, and judges nothing on the adoption."""

import sys
from pathlib import Path

import pytest

import changed
import gatelib
from tests.repos import branch, commit, git, repository, write

Out = pytest.CaptureFixture[str]
BINDING = str(gatelib.BINDING)
ECHO = [sys.executable, "-c", "import os, sys; print(sys.argv[1], os.environ['RATCHET_BASE'])"]


def run(root: Path, *command: str) -> int:
    return changed.main(["changed.py", "--root", str(root), "--", *command])


@pytest.fixture
def adopted(tmp_path: Path) -> Path:
    """A repository whose default branch holds a binding, on a branch with one commit more."""
    root = repository(tmp_path, {BINDING: "# Binding\n"})
    branch(root)
    write(root, "src/new.py", "x = 1\n")
    commit(root, "feat(core): more")
    return root


def test_a_gate_over_the_change_gets_the_base_in_its_words_and_its_environment(
    adopted: Path, capfd: pytest.CaptureFixture[str]
) -> None:
    base = git(adopted, "rev-parse", "main")
    assert run(adopted, *ECHO, "against={base}") == 0
    assert capfd.readouterr().out == f"against={base} {base}\n"
    assert run(adopted, sys.executable, "-c", "import sys; sys.exit(3)") == 3


def test_on_the_adoption_nothing_is_judged_as_changed(tmp_path: Path, capfd: Out) -> None:
    root = repository(tmp_path)
    branch(root, "adopt")
    write(root, BINDING, "# Binding\n")
    assert run(root, sys.executable, "-c", "import sys; sys.exit(3)") == 0
    out = capfd.readouterr().out
    assert "so this change is the adoption" in out
    assert "tier 2 judges the whole tree" in out
    commit(root, "chore(method): adopt")
    assert run(root, sys.executable, "-c", "import sys; sys.exit(3)") == 0
    git(root, "switch", "-q", "main")
    git(root, "merge", "-q", "--no-ff", "adopt", "-m", "chore(method): adopt")
    assert run(root, sys.executable, "-c", "import sys; sys.exit(3)") == 0, "its base is the init"
    write(root, "src/new.py", "x = 1\n")
    commit(root, "feat(core): the first feature")
    assert run(root, sys.executable, "-c", "import sys; sys.exit(3)") == 3


def test_in_a_bite_the_fault_is_the_change_so_the_gate_runs_on_the_adoption_too(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = repository(tmp_path)
    branch(root, "adopt")
    monkeypatch.setenv(changed.BITING, "1")
    assert run(root, sys.executable, "-c", "import sys; sys.exit(3)") == 3


def test_a_repository_of_one_commit_has_nothing_to_compare_with(tmp_path: Path, capfd: Out) -> None:
    root = repository(tmp_path)
    assert run(root, "false") == 0
    assert "no commit to compare the tree with" in capfd.readouterr().out


def test_a_command_that_cannot_start_and_no_command_at_all(adopted: Path, capfd: Out) -> None:
    assert run(adopted, "no-such-command-here") == 127
    assert "changed: cannot run no-such-command-here" in capfd.readouterr().out
    with pytest.raises(SystemExit) as caught:
        changed.main(["changed.py", "--root", str(adopted)])
    assert caught.value.code == 2
