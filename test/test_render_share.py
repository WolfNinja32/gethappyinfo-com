#!/usr/bin/env python3
"""Tests for scripts/render_share.py (run: python -m unittest test.test_render_share).

Stdlib-only checks always run; the full render runs only if Pillow + cairosvg
are installed (pip install -r render/requirements.txt).
"""
from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "scripts"))
import render_share as rs  # noqa: E402

HAVE_DEPS = all(importlib.util.find_spec(m) for m in ("PIL", "cairosvg"))


class StampAndFingerprintTest(unittest.TestCase):
    def test_stamp_extracted_from_index(self):
        svg = rs.extract_stamp_svg((REPO / "public" / "index.html").read_text(encoding="utf-8"))
        self.assertTrue(svg.startswith('<svg viewBox="0 0 112 132" xmlns="http://www.w3.org/2000/svg">'))
        self.assertTrue(svg.endswith("</svg>"))
        self.assertNotIn("stamp-svg", svg)
        self.assertNotIn("aria-hidden", svg)
        self.assertIn("GET HAPPY", svg)
        self.assertNotIn("postmark", svg)  # stops at the stamp's own </svg>

    def test_missing_stamp_raises(self):
        with self.assertRaises(RuntimeError):
            rs.extract_stamp_svg("<html></html>")

    def test_fingerprint_tracks_render_inputs_only(self):
        a = {"date": "2026-10-01", "line": "L", "paragraph": "P"}
        self.assertEqual(rs.fingerprint(a), rs.fingerprint(dict(a, unrelated=1)))
        self.assertNotEqual(rs.fingerprint(a), rs.fingerprint(dict(a, line="L2")))
        self.assertNotEqual(rs.fingerprint(a), rs.fingerprint(dict(a, date="2026-10-02")))
        self.assertNotEqual(rs.fingerprint(a), rs.fingerprint(dict(a, paragraph="P2")))

    def test_pinned_fonts_present(self):
        for p in rs.REQUIRED_FONTS:
            self.assertTrue(p.is_file(), p)


@unittest.skipUnless(HAVE_DEPS, "render deps not installed")
class RenderTest(unittest.TestCase):
    def test_render_1080_square_both_files_identical(self):
        from PIL import Image
        with tempfile.TemporaryDirectory() as d:
            out = Path(d)
            rs.render(REPO / "public" / "joy.json", REPO / "public" / "index.html", out)
            sv, fb = out / "share-vertical.png", out / "fb-share.png"
            self.assertEqual(sv.read_bytes(), fb.read_bytes())
            with Image.open(sv) as im:
                self.assertEqual(im.size, (1080, 1080))
            date = json.loads((REPO / "public" / "joy.json").read_text(encoding="utf-8"))["date"]
            with Image.open(out / "art" / f"{date}-portrait.png") as im:
                self.assertEqual(im.size, (1080, 1920))

    def test_bad_joy_writes_nothing(self):
        with tempfile.TemporaryDirectory() as d:
            joy = Path(d) / "joy.json"
            joy.write_text(json.dumps({"date": "2026-10-01", "line": "", "paragraph": "p"}))
            with self.assertRaises(RuntimeError):
                rs.render(joy, REPO / "public" / "index.html", Path(d) / "out")
            self.assertFalse((Path(d) / "out").exists())


if __name__ == "__main__":
    unittest.main()
