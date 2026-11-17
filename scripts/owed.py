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

Between upgrades the ledger only shrinks. An entry whose finding is gone is red until it goes,
and ``upgrade.py --apply`` drops it. Against the merge base, a change may add or alter an entry
only when it also moves the plugin pin to a newer version, and the entry it adds carries that
version; ``lowered.py``, which runs before every merge, holds that.

Usage: ``owed.py [--root DIR]`` prints how many things the project owes and which checks do not
run until they are fixed, in plain words, or nothing when it owes nothing. It always exits 0.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import tomllib
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

import gatelib
import plugin_pin

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

LEDGER = Path("quality/owed.toml")
OWN = "owed"
COMMENTS = "comments"
GATE_RULES = frozenset({COMMENTS})
# Why: a finding that another tier 0 or tier 1 gate also judges red is never owed: conformance
# would print it as owed while that gate stays red on the same thing. Each pair is a check here, or
# one file of it, and the gate that judges the same condition. The ledger's own findings are never
# owed either, or an entry could excuse the stale entry it sits beside.
NEVER: dict[tuple[str, str | None], str] = {
    (OWN, None): "ratchet-lowered reads the ledger and is red on one that does not parse",
    ("settings", None): "settings-check is red on the same drift of the permission rules",
    ("incidents", None): "the incidents gate runs this one check alone",
    (
        "installs",
        "justfile",
    ): "each recipe it names is red in the merge's work tree and in CI as it stands",
    ("glossary", "GLOSSARY.md (or GLOSSARY-MAP.md)"): "prose is red without GLOSSARY.md",
    (
        "mutation_filter",
        None,
    ): "the changed-code mutation gate fails on the recipe, whatever the ledger says",
    ("quality", "quality/thresholds.toml"): "ratchet-lowered reads the file and is red without it",
    ("quality", "quality/mutation-register.toml"): "the mutation gates read the register",
}
# Why: ADR 0050. A finding an agent writes from the repository alone (a section of the binding
# that the project's own tree and tools state, a recipe it converts) is filled first: the route
# names the step right after an upgrade, and neither a feature nor the drain starts while one is
# owed, so the window is one agent step. The checks are named by rule, the name the ledger already
# keys an entry by; the owner has nothing to do for any of them.
FILL_FIRST = frozenset(
    {
        "signs",
        "pins",
        "suppressions",
        "dependencies",
        "installs",
        "mutation",
        "changed_tests",
        "comments_section",
        "trigger_kinds",
        "documents",
    }
)
TABLE = "owed"
FIELDS = ("rule", "path", "problem", "since")

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


def fill_first(root: Path) -> list[Entry]:
    """The owed entries an agent fills first: those of a check ``FILL_FIRST`` names."""
    return [entry for entry in read(root).entries if entry.rule in FILL_FIRST]


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
    return "\n".join(blocks)


def excused(keys: Sequence[Key], entries: Sequence[Entry]) -> tuple[list[bool], Counter[Key]]:
    """Which findings, by key in order, an entry excuses, and the entries left over, by key.

    Each entry excuses one finding. A finding ``never`` names is never excused.
    """
    left = Counter(entry.key for entry in entries)
    owed = []
    for key in keys:
        hit = not never(key) and left[key] > 0
        left[key] -= hit
        owed.append(hit)
    return owed, +left


def never(key: Key) -> bool:
    """Whether a finding with ``key`` is one another gate judges, so no entry may excuse it."""
    rule, path, _ = key
    return (rule, None) in NEVER or (rule, path) in NEVER


BLOCK_START = re.compile(rf"^\[\[{TABLE}\]\]\n", re.MULTILINE)


def kept(found: Counter[Key]) -> gatelib.Want:
    """The want that leaves the ledger as many entries of each key as ``found`` has findings.

    Why: every stale entry goes in this one edit. One edit per entry was a trial each, and the
    time grew with the square of their number.
    """

    def keep(text: str | None) -> str:
        ledger = parse(text)
        head, *blocks = BLOCK_START.split(text or "")
        if ledger.problems or len(blocks) != len(ledger.entries):
            return text or ""
        seen: Counter[Key] = Counter()
        flags = []
        for block in blocks:
            (entry,) = parse(f"[[{TABLE}]]\n{block}").entries
            seen[entry.key] += 1
            flags.append(entry.rule in GATE_RULES or seen[entry.key] <= found[entry.key])
        if all(flags):
            return text or ""
        body = [f"[[{TABLE}]]\n{block}" for block, keep in zip(blocks, flags, strict=True) if keep]
        if body and not flags[-1]:
            body[-1] = body[-1].rstrip("\n") + "\n"
        return head + "".join(body)

    return keep


def recorded(added: Sequence[Entry]) -> gatelib.Want:
    """The want that adds ``added`` to the ledger, or writes nothing when there is none."""

    def record(text: str | None) -> str:
        ledger = parse(text)
        if not added or ledger.problems:
            return text or ""
        return render([*ledger.entries, *added])

    return record


def moved(before: str | None, after: str | None, *, floor: str | None) -> plugin_pin.Pin | None:
    """The pin a change moves to, when it moves to a newer version; ``None`` when it does not.

    ``before`` and ``after`` are the binding at the merge base and at head. Only such a change may
    add to the ledger. A binding that pinned nothing (an adoption older than the pin) moves to a
    pin no older than ``floor``, the plugin judging the change: an older plugin would not read
    the ledger at all.
    """
    old, new = plugin_pin.pinned(before), plugin_pin.pinned(after)
    if new is None:
        return None
    if old is None:
        return new if floor is None or not below(new.version, floor) else None
    return new if plugin_pin.newer(new.version, old.version) else None


def below(version: str, floor: str) -> bool:
    """Whether ``version`` is older than ``floor``, or cannot be ordered against it."""
    return version != floor and plugin_pin.newer(version, floor) is not True


def followed(
    added: Counter[Entry], gone: Counter[Entry], renamed: Mapping[str, str]
) -> Counter[Entry]:
    moves: Counter[Entry] = Counter()
    left = Counter(gone)
    for entry in sorted(added.elements()):
        source = next(
            (
                old
                for old in sorted(left.elements())
                if renamed.get(old.path) == entry.path
                and (old.rule, old.problem, old.since) == (entry.rule, entry.problem, entry.since)
            ),
            None,
        )
        if source is not None:
            left[source] -= 1
            moves[entry] += 1
    return moves


def gated(entries: Counter[Entry], first_listed: str | None) -> list[str]:
    return [
        f"{LEDGER}: adds '{entry.problem}' for {entry.path} ({entry.rule})"
        + (
            "; only the adoption and the change that first fills the lister record what the"
            f" {entry.rule} gate finds, and this change is neither; an entry may only go"
            if first_listed is None
            else f" since '{entry.since}'; the change first fills the lister under the pin"
            f" {first_listed}, so what it records is owed since {first_listed}"
        )
        for entry in sorted(entries.elements())
        if entry.since != first_listed
    ]


def grown(
    before: Ledger,
    after: Ledger,
    *,
    binding: tuple[str | None, str | None],
    floor: str | None,
    exempt: tuple[str | None, Mapping[str, str]] | None = None,
) -> list[str]:
    """Why the ledger at head is more than the one at the merge base allows, one line each.

    An entry may go. One that is new or changed is allowed only when the change moves the pin to
    a newer version, and then it carries that version as ``since``.
    """
    problems = [f"{LEDGER}: {problem}" for problem in after.problems]
    first_listed, renamed = exempt or (None, {})
    new = Counter(after.entries) - Counter(before.entries)
    new -= followed(new, Counter(before.entries) - Counter(after.entries), renamed)
    own = Counter({entry: count for entry, count in new.items() if entry.rule in GATE_RULES})
    problems += gated(own, first_listed)
    added = sorted((new - own).elements())
    if not added:
        return problems
    pin = moved(*binding, floor=floor)
    if pin is None:
        old, now = (plugin_pin.pinned(text) for text in binding)
        why = (
            f"with a pin of {now.version}; {now.version} is older than the plugin in use, {floor},"
            " and from a base that pins nothing the ledger grows only with a pin at least as new"
            " as the plugin that judges it"
            if old is None and now is not None and floor is not None
            else f"without moving the plugin pin to a newer version ({pin_name(old)} at the merge"
            f" base, {pin_name(now)} here)"
        )
        return problems + [
            f"{LEDGER}: adds or changes '{entry.problem}' for {entry.path} ({entry.rule}) {why};"
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


def summary(root: Path, *, waiting: Sequence[str] = ()) -> str | None:
    """The plain line that says how much the project owes, or ``None`` when it owes nothing.

    ``waiting`` names the checks that do not run until what is owed is fixed.
    """
    ledger = read(root)
    if ledger.problems:
        return (
            f"The list of what this project owes ({LEDGER}) cannot be read ({ledger.problems[0]}),"
            " so every check it held back is red until it reads again."
        )
    entries = ledger.entries
    if not entries:
        return None
    count = len(entries)
    things = "thing" if count == 1 else "things"
    latest = max(
        (entry.since for entry in entries),
        key=lambda version: tuple(int(part) for part in version.split(".") if part.isdigit()),
    )
    text = (
        f"{count} {things} this project owes since the upgrade to {latest}. None of them stops a"
        " merge, and between upgrades the list only gets shorter. Nothing fixes them by itself:"
        " ask an agent to."
    )
    if waiting:
        text += f" Checks that do not run until then: {', '.join(waiting)}."
    return text


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="owed.py")
    parser.add_argument("--root", type=Path, default=Path())
    parser.add_argument("--fill-first", action="store_true", help="list what an agent fills first")
    options = parser.parse_args(argv[1:])
    root = options.root.resolve()
    if options.fill_first:
        for entry in fill_first(root):
            print(f"{entry.rule}\t{entry.path}\t{entry.problem}")
        return 0
    waiting: list[str] = []
    if read(root).entries:
        # Why: conformance imports this module, and only a project that owes pays for its checks.
        import conformance  # noqa: PLC0415

        waiting = conformance.not_running(root, conformance.findings(root))
    line = summary(root, waiting=waiting)
    if line:
        print(line)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
