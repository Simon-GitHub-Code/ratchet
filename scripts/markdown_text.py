"""Markdown text, read and edited without a Markdown library: sections, tables and their rows.

``section``, ``empty_body`` and ``table_cells`` read. ``with_row`` and ``with_section`` return a
``gatelib.Want``: the text a file must hold once a row or a section of the template is in it.
Each obeys the laws every want does. It is idempotent. It keeps every line of the file in its
place, and writes only the lines it adds. It reads nothing but its argument. Two of them applied
in either order give the same text, because a row or a section goes by its rank in the template's
order, never by when it was asked for.

A table that a formatter padded is rendered padded again with the new row, at the new widths. A
table written by hand gets the template's line as written and no other line changes. A table that
does not split cleanly (a ragged row, no rule line) is left alone: ``table`` says ``None`` for it,
and the caller asks an agent.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from gatelib import Want

EMPTY_BODIES = {"none", "n/a", "na", "tbd", "todo", "-", "?"}
MIN_BODY = 20
TABLE_ROW = re.compile(r"^\|\s*([^|]+?)\s*\|\s*([^|]*?)\s*\|")
CELL_SPLIT = re.compile(r"(?<!\\)\|")
RULE_CELL = re.compile(r"(:?)-+(:?)")
SECTION_START = "## "
MIN_WIDTH = 3
MIN_TABLE_LINES = 2


def section(text: str, heading: str) -> str:
    start = text.find(heading)
    if start < 0:
        return ""
    body = text[start + len(heading) :]
    end = body.find("\n## ")
    return body if end < 0 else body[:end]


def empty_body(text: str, heading: str) -> bool:
    """Whether the section under ``heading`` says nothing: absent, blank, placeholder or short."""
    body = section(text, heading).strip()
    return body.lower().rstrip(".") in EMPTY_BODIES or len(body) < MIN_BODY


def table_cells(body: str) -> list[tuple[str, str]]:
    """The first two cells of every row under the header; an empty second cell is kept."""
    rows = []
    for line in body.splitlines():
        match = TABLE_ROW.match(line)
        if match and not set(match.group(1)) <= set("-: "):
            rows.append((match.group(1), match.group(2)))
    return rows[1:]


@dataclass(frozen=True)
class Table:
    """The first table under a heading: its cells, its alignment and where its lines sit.

    ``first`` and ``last`` count lines of the text from zero: the table is those from ``first`` up
    to, not including, ``last``.
    """

    header: tuple[str, ...]
    aligns: tuple[str, ...]
    rows: tuple[tuple[str, ...], ...]
    first: int
    last: int
    lines: tuple[str, ...]

    @property
    def padded(self) -> bool:
        """Whether a formatter wrote it: rendering it padded gives its lines back exactly."""
        return tuple(self.render(self.rows)) == self.lines

    def render(self, rows: Sequence[Sequence[str]]) -> list[str]:
        """The table as a formatter that pads every column to its widest cell writes it."""
        columns = range(len(self.header))
        widths = [
            max(MIN_WIDTH, len(self.header[i]), *(len(row[i]) for row in rows)) for i in columns
        ]

        def line(cells: Sequence[str]) -> str:
            fitted = (_fit(cell, widths[i], self.aligns[i]) for i, cell in enumerate(cells))
            return "| " + " | ".join(fitted) + " |"

        rule = "| " + " | ".join(_rule(self.aligns[i], widths[i]) for i in columns) + " |"
        return [line(self.header), rule, *map(line, rows)]


def _rule(align: str, width: int) -> str:
    left = ":" if align in {"left", "center"} else ""
    right = ":" if align in {"right", "center"} else ""
    return left + "-" * (width - len(left) - len(right)) + right


def _fit(cell: str, width: int, align: str) -> str:
    spare = width - len(cell)
    if align == "right":
        return " " * spare + cell
    if align == "center":
        return " " * (spare // 2) + cell + " " * (spare - spare // 2)
    return cell + " " * spare


def _cells(line: str) -> tuple[str, ...]:
    inner = line.strip().removeprefix("|").removesuffix("|")
    return tuple(cell.strip() for cell in CELL_SPLIT.split(inner))


def _clean(line: str) -> bool:
    """Whether the line is one row: a pipe at each end, and the last one is not an escaped cell."""
    text = line.strip()
    return len(text) > 1 and text.startswith("|") and text.endswith("|") and text[-2] != "\\"


def _align(rule: re.Match[str]) -> str:
    left, right = rule.groups()
    if left and right:
        return "center"
    return "left" if left else "right" if right else "none"


def _heading_at(lines: Sequence[str], heading: str) -> int | None:
    return next((i for i, line in enumerate(lines) if line.rstrip(" \t") == heading), None)


def _section_end(lines: Sequence[str], heading_index: int) -> int:
    """The line of the next ``## `` heading, or the end of the text."""
    return next(
        (i for i in range(heading_index + 1, len(lines)) if lines[i].startswith(SECTION_START)),
        len(lines),
    )


def table(text: str, heading: str) -> Table | None:
    """The first table under ``heading``, or ``None`` when there is none or it does not split."""
    lines = text.split("\n")
    at = _heading_at(lines, heading)
    if at is None:
        return None
    end = _section_end(lines, at)
    first = next((i for i in range(at + 1, end) if lines[i].startswith("|")), None)
    if first is None:
        return None
    last = first
    while last < end and lines[last].startswith("|"):
        last += 1
    block = lines[first:last]
    split = [_cells(line) for line in block]
    if len(block) < MIN_TABLE_LINES or not all(map(_clean, block)):
        return None
    rules = [RULE_CELL.fullmatch(cell) for cell in split[1]]
    if any(len(row) != len(split[0]) for row in split) or not all(rules):
        return None
    aligns = tuple(_align(rule) for rule in rules if rule)
    return Table(split[0], aligns, tuple(split[2:]), first, last, tuple(block))


def _place(keys: Sequence[str], key: str, rank: Mapping[str, int]) -> tuple[int, bool] | None:
    """Where ``key`` goes among ``keys``, a neighbour and whether it goes after it.

    After the nearest present key the template puts before it, else before the nearest present
    key it puts after it, else nowhere in particular (``None``). Keys the template does not rank
    are not neighbours.
    """
    mine = rank[key]
    present = [(rank[k], i) for i, k in enumerate(keys) if k in rank]
    below = max(((r, i) for r, i in present if r < mine), default=None)
    if below is not None:
        return below[1], True
    above = min(((r, i) for r, i in present if r > mine), default=None)
    return None if above is None else (above[1], False)


def _ranks(order: Sequence[str], key: str) -> dict[str, int]:
    if key not in order:
        message = f"{key!r} is not in the template's order {tuple(order)!r}"
        raise ValueError(message)
    return {name: place for place, name in enumerate(order)}


def with_row(heading: str, row: str, order: Sequence[str]) -> Want:
    """The want of a table with the template's ``row``, keyed by its first cell.

    ``order`` is the template table's first cells in the template's order. A row with that first
    cell is never touched. A table that does not split cleanly, or whose columns are not the
    row's, stays as it is.
    """
    new = _cells(row)
    rank = _ranks(order, new[0])

    def want(current: str | None) -> str:
        text = current or ""
        found = table(text, heading)
        if found is None or len(found.header) != len(new):
            return text
        keys = [cells[0] for cells in found.rows]
        if new[0] in keys:
            return text
        place = _place(keys, new[0], rank)
        index = 0 if place is None else place[0] + place[1]
        lines = text.split("\n")
        if found.padded:
            rows = [*found.rows[:index], new, *found.rows[index:]]
            lines[found.first : found.last] = found.render(rows)
        else:
            lines.insert(found.first + 2 + index, row)
        return "\n".join(lines)

    return want


def with_section(heading: str, block: str, order: Sequence[str]) -> Want:
    """The want of a file holding the template's ``## `` section ``block``, heading line first.

    ``order`` is the template's ``## `` headings in order. The block goes after the nearest
    present section the template puts before it, else before the nearest present one it puts
    after it, else at the end. A section with that heading already there is never touched.
    """
    rank = _ranks(order, heading)
    lines_of_block = block.rstrip("\n").split("\n")
    if lines_of_block[0] != heading:
        message = f"the block must start with its heading {heading!r}"
        raise ValueError(message)

    def want(current: str | None) -> str:
        text = current or ""
        if not text.strip():
            return block.rstrip("\n") + "\n"
        lines = text.split("\n")
        at = {name: i for name in order if (i := _heading_at(lines, name)) is not None}
        if heading in at:
            return text
        keys = sorted(at, key=at.__getitem__)
        place = _place(keys, heading, rank)
        if place is not None and not place[1]:
            start = at[keys[place[0]]]
            blank = [""] if start and lines[start - 1].strip() else []
            lines[start:start] = [*blank, *lines_of_block, ""]
            return "\n".join(lines)
        end = len(lines) if place is None else _section_end(lines, at[keys[place[0]]])
        while not lines[end - 1].strip():
            end -= 1
        lines[end:end] = ["", *lines_of_block]
        return "\n".join(lines)

    return want
