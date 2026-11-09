import time
import unittest

from shelfmark.export import Bookmark, export

BOOKMARKS = [
    Bookmark("https://docs.python.org", "Python docs", ("python", "web")),
    Bookmark("https://peps.python.org", "PEPs", ("python",)),
]


class ExportTests(unittest.TestCase):
    def test_stamped_with_the_current_time(self):
        self.assertLess(abs(export(BOOKMARKS)["generated_at"] - time.time()), 2)

    def test_tags_in_order(self):
        self.assertEqual(export(BOOKMARKS)["tags"], ["python", "web"])

    def test_bookmarks_keep_their_tags(self):
        self.assertEqual(export(BOOKMARKS)["bookmarks"][1]["tags"], ["python"])


if __name__ == "__main__":
    unittest.main()
