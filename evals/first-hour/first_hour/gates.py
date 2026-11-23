from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum

HEADER = re.compile(r"^==> (\S+) <==$")
VERDICT = re.compile(r"^parallel: (\S+): (green|red|could not judge)\b")
NOTHING = re.compile(
    r"^parallel: (\S+): (?:judged nothing|not run yet|not applicable) \((later|none): (.*)\)"
)
LATER = re.compile(r"^later: (.+)$")


class Status(StrEnum):
    GREEN = "green"
    RED = "red"
    UNJUDGED = "could not judge"
    LATER = "later"
    NONE = "none"


@dataclass(frozen=True)
class Gate:
    name: str
    status: Status
    trigger: str = ""


def parse(output: str) -> list[Gate]:
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
    return [g.name for g in parse(output) if g.status is Status.RED]
