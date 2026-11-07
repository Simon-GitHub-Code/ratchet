#!/usr/bin/env python3
"""The pins gate, the tool-neutral half: actions by commit, images by digest, permissions set.

Every ``uses:`` in a workflow names a commit SHA with the version in a trailing comment; every
``image:`` in a workflow or compose file and every ``FROM`` in a Dockerfile names a digest; every
workflow sets a top-level ``permissions:`` block. The stack-specific half (the lockfile matches
the manifest, no floating range) is bound beside this script in the ``pins`` entry point.

Usage: ``pins.py [--root DIR]``.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

import gatelib

WORKFLOWS = ".github/workflows/"
SHA = re.compile(r"@[0-9a-f]{40}\b")
USES = re.compile(r"^\s*-?\s*uses:\s*[\"']?([^\s\"'#]+)")
IMAGE = re.compile(r"^\s*(?:-\s*)?image:\s*[\"']?([^\s\"'#]+)")
FROM = re.compile(r"^\s*FROM\s+(?:--platform=\S+\s+)?(\S+)(?:\s+AS\s+(\S+))?", re.IGNORECASE)
VERSION_COMMENT = re.compile(r"#\s*v?\d")
DIGEST = "@sha256:"


def is_workflow(path: str) -> bool:
    return path.startswith(WORKFLOWS) and path.endswith((".yml", ".yaml"))


def is_compose(path: str) -> bool:
    name = path.rsplit("/", 1)[-1]
    return name.startswith(("compose", "docker-compose")) and name.endswith((".yml", ".yaml"))


def is_dockerfile(path: str) -> bool:
    name = path.rsplit("/", 1)[-1]
    return name == "Dockerfile" or name.startswith("Dockerfile.") or name.endswith(".Dockerfile")


def check_workflow(path: str, text: str) -> list[str]:
    problems = []
    if not re.search(r"^permissions:", text, re.MULTILINE):
        problems.append(f"{path}: no top-level permissions block")
    for number, line in enumerate(text.splitlines(), 1):
        match = USES.match(line)
        if match is None or match.group(1).startswith("./"):
            continue
        action = match.group(1)
        if action.startswith("docker://"):
            if DIGEST not in action:
                problems.append(f"{path}:{number}: image {action} is not pinned by digest")
        elif not SHA.search(action):
            problems.append(f"{path}:{number}: action {action} is not pinned to a commit")
        elif not VERSION_COMMENT.search(line):
            problems.append(f"{path}:{number}: pinned action lacks a version comment")
    return problems


def check_images(path: str, text: str) -> list[str]:
    return [
        f"{path}:{number}: image {match.group(1)} is not pinned by digest"
        for number, line in enumerate(text.splitlines(), 1)
        if (match := IMAGE.match(line)) and DIGEST not in match.group(1)
    ]


def check_dockerfile(path: str, text: str) -> list[str]:
    problems: list[str] = []
    stages: set[str] = set()
    for number, line in enumerate(text.splitlines(), 1):
        match = FROM.match(line)
        if match is None:
            continue
        image, alias = match.group(1), match.group(2)
        if alias:
            stages.add(alias.lower())
        if image.lower() in stages or image.lower() == "scratch" or image.startswith("$"):
            continue
        if DIGEST not in image:
            problems.append(f"{path}:{number}: FROM {image} is not pinned by digest")
    return problems


def problems(root: Path) -> list[str]:
    found: list[str] = []
    for path in gatelib.tracked_paths(root):
        text = gatelib.read_text(root / path)
        if text is None:
            continue
        if is_workflow(path):
            found += check_workflow(path, text) + check_images(path, text)
        elif is_compose(path):
            found += check_images(path, text)
        elif is_dockerfile(path):
            found += check_dockerfile(path, text)
    return found


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", default=".")
    options = parser.parse_args(argv[1:])
    return gatelib.report("pins", problems(Path(options.root).resolve()))


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
