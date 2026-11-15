#!/usr/bin/env python3
"""The release check: is a version tag main's commit plus its commit file? ADR 0068.

An install comes only from a tag ``vV``. The tag names a release commit C, which is the commit M
that merged version V on main plus one file, ``.claude-plugin/commit``, holding M's sha. This
script says whether a tag is that, so a moved or hand-made tag is red.

Usage: ``release_check.py <tag> [--remote NAME] [--repo DIR]``. It reads the tag and the default
branch from the remote, ``origin`` unless given, with ``git ls-remote``, so a tag moved there is
seen though the local clone still holds the old one; the commits themselves must be held here, and
a missing one is red with the ``git fetch`` that brings it. ``--repo`` is the repository to judge
and is the current directory unless given. ``--local [--main REF]`` reads the local tag and the
ref ``REF`` (``origin/main`` unless given) instead, and says ``local`` in its first line. Every run
starts with one line saying which it read. Anyone can run it, in the release workflow after the
tag is pushed or later.

Exit 0 when every condition holds. Exit 1 when one fails, with one line for each. Exit 2 for a
call this usage does not describe.

The conditions, each a line of its own when it fails:

* the tag names a commit C that holds ``.claude-plugin/commit`` as a regular file (mode 100644),
  40 lowercase hex digits and an optional newline, naming a commit M the repository holds;
* M is C's only parent;
* C's tree without that file equals M's tree, and both trees can be read;
* M is an ancestor of the main ref;
* the ``version`` in M's ``.claude-plugin/plugin.json`` is the tag without its ``v``;
* M is the commit on the main ref's first-parent line where that version became the version: its
  first parent holds another one. A tag moved to a later commit of the same version is red.

A red output ends with one line on what to do: an agent never moves or deletes a tag that fails
the check, since the ``v*`` ruleset forbids it; the owner is told and the next patch version is
released instead.
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path

import release

COMMIT_FILE = ".claude-plugin/commit"
COMMIT = re.compile(r"[0-9a-f]{40}")
NEXT_STEP = (
    "never move or delete this tag, the v* ruleset forbids it; "
    "tell the owner, and release the next patch version instead"
)


def git(repo: Path, *args: str) -> str | None:
    result = subprocess.run(
        ["git", "-C", str(repo), *args], capture_output=True, text=True, check=False
    )
    return result.stdout if result.returncode == 0 else None


def tree_without_file(repo: Path, commit: str) -> str | None:
    listing = git(repo, "ls-tree", "-r", commit)
    if listing is None:
        return None
    suffix = f"\t{COMMIT_FILE}"
    return "".join(line + "\n" for line in listing.splitlines() if not line.endswith(suffix))


def named_commit(repo: Path, tag: str, release_commit: str) -> tuple[str | None, str | None]:
    entry = git(repo, "ls-tree", release_commit, "--", COMMIT_FILE)
    if not entry:
        return None, f"{tag}: its commit holds no {COMMIT_FILE}"
    if not entry.startswith("100644 blob "):
        return None, f"{tag}: {COMMIT_FILE} is not a regular file (mode {entry.split()[0]})"
    recorded = git(repo, "show", f"{release_commit}:{COMMIT_FILE}") or ""
    text = recorded.removesuffix("\n")
    if not COMMIT.fullmatch(text):
        return None, f"{tag}: {COMMIT_FILE} is not one full lowercase commit sha"
    if git(repo, "cat-file", "-e", f"{text}^{{commit}}") is None:
        return None, f"{tag}: {COMMIT_FILE} names {text}, which is not a commit here"
    return text, None


def findings(repo: Path, tag: str, release_commit: str, *, main: str, label: str) -> list[str]:
    if git(repo, "cat-file", "-e", f"{release_commit}^{{commit}}") is None:
        return [f"{tag}: its commit {release_commit} is not held here; run git fetch and again"]
    if git(repo, "cat-file", "-e", f"{main}^{{commit}}") is None:
        return [f"{label}: its commit {main} is not held here; run git fetch and again"]
    commit, problem = named_commit(repo, tag, release_commit)
    if commit is None:
        return [problem or ""]
    found: list[str] = []
    parents = (git(repo, "rev-list", "--parents", "-n1", release_commit) or "").split()[1:]
    if parents != [commit]:
        found.append(f"{tag}: the release commit's only parent is not {commit}")
    released, base = tree_without_file(repo, release_commit), tree_without_file(repo, commit)
    if released is None or base is None:
        found.append(f"{tag}: the tree of {release_commit} or of {commit} cannot be read")
    elif released != base:
        found.append(f"{tag}: the release commit differs from {commit} by more than {COMMIT_FILE}")
    if git(repo, "merge-base", "--is-ancestor", commit, main) is None:
        found.append(f"{tag}: {commit} is not on {label}")
    version = release.version_in(repo, commit)
    if version is None or f"v{version}" != tag:
        found.append(f"{tag}: the version in {commit}'s {release.MANIFEST} is {version}, not {tag}")
    elif release.bump_commit(repo, main, version) != commit:
        found.append(
            f"{tag}: {commit} is not the commit on {label}'s first-parent line where the version"
            f" became {version}"
        )
    return found


def remote_reads(repo: Path, remote: str, tag: str) -> tuple[str, str, str] | str:
    """The tag's commit, the default branch's commit and its name on ``remote``, or a problem."""
    tags = git(repo, "ls-remote", remote, f"refs/tags/{tag}", f"refs/tags/{tag}^{{}}")
    head = git(repo, "ls-remote", "--symref", remote, "HEAD")
    if tags is None or head is None:
        return f"`git ls-remote {remote}` failed, so its tag and default branch cannot be read"
    named = {name: sha for sha, _, name in (line.partition("\t") for line in tags.splitlines())}
    sha = named.get(f"refs/tags/{tag}^{{}}") or named.get(f"refs/tags/{tag}")
    if sha is None:
        return f"{tag}: no such tag on {remote}"
    branch = next((line for line in head.splitlines() if line.startswith("ref: ")), "")
    tips = (line for line in head.splitlines() if line.endswith("\tHEAD") and line != branch)
    tip = next((line.split("\t")[0] for line in tips), "")
    name = branch.partition("\t")[0].removeprefix("ref: refs/heads/")
    if not branch or not tip:
        return f"{remote} names no default branch"
    return sha, tip, name


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="release_check.py")
    parser.add_argument("tag")
    parser.add_argument("--remote", default="origin")
    parser.add_argument("--local", action="store_true")
    parser.add_argument("--main", default="origin/main")
    parser.add_argument("--repo", type=Path, default=Path())
    options = parser.parse_args(argv[1:])
    tag, repo = options.tag, options.repo
    if options.local:
        print(f"release-check: read {tag} and {options.main} from the local clone (local)")
        local = git(repo, "rev-parse", "--verify", "-q", f"refs/tags/{tag}^{{commit}}")
        tip = git(repo, "rev-parse", "--verify", "-q", f"{options.main}^{{commit}}")
        if local is None:
            found = [f"{tag}: no such tag"]
        elif tip is None:
            found = [f"{options.main}: no such ref"]
        else:
            found = findings(repo, tag, local.strip(), main=tip.strip(), label=options.main)
    else:
        read = remote_reads(repo, options.remote, tag)
        if isinstance(read, str):
            found = [read]
        else:
            sha, tip, name = read
            print(f"release-check: read {tag} and {name} from {options.remote} (remote)")
            found = findings(repo, tag, sha, main=tip, label=f"{options.remote}/{name}")
    for line in found:
        print(f"release-check: {line}")
    if found:
        print(f"release-check: {NEXT_STEP}")
    if not found:
        print(f"release-check: {tag} green")
    return 1 if found else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
