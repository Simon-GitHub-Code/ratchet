"""A path in the project is judged from the project's checkout, whatever ``.git`` lies between.

A ``.git`` an agent makes inside the project (a directory, a file, a link, ``git init``) moved the
root the hook judged a path from, which stripped the ``.claude/`` or ``.github/`` from the front of
it and read the manifest of a repository that has none. Only a checkout git records for the project
is one of its own: a worktree under ``.claude/worktrees/``, and a submodule its default branch
records.
"""

import io
import json
import os
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path

import pytest

from ratchet_guards import hook
from tests.test_hook import SHIM, _git, checkout, switch_to_a_branch


def empty_directory(directory: Path) -> None:
    (directory / ".git").mkdir(parents=True)


def empty_file(directory: Path) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    (directory / ".git").touch()


def link_to_nowhere(directory: Path) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    (directory / ".git").symlink_to("/nonexistent")


def git_init(directory: Path) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    _git(directory, "init", "-q", "-b", "main")


MARKERS: dict[str, Callable[[Path], None]] = {
    "an empty .git directory": empty_directory,
    "an empty .git file": empty_file,
    "a .git link to nowhere": link_to_nowhere,
    "git init": git_init,
}


def answer(cwd: Path, case: dict[str, object], project: Path | None = None) -> dict[str, str]:
    """What the hook prints for ``case`` in a session at ``cwd`` of ``project``, or ``{}``."""
    stdout = io.StringIO()
    assert hook.main(cwd, io.StringIO(json.dumps(case)), stdout, project=project) == 0
    return json.loads(stdout.getvalue())["hookSpecificOutput"] if stdout.getvalue() else {}


def write(cwd: Path, file: Path) -> dict[str, object]:
    return {"tool_name": "Write", "tool_input": {"file_path": str(file)}, "cwd": str(cwd)}


def shell(cwd: Path, command: str) -> dict[str, object]:
    return {"tool_name": "Bash", "tool_input": {"command": command}, "cwd": str(cwd)}


@pytest.fixture
def project(tmp_path: Path) -> Path:
    """An adopted checkout on a branch, so only a locked or an acting path gets a verdict."""
    root = checkout(tmp_path, name="project")
    switch_to_a_branch(root)
    return root


@pytest.mark.parametrize("marker", MARKERS)
@pytest.mark.parametrize(
    ("at", "path", "verdict"),
    [
        (".claude", ".claude/rules/x.md", ("deny", ".claude/rules/x.md is in guard group")),
        (".claude", ".claude/settings.json", ("deny", ".claude/settings.json is in guard group")),
        (".github", ".github/workflows/x.yml", ("ask", "(.github/workflows/x.yml)")),
        (".scratch/x", ".scratch/x/CLAUDE.md", ("ask", "(.scratch/x/CLAUDE.md)")),
    ],
)
def test_a_git_marker_inside_the_project_moves_neither_its_root_nor_its_manifest(
    *, project: Path, marker: str, at: str, path: str, verdict: tuple[str, str]
) -> None:
    decision, named = verdict
    MARKERS[marker](project / at)
    said = answer(project, write(project, project / path))
    assert said.get("permissionDecision") == decision
    assert named in said["permissionDecisionReason"]


def test_a_marker_in_the_git_directory_leaves_its_refs_locked(project: Path) -> None:
    (project / ".git/.git").mkdir()
    said = answer(project, shell(project, "git rev-parse HEAD > .git/refs/remotes/origin/main"))
    assert said["permissionDecision"] == "deny"
    assert ".git/refs/remotes/origin/main (git-state)" in said["permissionDecisionReason"]


def test_a_worktree_under_claude_worktrees_is_a_checkout_of_its_own(project: Path) -> None:
    _git(project, "worktree", "add", "-q", ".claude/worktrees/w", "-b", "w")
    worktree = project / ".claude/worktrees/w"
    assert answer(project, write(project, worktree / "src/app.py")) == {}
    for cwd in (project, worktree):
        said = answer(cwd, write(cwd, worktree / ".claude/settings.json"), project=project)
        assert said["permissionDecision"] == "deny"
        assert said["permissionDecisionReason"].count(".claude/settings.json is in guard") == 1
        assert ".claude/worktrees/w/.claude" not in said["permissionDecisionReason"]


def test_a_worktree_elsewhere_in_the_project_is_judged_as_part_of_it(project: Path) -> None:
    _git(project, "worktree", "add", "-q", ".claude/skills/w", "-b", "w")
    said = answer(project, write(project, project / ".claude/skills/w/SKILL.md"))
    assert said["permissionDecision"] == "deny"
    assert (
        ".claude/skills/w/SKILL.md is in guard group 'harness'" in said["permissionDecisionReason"]
    )


def test_a_submodule_the_default_branch_records_is_judged_by_its_own_manifest(
    tmp_path: Path,
) -> None:
    library = checkout(tmp_path, name="library")
    root = checkout(tmp_path, name="project")
    allow = ("-c", "protocol.file.allow=always")
    _git(root, *allow, "submodule", "add", "-q", str(library), "vendor/library")
    _git(root, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", "library")
    switch_to_a_branch(root)
    git_init(root / "vendor/loose")
    recorded = answer(root, write(root, root / "vendor/library/.claude/settings.json"))
    assert recorded["permissionDecision"] == "deny"
    assert recorded["permissionDecisionReason"].startswith(
        "Ratchet guard denied this tool call; no part of it ran. It edits a guard path that only "
        "the owner changes, by hand, outside Claude Code: .claude/settings.json is in guard group"
    )
    loose = answer(root, write(root, root / "vendor/loose/.claude/settings.json"))
    assert loose["permissionDecision"] == "ask"
    assert "(vendor/loose/.claude/settings.json)" in loose["permissionDecisionReason"]


def test_the_shim_judges_from_the_project_directory_the_harness_names(project: Path) -> None:
    git_init(project / ".claude")
    inside = project / ".claude"
    case = write(inside, inside / "rules/x.md")
    done = subprocess.run(  # noqa: S603 # -- the interpreter running these tests, on a fixed script
        [sys.executable, str(SHIM)],
        input=json.dumps(case),
        capture_output=True,
        text=True,
        check=False,
        env={**os.environ, "PYTHONPATH": "", "CLAUDE_PROJECT_DIR": str(project)},
    )
    said = json.loads(done.stdout)["hookSpecificOutput"]
    assert said["permissionDecision"] == "deny"
    assert ".claude/rules/x.md is in guard group 'harness'" in said["permissionDecisionReason"]
