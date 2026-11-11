"""The default branch's ref is what the path guards read; a command that moves it is refused.

Each case is a real repository. The manifest is committed on ``main`` as the second commit and
``old`` is the commit before it, so a ref moved to ``old`` reads a history that holds no
manifest, and every path guard is off. The first test shows that each command here does switch
them off when nothing stands in the way. The second asks the hook first, as an agent's tool call
would, and runs the command only if the hook let it through (ADR 0016).
"""

import io
import json
import subprocess
from collections.abc import Callable
from pathlib import Path

import pytest

from ratchet_guards import hook
from tests.repos import commit, git, repository, write
from tests.support import MANIFEST_TEXT

PROBE = "scripts/gate.py"


def local(tmp_path: Path) -> Path:
    """``main`` holds the manifest, ``old`` precedes it, and ``feature/x`` is checked out."""
    root = tmp_path / "local"
    root.mkdir()
    repository(root)
    git(root, "branch", "old")
    write(root, "guards/manifest.toml", MANIFEST_TEXT)
    commit(root, "chore(guards): the manifest")
    git(root, "switch", "-q", "-c", "feature/x", "old")
    commit(root, "feat(core): work")
    return root


def on_main(tmp_path: Path) -> Path:
    root = local(tmp_path)
    git(root, "switch", "-q", "main")
    return root


def cloned(tmp_path: Path) -> Path:
    """The same history behind a remote, so that ``origin/HEAD`` names the default branch."""
    source = local(tmp_path)
    git(source, "switch", "-q", "main")
    clone = tmp_path / "clone"
    git(tmp_path, "clone", "-q", str(source), str(clone))
    git(clone, "switch", "-q", "-c", "feature/x", "origin/old")
    return clone


def decision(root: Path, tool: str, tool_input: dict[str, str]) -> str:
    """What the hook prints for one tool call in ``root``: ``ask``, ``deny`` or nothing."""
    payload = {"tool_name": tool, "tool_input": tool_input, "cwd": str(root)}
    stdout = io.StringIO()
    assert hook.main(root, io.StringIO(json.dumps(payload)), stdout) == 0
    if not stdout.getvalue():
        return ""
    return str(json.loads(stdout.getvalue())["hookSpecificOutput"]["permissionDecision"])


def probe(root: Path) -> str:
    """The decision on a Write of a guard path: ``ask`` while the path guards are on."""
    return decision(root, "Write", {"file_path": str(root / PROBE)})


def run(root: Path, command: str) -> None:
    done = subprocess.run(  # noqa: S602 # -- the attack under test, in a temporary repository
        command, shell=True, cwd=root, capture_output=True, text=True, check=False
    )
    assert done.returncode == 0, done.stderr


Scenario = Callable[[Path], Path]
HEREDOC = "git update-ref --stdin <<'EOF'\nupdate refs/heads/main old\nEOF"
PYTHON = "import subprocess; subprocess.run(['git', 'branch', '-f', 'main', 'old'])"
ATTACKS: dict[Scenario, list[str]] = {
    local: [
        "git branch -f main old",
        "git branch -M old main",
        "git branch -m main gone",
        "git branch -D main",
        "git update-ref refs/heads/main old",
        "git update-ref -d refs/heads/main",
        "git checkout -B main old",
        "git switch -C main old",
        "git push . +old:main",
        "git fetch . +old:main",
        "git fetch . +refs/heads/old:refs/heads/main",
        "git worktree add -B main ../elsewhere old",
        "git replace main old",
        "git replace --force main old",
        HEREDOC,
        "git symbolic-ref refs/heads/main refs/heads/old",
        "cp .git/refs/heads/old .git/refs/heads/main",
        f'python3 -c "{PYTHON}"',
        "sh -c 'git branch -f main old'",
        "env git branch -f main old",
        "echo main | xargs -I{} git branch -f main old",
        "eval 'git branch -f main old'",
    ],
    on_main: [
        "git reset --soft old",
        "git reset old",
        "git reset --mixed old",
        "git reset --keep old",
        "git reset --merge old",
        "git reset --hard old",
        "git rebase --onto old main",
        "git branch -m gone",
        "git update-ref HEAD old",
    ],
    cloned: [
        "git update-ref refs/remotes/origin/main origin/old",
        "git symbolic-ref refs/remotes/origin/HEAD refs/remotes/origin/old",
        "git remote set-head origin old",
        "git fetch . +refs/remotes/origin/old:refs/remotes/origin/main",
        "git replace origin/main origin/old",
        (
            "git config remote.origin.fetch +refs/heads/old:refs/remotes/origin/main"
            " && git fetch origin"
        ),
    ],
}
CASES = [
    pytest.param(scenario, command, id=f"{scenario.__name__}: {command.splitlines()[0]}")
    for scenario, commands in ATTACKS.items()
    for command in commands
]


@pytest.mark.parametrize(("scenario", "command"), CASES)
def test_the_command_switches_the_path_guards_off_when_nothing_stops_it(
    tmp_path: Path, scenario: Scenario, command: str
) -> None:
    root = scenario(tmp_path)
    assert probe(root) == "ask"
    run(root, command)
    assert probe(root) == ""


@pytest.mark.parametrize(("scenario", "command"), CASES)
def test_an_agent_cannot_move_the_default_ref_and_then_write_a_guard_path(
    tmp_path: Path, scenario: Scenario, command: str
) -> None:
    root = scenario(tmp_path)
    if decision(root, "Bash", {"command": command}) == "":
        run(root, command)
    assert probe(root) == "ask"
