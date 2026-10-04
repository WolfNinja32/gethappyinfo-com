#!/usr/bin/env python3
"""Tests for scripts/render_portrait.py (run: python -m unittest test.test_render_portrait).

Needs the render deps (pip install -r render/requirements.txt); skipped otherwise.
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
import render_portrait as rp  # noqa: E402
import render_share as rs  # noqa: E402

HAVE_DEPS = all(importlib.util.find_spec(m) for m in ("PIL", "cairosvg"))

LINE = "Share your leftovers instead of letting them go to waste."
SHORT = "A neighbour left soup on the step. That was all it took."
# 2026-09-22 from joy-archive.json (637 chars, the longest so far): needs shrinking.
LONG = (
    "At Redgranite Correctional Institute in Wisconsin, inmates are raising monarch "
    "butterflies from eggs through migration. The program was designed for incarcerated "
    "people who cannot join sports or other physical activities. Men who might otherwise "
    "sit idle now tend delicate caterpillars, feeding them milkweed and watching wings form "
    "in chrysalises. When the monarchs are ready, they are tagged and released to fly "
    "south. The returning butterflies carry no knowledge of walls or sentencing, only the "
    "weight of long miles. Someone thought of the ones who are usually left out, and now "
    "thousands of orange wings trace the sky because of it."
)
ABSURD = " ".join([LONG] * 4)


def joy(paragraph: str, line: str = LINE, date: str = "2026-10-01") -> dict:
    return {"date": date, "line": line, "paragraph": paragraph,
            "seed": {"sourceUrl": "https://example.org/x", "sourceTitle": "X"}}


class LayoutConstantsTest(unittest.TestCase):
    def test_card_inside_reel_safe_zones(self):
        self.assertGreaterEqual(rp.CARD_Y0, 250)
        self.assertLessEqual(rp.CARD_Y1, 1920 - 350)
        self.assertLessEqual(rp.CARD_X1, 1080 - 120)
        self.assertGreaterEqual(rp.CARD_X0, 48)
        self.assertEqual((rp.W, rp.H), (1080, 1920))

    def test_font_ladder(self):
        self.assertEqual(list(rp.PARA_SIZES), sorted(rp.PARA_SIZES, reverse=True))
        self.assertGreaterEqual(rp.PARA_MIN, 28)
        self.assertGreaterEqual(min(rp.LINE_SIZES), 46)  # the line stays large
        self.assertGreater(min(rp.LINE_SIZES), max(rp.PARA_SIZES))


@unittest.skipUnless(HAVE_DEPS, "render deps not installed")
class AutoFitTest(unittest.TestCase):
    def test_short_paragraph_uses_largest_sizes(self):
        plan = rp.fit(joy(SHORT))
        self.assertEqual((plan["line_size"], plan["para_size"]), (rp.LINE_SIZES[0], rp.PARA_SIZES[0]))

    def test_long_paragraph_shrinks_and_reflows(self):
        big = rp.fit(joy(SHORT))
        plan = rp.fit(joy(LONG))
        self.assertLess(plan["para_size"], rp.PARA_SIZES[0])
        self.assertGreaterEqual(plan["para_size"], rp.PARA_MIN)
        self.assertLessEqual(plan["height"], rp.BODY_AVAIL)
        # reflowed: every wrapped line fits the text column at the chosen size
        probe = rs.ImageDraw.Draw(rs.Image.new("RGB", (8, 8)))
        for ln in plan["para_lines"]:
            self.assertLessEqual(probe.textlength(ln, font=plan["f_para"]), rp.TEXT_W)
        self.assertEqual(" ".join(plan["para_lines"]), " ".join(LONG.split()))  # nothing dropped
        self.assertGreater(len(plan["para_lines"]), len(big["para_lines"]))

    def test_absurd_paragraph_fails(self):
        with self.assertRaises(rp.PortraitFitError):
            rp.fit(joy(ABSURD))

    def test_unbreakable_word_fails(self):
        with self.assertRaises(rp.PortraitFitError):
            rp.fit(joy("x" * 200))

    def test_archive_days_fit(self):
        days = json.loads((REPO / "public" / "joy-archive.json").read_text(encoding="utf-8"))["days"]
        for date, day in days.items():
            if day.get("paragraph") and day.get("line"):
                with self.subTest(date=date):
                    rp.fit(day)


@unittest.skipUnless(HAVE_DEPS, "render deps not installed")
class PortraitRenderTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.stamp = rs.extract_stamp_svg((REPO / "public" / "index.html").read_text(encoding="utf-8"))

    def test_size_and_everything_light_inside_safe_zone(self):
        img = rp.render_portrait(joy(LONG), self.stamp)
        self.assertEqual(img.size, (1080, 1920))
        # Paper, stamp and text are light-ish (R>120); the table background and
        # card shadow are dark. Every light pixel must lie inside the safe zone.
        mask = img.convert("RGB").split()[0].point(lambda v: 255 if v > 120 else 0)
        x0, y0, x1, y1 = mask.getbbox()
        self.assertGreaterEqual(y0, 250)
        self.assertLessEqual(y1, 1920 - 350)
        self.assertLessEqual(x1, 1080 - 120)
        self.assertGreaterEqual(x0, 48)

    def test_render_share_writes_dated_portrait_only(self):
        from PIL import Image
        with tempfile.TemporaryDirectory() as d:
            out = Path(d)
            (out / "art").mkdir()
            other = out / "art" / "2026-09-30-portrait.png"
            other.write_bytes(b"keep me")
            j = out / "joy.json"
            j.write_text(json.dumps(joy(LONG)), encoding="utf-8")
            rs.render(j, REPO / "public" / "index.html", out)
            with Image.open(out / "art" / "2026-10-01-portrait.png") as im:
                self.assertEqual(im.size, (1080, 1920))
            self.assertEqual(other.read_bytes(), b"keep me")
            self.assertEqual(sorted(p.name for p in (out / "art").iterdir()),
                             ["2026-09-30-portrait.png", "2026-10-01-portrait.png"])

    def test_portrait_overflow_writes_nothing(self):
        with tempfile.TemporaryDirectory() as d:
            out = Path(d) / "out"
            out.mkdir()
            (out / "share-vertical.png").write_bytes(b"previous")
            j = Path(d) / "joy.json"
            j.write_text(json.dumps(joy(ABSURD)), encoding="utf-8")
            with self.assertRaises(RuntimeError):  # PortraitFitError or share letterbox overflow
                rs.render(j, REPO / "public" / "index.html", out)
            self.assertEqual((out / "share-vertical.png").read_bytes(), b"previous")
            self.assertFalse((out / "art").exists())

    def test_portrait_fit_error_alone_blocks_share_images(self):
        # A paragraph short enough for the 1080 share card but too long for the portrait
        # must still leave the previous share images in place (one degrade path).
        orig = rp.fit
        def boom(_joy):
            raise rp.PortraitFitError("forced")
        rp.fit = boom
        try:
            with tempfile.TemporaryDirectory() as d:
                out = Path(d)
                (out / "share-vertical.png").write_bytes(b"previous")
                with self.assertRaises(rp.PortraitFitError):
                    rs.render(REPO / "public" / "joy.json", REPO / "public" / "index.html", out)
                self.assertEqual((out / "share-vertical.png").read_bytes(), b"previous")
                self.assertFalse((out / "fb-share.png").exists())
        finally:
            rp.fit = orig


if __name__ == "__main__":
    unittest.main()
