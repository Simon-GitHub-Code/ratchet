#!/usr/bin/env python3
from __future__ import annotations

import argparse
import re
import sys
from datetime import UTC, datetime
from pathlib import Path

import gatelib

LOG = "questions.tsv"
HEADER = "date\tskill\tkey\tkind\trecommended"
DIRECTION = "direction"
KINDS = (DIRECTION, "irreversible", "tooling")
TAKEN = "yes"
ANSWERS = (TAKEN, "no")
STREAK = 3
NAME = re.compile(r"[a-z0-9]+(?:[.-][a-z0-9]+)*")
STANDING = re.compile(r"^## Standing answers[ \t]*\n(.*?)(?=^## |\Z)", re.MULTILINE | re.DOTALL)
STANDING_KEY = re.compile(r"^- `([^`]+)`", re.MULTILINE)
AGENTS = "AGENTS.md"


def today() -> str:
    return datetime.now(tz=UTC).astimezone().date().isoformat()


def record(root: Path, fields: tuple[str, str, str, str]) -> int:
    skill, name, _kind, _recommended = fields
    wrong = [text for text in (skill, name) if not NAME.fullmatch(text)]
    if wrong:
        print(f"questions: not a key of lower-case words joined by `.` or `-`: {', '.join(wrong)}")
        return 2
    log = gatelib.state_dir(root) / LOG
    if not log.is_file():
        log.write_text(HEADER + "\n", encoding="utf-8")
    with log.open("a", encoding="utf-8") as lines:
        lines.write("\t".join((today(), *fields)) + "\n")
    print(f"questions: recorded {name}")
    return 0


def standing(root: Path) -> set[str]:
    section = STANDING.search(gatelib.read_text(root / AGENTS) or "")
    return set(STANDING_KEY.findall(section.group(1))) if section else set()


def proposals(root: Path) -> list[str]:
    rows = (gatelib.read_text(root / gatelib.STATE / LOG) or "").splitlines()[1:]
    streaks: dict[str, int] = {}
    kinds: dict[str, str] = {}
    for row in rows:
        cells = row.split("\t")
        if len(cells) != len(HEADER.split("\t")):
            continue
        _date, _skill, name, kind, recommended = cells
        streaks[name] = streaks.get(name, 0) + 1 if recommended == TAKEN else 0
        kinds[name] = kind
    settled = standing(root)
    return [
        f"you have taken the recommendation for `{name}` {count} times running: say the word and"
        f" it becomes a standing answer in {AGENTS}, no longer asked"
        for name, count in sorted(streaks.items())
        if count >= STREAK and kinds[name] != DIRECTION and name not in settled
    ]


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=["record", "report"])
    parser.add_argument("--root", default=".")
    parser.add_argument("--skill", default="")
    parser.add_argument("--key", default="")
    parser.add_argument("--kind", choices=KINDS, default=DIRECTION)
    parser.add_argument("--recommended", choices=ANSWERS, default=ANSWERS[1])
    options = parser.parse_args(argv[1:])
    root = Path(options.root).resolve()
    if options.action == "record":
        return record(root, (options.skill, options.key, options.kind, options.recommended))
    for line in proposals(root):
        print(f"proposal: {line}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
