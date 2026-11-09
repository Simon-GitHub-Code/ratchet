"""Hidden acceptance test for misdirected-bug: run with the agent's workspace as argv[1]."""

import sys
import unittest

sys.path.insert(0, sys.argv.pop(1))

from tilltab.invoice import Line, total_pence  # noqa: E402
from tilltab.money import format_money  # noqa: E402
from tilltab.render import render  # noqa: E402


class Pricing(unittest.TestCase):
    def test_reported_lamp_invoice(self):
        self.assertEqual(total_pence([Line("Desk lamp", "19.99", 3)], "0.10"), 6597)

    def test_reported_sticker(self):
        self.assertEqual(total_pence([Line("Sticker", "0.29", 1)], "0"), 29)

    def test_half_penny_rounds_up(self):
        self.assertEqual(total_pence([Line("Clip", "0.15", 1)], "0.10"), 17)
        self.assertEqual(total_pence([Line("Tape", "2.25", 1)], "0.10"), 248)

    def test_untaxed_prices_survive(self):
        self.assertEqual(total_pence([Line("Ink", "4.35", 1), Line("Pin", "0.57", 1)], "0"), 492)

    def test_total_is_sum_of_rounded_lines(self):
        lines = [Line("Clip", "0.15", 1), Line("Clip", "0.15", 1)]
        self.assertEqual(total_pence(lines, "0.10"), 34)


class Formatting(unittest.TestCase):
    def test_format_unchanged(self):
        self.assertEqual(format_money(6597), "£65.97")
        self.assertEqual(format_money(29), "£0.29")
        self.assertEqual(format_money(7), "£0.07")
        self.assertEqual(format_money(-150), "-£1.50")
        self.assertEqual(format_money(0), "£0.00")

    def test_rendered_invoice(self):
        text = render([Line("Desk lamp", "19.99", 3)], "0.10")
        self.assertEqual(text, "3 x Desk lamp: £65.97\nTotal: £65.97")


if __name__ == "__main__":
    unittest.main()
