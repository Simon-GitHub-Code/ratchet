#!/usr/bin/env python3
"""The output of each red gate in a ``parallel.py`` log, and nothing else.

``parallel.py`` prints every gate whole under a ``==> name <==`` header, then one status line, so a
log of a tier holds the reason a gate was red right beside its name. A report that quotes the end
of the whole log shows the last gates to finish instead, which are usually green. This reads the
log and prints each red gate's header, the end of its output and its status line, in the order
they finished, so the report keeps what the red gates said.

Usage: ``red_gates.py LOG [--lines N]``. Exit status 0 when it printed a red gate, 1 when the log
holds none: no gate ran, or the runner is not ``parallel.py``. The caller then falls back to the
end of the log.
"""

from __future__ import annotations

import argparse
import re
import sys
from dataclasses import dataclass
from pathlib import Path

import gatelib

LINES = 40
# Why: a forge refuses an issue or a comment of 65536 characters, so one gate may not take it all.
CHARACTERS = 6000
CLIPPED = "... (only the end of the output is shown)"
STATUS = re.compile(r"^parallel: (?P<name>.+): (?P<how>green|red \(exit \d+\)) in \d+\.\d+s$")


@dataclass(frozen=True)
class Gate:
    """One finished gate of the log: what it printed and the status line that ended it."""

    name: str
    output: list[str]
    status: str
    red: bool


def header(name: str) -> str:
    return f"==> {name} <=="


def gates(log: str) -> list[Gate]:
    """Every gate that finished in ``log``, in order."""
    lines = log.splitlines()
    found = []
    floor = 0
    for index, line in enumerate(lines):
        status = STATUS.match(line)
        if status is None:
            continue
        name = status["name"]
        start = next(
            (back for back in range(index - 1, floor - 1, -1) if lines[back] == header(name)),
            None,
        )
        output = [] if start is None else lines[start + 1 : index]
        found.append(Gate(name, output, line, status["how"] != "green"))
        floor = index + 1
    return found


def tail(output: list[str], lines: int) -> list[str]:
    """The last ``lines`` lines of ``output``, at most ``CHARACTERS`` characters of them."""
    text = "\n".join(output[-lines:])
    clipped = len(output) > lines or len(text) > CHARACTERS
    kept = text[-CHARACTERS:].split("\n") if text else []
    return [CLIPPED, *kept] if clipped else kept


def report(log: str, lines: int = LINES) -> str | None:
    """The header, the end of the output and the status line of each red gate, or ``None``."""
    red = [gate for gate in gates(log) if gate.red]
    blocks = [
        "\n".join([header(gate.name), *tail(gate.output, lines), gate.status]) for gate in red
    ]
    return "\n\n".join(blocks) if blocks else None


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="red_gates.py", description=__doc__.splitlines()[0])
    parser.add_argument("log", type=Path)
    parser.add_argument("--lines", type=int, default=LINES)
    options = parser.parse_args(argv[1:])
    if options.lines < 1:
        parser.error("--lines is at least 1")
    answer = report(gatelib.read_text(options.log) or "", options.lines)
    if answer is None:
        return 1
    print(answer)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
