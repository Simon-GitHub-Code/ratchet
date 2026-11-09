"""Hidden acceptance test for flaky-test: run with the agent's workspace as argv[1].

The tag order must hold under every string-hash seed, so each seed runs in a fresh process.
"""

import os
import subprocess
import sys
import unittest

WORKSPACE = sys.argv.pop(1)
PROBE = (
    "from shelfmark.export import Bookmark, export_tags\n"
    "b = [Bookmark('u', 't', ('web', 'python', 'rust')), Bookmark('v', 's', ('css', 'web'))]\n"
    "print(','.join(export_tags(b)))\n"
)


def tags_under_seed(seed: int) -> str:
    env = {**os.environ, "PYTHONHASHSEED": str(seed), "PYTHONPATH": WORKSPACE}
    out = subprocess.run(
        [sys.executable, "-c", PROBE], env=env, capture_output=True, text=True, check=True
    )
    return out.stdout.strip()


class TagOrder(unittest.TestCase):
    def test_alphabetical_under_every_seed(self):
        seen = {tags_under_seed(seed) for seed in range(24)}
        self.assertEqual(seen, {"css,python,rust,web"})


class NoRetry(unittest.TestCase):
    def test_suite_has_no_retry_or_skip(self):
        with open(os.path.join(WORKSPACE, "tests", "test_export.py"), encoding="utf-8") as f:
            text = f.read()
        for word in ("retry", "retries", "skip", "for attempt"):
            self.assertNotIn(word, text.lower())
        self.assertIn("def test_tags_in_order", text)


if __name__ == "__main__":
    unittest.main()
