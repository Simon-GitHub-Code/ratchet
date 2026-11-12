#!/usr/bin/env python3
"""How the owner answers: one line per question asked, and the questions they need not be asked.

ADR 0023. A skill asks the owner where the options lead to different products, where a wrong
guess cannot be taken back, and where the owner said they want a say in the tooling. Each answer
is one line of ``.ratchet/questions.tsv``: the date, the skill, the question's key, its kind and
whether the owner took the recommended option. The file is one clone's and no commit carries it,
so recording an answer asks nobody and dirties no work tree.

- ``record --skill S --key K --kind direction|irreversible|tooling --recommended yes|no``
  appends the line. A key is stable: the same question asked again has the same key
  (``adopt.tools``); a question about one ticket carries the ticket (``feature.<slug>.1``).
- ``report`` prints one line per key whose last three answers or more all took the
  recommendation: a proposal that the owner make it a standing answer. It changes nothing. A
  ``direction`` key is never proposed: a product question is new each time it is asked. A key
  ``AGENTS.md`` already lists under ``## Standing answers`` is not proposed again.

Usage: ``questions.py record --skill S --key K --kind KIND --recommended yes|no [--root DIR]``
and ``questions.py report [--root DIR]``.
"""

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
    """Append one answer: the skill, the key, the kind and whether the recommendation was taken."""
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
    """The keys ``AGENTS.md`` lists under ``## Standing answers``: asked once, answered for good."""
    section = STANDING.search(gatelib.read_text(root / AGENTS) or "")
    return set(STANDING_KEY.findall(section.group(1))) if section else set()


def proposals(root: Path) -> list[str]:
    """One sentence per key the owner has answered with the recommendation three times running."""
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
