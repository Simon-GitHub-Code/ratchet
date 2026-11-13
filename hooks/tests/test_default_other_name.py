"""The path guards and the ref-move refusals hold on a default branch of any name (ADR 0016, 0021)."""

import io
import json
from pathlib import Path

import pytest
from ratchet_guards import hook
from tests.conftest import isolated_git_config  # noqa: F401
from tests.repos import commit, git, repository, write
from tests.test_drain import FILES, MERGE, binding, scratch  # noqa: F401


def decide(root: Path, case: dict) -> str:
    out = io.StringIO()
    hook.main(root, io.StringIO(json.dumps(case)), out)
    if not out.getvalue():
        return "allow"
    return json.loads(out.getvalue())["hookSpecificOutput"]["permissionDecision"]


def repo(tmp_path: Path, name: str) -> Path:
    """A repository with no remote, the manifest committed on its default branch ``name``."""
    root = tmp_path / name
    root.mkdir(parents=True)
    bound = binding(MERGE).replace("`main`", f"`{name}`")
    repository(root, {**FILES, "docs/process/binding.md": bound})
    if name != "main":
        git(root, "branch", "-m", "main", name)
    write(root, "src/c.py", "C = 1\n")
    commit(root, "feat(core): second")
    git(root, "branch", "feature/x")
    return root


def bash(root: Path, command: str) -> str:
    return decide(root, {"tool_name": "Bash", "tool_input": {"command": command}, "cwd": str(root)})


COMMANDS = [
    "git reset --hard HEAD~1",
    "git branch -f {d} HEAD~1",
    "git update-ref refs/heads/{d} HEAD~1",
    "git merge feature/x",
    "git push . feature/x:{d}",
    "git switch -C {d}",
    "git branch -m {d} other",
]


@pytest.mark.parametrize("command", COMMANDS)
@pytest.mark.parametrize("name", ["trunk", "develop"])
def test_a_command_is_judged_alike_on_any_default_branch_name(
    tmp_path: Path, name: str, command: str
) -> None:
    cmd = command.format(d=name)
    on_main = bash(repo(tmp_path / "a", "main"), cmd.replace(name, "main"))
    assert on_main != "allow"
    assert bash(repo(tmp_path / "b", name), cmd) != "allow"


def test_the_manifest_is_guarded_on_any_default_branch_name(tmp_path: Path) -> None:
    root = repo(tmp_path, "trunk")
    edit = {
        "tool_name": "Edit",
        "cwd": str(root),
        "tool_input": {
            "file_path": str(root / "guards/manifest.toml"),
            "old_string": "a",
            "new_string": "b",
        },
    }
    assert decide(root, edit) != "allow"
