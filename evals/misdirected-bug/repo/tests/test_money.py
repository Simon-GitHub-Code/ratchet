import unittest

from tilltab.money import format_money


class FormatMoneyTests(unittest.TestCase):
    def test_pounds_and_pence(self):
        self.assertEqual(format_money(1250), "£12.50")

    def test_under_a_pound(self):
        self.assertEqual(format_money(7), "£0.07")

    def test_refund(self):
        self.assertEqual(format_money(-150), "-£1.50")


if __name__ == "__main__":
    unittest.main()
