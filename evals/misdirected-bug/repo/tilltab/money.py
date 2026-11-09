"""Turning pence into the text a customer reads."""


def format_money(pence: int) -> str:
    """Format an amount in pence as pounds, e.g. 6597 -> '£65.97' and -150 -> '-£1.50'."""
    sign = "-" if pence < 0 else ""
    pounds, rest = divmod(abs(pence), 100)
    return f"{sign}£{pounds}.{rest:02d}"
