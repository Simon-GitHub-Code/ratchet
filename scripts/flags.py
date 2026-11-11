#!/usr/bin/env python3
"""The flags gate: every feature flag is declared once, owned, off when new, and due for removal.

``flags.toml`` at the repository root is the registry. One ``[[flag]]`` table per flag::

    [[flag]]
    key = "new-checkout"
    owner = "payments"
    default = false
    remove-by = 2027-02-01
    reason = "Ships the new checkout dark until the load gate has seen it."

A flag that is meant to stay (a kill switch) says ``permanent = true`` instead of ``remove-by``.
An empty file declares no flags; a missing one is red, since a repository on the method always
has the registry. A flag the merge base lacks must default to ``false``: new behaviour ships off.
``--used-in DIR`` (repeatable) also fails a declared key that no file under those directories
names, so a flag whose code is gone leaves the registry with it.

``--expired`` is the other question, asked on the default branch and nightly rather than on a
pull request, since it goes red by the calendar: a flag past its ``remove-by`` date.

``--changed`` judges nothing: it prints the key of every flag the change adds or edits, one per
line, for the ``flag-states`` gate to run the suite with each of them off and on.

Usage: ``flags.py [--root DIR] [--base REF] [--used-in DIR]... [--expired [--today DATE]]
[--changed]``.
"""

from __future__ import annotations

import argparse
import datetime as dt
import re
import sys
import tomllib
from pathlib import Path

import gatelib

REGISTRY = "flags.toml"
KEY = re.compile(r"[a-z][a-z0-9]*(?:[._-][a-z0-9]+)*")
MIN_REASON = 20
Flag = dict[str, object]


def parse(text: str) -> tuple[list[Flag], list[str]]:
    """The registry's flag tables and what is wrong with its shape."""
    try:
        data = tomllib.loads(text)
    except tomllib.TOMLDecodeError as error:
        return [], [f"{REGISTRY}: does not parse: {error}"]
    entries = data.get("flag", [])
    if not isinstance(entries, list):
        return [], [f"{REGISTRY}: 'flag' is not an array of tables; write [[flag]]"]
    return [entry for entry in entries if isinstance(entry, dict)], []


def name(flag: Flag) -> str:
    return str(flag.get("key", "<no key>"))


def shape(flag: Flag) -> list[str]:
    """What one flag table lacks."""
    problems = []
    key = flag.get("key")
    if not isinstance(key, str) or not KEY.fullmatch(key):
        problems.append("key is missing or is not lower-case words joined by . _ or -")
    owner = flag.get("owner")
    if not isinstance(owner, str) or not owner.strip():
        problems.append("owner is missing")
    if not isinstance(flag.get("default"), bool):
        problems.append("default is missing or is not true or false")
    reason = flag.get("reason")
    if not isinstance(reason, str) or len(reason.strip()) < MIN_REASON:
        problems.append(f"reason is missing or under {MIN_REASON} characters")
    dated = isinstance(flag.get("remove-by"), dt.date)
    if dated == (flag.get("permanent") is True):
        problems.append("needs exactly one of remove-by = <date> and permanent = true")
    return [f"{REGISTRY}: flag '{name(flag)}': {problem}" for problem in problems]


def duplicates(flags: list[Flag]) -> list[str]:
    keys = [name(flag) for flag in flags]
    return [
        f"{REGISTRY}: flag '{key}': declared {keys.count(key)} times"
        for key in sorted(set(keys))
        if keys.count(key) > 1
    ]


def born_on(flags: list[Flag], before: list[Flag]) -> list[str]:
    """One line per flag the merge base lacks that does not default to off."""
    known = {name(flag) for flag in before}
    return [
        f"{REGISTRY}: flag '{name(flag)}': a new flag defaults to false, so the change ships off"
        for flag in flags
        if name(flag) not in known and flag.get("default") is True
    ]


def changed(flags: list[Flag], before: list[Flag]) -> list[str]:
    """The key of every flag whose table the merge base lacks or holds differently."""
    return sorted(name(flag) for flag in flags if flag not in before)


def unused(root: Path, flags: list[Flag], directories: list[str]) -> list[str]:
    """One line per declared key that no file under ``directories`` names."""
    if not directories:
        return []
    prefixes = tuple(directory.rstrip("/") + "/" for directory in directories)
    texts = [
        gatelib.read_text(root / path) or ""
        for path in gatelib.tracked_paths(root)
        if path.startswith(prefixes)
    ]
    return [
        f"{REGISTRY}: flag '{name(flag)}': no file under {', '.join(directories)} names it; "
        "remove the entry with the code"
        for flag in flags
        if not any(name(flag) in text for text in texts)
    ]


def expired(flags: list[Flag], today: dt.date) -> list[str]:
    """One line per flag whose removal date has passed."""
    return [
        f"{REGISTRY}: flag '{name(flag)}': remove-by {due} has passed; remove the flag and "
        "the branch it guards, or move the date in a change that says why"
        for flag in flags
        if isinstance(due := flag.get("remove-by"), dt.date) and due < today
    ]


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", default=".")
    parser.add_argument("--base")
    parser.add_argument("--used-in", action="append", default=[])
    parser.add_argument("--expired", action="store_true")
    parser.add_argument("--changed", action="store_true")
    parser.add_argument("--today", type=dt.date.fromisoformat)
    options = parser.parse_args(argv[1:])
    root = Path(options.root).resolve()
    text = gatelib.read_text(root / REGISTRY)
    if text is None:
        return gatelib.report("flags", [f"{REGISTRY}: missing; an empty file declares no flags"])
    flags, problems = parse(text)
    if options.expired:
        today = options.today or dt.datetime.now(tz=dt.UTC).date()
        return gatelib.report("flags-expired", problems + expired(flags, today))
    base = gatelib.base_commit(root, options.base)
    before = parse(gatelib.show(root, base, REGISTRY) or "")[0] if base else []
    if options.changed:
        print("\n".join(changed(flags, before)), end="\n" if changed(flags, before) else "")
        return 1 if problems else 0
    problems += [line for flag in flags for line in shape(flag)]
    problems += duplicates(flags) + born_on(flags, before) + unused(root, flags, options.used_in)
    return gatelib.report("flags", problems, base)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
