"""Run: python -m unittest scripts/test_check_repeat_card.py"""
import sys, unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))
from check_repeat_card import find_repeat

PREV = {"line": "Help someone reach something on a high shelf.", "paragraph": "P1",
        "seed": {"sourceUrl": "https://x/hvac", "summary": "S1"}}


class T(unittest.TestCase):
    def test_fresh(self):
        self.assertEqual(find_repeat({"line": "Let a child win.", "paragraph": "P2",
                                      "seed": {"sourceUrl": "https://x/bolt", "summary": "S2"}}, PREV), [])

    def test_full_repeat(self):
        self.assertEqual(find_repeat(dict(PREV), PREV), ["line", "story"])

    def test_line_only(self):
        self.assertEqual(find_repeat({"line": " help someone reach something on a HIGH shelf. ",
                                      "seed": {"sourceUrl": "https://x/new"}}, PREV), ["line"])

    def test_story_only(self):
        self.assertEqual(find_repeat({"line": "New.", "seed": {"sourceUrl": "https://x/hvac"}}, PREV), ["story"])

    def test_no_prev(self):
        self.assertEqual(find_repeat({"line": "x"}, None), [])


if __name__ == "__main__":
    unittest.main()
