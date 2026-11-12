"""List every changed guard path against the merge base: ``just guard-diff``.

Changed paths are ``git diff --name-only --no-renames <merge base>`` (committed and
uncommitted changes to tracked files; a rename shows as a deletion plus an addition, so
moving a guard file away is caught) plus untracked files. They are matched against the
manifest at the merge base and at the work tree together, so a change that drops a pattern
from the manifest still reports the paths that pattern guarded.

Usage, with the plugin's ``hooks/`` directory on the path: ``PYTHONPATH="$RATCHET_PLUGIN/hooks"
python3 -m ratchet_guards.diff [--root DIR] [--base REF] [--summary FILE] [--annotate]
[--fail-if-present] [--fail-unless-additive]``. The base defaults to the default branch as
``scripts/gatelib.py`` resolves it for every gate (the remote's ``HEAD``, the binding's, else
``main`` or ``master``), so this check and the lowered gate never disagree about "before".
``--summary`` appends a Markdown report (CI passes ``$GITHUB_STEP_SUMMARY``), ``--annotate``
prints GitHub error annotations, ``--fail-if-present`` exits 1 when any guard path changed, and
``--fail-unless-additive`` exits 1 when any guard path changed other than by an addition to a
record; otherwise the exit is 0.

Every changed guard path is listed and counted, dependency files and records included. Under
a manifest, a lockfile or a snapshot of one, an indented line says what the change does to
dependencies (``ratchet_guards.dependencies``): ``dependencies added:`` with each new entry, or
``lockfile:`` with each package's old and new versions, when that is all it does; otherwise
``not dependency-only:`` and why. The pipeline matches the added names against the ones its spec
named (ADR 0011); the owner reads the versions. Under a path in a group marked
``edit = "append"`` the line says what the change adds (``ratchet_guards.records``):
``additions:`` with each new term, ``_Avoid_`` item and decision record, when adding is all it
does; otherwise ``not additive:`` and why (ADR 0014).
"""

import argparse
import subprocess
import sys
from collections.abc import Callable, Sequence
from pathlib import Path

from ratchet_guards import dependencies, records
from ratchet_guards.manifest import MANIFEST_PATH, Group, Manifest, ManifestError, parse

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
import gatelib  # -- the gate library lives beside the gate scripts

Git = Callable[[Sequence[str]], str | None]


def run_git(args: Sequence[str]) -> str | None:
    """``git args`` stdout, or ``None`` when git fails."""
    completed = subprocess.run(  # noqa: S603 # -- a fixed argv, no shell
        ["git", *args],  # noqa: S607 # -- git is resolved from PATH on purpose
        capture_output=True,
        text=True,
        check=False,  # pragma: no mutate # -- a failure returns None either way
    )
    return completed.stdout if completed.returncode == 0 else None


def git_in(root: Path) -> Git:
    """``run_git`` inside ``root``."""
    return lambda args: run_git(["-C", str(root), *args])


def default_base(git: Git, root: Path) -> str:
    """The first default-branch candidate that exists, else the first candidate by name."""
    candidates = gatelib.default_branches(root)
    for ref in candidates:
        if git(["rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}"]):
            return ref
    return candidates[0]


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


def dependency_notes(
    git: Git, base: str, worktree: Path, *, paths: Sequence[str]
) -> dict[str, str]:
    """For each dependency manifest or lockfile in ``paths``, what its change does."""
    merge_base = _required(git, ["merge-base", base, "HEAD"]).strip()
    notes: dict[str, str] = {}
    for path in paths:
        if not dependencies.recognised(path):
            continue
        current = worktree / path
        after = current.read_bytes().decode(errors="replace") if current.is_file() else None
        try:
            found = dependencies.changes(path, git(["show", f"{merge_base}:{path}"]), after)
        except dependencies.NotDependencyOnlyError as reason:
            notes[path] = f"not dependency-only: {reason}"
            continue
        lock = dependencies.file_name(path) in dependencies.LOCKFILES
        heading = "lockfile" if lock else "dependencies added"
        notes[path] = f"{heading}: " + ("; ".join(map(str, found)) or "none")
    return notes


def record_notes(
    git: Git, base: str, worktree: Path, *, matches: Sequence[tuple[str, Group]]
) -> dict[str, str]:
    """For each changed path in a group marked ``edit = "append"``, what its change adds."""
    merge_base = _required(git, ["merge-base", base, "HEAD"]).strip()
    notes: dict[str, str] = {}
    for path, group in matches:
        if not group.additive:
            continue
        current = worktree / path
        after = current.read_bytes().decode(errors="replace") if current.is_file() else None
        notes[path] = records.note(path, records.before(git, merge_base, path), after)
    return notes


def additive(note: str | None) -> bool:
    """Whether a path's note says its change only adds to a record."""
    return note is not None and note.startswith(records.ADDITIONS)


def markdown(
    matches: Sequence[tuple[str, Group]], base: str, notes: dict[str, str] | None = None
) -> str:
    """The job summary."""
    if not matches:
        return f"## Guard paths\n\nNo guard path changed against the merge base with `{base}`.\n"
    notes = notes or {}
    rows = "".join(
        f"| `{_code(path)}` | {group.name} | {_code(notes.get(path, ''))} |\n"
        for path, group in matches
    )
    return (
        f"## :warning: {len(matches)} guard path(s) changed\n\n"
        f"Against the merge base with `{base}`. Guard paths define the quality gates "
        "(guards/manifest.toml). Read each change, and its gate dispute in "
        "docs/disputes/ if an agent raised one, before merging.\n\n"
        f"| Path | Group | What the change does |\n| --- | --- | --- |\n{rows}"
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
    parser.add_argument("--root", type=Path, default=Path())
    parser.add_argument("--base")
    parser.add_argument("--summary", type=Path)
    parser.add_argument("--annotate", action="store_true")
    parser.add_argument("--fail-if-present", action="store_true")
    parser.add_argument("--fail-unless-additive", action="store_true")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None, git: Git | None = None) -> int:
    """Print changed guard paths; see the module docstring for the flags."""
    arguments = _arguments(argv)
    root = Path(arguments.root).resolve()
    git = git or git_in(root)
    base = arguments.base or default_base(git, root)
    worktree = Path(_required(git, ["rev-parse", "--show-toplevel"]).strip())
    found = manifests(git, base, worktree)
    matches = guarded(changed_paths(git, base), found)
    notes = record_notes(git, base, worktree, matches=matches)
    notes |= dependency_notes(git, base, worktree, paths=[path for path, _ in matches])
    for path, group in matches:
        print(f"{group.name}\t{path}")
        if path in notes:
            print(f"  {notes[path]}")
    if not matches:
        print(f"no guard path changed against the merge base with {base}")
    if arguments.annotate:
        for line in annotations(matches):
            print(line)
    if arguments.summary is not None:
        with arguments.summary.open("ab") as summary:
            summary.write(markdown(matches, base, notes).encode())
    if arguments.fail_if_present and matches:
        return 1
    more = [path for path, _ in matches if not additive(notes.get(path))]
    return 1 if arguments.fail_unless_additive and more else 0


if __name__ == "__main__":
    raise SystemExit(main())
