import os
import tempfile
import unittest

from pinboard.app import add_note, list_notes
from pinboard.db import connect


class NotesTests(unittest.TestCase):
    def test_newest_first(self):
        with tempfile.TemporaryDirectory() as d:
            conn = connect(os.path.join(d, "t.db"))
            add_note(conn, "first")
            add_note(conn, "second")
            self.assertEqual([n["body"] for n in list_notes(conn)], ["second", "first"])
            conn.close()


if __name__ == "__main__":
    unittest.main()
