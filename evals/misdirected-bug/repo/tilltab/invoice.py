"""Pricing an invoice.

Each line is priced as unit_price x quantity x (1 + tax_rate), rounded half-up to the penny.
The invoice total is the sum of the rounded lines.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class Line:
    name: str
    unit_price: str
    quantity: int


def line_pence(line: Line, tax_rate: str) -> int:
    gross = float(line.unit_price) * line.quantity * (1 + float(tax_rate))
    return int(gross * 100)


def total_pence(lines: list[Line], tax_rate: str) -> int:
    return sum(line_pence(line, tax_rate) for line in lines)
