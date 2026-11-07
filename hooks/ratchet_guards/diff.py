"""List every changed guard path against the merge base: ``just guard-diff``.

Changed paths are ``git diff --name-only --no-renames <merge base>`` (committed and
uncommitted changes to tracked files; a rename shows as a deletion plus an addition, so
moving a guard file away is caught) plus untracked files. They are matched against the
manifest at the merge base and at the work tree together, so a change that drops a pattern
from the manifest still reports the paths that pattern guarded.

Usage, from inside the repository, with the plugin's ``hooks/`` directory on the path:
``PYTHONPATH="$CLAUDE_PLUGIN_ROOT/hooks" python3 -m ratchet_guards.diff [--base REF]
[--summary FILE] [--annotate] [--fail-if-present]``. ``--summary`` appends a Markdown report
(CI passes ``$GITHUB_STEP_SUMMARY``), ``--annotate`` prints GitHub error annotations, and
``--fail-if-present`` exits 1 when any guard path changed; otherwise the exit is 0.
"""

import argparse
import subprocess
from collections.abc import Callable, Sequence
from pathlib import Path

from ratchet_guards.manifest import MANIFEST_PATH, Group, Manifest, ManifestError, parse

Git = Callable[[Sequence[str]], str | None]
DEFAULT_BASE = "origin/main"


def run_git(args: Sequence[str]) -> str | None:
    """``git args`` stdout, or ``None`` when git fails."""
    completed = subprocess.run(  # noqa: S603 # -- a fixed argv, no shell
        ["git", *args],  # noqa: S607 # -- git is resolved from PATH on purpose
        capture_output=True,
        text=True,
        check=False,  # pragma: no mutate # -- a failure returns None either way
    )
    return completed.stdout if completed.returncode == 0 else None


def _required(git: Git, args: Sequence[str]) -> str:
    output = git(args)
    if output is None:
        msg = f"git {' '.join(args)} failed"
        raise RuntimeError(msg)
    return output


def changed_paths(git: Git, base: str) -> list[str]:
    """Paths changed since the merge base of ``base`` and ``HEAD``, untracked files included."""
    merge_base = _required(git, ["merge-base", base, "HEAD"]).strip()
    tracked = _required(git, ["diff", "--name-only", "--no-renames", "-z", merge_base])
    untracked = _required(
        git, ["ls-files", "--others", "--exclude-standard", "--full-name", "-z", ":/"]
    )
    return sorted({path for path in (tracked + untracked).split("\0") if path})


def manifests(git: Git, base: str, worktree: Path) -> list[Manifest]:
    """The manifest at the merge base (if it has one) and in the work tree (if it parses)."""
    found: list[Manifest] = []
    merge_base = _required(git, ["merge-base", base, "HEAD"]).strip()
    sources = [git(["show", f"{merge_base}:{MANIFEST_PATH}"])]
    path = worktree / MANIFEST_PATH
    sources.append(path.read_bytes().decode() if path.is_file() else None)
    for text in sources:
        if text is None:
            continue
        try:
            found.append(parse(text))
        except ManifestError:
            continue
    return found


def guarded(paths: Sequence[str], found: Sequence[Manifest]) -> list[tuple[str, Group]]:
    """Each changed path that any manifest guards, with the first group that does."""
    matches: list[tuple[str, Group]] = []
    for path in paths:
        group = next((g for m in found if (g := m.group_of(path)) is not None), None)
        if group is not None:
            matches.append((path, group))
    return matches


def markdown(matches: Sequence[tuple[str, Group]], base: str) -> str:
    """The job summary."""
    if not matches:
        return f"## Guard paths\n\nNo guard path changed against the merge base with `{base}`.\n"
    rows = "".join(f"| `{_code(path)}` | {group.name} |\n" for path, group in matches)
    return (
        f"## :warning: {len(matches)} guard path(s) changed\n\n"
        f"Against the merge base with `{base}`. Guard paths define the quality gates "
        "(guards/manifest.toml). Read each change, and its gate dispute in "
        "docs/disputes/ if an agent raised one, before merging.\n\n"
        f"| Path | Group |\n| --- | --- |\n{rows}"
    )


def _code(path: str) -> str:
    return path.replace("`", "'").replace("|", "\\|")


def _escape(text: str) -> str:
    return text.replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")


def annotations(matches: Sequence[tuple[str, Group]]) -> list[str]:
    """GitHub workflow commands that flag each changed guard path."""
    return [
        f"::error title=Guard path changed ({group.name})::{_escape(path)}"
        for path, group in matches
    ]


def _arguments(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="List changed guard paths.")
    parser.add_argument("--base", default=DEFAULT_BASE)
    parser.add_argument("--summary", type=Path)
    parser.add_argument("--annotate", action="store_true")
    parser.add_argument("--fail-if-present", action="store_true")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None, git: Git = run_git) -> int:
    """Print changed guard paths; see the module docstring for the flags."""
    arguments = _arguments(argv)
    worktree = Path(_required(git, ["rev-parse", "--show-toplevel"]).strip())
    found = manifests(git, arguments.base, worktree)
    matches = guarded(changed_paths(git, arguments.base), found)
    for path, group in matches:
        print(f"{group.name}\t{path}")
    if not matches:
        print(f"no guard path changed against the merge base with {arguments.base}")
    if arguments.annotate:
        for line in annotations(matches):
            print(line)
    if arguments.summary is not None:
        with arguments.summary.open("ab") as summary:
            summary.write(markdown(matches, arguments.base).encode())
    return 1 if arguments.fail_if_present and matches else 0


if __name__ == "__main__":
    raise SystemExit(main())
