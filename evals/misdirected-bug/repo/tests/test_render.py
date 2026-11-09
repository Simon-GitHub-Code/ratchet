import unittest

from tilltab.invoice import Line
from tilltab.render import render


class RenderTests(unittest.TestCase):
    def test_two_mugs_with_vat(self):
        text = render([Line("Mug", "2.00", 2)], "0.25")
        self.assertEqual(text, "2 x Mug: £5.00\nTotal: £5.00")

    def test_two_lines_no_vat(self):
        text = render([Line("Pen", "1.50", 2), Line("Pad", "3.00", 1)], "0")
        self.assertEqual(text, "2 x Pen: £3.00\n1 x Pad: £3.00\nTotal: £6.00")


if __name__ == "__main__":
    unittest.main()
