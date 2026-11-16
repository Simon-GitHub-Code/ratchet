#!/usr/bin/env python3
"""The release: the tag ``vV`` on M plus its commit file, made once per version. ADR 0068.

The release workflow runs this on a push to main that changed ``.claude-plugin/plugin.json``.
Run from a checkout of main at any commit, it reads the version V from ``HEAD``'s manifest. When
the tag ``vV`` exists it says so and does nothing. Otherwise it needs the ``## V`` section of
``CHANGELOG.md`` to be there with a body, and finds M, the commit on ``HEAD``'s first-parent line
where the version became V: M holds V and its first parent holds another version. A late or
dropped workflow run therefore tags the same commit as an on-time one. It makes the release commit
C, which is M plus ``.claude-plugin/commit`` holding M's full sha and a newline, with M as its
only parent, and tags C as a lightweight tag ``vV``. Author and committer come from the
environment, as for any ``git commit``. It never pushes; the workflow pushes the tag and then runs
``scripts/release_check.py``.

A version that is not above every tagged version is refused, with one line naming both: a revert
of the manifest never makes a release, and the first commit where a version became V is its M.

Usage: ``release.py [--repo DIR] [--existing]``. It prints ``created vV C`` when it made a tag and
``exists vV`` when it did nothing, and exits 1 with a line on stderr when it cannot go on. With
``--existing`` it never makes a tag: a version with no tag is exit 1.

``release.py --notes V [--repo DIR]`` prints the ``## V`` section of ``CHANGELOG.md`` in the
checkout, from the line after the heading to the next ``## `` heading, with the blank lines at
its ends dropped; a version with no section, or an empty one, is exit 1.

Cutting a release:

* The version PR bumps the version in ``.claude-plugin/plugin.json`` and writes the CHANGELOG
  ``## V`` section.
* In the same PR it switches the ratchet entry of ``.claude-plugin/marketplace.json`` to
  ``{"source": "github", "repo": "Simon-GitHub-Code/ratchet", "ref": "vV"}`` and removes the
  strict ``xfail`` mark on the marketplace-entry test.
* Merging it to main runs ``.github/workflows/release.yml``, which makes and pushes the tag, runs
  ``scripts/release_check.py`` and publishes the release page. When the tag already exists, the
  run checks it again and publishes the page only if it is missing. A manual run of the workflow
  does that alone and never makes or moves a tag, so it mends a page step that failed.
* A person's token merges the version PR, not a workflow's ``GITHUB_TOKEN``: GitHub starts no
  workflow from a push made with that token, so ``release.yml`` would not run.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import NoReturn

COMMIT_FILE = ".claude-plugin/commit"
MANIFEST = ".claude-plugin/plugin.json"
CHANGELOG = "CHANGELOG.md"
DOTTED = re.compile(r"\d+(?:\.\d+)*")


class Stop(Exception):  # noqa: N818 # -- a stop is an answer, not a fault
    """A reason the release cannot go on, said on stderr."""


def stop(reason: str) -> NoReturn:
    raise Stop(reason)


def try_git(
    repo: Path, *args: str, stdin: str | None = None, env: dict[str, str] | None = None
) -> str | None:
    result = subprocess.run(
        ["git", "-C", str(repo), *args],
        input=stdin,
        capture_output=True,
        text=True,
        check=False,
        env=env,
    )
    return result.stdout.strip() if result.returncode == 0 else None


def git(repo: Path, *args: str, stdin: str | None = None, env: dict[str, str] | None = None) -> str:
    out = try_git(repo, *args, stdin=stdin, env=env)
    if out is None:
        stop(f"git {args[0]} failed")
    return out


def version_in(repo: Path, commit: str) -> str | None:
    """The ``version`` of the manifest ``commit`` holds; ``None`` when it holds none."""
    try:
        manifest = json.loads(try_git(repo, "show", f"{commit}:{MANIFEST}") or "")
    except ValueError:
        return None
    version = manifest.get("version") if isinstance(manifest, dict) else None
    return version if isinstance(version, str) else None


def bump_commit(repo: Path, ref: str, version: str) -> str | None:
    """The first commit on ``ref``'s first-parent line where the version became ``version``.

    The commits that changed the manifest, oldest first, are each a change of the version or a
    touch of the file, so the first that holds ``version`` is where it became that. ``None`` when
    no commit on the line holds it.
    """
    listed = try_git(repo, "log", "--first-parent", "--format=%H", ref, "--", MANIFEST) or ""
    for commit in reversed(listed.splitlines()):
        if version_in(repo, commit) == version:
            return commit
    return None


def version_at_head(repo: Path) -> str:
    version = version_in(repo, "HEAD")
    if version is None:
        stop(f"{MANIFEST} at HEAD is missing, not JSON or has no version")
    return version


def release_commit(repo: Path, base: str, version: str) -> str:
    with tempfile.TemporaryDirectory() as scratch:
        env = {**os.environ, "GIT_INDEX_FILE": str(Path(scratch) / "index")}
        git(repo, "read-tree", base, env=env)
        blob = git(repo, "hash-object", "-w", "--stdin", stdin=f"{base}\n")
        git(repo, "update-index", "--add", "--cacheinfo", f"100644,{blob},{COMMIT_FILE}", env=env)
        tree = git(repo, "write-tree", env=env)
    return git(repo, "commit-tree", tree, "-p", base, "-m", f"release: v{version}")


def dotted(version: str) -> tuple[int, ...] | None:
    return tuple(int(part) for part in version.split(".")) if DOTTED.fullmatch(version) else None


def refuse_unless_above_every_tag(repo: Path, version: str) -> None:
    here = dotted(version)
    if here is None:
        stop(f"the version {version} is not dotted numbers")
    tagged = [
        (found, name)
        for name in (try_git(repo, "tag", "--list", "v*") or "").splitlines()
        if (found := dotted(name.removeprefix("v"))) is not None
    ]
    highest = max(tagged, default=None)
    if highest is not None and highest[0] > here:
        stop(f"the version {version} is not above the tagged {highest[1]}; versions only go up")


def release(repo: Path, *, create: bool = True) -> str:
    version = version_at_head(repo)
    tag = f"v{version}"
    refuse_unless_above_every_tag(repo, version)
    if try_git(repo, "rev-parse", "-q", "--verify", f"refs/tags/{tag}") is not None:
        return f"exists {tag}"
    if not create:
        stop(f"{tag} has no tag, and a manual run never makes one")
    notes(repo, version)
    base = bump_commit(repo, "HEAD", version)
    if base is None:
        stop(f"no commit on HEAD's first-parent line is where the version became {version}")
    commit = release_commit(repo, base, version)
    git(repo, "tag", tag, commit)
    return f"created {tag} {commit}"


def notes(repo: Path, version: str) -> str:
    try:
        lines = (repo / CHANGELOG).read_text(encoding="utf-8").splitlines()
    except OSError:
        stop(f"{CHANGELOG} cannot be read")
    heading = f"## {version}"
    if heading not in lines:
        stop(f"{CHANGELOG} has no section {heading}")
    body: list[str] = []
    for line in lines[lines.index(heading) + 1 :]:
        if line.startswith("## "):
            break
        body.append(line)
    text = "\n".join(body).strip()
    if not text:
        stop(f"{CHANGELOG} section {heading} is empty")
    return text


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="release.py")
    parser.add_argument("--repo", type=Path, default=Path())
    parser.add_argument("--notes", metavar="V")
    parser.add_argument("--existing", action="store_true")
    options = parser.parse_args(argv[1:])
    try:
        print(
            notes(options.repo, options.notes)
            if options.notes
            else release(options.repo, create=not options.existing)
        )
    except Stop as stop:
        print(f"release: {stop}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
