#!/usr/bin/env python3
"""Tests for scripts/build_fb_handoff.py (run: python -m unittest test.test_fb_handoff).

All writes happen in a temp scratch repo; the real public/fb/ is never touched.
"""
from __future__ import annotations

import io
import json
import sys
import tempfile
import unittest
from contextlib import redirect_stderr
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "scripts"))
import build_fb_handoff as fb  # noqa: E402

LINE = "Share your leftovers instead of letting them go to waste."


class FbHandoffTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        (self.root / "public").mkdir()
        (self.root / "fb" / "captions").mkdir(parents=True)
        self.write_joy("2026-10-01", LINE)

    def tearDown(self):
        self._tmp.cleanup()

    def write_joy(self, date, line):
        (self.root / "public" / "joy.json").write_text(
            json.dumps({"date": date, "line": line, "paragraph": "p"}), encoding="utf-8")

    def run_build(self, **kw):
        with redirect_stderr(io.StringIO()):
            return fb.build(self.root, out=io.StringIO(), **kw)

    def read(self, name):
        return (self.root / "public" / "fb" / name).read_bytes().decode("utf-8")

    def test_default_uses_joy_line_exact_format(self):
        self.assertEqual(self.run_build(), 0)
        expected = (f"{LINE}\n\nPass it on ✉\nhttps://gethappyinfo.com/2026-10-01\n\n"
                    "Image: https://gethappyinfo.com/share-vertical.png\n")
        self.assertEqual(self.read("today.txt"), expected)
        self.assertEqual(self.read("2026-10-01.txt"), expected)

    def touch_portrait(self, date, base=None):
        art = (base or self.root / "public" / "art")
        art.mkdir(parents=True, exist_ok=True)
        (art / f"{date}-portrait.png").write_bytes(b"png")

    def test_portrait_line_after_image_when_file_exists(self):
        self.touch_portrait("2026-10-01")
        self.run_build()
        expected = (f"{LINE}\n\nPass it on ✉\nhttps://gethappyinfo.com/2026-10-01\n\n"
                    "Image: https://gethappyinfo.com/share-vertical.png\n"
                    "Portrait: https://gethappyinfo.com/art/2026-10-01-portrait.png\n")
        self.assertEqual(self.read("today.txt"), expected)
        self.assertEqual(self.read("2026-10-01.txt"), expected)

    def test_no_portrait_line_without_file_for_that_date(self):
        self.touch_portrait("2026-09-30")  # another date's portrait doesn't count
        self.run_build()
        self.assertNotIn("Portrait:", self.read("today.txt"))
        self.assertTrue(self.read("today.txt").endswith("share-vertical.png\n"))

    def test_portrait_appearing_makes_check_stale(self):
        self.run_build()
        self.assertEqual(self.run_build(check=True), 0)
        self.touch_portrait("2026-10-01")
        self.assertEqual(self.run_build(check=True), 1)
        self.run_build()
        self.assertEqual(self.run_build(check=True), 0)

    def test_art_dir_override_for_dry_run(self):
        alt = self.root / "elsewhere" / "art"
        self.touch_portrait("2026-10-01", base=alt)
        buf = io.StringIO()
        with redirect_stderr(io.StringIO()):
            fb.build(self.root, dry_run=True, out=buf, art_dir=alt)
        self.assertTrue(buf.getvalue().endswith(
            "Portrait: https://gethappyinfo.com/art/2026-10-01-portrait.png\n"))
        buf = io.StringIO()
        with redirect_stderr(io.StringIO()):
            fb.build(self.root, dry_run=True, out=buf)  # default public/art has none
        self.assertNotIn("Portrait:", buf.getvalue())

    def test_backfill_uses_that_dates_portrait(self):
        self.touch_portrait("2026-09-30")
        (self.root / "fb" / "captions" / "2026-09-30.txt").write_text("Old caption", encoding="utf-8")
        self.run_build(date="2026-09-30")
        self.assertIn("Portrait: https://gethappyinfo.com/art/2026-09-30-portrait.png\n",
                      self.read("2026-09-30.txt"))

    def test_override_file_wins_and_is_normalized(self):
        (self.root / "fb" / "captions" / "2026-10-01.txt").write_bytes(
            "Line one  \r\nLine two\t\r\n\r\n  \r\n".encode("utf-8"))
        self.run_build()
        self.assertTrue(self.read("today.txt").startswith("Line one\nLine two\n\nPass it on ✉\n"))
        self.assertTrue(self.read("today.txt").endswith(".png\n"))
        self.assertFalse(self.read("today.txt").endswith("\n\n"))

    def test_empty_override_falls_back_to_line(self):
        (self.root / "fb" / "captions" / "2026-10-01.txt").write_text(" \n\n", encoding="utf-8")
        self.run_build()
        self.assertTrue(self.read("today.txt").startswith(LINE + "\n\n"))

    def test_idempotent_and_check(self):
        self.assertEqual(self.run_build(check=True), 1)  # nothing written yet
        self.run_build()
        mtime = (self.root / "public" / "fb" / "today.txt").stat().st_mtime_ns
        self.run_build()
        self.assertEqual((self.root / "public" / "fb" / "today.txt").stat().st_mtime_ns, mtime)
        self.assertEqual(self.run_build(check=True), 0)

    def test_other_dates_never_overwritten_and_today_flips(self):
        self.run_build()
        first = self.read("2026-10-01.txt")
        self.write_joy("2026-10-02", "Tomorrow's line.")
        self.run_build()
        self.assertEqual(self.read("2026-10-01.txt"), first)
        self.assertIn("/2026-10-02\n", self.read("today.txt"))

    def test_backfill_other_date_leaves_today_untouched(self):
        self.run_build()
        today = self.read("today.txt")
        (self.root / "fb" / "captions" / "2026-09-30.txt").write_text("Old caption", encoding="utf-8")
        self.run_build(date="2026-09-30")
        self.assertEqual(self.read("today.txt"), today)
        self.assertIn("/2026-09-30\n", self.read("2026-09-30.txt"))

    def test_other_date_without_caption_fails(self):
        with self.assertRaises(SystemExit):
            self.run_build(date="2026-09-30")

    def test_dry_run_writes_nothing(self):
        buf = io.StringIO()
        with redirect_stderr(io.StringIO()):
            fb.build(self.root, dry_run=True, out=buf)
        self.assertIn("Pass it on ✉", buf.getvalue())
        self.assertFalse((self.root / "public" / "fb").exists())


if __name__ == "__main__":
    unittest.main()
