#!/usr/bin/env python3
"""The owed ledger: what a project owes since an upgrade, which is counted and not red.

An upgrade moves the pin and makes every edit it can derive. What it cannot make, a finding an
agent or the owner settles, it records in ``quality/owed.toml``, one entry per finding (ADR 0037)::

    [[owed]]
    rule = "bites"
    path = "quality/bites.toml"
    problem = "gate 'lint' (lint, near-all rules) is bound to a tool and no [[bite]] plants ..."
    since = "0.12.0"

``rule`` is the conformance check that raised the finding, ``path`` the file it names,
``problem`` what stays the same while the finding stands, and ``since`` the plugin version whose
upgrade recorded it. An entry excuses one finding: the one that check raises on that path with
that problem. Two equal entries excuse two equal findings. A finding no entry excuses is red as
before, so a new violation of the same check in the same file, or the same one in another file,
stays red.

The ledger only shrinks. An entry whose finding is gone is red until it goes, and ``upgrade.py
--apply`` drops it. Against the merge base, a change may add or alter an entry only when it also
moves the plugin pin to a newer version, and the entry it adds carries that version;
``lowered.py``, which runs before every merge, holds that.

Usage: ``owed.py [--root DIR]`` prints how many things the project owes, in plain words, or
nothing when it owes nothing. It always exits 0.
"""

from __future__ import annotations

import argparse
import json
import sys
import tomllib
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

import gatelib
import plugin_pin

if TYPE_CHECKING:
    from collections.abc import Sequence

LEDGER = Path("quality/owed.toml")
# Why: the ledger's own findings (an entry no finding matches, a ledger that does not parse) are
# never owed, or an entry could excuse the stale entry it sits beside.
OWN = "owed"
TABLE = "owed"
FIELDS = ("rule", "path", "problem", "since")
HEADER = """\
# What this project still owes since an upgrade of the Ratchet plugin (its ADR 0037).
# Each entry is one thing a check found that the upgrade could not settle by itself: the check
# that found it (`rule`), the file (`path`), what is wrong (`problem`) and the plugin version
# whose upgrade wrote it down (`since`). While an entry is here, its check counts that one thing
# as owed instead of failing. It excuses nothing else.
# The list only gets shorter. Once a thing is fixed, `upgrade.py --apply` removes its entry. A
# change that adds or alters an entry fails before it can merge, unless it also moves the plugin
# to a newer version. Nobody needs to edit this file by hand.
"""

Key = tuple[str, str, str]


@dataclass(frozen=True, order=True)
class Entry:
    """One finding the project owes: sorted by file, then check, then problem."""

    path: str
    rule: str
    problem: str
    since: str

    @property
    def key(self) -> Key:
        """What the entry excuses: the check, the file and the problem."""
        return (self.rule, self.path, self.problem)


@dataclass(frozen=True)
class Ledger:
    """The entries of a ledger, and what keeps it from being read as one."""

    entries: tuple[Entry, ...]
    problems: tuple[str, ...]


def parse(text: str | None) -> Ledger:
    """The ledger ``text`` holds: absent or empty, it owes nothing."""
    try:
        data = tomllib.loads(text or "")
    except tomllib.TOMLDecodeError as error:
        return Ledger((), (f"does not parse: {error}",))
    problems = [f"unknown key '{key}'" for key in sorted(set(data) - {TABLE})]
    tables = data.get(TABLE, [])
    if not isinstance(tables, list):
        return Ledger((), (*problems, f"'{TABLE}' is not an array of tables"))
    entries = []
    for number, table in enumerate(tables, start=1):
        if not isinstance(table, dict) or set(table) != set(FIELDS):
            got = sorted(table) if isinstance(table, dict) else type(table).__name__
            problems.append(f"entry {number} needs exactly {', '.join(FIELDS)}; it has {got}")
        elif not all(isinstance(table[field], str) and table[field] for field in FIELDS):
            problems.append(f"entry {number}: every field is a string that is not empty")
        else:
            entries.append(Entry(**{field: table[field] for field in FIELDS}))
    return Ledger(tuple(entries), tuple(problems))


def read(root: Path) -> Ledger:
    """The ledger the repository at ``root`` holds."""
    return parse(gatelib.read_text(root / LEDGER))


def quoted(value: str) -> str:
    """``value`` as a TOML basic string."""
    return json.dumps(value, ensure_ascii=False).replace("\x7f", "\\u007f")


def render(entries: Sequence[Entry]) -> str:
    """The ledger file holding ``entries``, in their sorted order."""
    blocks = [
        f"[[{TABLE}]]\n"
        + "".join(f"{field} = {quoted(getattr(entry, field))}\n" for field in FIELDS)
        for entry in sorted(entries)
    ]
    return "\n".join([HEADER, *blocks])


def excused(keys: Sequence[Key], entries: Sequence[Entry]) -> tuple[list[bool], Counter[Key]]:
    """Which findings, by key in order, an entry excuses, and the entries left over, by key.

    Each entry excuses one finding. A finding of the ledger's own is never excused.
    """
    left = Counter(entry.key for entry in entries)
    owed = []
    for key in keys:
        hit = key[0] != OWN and left[key] > 0
        left[key] -= hit
        owed.append(hit)
    return owed, +left


def kept(found: Counter[Key]) -> gatelib.Want:
    """The want that leaves the ledger as many entries of each key as ``found`` has findings.

    Why: every stale entry goes in this one edit. One edit per entry was a trial each, and the
    time grew with the square of their number.
    """

    def keep(text: str | None) -> str:
        ledger = parse(text)
        if ledger.problems:
            return text or ""
        seen: Counter[Key] = Counter()
        entries = []
        for entry in ledger.entries:
            seen[entry.key] += 1
            if seen[entry.key] <= found[entry.key]:
                entries.append(entry)
        return render(entries) if len(entries) < len(ledger.entries) else text or ""

    return keep


def recorded(added: Sequence[Entry]) -> gatelib.Want:
    """The want that adds ``added`` to the ledger, or writes nothing when there is none."""

    def record(text: str | None) -> str:
        ledger = parse(text)
        if not added or ledger.problems:
            return text or ""
        return render([*ledger.entries, *added])

    return record


def moved(before: str | None, after: str | None) -> plugin_pin.Pin | None:
    """The pin a change moves to, when it moves to a newer version; ``None`` when it does not.

    ``before`` and ``after`` are the binding at the merge base and at head. Only such a change may
    add to the ledger. A binding that pinned nothing (an adoption older than the pin) moves to any.
    """
    old, new = plugin_pin.pinned(before), plugin_pin.pinned(after)
    if new is None or (old is not None and not plugin_pin.newer(new.version, old.version)):
        return None
    return new


def grown(before: Ledger, after: Ledger, *, binding: tuple[str | None, str | None]) -> list[str]:
    """Why the ledger at head is more than the one at the merge base allows, one line each.

    An entry may go. One that is new or changed is allowed only when the change moves the pin to
    a newer version, and then it carries that version as ``since``.
    """
    problems = [f"{LEDGER}: {problem}" for problem in after.problems]
    added = sorted((Counter(after.entries) - Counter(before.entries)).elements())
    pin = moved(*binding)
    if pin is None:
        old, new = (pin_name(plugin_pin.pinned(text)) for text in binding)
        return problems + [
            f"{LEDGER}: adds or changes '{entry.problem}' for {entry.path} ({entry.rule}) without"
            f" moving the plugin pin to a newer version ({old} at the merge base, {new} here);"
            " an entry may only go"
            for entry in added
        ]
    return problems + [
        f"{LEDGER}: adds '{entry.problem}' for {entry.path} ({entry.rule}) since '{entry.since}';"
        f" the change moves the plugin pin to {pin.version}, so a new entry is owed since"
        f" {pin.version}"
        for entry in added
        if entry.since != pin.version
    ]


def pin_name(pin: plugin_pin.Pin | None) -> str:
    """A pin as the gate's line names it."""
    return f"{pin.version} at {pin.commit[:12]}" if pin else "no pin"


def summary(root: Path) -> str | None:
    """The plain line that says how much the project owes, or ``None`` when it owes nothing."""
    entries = read(root).entries
    if not entries:
        return None
    count = len(entries)
    things = "thing" if count == 1 else "things"
    latest = max(
        (entry.since for entry in entries),
        key=lambda version: tuple(int(part) for part in version.split(".") if part.isdigit()),
    )
    return (
        f"{count} {things} this project owes since the upgrade to {latest}. None of them stops a"
        " merge, and the list only gets shorter: an agent works through them one at a time."
    )


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="owed.py")
    parser.add_argument("--root", type=Path, default=Path())
    options = parser.parse_args(argv[1:])
    line = summary(options.root.resolve())
    if line:
        print(line)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
