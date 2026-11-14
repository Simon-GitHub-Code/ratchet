#!/usr/bin/env python3
"""The ratchet's first rule as a gate: no threshold lowered, no ceiling raised, none missing.

Compares ``quality/thresholds.toml`` at head with the merge base. Every key only rises, except
the keys under ``ceilings`` (the speed gate's seconds per tier and gate, the change-size
ceilings), which only fall. The change ceiling's older keys ``files`` and ``lines`` may move to
``product_files`` and ``product_lines`` (ADR 0045) at the same value or lower. A flag that is
``true`` (``absolute``) stays ``true``.

Relaxing a value is the owner's change with an ADR behind it, so a loosening passes when an ADR
under ``docs/adr/`` names it exactly, on a line of its own:
``Relaxes: <key> from <old> to <new>``, with ``removed`` as the new value of a dropped key. An
agent may add that ADR (ADR 0014), but ``quality/thresholds.toml`` is a guard path, so the
loosening itself is the owner's edit.

The owed ledger, ``quality/owed.toml``, is a ratchet too (ADR 0037): against the merge base an
entry may go, and none may be added or changed unless the same change moves the binding's plugin
pin to a newer version and the new entry carries that version. Otherwise a change could silence a
new red finding by writing an entry for it.

Usage: ``lowered.py [--root DIR] [--base REF] [--file PATH]``. The base is ``gatelib.base_commit``:
the merge base of HEAD with the default branch, or ``HEAD~1`` on the default branch itself. A
base without the file passes: that is the first adoption.
"""

from __future__ import annotations

import argparse
import math
import re
import sys
import tomllib
from collections import Counter
from pathlib import Path

import gatelib
import owed
import plugin_pin

CEILINGS = "ceilings."
# Why: ADR 0045 moved the change ceiling to product-only keys. The old key's value is the bound the
# new one is held to, so the move is a loosening only when the new ceiling is higher.
MOVED = {
    "ceilings.change.files": "ceilings.change.product_files",
    "ceilings.change.lines": "ceilings.change.product_lines",
}
ADRS = "docs/adr"
_VALUE = r"true|false|-?\d+(?:\.\d+)?"
RELAXES = re.compile(
    rf"^\W*Relaxes:\s*(?P<key>\S+)\s+from\s+(?P<old>{_VALUE})\s+to\s+(?P<new>{_VALUE}|removed)\s*$"
)
Value = float | bool
Loosening = tuple[str, Value, Value | None, str]


def flatten(data: dict[str, object], prefix: str = "") -> dict[str, Value]:
    flat: dict[str, Value] = {}
    for key, value in data.items():
        name = f"{prefix}.{key}" if prefix else key
        if isinstance(value, dict):
            flat.update(flatten(value, name))
        elif isinstance(value, bool):
            flat[name] = value
        elif isinstance(value, (int, float)):
            flat[name] = float(value)
    return flat


def show(value: Value) -> str:
    return str(value).lower() if isinstance(value, bool) else str(value)


def renamed(before: dict[str, Value], after: dict[str, Value]) -> dict[str, Value]:
    """``before`` with each moved key under the name ``after`` holds it by.

    A key counts as moved when ``after`` dropped the old name and holds the new one, which the
    base did not: the new value is then judged against the old one, as any ceiling is.
    """
    moved = dict(before)
    for old, new in MOVED.items():
        if old in moved and old not in after and new in after and new not in moved:
            moved[new] = moved.pop(old)
    return moved


def _loosenings(before: dict[str, Value], after: dict[str, Value]) -> list[Loosening]:
    before = renamed(before, after)
    found: list[Loosening] = []
    for key, old in sorted(before.items()):
        new = after.get(key)
        if new is None:
            found.append((key, old, None, f"{key}: removed (was {show(old)})"))
        elif isinstance(old, bool) != isinstance(new, bool):
            kinds = (
                ("a boolean", "a number") if isinstance(old, bool) else ("a number", "a boolean")
            )
            found.append((key, old, new, f"{key}: changed from {kinds[0]} to {kinds[1]}"))
        elif isinstance(old, bool):
            if old and not new:
                found.append((key, old, new, f"{key}: turned off"))
        elif not math.isfinite(new):
            found.append((key, old, new, f"{key}: {show(new)} is not a finite number"))
        elif key.startswith(CEILINGS) and new > old:
            found.append((key, old, new, f"{key}: ceiling raised from {show(old)} to {show(new)}"))
        elif not key.startswith(CEILINGS) and new < old:
            found.append((key, old, new, f"{key}: lowered from {show(old)} to {show(new)}"))
    return found


def loosened(before: dict[str, Value], after: dict[str, Value]) -> list[str]:
    """Every key that was removed, lowered (a threshold), raised (a ceiling) or turned off."""
    return [message for *_, message in _loosenings(before, after)]


def lowered(before: dict[str, Value], after: dict[str, Value]) -> list[str]:
    return loosened(before, after)


def _spelled(text: str) -> str:
    """A value as ``RELAXES`` spells it, in the form ``show`` prints."""
    if text in {"removed", "true", "false"}:
        return text
    return show(float(text))


def _named(value: Value | None) -> str:
    return "removed" if value is None else show(value)


def relaxations(root: Path) -> dict[tuple[str, str, str], str]:
    """Each loosening an ADR names, mapped to the ADR's path.

    Keyed on the shown values: ``1.0 == True`` in Python, so values would let a number pass a flag.
    """
    named: dict[tuple[str, str, str], str] = {}
    for adr in sorted((root / ADRS).glob("*.md")):
        for line in (gatelib.read_text(adr) or "").splitlines():
            if match := RELAXES.match(line):
                key = (match["key"], _spelled(match["old"]), _spelled(match["new"]))
                named[key] = adr.relative_to(root).as_posix()
    return named


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", default=".")
    parser.add_argument("--base")
    parser.add_argument("--file", default="quality/thresholds.toml")
    options = parser.parse_args(argv[1:])
    root = Path(options.root).resolve()
    current_path = root / options.file
    if not current_path.is_file():
        print(f"{options.file}: missing")
        return 1
    base = gatelib.base_commit(root, options.base)
    if base is None:
        print(f"ratchet-lowered: no base commit yet; {options.file} accepted as is")
        return 0
    problems = owed_grown(root, base)
    previous = gatelib.show(root, base, options.file)
    if previous is None:
        print(f"ratchet-lowered: {options.file} is new at {base[:12]}; accepted as is")
        return gatelib.report("ratchet-lowered", problems, base) if problems else 0
    found = _loosenings(
        flatten(tomllib.loads(previous)), flatten(tomllib.loads(current_path.read_text()))
    )
    named = relaxations(root)
    for key, old, new, message in found:
        adr = named.get((key, show(old), _named(new)))
        if adr is None:
            problems.append(message)
        else:
            print(f"{message}, relaxed by {adr}")
    return gatelib.report("ratchet-lowered", problems, base)


def owed_grown(root: Path, base: str) -> list[str]:
    """What the owed ledger at head adds against ``base`` that no move of the pin allows."""
    binding = str(gatelib.BINDING)
    before, after = owed.parse(gatelib.show(root, base, str(owed.LEDGER))), owed.read(root)
    pins = (gatelib.show(root, base, binding), gatelib.read_text(root / binding))
    floor = plugin_pin.version_of(plugin_pin.PLUGIN)
    problems = owed.grown(before, after, binding=pins, floor=floor)
    added = Counter(after.entries) - Counter(before.entries)
    pin = owed.moved(*pins, floor=floor)
    if problems or not added or pin is None:
        return problems
    return unfound(root, base, pin=pin, added=added)


def unfound(root: Path, base: str, *, pin: plugin_pin.Pin, added: Counter[owed.Entry]) -> list[str]:
    """Each entry the change adds that its upgrade could not have written, one line each.

    Why: ADR 0037. An entry may be added only for a finding red at head that the same plan also
    leaves red on the merge base, so a violation the change itself brought is never owed. The plan
    is the plugin's, so the answer is the pinned plugin's: another plugin skips this, and says so.
    CI always runs the pinned one.
    """
    if plugin_pin.relation(pin, plugin_pin.PLUGIN) is not plugin_pin.Relation.PINNED:
        print(
            f"ratchet-lowered: note: whether the merge base also owes each entry this change adds"
            f" is asked of the plugin the binding pins, {pin.version} at {pin.commit[:12]}; this"
            f" run uses {plugin_pin.in_use(plugin_pin.PLUGIN)}, so it is not asked here, and CI,"
            " which runs the pinned plugin, asks it"
        )
        return []
    import conformance  # noqa: PLC0415 -- only a change that moves the pin and adds pays for it
    import upgrade  # noqa: PLC0415

    allowed = upgrade.red_at_base(root, base, plugin_pin.PLUGIN) & Counter(
        finding.key() for finding in conformance.findings(root)
    )
    problems = []
    for entry in sorted(added.elements()):
        if allowed[entry.key] > 0:
            allowed[entry.key] -= 1
            continue
        problems.append(
            f"{owed.LEDGER}: adds '{entry.problem}' for {entry.path} ({entry.rule}), which the"
            f" upgrade's own plan does not leave red on the merge base {base[:12]}: a change owes"
            " only what the new version asks of the project as it stood, so this stays red"
        )
    return problems


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
