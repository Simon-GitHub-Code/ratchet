import argparse
import subprocess
import sys
from collections.abc import Callable, Sequence
from contextlib import AbstractContextManager
from pathlib import Path

from ratchet_guards import dependencies, records
from ratchet_guards.dependency_entry import Entry
from ratchet_guards.manifest import MANIFEST_PATH, Group, Manifest, ManifestError, parse

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
import gatelib

Git = Callable[[Sequence[str]], str | None]


def run_git(args: Sequence[str]) -> str | None:
    completed = subprocess.run(  # noqa: S603 # -- a fixed argv, no shell
        ["git", *args],  # noqa: S607 # -- git is resolved from PATH on purpose
        capture_output=True,
        text=True,
        check=False,  # pragma: no mutate # -- a failure returns None either way
    )
    return completed.stdout if completed.returncode == 0 else None


def git_in(root: Path) -> Git:
    return lambda args: run_git(["-C", str(root), *args])


def default_base(git: Git, root: Path) -> str:
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
    merge_base = _required(git, ["merge-base", base, "HEAD"]).strip()
    tracked = _required(git, ["diff", "--name-only", "--no-renames", "-z", merge_base])
    untracked = _required(
        git, ["ls-files", "--others", "--exclude-standard", "--full-name", "-z", ":/"]
    )
    return sorted({path for path in (tracked + untracked).split("\0") if path})


def manifests(git: Git, base: str, worktree: Path) -> list[Manifest]:
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
    matches: list[tuple[str, Group]] = []
    for path in paths:
        group = next((g for m in found if (g := m.group_of(path)) is not None), None)
        if group is not None:
            matches.append((path, group))
    return matches


def dependency_notes(
    git: Git,
    base: str,
    worktree: Path,
    *,
    paths: Sequence[str],
    entry: Callable[[Path, str], AbstractContextManager[dependencies.Runner]] = Entry,
) -> dict[str, str]:
    if not paths:
        return {}
    merge_base = _required(git, ["merge-base", base, "HEAD"]).strip()
    contract = dependencies.parse(git(["show", f"{merge_base}:{gatelib.BINDING}"]))
    if contract is None:
        return {}
    changed: dict[str, tuple[str | None, str | None]] = {}
    for path in paths:
        if contract.recognised(path):
            current = worktree / path
            after = current.read_bytes().decode(errors="replace") if current.is_file() else None
            changed[path] = (git(["show", f"{merge_base}:{path}"]), after)
    if not changed:
        return {}
    with entry(worktree, merge_base) as run:
        verdicts = dependencies.judge(contract, changed, run)
    notes: dict[str, str] = {}
    for path, verdict in verdicts.items():
        if isinstance(verdict, dependencies.NotDependencyOnlyError):
            notes[path] = f"not dependency-only: {verdict}"
            continue
        heading = "lockfile" if contract.is_lockfile(path) else "dependencies added"
        notes[path] = f"{heading}: " + ("; ".join(map(str, verdict)) or "none")
    return notes


def record_notes(
    git: Git, base: str, worktree: Path, *, matches: Sequence[tuple[str, Group]]
) -> dict[str, str]:
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
    return note is not None and note.startswith(records.ADDITIONS)


def markdown(
    matches: Sequence[tuple[str, Group]], base: str, notes: dict[str, str] | None = None
) -> str:
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
