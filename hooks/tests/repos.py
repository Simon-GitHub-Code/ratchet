"""Real scratch repositories for the gate-script tests."""

import subprocess
from pathlib import Path


def git(root: Path, *args: str) -> str:
    completed = subprocess.run(  # noqa: S603 # -- a fixed argv, no shell
        ["git", "-C", str(root), *args],  # noqa: S607 # -- git is resolved from PATH on purpose
        capture_output=True,
        text=True,
        check=True,
    )
    return completed.stdout.strip()


def write(root: Path, path: str, text: str) -> None:
    target = root / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text, encoding="utf-8")


def repository(root: Path, files: dict[str, str] | None = None) -> Path:
    """A repository on ``main`` with one commit holding ``files`` (a README by default)."""
    git(root, "init", "-q", "-b", "main")
    git(root, "config", "user.email", "t@example.invalid")
    git(root, "config", "user.name", "t")
    git(root, "config", "commit.gpgsign", "false")
    for path, text in (files or {"README.md": "# A\n"}).items():
        write(root, path, text)
    commit(root, "feat(core): the clean state")
    return root


def commit(root: Path, subject: str) -> str:
    git(root, "add", "-A")
    git(root, "commit", "-q", "--allow-empty", "-m", subject)
    return git(root, "rev-parse", "HEAD")


def branch(root: Path, name: str = "change") -> None:
    git(root, "checkout", "-q", "-b", name)
