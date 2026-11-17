import subprocess
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path

from ratchet_guards.manifest import MANIFEST_PATH
from ratchet_guards.refs import LOCAL, REMOTE_HEAD, TRACKING

Git = Callable[[Path, Sequence[str]], str | None]
GIT_TIMEOUT_SECONDS = 5
FALLBACK_BRANCHES = (*TRACKING, *LOCAL)
_LAST_HELD = ("-c", "log.follow=false", "log", "-1", "--format=%H", "--diff-filter=AM")


@dataclass(frozen=True, slots=True)
class Committed:
    text: str
    gone: bool = False


def run_git(cwd: Path, args: Sequence[str]) -> str | None:
    try:
        completed = subprocess.run(  # noqa: S603 # -- a fixed argv, no shell
            ["git", "-C", str(cwd), *args],  # noqa: S607 # -- git is resolved from PATH on purpose
            capture_output=True,
            text=True,
            timeout=GIT_TIMEOUT_SECONDS,  # pragma: no mutate # -- only bounds a hung git
            check=False,  # pragma: no mutate # -- a failure returns None either way
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return completed.stdout.strip() if completed.returncode == 0 else None


_MOVING = "checkout: moving from "


def _main_worktree_branches(checkout: Path, git: Git) -> list[str]:
    listed = git(checkout, ["worktree", "list", "--porcelain"])
    first = (listed or "").split("\n\n", 1)[0].splitlines()
    path = next((ln.removeprefix("worktree ") for ln in first if ln.startswith("worktree ")), "")
    current = next((ln.removeprefix("branch ") for ln in first if ln.startswith("branch ")), "")
    log = git(Path(path), ["log", "-g", "--format=%gs", "HEAD"]) if path else None
    moves = [line for line in reversed((log or "").splitlines()) if line.startswith(_MOVING)]
    previous = [line.removeprefix(_MOVING).rpartition(" to ")[0] for line in moves]
    return [*(f"refs/heads/{name}" for name in previous), *([current] if current else [])]


def _first_existing(checkout: Path, refs: Sequence[str], git: Git) -> str | None:
    return next((r for r in refs if git(checkout, ["show-ref", "--verify", "--hash", r])), None)


def _fixed(checkout: Path, git: Git) -> str | None:
    remote_head = git(checkout, ["symbolic-ref", REMOTE_HEAD])
    return _first_existing(
        checkout, [*([remote_head] if remote_head else []), *FALLBACK_BRANCHES], git
    )


def default_branch(checkout: Path, git: Git = run_git) -> str | None:
    return _fixed(checkout, git) or _first_existing(
        checkout, _main_worktree_branches(checkout, git), git
    )


def anchored_on_reflog(checkout: Path, git: Git = run_git) -> bool:
    return _fixed(checkout, git) is None and default_branch(checkout, git) is not None


def committed_manifest(checkout: Path, git: Git = run_git) -> Committed | None:
    ref = default_branch(checkout, git)
    if ref is None:
        return None
    text = git(checkout, ["show", f"{ref}:{MANIFEST_PATH}"])
    if text is not None:
        return Committed(text)
    last = git(checkout, [*_LAST_HELD, ref, "--", MANIFEST_PATH])
    held = git(checkout, ["show", f"{last}:{MANIFEST_PATH}"]) if last else None
    return None if held is None else Committed(held, gone=True)
