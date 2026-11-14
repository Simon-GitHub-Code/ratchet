#!/usr/bin/env python3
"""The pins gate, the tool-neutral half: actions by commit, images by digest, permissions set.

Every ``uses:`` in a workflow names a commit SHA with the version in a trailing comment; every
``image:`` of a workflow and every action run from an image (a ``uses:`` with a URL scheme) names
a digest; every workflow sets a top-level ``permissions:`` block. The stack-specific half (the
lockfile matches the manifest, no floating range) is bound beside this script in the ``pins``
entry point.

The files of the project's own that name an image to build from or run are the stack's, not the
plugin's (ADR 0050). The binding's ``## Image pins`` section lists them, one row each::

    | Files | Reference |
    | --- | --- |
    | `<glob>, <glob>` | `<regex>` |

``Files`` are globs over tracked files. ``Reference`` is a regular expression (Python's ``re``)
that each line of those files is searched with: group 1 is the image reference, which must name a
digest (``@<algorithm>:<hex>``); an optional group 2 is a name the line gives to a stage that later
lines may build from, and a reference to one is no image. A reference that starts with ``$`` is a
variable the gate cannot read and is left alone. A row that says ``none: <reason>`` in ``Files``
says the project names no image in a file of its own. The merge base's section judges a change, so
a change cannot narrow what it checks. Until the section exists no such file is read, and the gate
says so; conformance names the missing section.

Usage: ``pins.py [--root DIR]``.
"""

from __future__ import annotations

import argparse
import re
import sys
from dataclasses import dataclass
from pathlib import Path

import gatelib
import markdown_text

SECTION = "## Image pins"
COLUMNS = ("Files", "Reference")
NONE_PREFIX = "none:"
MIN_REASON = 10
WORKFLOWS = ".github/workflows/"
SHA = re.compile(r"@[0-9a-f]{40}\b")
USES = re.compile(r"^\s*-?\s*uses:\s*[\"']?([^\s\"'#]+)")
IMAGE = re.compile(r"^\s*(?:-\s*)?image:\s*[\"']?([^\s\"'#]+)")
VERSION_COMMENT = re.compile(r"#\s*v?\d")
DIGEST = re.compile(r"@[a-z0-9]+(?:[+.-][a-z0-9]+)*:[0-9a-f]+", re.IGNORECASE)


def is_workflow(path: str) -> bool:
    return path.startswith(WORKFLOWS) and path.endswith((".yml", ".yaml"))


def check_workflow(path: str, text: str) -> list[str]:
    problems = []
    if not re.search(r"^permissions:", text, re.MULTILINE):
        problems.append(f"{path}: no top-level permissions block")
    for number, line in enumerate(text.splitlines(), 1):
        match = USES.match(line)
        if match is None or match.group(1).startswith("./"):
            continue
        action = match.group(1)
        if "://" in action:
            if not DIGEST.search(action):
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
        if (match := IMAGE.match(line)) and not DIGEST.search(match.group(1))
    ]


@dataclass(frozen=True)
class Rule:
    """One row of the image pins table: the files, and how a line of them names an image."""

    files: tuple[str, ...]
    reference: re.Pattern[str]


def parse(binding: str | None) -> tuple[list[Rule] | None, list[str]]:
    """The rules of the binding's image pins section and what is wrong with it.

    The rules are ``None`` when the section is missing or has a problem, and ``[]`` when it says
    ``none``.
    """
    if binding is None or not markdown_text.has_section(binding, SECTION):
        return None, [f"has no '{SECTION}' section"]
    found = [t for t in markdown_text.tables(binding, SECTION) if t.header == COLUMNS]
    if not found:
        return None, [f"'{SECTION}' lacks its table with the columns {' | '.join(COLUMNS)}"]
    rules: list[Rule] = []
    problems: list[str] = []
    for files_cell, reference_cell in found[0].rows:
        plain = files_cell.strip("` ")
        if files_cell.startswith("<") or reference_cell.startswith("<"):
            problems.append("the image pins table still has the template's placeholder row")
        elif plain.lower().startswith(NONE_PREFIX):
            if len(plain[len(NONE_PREFIX) :].strip()) < MIN_REASON:
                problems.append("an image pins row says none with no reason: say why")
        elif rule := _rule(files_cell, reference_cell, problems):
            rules.append(rule)
    return (None if problems else rules), problems


def _rule(files_cell: str, reference_cell: str, problems: list[str]) -> Rule | None:
    listed = [glob for cell in markdown_text.ticked(files_cell) for glob in cell.split(",")]
    globs = tuple(glob.strip() for glob in listed if glob.strip())
    ticked = markdown_text.ticked(reference_cell)
    if not globs or len(ticked) != 1:
        problems.append(f"the image pins row '{files_cell}' needs backticked globs and one regex")
        return None
    try:
        reference = re.compile(ticked[0].replace("\\|", "|"))
    except re.error as error:
        problems.append(
            f"the image pins row '{files_cell}' has a Reference that is no regex: {error}"
        )
        return None
    if reference.groups < 1:
        problems.append(f"the image pins row '{files_cell}' has a Reference with no group 1")
        return None
    return Rule(globs, reference)


def rules_in_force(root: Path) -> list[Rule] | None:
    """The rules of the merge base's binding, else the work tree's; ``None`` when unsaid."""
    base = gatelib.base_commit(root)
    for text in (
        gatelib.show(root, base, str(gatelib.BINDING)) if base else None,
        gatelib.read_text(root / gatelib.BINDING),
    ):
        rules, _ = parse(text)
        if rules is not None:
            return rules
    return None


def check_references(path: str, text: str, rule: Rule) -> list[str]:
    stages: set[str] = set()
    problems = []
    for number, line in enumerate(text.splitlines(), 1):
        match = rule.reference.search(line)
        if match is None:
            continue
        image = match.group(1)
        alias = match.group(2) if rule.reference.groups > 1 else None
        if alias:
            stages.add(alias.lower())
        if image.lower() in stages or image.startswith("$"):
            continue
        if not DIGEST.search(image):
            problems.append(f"{path}:{number}: image {image} is not pinned by digest")
    return problems


def problems(root: Path) -> list[str]:
    found: list[str] = []
    rules = rules_in_force(root)
    if rules is None:
        print(f"pins: no image file is read: the binding has no filled '{SECTION}' section")
    for path in gatelib.tracked_paths(root):
        text = gatelib.read_text(root / path)
        if text is None:
            continue
        if is_workflow(path):
            found += check_workflow(path, text) + check_images(path, text)
        for rule in rules or []:
            if gatelib.matches(path, list(rule.files)):
                found += check_references(path, text, rule)
    return found


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", default=".")
    options = parser.parse_args(argv[1:])
    return gatelib.report("pins", problems(Path(options.root).resolve()))


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
