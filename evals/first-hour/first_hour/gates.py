"""Read gate results out of the output of the plugin's parallel runner.

``parallel.py`` prints each gate's output whole under ``==> gate <==`` and one line per gate,
``parallel: gate: green in 0.1s`` or ``parallel: gate: red (exit 1) in 2.0s``. A gate that is not
asked yet prints ``later: <trigger>``. Older runners call it green; newer ones say
``parallel: gate: judged nothing (later: <trigger>)``, and ``judged nothing (none: <why>)`` for a
gate that does not apply to the project at all. Both shapes read the same here.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum

HEADER = re.compile(r"^==> (\S+) <==$")
VERDICT = re.compile(r"^parallel: (\S+): (green|red)\b")
NOTHING = re.compile(r"^parallel: (\S+): judged nothing \((later|none): (.*)\)")
LATER = re.compile(r"^later: (.+)$")


class Status(StrEnum):
    """What a gate did."""

    GREEN = "green"
    RED = "red"
    LATER = "later"
    NONE = "none"


@dataclass(frozen=True)
class Gate:
    """One gate's result."""

    name: str
    status: Status
    trigger: str = ""


def parse(output: str) -> list[Gate]:
    """Every gate the runner reported, in the order of their verdicts."""
    later: dict[str, str] = {}
    verdicts: dict[str, Gate] = {}
    section = None
    for line in output.splitlines():
        if header := HEADER.match(line):
            section = header.group(1)
        elif (match := LATER.match(line)) and section is not None:
            later.setdefault(section, match.group(1).strip())
        elif nothing := NOTHING.match(line):
            name, kind, why = nothing.groups()
            verdicts[name] = Gate(name, Status(kind), why.strip())
        elif verdict := VERDICT.match(line):
            name = verdict.group(1)
            status = Status(verdict.group(2))
            waiting = status is Status.GREEN and name in later
            verdicts[name] = (
                Gate(name, Status.LATER, later[name]) if waiting else Gate(name, status)
            )
    return list(verdicts.values())


def red(output: str) -> list[str]:
    """The names of the red gates in ``output``."""
    return [g.name for g in parse(output) if g.status is Status.RED]
