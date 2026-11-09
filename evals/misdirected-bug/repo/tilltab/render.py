"""The plain-text invoice we email."""

from tilltab.invoice import Line, line_pence, total_pence
from tilltab.money import format_money


def render(lines: list[Line], tax_rate: str) -> str:
    rows = [
        f"{line.quantity} x {line.name}: {format_money(line_pence(line, tax_rate))}"
        for line in lines
    ]
    rows.append(f"Total: {format_money(total_pence(lines, tax_rate))}")
    return "\n".join(rows)
