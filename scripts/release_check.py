#!/usr/bin/env python3
"""The release check: is a version tag main's commit plus its commit file? ADR 0068.

An install comes only from a tag ``vV``. The tag names a release commit C, which is the commit M
that merged version V on main plus one file, ``.claude-plugin/commit``, holding M's sha. This
script says whether a tag is that, so a moved or hand-made tag is red.

Usage: ``release_check.py <tag> [--main REF] [--repo DIR]``. ``--main`` names the default branch
as a ref and is ``origin/main`` unless given; ``--repo`` is the repository to judge and is the
current directory unless given. Anyone can run it, in the release workflow after the tag is
pushed or later.

Exit 0 when every condition holds. Exit 1 when one fails, with one line for each. Exit 2 for a
call this usage does not describe.

The conditions, each a line of its own when it fails:

* the tag names a commit C that holds ``.claude-plugin/commit``, 40 lowercase hex digits and an
  optional newline, naming a commit M the repository holds;
* M is C's only parent;
* C's tree without that file equals M's tree;
* M is an ancestor of the main ref;
* the ``version`` in M's ``.claude-plugin/plugin.json`` is the tag without its ``v``.

A red output ends with one line on what to do: an agent never moves or deletes a tag that fails
the check, since the ``v*`` ruleset forbids it; the owner is told and the next patch version is
released instead.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

COMMIT_FILE = ".claude-plugin/commit"
MANIFEST = ".claude-plugin/plugin.json"
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


def tree_without_file(repo: Path, commit: str) -> str:
    listing = git(repo, "ls-tree", "-r", commit) or ""
    suffix = f"\t{COMMIT_FILE}"
    return "".join(line + "\n" for line in listing.splitlines() if not line.endswith(suffix))


def named_commit(repo: Path, tag: str) -> tuple[str | None, str | None]:
    release = git(repo, "rev-parse", "--verify", "-q", f"refs/tags/{tag}^{{commit}}")
    if release is None:
        return None, f"{tag}: no such tag"
    recorded = git(repo, "show", f"{release.strip()}:{COMMIT_FILE}")
    if recorded is None:
        return None, f"{tag}: its commit holds no {COMMIT_FILE}"
    text = recorded.removesuffix("\n")
    if not COMMIT.fullmatch(text):
        return None, f"{tag}: {COMMIT_FILE} is not one full lowercase commit sha"
    if git(repo, "cat-file", "-e", f"{text}^{{commit}}") is None:
        return None, f"{tag}: {COMMIT_FILE} names {text}, which is not a commit here"
    return text, None


def findings(repo: Path, tag: str, main: str) -> list[str]:
    commit, problem = named_commit(repo, tag)
    if commit is None:
        return [problem or ""]
    release = (git(repo, "rev-parse", f"refs/tags/{tag}^{{commit}}") or "").strip()
    found: list[str] = []
    parents = (git(repo, "rev-list", "--parents", "-n1", release) or "").split()[1:]
    if parents != [commit]:
        found.append(f"{tag}: the release commit's only parent is not {commit}")
    if tree_without_file(repo, release) != tree_without_file(repo, commit):
        found.append(f"{tag}: the release commit differs from {commit} by more than {COMMIT_FILE}")
    if git(repo, "merge-base", "--is-ancestor", commit, main) is None:
        found.append(f"{tag}: {commit} is not on {main}")
    try:
        manifest = json.loads(git(repo, "show", f"{commit}:{MANIFEST}") or "")
    except ValueError:
        manifest = None
    version = manifest.get("version") if isinstance(manifest, dict) else None
    if f"v{version}" != tag:
        found.append(f"{tag}: the version in {commit}'s {MANIFEST} is {version}, not {tag}")
    return found


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="release_check.py")
    parser.add_argument("tag")
    parser.add_argument("--main", default="origin/main")
    parser.add_argument("--repo", type=Path, default=Path())
    options = parser.parse_args(argv[1:])
    found = findings(options.repo, options.tag, options.main)
    for line in found:
        print(f"release-check: {line}")
    if found:
        print(f"release-check: {NEXT_STEP}")
    if not found:
        print(f"release-check: {options.tag} green")
    return 1 if found else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
