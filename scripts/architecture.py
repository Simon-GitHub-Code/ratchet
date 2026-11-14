"""The architecture a repository declares: the binding's section and the record it names.

ADR 0049. The owner chooses the architecture and its tools at adoption, and the choice lands in
two places a gate reads. The binding's ``## Architecture`` section names the decision record on its
``- Decision:`` line and declares the parts in a table, inner to outer::

    | Part | Path | May depend on |
    | --- | --- | --- |
    | rules | `src/rules/**` | nothing |
    | web | `src/web/**` | rules |

The record holds ``## Decision`` with a ``- Chosen by:`` line, ``## Considered options`` with at
least two alternatives as list items, ``## Reversal cost`` and ``## Refutation criterion``. These
readers judge structure only: sections, fields, list items and cells, never how well the prose
argues, which is the merge's to judge.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

import markdown_text

SECTION = "## Architecture"
DECISION = "Decision"
DECISION_SECTION = "## Decision"
CHOSEN_BY = "Chosen by"
OPTIONS_SECTION = "## Considered options"
REVERSAL_SECTION = "## Reversal cost"
COLUMNS = ("Part", "Path", "May depend on")
NOTHING = "nothing"
MIN_ALTERNATIVES = 2
PLACEHOLDER = "<"
ITEM = re.compile(r"^- \S", re.MULTILINE)


@dataclass(frozen=True)
class Declared:
    """What the binding's section says: the record's path, if it names one, and what is wrong."""

    record: str | None
    problems: tuple[str, ...]


def declared(binding: str) -> Declared:
    """Read the binding's ``## Architecture`` section; ``problems`` is empty when it is whole."""
    body = markdown_text.section(binding, SECTION)
    value = markdown_text.bullets(body).get(DECISION, "")
    named = markdown_text.ticked(value)
    record = named[0] if named and not value.startswith(PLACEHOLDER) else None
    problems = [] if record else [NO_RECORD]
    problems += _part_problems(binding)
    return Declared(record, tuple(problems))


NO_RECORD = (
    "the Architecture section's '- Decision:' line names no record; write the path of the"
    " architecture decision record in backticks (ADR 0049)"
)


def _part_problems(binding: str) -> list[str]:
    table = markdown_text.table(binding, SECTION)
    rows = [] if table is None or table.header != COLUMNS else list(table.rows)
    if not rows:
        header = " | ".join(COLUMNS)
        missing = (
            "the Architecture section declares no part: one row per part, inner to outer,"
            f" under '| {header} |' (ADR 0049)"
        )
        return [missing]
    names = {row[0] for row in rows}
    problems: list[str] = []
    for name, path, uses in rows:
        if any(not cell or cell.startswith(PLACEHOLDER) for cell in (name, path, uses)):
            problems.append(f"the Architecture table's row '{name}' has a cell still to fill")
            continue
        if not markdown_text.ticked(path):
            problems.append(f"part '{name}' gives no path in backticks")
        used = (item.strip().strip("`") for item in uses.split(","))
        problems.extend(
            f"part '{name}' may depend on '{part}', which the Architecture table does not declare"
            for part in used
            if part not in (*names, NOTHING)
        )
    return problems


def record_problems(text: str) -> list[str]:
    """What the decision record lacks of the fields ADR 0049 names.

    The refutation criterion is not read here: the check every record has already reads it.
    """
    problems: list[str] = []
    decision = markdown_text.section(text, DECISION_SECTION)
    if not decision.strip():
        problems.append(f"has no '{DECISION_SECTION}' section, which says the system chosen")
    chosen = markdown_text.bullets(decision).get(CHOSEN_BY, "")
    if not chosen or chosen.startswith(PLACEHOLDER):
        problems.append(
            f"says under '{DECISION_SECTION}' no '- {CHOSEN_BY}:' line: the owner, or the"
            " recommendation because the owner left it to the agent"
        )
    alternatives = len(ITEM.findall(markdown_text.section(text, OPTIONS_SECTION)))
    if alternatives < MIN_ALTERNATIVES:
        problems.append(
            f"lists {alternatives} alternative{'s' * (alternatives != 1)} under"
            f" '{OPTIONS_SECTION}'; at least {MIN_ALTERNATIVES}, each with why it was not chosen"
        )
    if markdown_text.empty_body(text, REVERSAL_SECTION):
        problems.append(
            f"says nothing under '{REVERSAL_SECTION}': what changing it later costs, part by part"
        )
    return problems
