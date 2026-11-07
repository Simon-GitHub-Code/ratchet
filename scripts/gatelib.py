"""What the gate scripts share: git, the merge base, the changed paths, the thresholds file.

Every gate that compares head with "before" means the same before: the merge base of ``HEAD``
with the default branch, which is the remote's ``HEAD``, else the branch the binding names under
``- Default branch:``, else ``main`` or ``master``. On the default branch itself, or when ``HEAD``
is the merge base, before is ``HEAD~1``. ``lowered``, ``guard-diff`` and the gates here all call
``base_commit`` so no two of them can disagree about it.
"""

from __future__ import annotations

import re
import subprocess
import tomllib
from pathlib import Path, PurePosixPath

FALLBACK_BRANCHES = ("main", "master")
BINDING = Path("docs/process/binding.md")
THRESHOLDS = Path("quality/thresholds.toml")
DEFAULT_BRANCH_LINE = re.compile(r"^- Default branch: `([^`]+)`$", re.MULTILINE)


def git(root: Path, *args: str) -> str | None:
    """``git -C root args`` stdout, or ``None`` when git fails."""
    result = subprocess.run(
        ["git", "-C", str(root), *args], capture_output=True, text=True, check=False
    )
    return result.stdout.strip() if result.returncode == 0 else None


def default_branches(root: Path) -> list[str]:
    """Candidate default branches, most authoritative first."""
    candidates: list[str] = []
    remote_head = git(root, "symbolic-ref", "--short", "refs/remotes/origin/HEAD")
    if remote_head:
        candidates.append(remote_head)
    binding = root / BINDING
    if binding.is_file():
        match = DEFAULT_BRANCH_LINE.search(binding.read_text(encoding="utf-8"))
        if match:
            candidates.extend((f"origin/{match.group(1)}", match.group(1)))
    for name in FALLBACK_BRANCHES:
        candidates.extend((f"origin/{name}", name))
    return list(dict.fromkeys(candidates))


def base_commit(root: Path, base: str | None = None) -> str | None:
    """The merge base with the default branch, ``HEAD~1`` on that branch, ``None`` when neither."""
    head = git(root, "rev-parse", "--abbrev-ref", "HEAD")
    for branch in [base] if base else default_branches(root):
        merge_base = git(root, "merge-base", "HEAD", branch)
        if merge_base is None:
            continue
        if head in (branch, branch.removeprefix("origin/")):
            return git(root, "rev-parse", "HEAD~1")
        # Why: a fresh branch with uncommitted work has HEAD as its merge base; the work tree
        # is then compared with HEAD, not with the commit before it.
        return merge_base
    return git(root, "rev-parse", "HEAD~1")


def changed_paths(root: Path, base: str) -> list[str]:
    """Paths changed since ``base``, committed or not, untracked files included; renames split."""
    tracked = git(root, "diff", "--name-only", "--no-renames", "-z", base) or ""
    untracked = git(root, "ls-files", "--others", "--exclude-standard", "--full-name", "-z", ":/")
    return sorted({p for p in (tracked + (untracked or "")).split("\0") if p})


def tracked_paths(root: Path, ref: str | None = None) -> list[str]:
    """Every path git tracks at ``ref`` (the work tree when ``None``), untracked files included."""
    if ref is not None:
        listed = git(root, "ls-tree", "-r", "--name-only", "-z", ref) or ""
        return sorted(p for p in listed.split("\0") if p)
    listed = git(root, "ls-files", "-z", "--cached", "--others", "--exclude-standard") or ""
    return sorted({p for p in listed.split("\0") if p and (root / p).is_file()})


def show(root: Path, ref: str, path: str) -> str | None:
    """The file's text at ``ref``, or ``None`` when it is not there."""
    return git(root, "show", f"{ref}:{path}")


def subjects(root: Path, base: str) -> list[str]:
    """The subject line of each non-merge commit after ``base`` up to ``HEAD``."""
    listed = git(root, "log", "--no-merges", "--format=%s", f"{base}..HEAD")
    return [line for line in (listed or "").splitlines() if line]


def read_text(path: Path) -> str | None:
    """The file's text, decoded leniently, or ``None`` when it is missing or binary-looking."""
    try:
        data = path.read_bytes()
    except OSError:
        return None
    if b"\0" in data[:8000]:
        return None
    return data.decode("utf-8", errors="replace")


def thresholds(root: Path) -> dict[str, object]:
    """``quality/thresholds.toml`` parsed, or an empty table when it is missing."""
    path = root / THRESHOLDS
    return tomllib.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}


def table(data: dict[str, object], *keys: str) -> dict[str, object]:
    """The nested table at ``keys``, or an empty one."""
    current: object = data
    for key in keys:
        current = current.get(key, {}) if isinstance(current, dict) else {}
    return current if isinstance(current, dict) else {}


def matches(path: str, patterns: list[str]) -> bool:
    """Whether a repository-relative path matches one of the globs (``**`` spans segments)."""
    return any(PurePosixPath(path).full_match(pattern) for pattern in patterns)


def report(name: str, problems: list[str], against: str | None = None) -> int:
    """Print each problem and the verdict line; the exit code."""
    for line in problems:
        print(line)
    where = f" against {against[:12]}" if against else ""
    print(f"{name}: {'red' if problems else 'green'}{where}")
    return 1 if problems else 0
