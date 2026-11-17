#!/usr/bin/env python3
from __future__ import annotations

import argparse
import math
import re
import sys
import tomllib
from collections import Counter
from pathlib import Path

import comments
import documents
import gatelib
import markdown_text
import owed
import plugin_pin

CEILINGS = "ceilings."
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
    return [message for *_, message in _loosenings(before, after)]


def lowered(before: dict[str, Value], after: dict[str, Value]) -> list[str]:
    return loosened(before, after)


def _spelled(text: str) -> str:
    if text in {"removed", "true", "false"}:
        return text
    return show(float(text))


def _named(value: Value | None) -> str:
    return "removed" if value is None else show(value)


def relaxations(root: Path, base: str) -> dict[tuple[str, str, str], str]:
    named: dict[tuple[str, str, str], str] = {}
    for adr in sorted((root / ADRS).glob("*.md")):
        relative = adr.relative_to(root).as_posix()
        held = Counter((gatelib.show(root, base, relative) or "").splitlines())
        for line in (gatelib.read_text(adr) or "").splitlines():
            if held[line] > 0:
                held[line] -= 1
                continue
            if match := RELAXES.match(line):
                key = (match["key"], _spelled(match["old"]), _spelled(match["new"]))
                named[key] = relative
    return named


def forgotten(root: Path, base: str, path: str, *, wanted: set[str]) -> dict[str, Value]:
    found: dict[str, Value] = {}
    listed = gatelib.git(root, "log", "--format=%H", base, "--", path) or ""
    for commit in listed.split():
        text = gatelib.show(root, commit, path)
        try:
            flat = flatten(tomllib.loads(text)) if text else {}
        except tomllib.TOMLDecodeError:
            continue
        for key in wanted - found.keys():
            if key in flat:
                found[key] = flat[key]
        if found.keys() == wanted:
            break
    return found


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
    problems = [*dropped_answers(root, base), *owed_grown(root, base)]
    previous = gatelib.show(root, base, options.file)
    if previous is None:
        print(f"ratchet-lowered: {options.file} is new at {base[:12]}; accepted as is")
        return gatelib.report("ratchet-lowered", problems, base) if problems else 0
    before = flatten(tomllib.loads(previous))
    after = flatten(tomllib.loads(current_path.read_text()))
    if absent := set(after) - set(before):
        before = {**forgotten(root, base, options.file, wanted=absent), **before}
    found = _loosenings(before, after)
    named = relaxations(root, base)
    for key, old, new, message in found:
        adr = named.get((key, show(old), _named(new)))
        if adr is None:
            problems.append(message)
        else:
            print(f"{message}, relaxed by {adr}")
    return gatelib.report("ratchet-lowered", problems, base)


def dropped_answers(root: Path, base: str) -> list[str]:
    gone = gatelib.git(root, "diff", "--name-only", "--diff-filter=D", "-M", base, "HEAD") or ""
    brief = gatelib.read_text(root / documents.BRIEF) or ""
    held = {
        documents.parts(row)[1]
        for row in markdown_text.section(brief, documents.OWNERS_WORDS).split("\n")
    }
    return [
        f"{path}: its answer `{line}` is not in {documents.BRIEF} under `{documents.OWNERS_WORDS}`"
        for path in gone.split("\n")
        if documents.is_steering(path)
        for line in documents.answers_of(gatelib.show(root, base, path) or "")
        if documents.parts(line)[1] not in held
    ]


def owed_grown(root: Path, base: str) -> list[str]:
    binding = str(gatelib.BINDING)
    before, after = owed.parse(gatelib.show(root, base, str(owed.LEDGER))), owed.read(root)
    pins = (gatelib.show(root, base, binding), gatelib.read_text(root / binding))
    floor = plugin_pin.version_of(plugin_pin.PLUGIN)
    problems = owed.grown(
        before,
        after,
        binding=pins,
        floor=floor,
        exempt=(comments.first_listed(root, base), gatelib.renamed(root, base)),
    )
    added = Counter(after.entries) - Counter(before.entries)
    pin = owed.moved(*pins, floor=floor)
    if problems or not added or pin is None:
        return problems
    return unfound(root, base, pin=pin, added=added)


def unfound(root: Path, base: str, *, pin: plugin_pin.Pin, added: Counter[owed.Entry]) -> list[str]:
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

    red = Counter(finding.key() for finding in conformance.findings(root))
    allowed = upgrade.red_at_base(root, base, plugin_pin.PLUGIN) & red
    problems = []
    for entry in sorted(added.elements()):
        if red[entry.key] == 0:
            continue
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
