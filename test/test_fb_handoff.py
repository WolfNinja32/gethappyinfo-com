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
REEL = (f"{LINE}\n\nSend this to someone who needs it today ✉\n\n#kindness #gethappy\n")
PORTRAIT_TXT = (f"{LINE}\n\nPass it on ✉\nhttps://gethappyinfo.com/2026-10-01\n\n"
                "Image: https://gethappyinfo.com/share-vertical.png\n"
                "Portrait: https://gethappyinfo.com/art/2026-10-01-portrait.png\n")


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

    def exists(self, name):
        return (self.root / "public" / "fb" / name).exists()

    def test_portrait_line_after_image_then_reel_section_when_file_exists(self):
        self.touch_portrait("2026-10-01")
        self.run_build()
        expected = PORTRAIT_TXT + "\nReel caption:\n" + REEL
        self.assertEqual(self.read("today.txt"), expected)
        self.assertEqual(self.read("2026-10-01.txt"), expected)
        self.assertEqual(self.read("today.reel.txt"), REEL)
        self.assertEqual(self.read("2026-10-01.reel.txt"), REEL)

    def test_reel_section_is_last_and_parseable(self):
        self.touch_portrait("2026-10-01")
        self.run_build()
        text = self.read("today.txt")
        head, sep, reel = text.partition("\nReel caption:\n")
        self.assertEqual(sep, "\nReel caption:\n")
        self.assertEqual(head, PORTRAIT_TXT)  # existing lines unchanged; blank line, then header
        self.assertEqual(reel, self.read("today.reel.txt"))
        self.assertEqual(text.count("Reel caption:"), 1)
        self.assertTrue(text.endswith("#kindness #gethappy\n"))
        self.assertFalse(text.endswith("\n\n"))

    def test_no_reel_without_portrait(self):
        self.run_build()
        self.assertNotIn("Reel caption:", self.read("today.txt"))
        self.assertFalse(self.exists("today.reel.txt"))
        self.assertFalse(self.exists("2026-10-01.reel.txt"))

    def test_reel_override_used_verbatim_and_normalized(self):
        self.touch_portrait("2026-10-01")
        (self.root / "fb" / "captions" / "2026-10-01.reel.txt").write_bytes(
            "\r\nCustom reel  \r\n\r\n#one #two\t\r\n\r\n".encode("utf-8"))
        self.run_build()
        self.assertEqual(self.read("today.reel.txt"), "Custom reel\n\n#one #two\n")
        self.assertTrue(self.read("today.txt").endswith(
            "Portrait: https://gethappyinfo.com/art/2026-10-01-portrait.png\n\n"
            "Reel caption:\nCustom reel\n\n#one #two\n"))
        self.assertTrue(self.read("today.txt").startswith(LINE + "\n\n"))  # photo caption unaffected

    def test_empty_reel_override_falls_back_to_template(self):
        self.touch_portrait("2026-10-01")
        (self.root / "fb" / "captions" / "2026-10-01.reel.txt").write_text(" \n\n", encoding="utf-8")
        self.run_build()
        self.assertEqual(self.read("today.reel.txt"), REEL)

    def test_photo_override_does_not_change_reel_default(self):
        self.touch_portrait("2026-10-01")
        (self.root / "fb" / "captions" / "2026-10-01.txt").write_text("Photo only", encoding="utf-8")
        self.run_build()
        self.assertTrue(self.read("today.txt").startswith("Photo only\n\n"))
        self.assertEqual(self.read("today.reel.txt"), REEL)

    def test_reel_fails_without_joy_line(self):
        self.write_joy("2026-10-01", "")
        (self.root / "fb" / "captions" / "2026-10-01.txt").write_text("Photo only", encoding="utf-8")
        self.touch_portrait("2026-10-01")
        with self.assertRaises(SystemExit):
            self.run_build()
        self.assertFalse((self.root / "public" / "fb").exists())  # nothing half-written

    def test_reel_override_ok_without_joy_line(self):
        self.write_joy("2026-10-01", "")
        (self.root / "fb" / "captions" / "2026-10-01.txt").write_text("Photo only", encoding="utf-8")
        (self.root / "fb" / "captions" / "2026-10-01.reel.txt").write_text("Reel only", encoding="utf-8")
        self.touch_portrait("2026-10-01")
        self.run_build()
        self.assertEqual(self.read("today.reel.txt"), "Reel only\n")

    def test_stale_today_reel_removed_when_no_portrait(self):
        self.touch_portrait("2026-10-01")
        self.run_build()
        self.write_joy("2026-10-02", "Tomorrow's line.")  # no portrait for 10-02
        self.assertEqual(self.run_build(check=True), 1)
        self.assertTrue(self.exists("today.reel.txt"))  # --check writes/deletes nothing
        self.run_build()
        self.assertFalse(self.exists("today.reel.txt"))
        self.assertEqual(self.read("2026-10-01.reel.txt"), REEL)  # dated file kept
        self.assertNotIn("Reel caption:", self.read("today.txt"))
        self.assertEqual(self.run_build(check=True), 0)

    def test_reel_override_change_makes_check_stale(self):
        self.touch_portrait("2026-10-01")
        self.run_build()
        self.assertEqual(self.run_build(check=True), 0)
        (self.root / "fb" / "captions" / "2026-10-01.reel.txt").write_text("New", encoding="utf-8")
        self.assertEqual(self.run_build(check=True), 1)

    def test_dry_run_reel_out(self):
        self.touch_portrait("2026-10-01")
        dest = self.root / "scratch" / "today.reel.txt"
        buf = io.StringIO()
        with redirect_stderr(io.StringIO()):
            fb.build(self.root, dry_run=True, out=buf, reel_out=dest)
        self.assertEqual(dest.read_text(encoding="utf-8"), REEL)
        self.assertTrue(buf.getvalue().endswith("Reel caption:\n" + REEL))
        self.assertFalse((self.root / "public" / "fb").exists())

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
        self.assertIn("Portrait: https://gethappyinfo.com/art/2026-10-01-portrait.png\n\n"
                      "Reel caption:\n", buf.getvalue())
        buf = io.StringIO()
        with redirect_stderr(io.StringIO()):
            fb.build(self.root, dry_run=True, out=buf)  # default public/art has none
        self.assertNotIn("Portrait:", buf.getvalue())
        self.assertNotIn("Reel caption:", buf.getvalue())

    def test_backfill_uses_that_dates_portrait(self):
        self.touch_portrait("2026-09-30")
        (self.root / "fb" / "captions" / "2026-09-30.txt").write_text("Old caption", encoding="utf-8")
        (self.root / "fb" / "captions" / "2026-09-30.reel.txt").write_text("Old reel", encoding="utf-8")
        self.run_build(date="2026-09-30")
        self.assertIn("Portrait: https://gethappyinfo.com/art/2026-09-30-portrait.png\n",
                      self.read("2026-09-30.txt"))
        self.assertEqual(self.read("2026-09-30.reel.txt"), "Old reel\n")
        self.assertFalse(self.exists("today.reel.txt"))  # back-fill never touches today.*

    def test_backfill_with_portrait_but_no_reel_caption_fails(self):
        self.touch_portrait("2026-09-30")
        (self.root / "fb" / "captions" / "2026-09-30.txt").write_text("Old caption", encoding="utf-8")
        with self.assertRaises(SystemExit):  # never invents a reel caption from another day's joy
            self.run_build(date="2026-09-30")

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



class LandscapeLineTest(unittest.TestCase):
    """The Landscape: line (1200x630 art/<date>-landscape.png)."""
    setUp, tearDown, write_joy, run_build, read, touch_portrait, exists = (
        FbHandoffTest.setUp, FbHandoffTest.tearDown, FbHandoffTest.write_joy,
        FbHandoffTest.run_build, FbHandoffTest.read, FbHandoffTest.touch_portrait,
        FbHandoffTest.exists)
    LS = "Landscape: https://gethappyinfo.com/art/2026-10-01-landscape.png\n"

    def touch_landscape(self, date, base=None):
        art = (base or self.root / "public" / "art")
        art.mkdir(parents=True, exist_ok=True)
        (art / f"{date}-landscape.png").write_bytes(b"png")

    def test_landscape_directly_after_portrait_before_reel_block(self):
        self.touch_portrait("2026-10-01")
        self.touch_landscape("2026-10-01")
        self.run_build()
        expected = PORTRAIT_TXT + self.LS + "\nReel caption:\n" + REEL
        self.assertEqual(self.read("today.txt"), expected)
        self.assertEqual(self.read("2026-10-01.txt"), expected)
        lines = self.read("today.txt").split("\n")
        i = lines.index(self.LS.strip())
        self.assertTrue(lines[i - 1].startswith("Portrait: "))
        self.assertEqual(lines[i + 1], "")
        self.assertEqual(lines[i + 2], "Reel caption:")
        # reel caption outputs are unchanged by the landscape
        self.assertEqual(self.read("today.reel.txt"), REEL)
        head, _, reel = self.read("today.txt").partition("\nReel caption:\n")
        self.assertEqual(reel, REEL)

    def test_no_landscape_file_no_line(self):
        self.touch_portrait("2026-10-01")
        self.run_build()
        self.assertNotIn("Landscape:", self.read("today.txt"))
        self.assertEqual(self.read("today.txt"), PORTRAIT_TXT + "\nReel caption:\n" + REEL)

    def test_other_dates_landscape_does_not_count(self):
        self.touch_portrait("2026-10-01")
        self.touch_landscape("2026-09-30")
        self.run_build()
        self.assertNotIn("Landscape:", self.read("today.txt"))

    def test_landscape_without_portrait_follows_image(self):
        self.touch_landscape("2026-10-01")
        self.run_build()
        self.assertEqual(self.read("today.txt"),
                         f"{LINE}\n\nPass it on ✉\nhttps://gethappyinfo.com/2026-10-01\n\n"
                         "Image: https://gethappyinfo.com/share-vertical.png\n" + self.LS)
        self.assertFalse(self.exists("today.reel.txt"))

    def test_line_disappears_when_file_removed(self):
        # landscape failure path: file deleted, rebuild drops the line, rest identical
        self.touch_portrait("2026-10-01")
        self.touch_landscape("2026-10-01")
        self.run_build()
        (self.root / "public" / "art" / "2026-10-01-landscape.png").unlink()
        self.assertEqual(self.run_build(check=True), 1)  # stale: line must go
        self.run_build()
        self.assertEqual(self.read("today.txt"), PORTRAIT_TXT + "\nReel caption:\n" + REEL)
        self.assertEqual(self.run_build(check=True), 0)

    def test_art_dir_override_finds_landscape_for_dry_run(self):
        other = self.root / "elsewhere" / "art"
        self.touch_portrait("2026-10-01", base=other)
        self.touch_landscape("2026-10-01", base=other)
        buf = io.StringIO()
        with redirect_stderr(io.StringIO()):
            fb.build(self.root, dry_run=True, out=buf, art_dir=other)
        self.assertEqual(buf.getvalue(), PORTRAIT_TXT + self.LS + "\nReel caption:\n" + REEL)
        self.assertFalse((self.root / "public" / "fb").exists())

if __name__ == "__main__":
    unittest.main()
