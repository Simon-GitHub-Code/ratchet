"""Markdown text, read and edited without a Markdown library: sections, tables and their rows."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, NamedTuple

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


def _headings_at(lines: Sequence[str], heading: str) -> list[int]:
    """Every line that is ``heading``: the whole line, less the whitespace after it, a CR too."""
    return [i for i, line in enumerate(lines) if line.rstrip() == heading]


def has_section(text: str, heading: str) -> bool:
    """Whether a line of the text is the heading, as every reader and writer here finds it."""
    return bool(_headings_at(text.split("\n"), heading))


def section(text: str, heading: str) -> str:
    """What the ``heading`` sections hold, from the end of each heading line to the next ``## ``.

    Why: a text that repeats a heading holds the section's lines under every copy, so a reader
    that took the first copy alone would miss the table an old edit left below another.
    """
    lines = text.split("\n")
    bodies = [
        "\n".join(lines[at + 1 : _section_end(lines, at)]) for at in _headings_at(lines, heading)
    ]
    return "".join(f"\n{body}" for body in bodies)


def empty_body(text: str, heading: str) -> bool:
    """Whether the section under ``heading`` says nothing: absent, blank, placeholder or short."""
    body = section(text, heading).strip()
    return body.lower().rstrip(".") in EMPTY_BODIES or len(body) < MIN_BODY


def table_cells(body: str) -> list[tuple[str, str]]:
    """The first two cells of every row of every table in ``body``; an empty second cell is kept.

    A table's header is the row above its rule row, so a section that holds two tables gives the
    rows of both and neither header.
    """
    matches = [TABLE_ROW.match(line) for line in body.splitlines()]
    rules = [match is not None and set(match.group(1)) <= set("-: ") for match in matches]
    return [
        (match.group(1), match.group(2))
        for i, match in enumerate(matches)
        if match is not None and not rules[i] and not (i + 1 < len(rules) and rules[i + 1])
    ]


@dataclass(frozen=True)
class Table:
    """The first table under a heading: its cells, its alignment and where its lines sit."""

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
    return next(iter(_headings_at(lines, heading)), None)


def _section_end(lines: Sequence[str], heading_index: int) -> int:
    """The line of the next ``## `` heading, or the end of the text."""
    return next(
        (i for i in range(heading_index + 1, len(lines)) if lines[i].startswith(SECTION_START)),
        len(lines),
    )


def _table_at(lines: Sequence[str], first: int, end: int) -> Table | None:
    """The table whose lines start at ``first``, or ``None`` when they do not split."""
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


def table(text: str, heading: str) -> Table | None:
    """The first table under ``heading``, or ``None`` when there is none or it does not split."""
    lines = text.split("\n")
    for at in _headings_at(lines, heading):
        end = _section_end(lines, at)
        first = next((i for i in range(at + 1, end) if lines[i].startswith("|")), None)
        if first is not None:
            return _table_at(lines, first, end)
    return None


def tables(text: str, heading: str) -> list[Table]:
    """Every table under ``heading`` that splits, in the order the text holds them."""
    lines = text.split("\n")
    found: list[Table] = []
    for at in _headings_at(lines, heading):
        end = _section_end(lines, at)
        line = at + 1
        while line < end:
            if not lines[line].startswith("|"):
                line += 1
                continue
            made = _table_at(lines, line, end)
            found += [made] if made else []
            while line < end and lines[line].startswith("|"):
                line += 1
    return found


BULLET = re.compile(r"^- ([^:\n]+):[ \t]*(.*?)[ \t]*$", re.MULTILINE)
TICKED = re.compile(r"`([^`\n]+)`")


def bullets(body: str) -> dict[str, str]:
    """The ``- Label: value`` lines of a section body, by label; the first line of a label wins."""
    found: dict[str, str] = {}
    for label, value in BULLET.findall(body):
        found.setdefault(label.strip(), value)
    return found


def ticked(cell: str) -> list[str]:
    """The backticked items of a cell or a bullet's value, in order."""
    return [item.strip() for item in TICKED.findall(cell) if item.strip()]


def padded(text: str, heading: str) -> str:
    """The text with the first table under ``heading`` padded, as a formatter writes it."""
    found = table(text, heading)
    if found is None:
        return text
    lines = text.split("\n")
    lines[found.first : found.last] = found.render(found.rows)
    return "\n".join(lines)


def padded_tables(text: str) -> str:
    """The text with every table that splits padded, as a formatter writes it."""
    lines = text.split("\n")
    at = 0
    while at < len(lines):
        if not lines[at].startswith("|"):
            at += 1
            continue
        found = _table_at(lines, at, len(lines))
        if found:
            lines[found.first : found.last] = found.render(found.rows)
            at = found.last
            continue
        while at < len(lines) and lines[at].startswith("|"):
            at += 1
    return "\n".join(lines)


class _Neighbour(NamedTuple):
    """The key a new key goes beside: its position among the keys, and whether it goes after."""

    beside: int
    after: bool


def _place(keys: Sequence[str], key: str, rank: Mapping[str, int]) -> _Neighbour | None:
    """Where ``key`` goes among ``keys``, a neighbour and whether it goes after it."""
    mine = rank[key]
    present = [(rank[k], i) for i, k in enumerate(keys) if k in rank]
    below = max(((r, i) for r, i in present if r < mine), default=None)
    if below is not None:
        return _Neighbour(below[1], after=True)
    above = min(((r, i) for r, i in present if r > mine), default=None)
    return None if above is None else _Neighbour(above[1], after=False)


def _ranks(template_order: Sequence[str], key: str) -> dict[str, int]:
    if key not in template_order:
        message = f"{key!r} is not in the template's order {tuple(template_order)!r}"
        raise ValueError(message)
    return {name: place for place, name in enumerate(template_order)}


def with_row(heading: str, row: str, template_order: Sequence[str]) -> Want:
    """The want of a table with the template's ``row``, keyed by its first cell."""
    new = _cells(row)
    rank = _ranks(template_order, new[0])

    def want(current: str | None) -> str:
        text = current or ""
        found = table(text, heading)
        if found is None or len(found.header) != len(new):
            return text
        keys = [cells[0] for cells in found.rows]
        if new[0] in keys:
            return text
        place = _place(keys, new[0], rank)
        index = 0 if place is None else place.beside + place.after
        lines = text.split("\n")
        if found.padded:
            rows = [*found.rows[:index], new, *found.rows[index:]]
            lines[found.first : found.last] = found.render(rows)
        else:
            lines.insert(found.first + 2 + index, row)
        return "\n".join(lines)

    return want


def with_section(heading: str, block: str, template_order: Sequence[str]) -> Want:
    """The want of a file holding the template's ``## `` section ``block``, heading line first."""
    rank = _ranks(template_order, heading)
    lines_of_block = block.rstrip("\n").split("\n")
    if lines_of_block[0] != heading:
        message = f"the block must start with its heading {heading!r}"
        raise ValueError(message)

    def want(current: str | None) -> str:
        text = current or ""
        if not text.strip():
            return block.rstrip("\n") + "\n"
        lines = text.split("\n")
        at = {name: i for name in template_order if (i := _heading_at(lines, name)) is not None}
        if heading in at:
            return text
        keys = sorted(at, key=at.__getitem__)
        place = _place(keys, heading, rank)
        if place is not None and not place.after:
            start = at[keys[place.beside]]
            blank = [""] if start and lines[start - 1].strip() else []
            lines[start:start] = [*blank, *lines_of_block, ""]
            return "\n".join(lines)
        end = len(lines) if place is None else _section_end(lines, at[keys[place.beside]])
        while not lines[end - 1].strip():
            end -= 1
        lines[end:end] = ["", *lines_of_block]
        return "\n".join(lines)

    return want
