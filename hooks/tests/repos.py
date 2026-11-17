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


@functools.cache
def installed_copy() -> Path:
    here = Path(__file__).resolve().parents[2]
    scratch = Path(tempfile.mkdtemp(prefix="ratchet-installed-"))
    atexit.register(shutil.rmtree, scratch, ignore_errors=True)
    copy = scratch / "copy"
    for name in git(here, "ls-files", "-z").split("\0"):
        source = here / name
        if name and (source.is_symlink() or source.is_file()):
            (copy / name).parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, copy / name, follow_symlinks=False)
    return copy


def write(root: Path, path: str, text: str) -> None:
    target = root / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text, encoding="utf-8")


@functools.cache
def skeleton() -> Path:
    directory = Path(tempfile.mkdtemp(prefix="ratchet-skeleton-"))
    atexit.register(shutil.rmtree, directory, ignore_errors=True)
    git(directory, "init", "-q", "-b", "main")
    git(directory, "config", "user.email", "t@example.invalid")
    git(directory, "config", "user.name", "t")
    git(directory, "config", "commit.gpgsign", "false")
    git(directory, "config", "maintenance.auto", "false")
    return directory / ".git"


def repository(root: Path, files: dict[str, str] | None = None) -> Path:
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
    path = directory / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"#!/bin/sh\n{body}\n", encoding="utf-8")
    path.chmod(0o755)
    return path


def answering(name: str) -> str:
    return f'[ -n "$RATCHET_HOOK_PROBE" ] && echo {name} > "$RATCHET_HOOK_PROBE"\nexit 0'


def install_hooks(root: Path) -> None:
    for name in ("pre-commit", "commit-msg"):
        hook_script(root / ".git/hooks", name, answering(name))


CHAIN = (
    'local="$(git rev-parse --git-common-dir)/hooks/{name}"\n'
    '[ -x "$local" ] && exec "$local" "$@"\nexit 0'
)


def chaining_hooks(directory: Path) -> None:
    for name in ("pre-commit", "commit-msg"):
        hook_script(directory, name, CHAIN.format(name=name))
