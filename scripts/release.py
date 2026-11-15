#!/usr/bin/env python3
"""The release: the tag ``vV`` on M plus its commit file, made once per version. ADR 0068.

The release workflow runs this on a push to main that changed ``.claude-plugin/plugin.json``.
Run from a checkout whose ``HEAD`` is M, it reads the version V from M's manifest. When the tag
``vV`` exists it says so and does nothing. Otherwise it makes the release commit C, which is M
plus ``.claude-plugin/commit`` holding M's full sha and a newline, with M as its only parent,
and tags C as a lightweight tag ``vV``. Author and committer come from the environment, as for
any ``git commit``. It never pushes; the workflow pushes the tag and then runs
``scripts/release_check.py``.

Usage: ``release.py [--repo DIR]``. It prints ``created vV C`` when it made a tag and
``exists vV`` when it did nothing, and exits 1 with a line on stderr when it cannot go on.

``release.py --notes V [--repo DIR]`` prints the ``## V`` section of ``CHANGELOG.md`` in the
checkout, from the line after the heading to the next ``## `` heading, with the blank lines at
its ends dropped; a version with no section is exit 1.

Cutting a release:

* The version PR bumps the version in ``.claude-plugin/plugin.json`` and writes the CHANGELOG
  ``## V`` section.
* In the same PR it switches the ratchet entry of ``.claude-plugin/marketplace.json`` to
  ``{"source": "github", "repo": "Simon-GitHub-Code/ratchet", "ref": "vV"}`` and removes the
  strict ``xfail`` mark on the marketplace-entry test.
* Merging it to main runs ``.github/workflows/release.yml``, which makes and pushes the tag, runs
  ``scripts/release_check.py`` and publishes the release page.
* A person's token merges the version PR, not a workflow's ``GITHUB_TOKEN``: GitHub starts no
  workflow from a push made with that token, so ``release.yml`` would not run.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import NoReturn

COMMIT_FILE = ".claude-plugin/commit"
MANIFEST = ".claude-plugin/plugin.json"
CHANGELOG = "CHANGELOG.md"


class Stop(Exception):  # noqa: N818 # -- a stop is an answer, not a fault
    """A reason the release cannot go on, said on stderr."""


def stop(reason: str) -> NoReturn:
    raise Stop(reason)


def git(repo: Path, *args: str, stdin: str | None = None, env: dict[str, str] | None = None) -> str:
    result = subprocess.run(
        ["git", "-C", str(repo), *args],
        input=stdin,
        capture_output=True,
        text=True,
        check=False,
        env=env,
    )
    if result.returncode:
        stop(f"git {args[0]} failed: {result.stderr.strip()}")
    return result.stdout.strip()


def version_at_head(repo: Path) -> str:
    try:
        manifest = json.loads(git(repo, "show", f"HEAD:{MANIFEST}"))
    except ValueError:
        stop(f"{MANIFEST} at HEAD is not JSON")
    version = manifest.get("version") if isinstance(manifest, dict) else None
    if not isinstance(version, str):
        stop(f"{MANIFEST} at HEAD has no version")
    return version


def release_commit(repo: Path, version: str) -> str:
    base = git(repo, "rev-parse", "HEAD")
    with tempfile.TemporaryDirectory() as scratch:
        env = {**os.environ, "GIT_INDEX_FILE": str(Path(scratch) / "index")}
        git(repo, "read-tree", base, env=env)
        blob = git(repo, "hash-object", "-w", "--stdin", stdin=f"{base}\n")
        git(repo, "update-index", "--add", "--cacheinfo", f"100644,{blob},{COMMIT_FILE}", env=env)
        tree = git(repo, "write-tree", env=env)
    return git(repo, "commit-tree", tree, "-p", base, "-m", f"release: v{version}")


def release(repo: Path) -> str:
    version = version_at_head(repo)
    tag = f"v{version}"
    existing = subprocess.run(
        ["git", "-C", str(repo), "rev-parse", "-q", "--verify", f"refs/tags/{tag}"],
        capture_output=True,
        check=False,
    )
    if existing.returncode == 0:
        return f"exists {tag}"
    commit = release_commit(repo, version)
    git(repo, "tag", tag, commit)
    return f"created {tag} {commit}"


def notes(repo: Path, version: str) -> str:
    lines = (repo / CHANGELOG).read_text(encoding="utf-8").splitlines()
    heading = f"## {version}"
    if heading not in lines:
        stop(f"{CHANGELOG} has no section {heading}")
    body: list[str] = []
    for line in lines[lines.index(heading) + 1 :]:
        if line.startswith("## "):
            break
        body.append(line)
    return "\n".join(body).strip()


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="release.py")
    parser.add_argument("--repo", type=Path, default=Path())
    parser.add_argument("--notes", metavar="V")
    options = parser.parse_args(argv[1:])
    try:
        print(notes(options.repo, options.notes) if options.notes else release(options.repo))
    except Stop as stop:
        print(f"release: {stop}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
