"""Real scratch repositories for the gate-script tests."""

import atexit
import functools
import shutil
import subprocess
import tempfile
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


@functools.cache
def skeleton() -> Path:
    """The ``.git`` of an empty repository on ``main`` with a committer, made once per process.

    Copying it costs microseconds; ``git init`` and three ``git config`` calls cost a hundred
    milliseconds of process spawns, once per scratch repository, a thousand times a run.
    """
    directory = Path(tempfile.mkdtemp(prefix="ratchet-skeleton-"))
    atexit.register(shutil.rmtree, directory, ignore_errors=True)
    git(directory, "init", "-q", "-b", "main")
    git(directory, "config", "user.email", "t@example.invalid")
    git(directory, "config", "user.name", "t")
    git(directory, "config", "commit.gpgsign", "false")
    return directory / ".git"


def repository(root: Path, files: dict[str, str] | None = None) -> Path:
    """A repository on ``main`` with one commit holding ``files`` (a README by default)."""
    shutil.copytree(skeleton(), root / ".git", symlinks=True)
    for path, text in (files or {"README.md": "# A\n"}).items():
        write(root, path, text)
    snapshot(root, "feat(core): the clean state")
    return root


def snapshot(root: Path, subject: str) -> None:
    git(root, "add", "-A")
    git(root, "commit", "-q", "--allow-empty", "-m", subject)


def commit(root: Path, subject: str) -> str:
    snapshot(root, subject)
    return git(root, "rev-parse", "HEAD")


def branch(root: Path, name: str = "change") -> None:
    git(root, "checkout", "-q", "-b", name)


def hook_script(directory: Path, name: str, body: str) -> Path:
    """An executable hook ``name`` in ``directory``."""
    path = directory / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"#!/bin/sh\n{body}\n", encoding="utf-8")
    path.chmod(0o755)
    return path


def answering(name: str) -> str:
    """A hook body that answers the probe as the template recipe ``name`` does."""
    return f'[ -n "$RATCHET_HOOK_PROBE" ] && echo {name} > "$RATCHET_HOOK_PROBE"\nexit 0'


def install_hooks(root: Path) -> None:
    """The repository's own hooks, as ``pre-commit install`` leaves them: both answer the probe."""
    for name in ("pre-commit", "commit-msg"):
        hook_script(root / ".git/hooks", name, answering(name))


CHAIN = (
    'local="$(git rev-parse --git-common-dir)/hooks/{name}"\n'
    '[ -x "$local" ] && exec "$local" "$@"\nexit 0'
)


def chaining_hooks(directory: Path) -> None:
    """Global hooks that run the repository's own, as the machine's global hooks do."""
    for name in ("pre-commit", "commit-msg"):
        hook_script(directory, name, CHAIN.format(name=name))
