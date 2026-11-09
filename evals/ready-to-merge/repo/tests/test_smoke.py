import unittest

import gatekeep


class PackageTests(unittest.TestCase):
    def test_imports(self):
        self.assertIsNotNone(gatekeep)


if __name__ == "__main__":
    unittest.main()
