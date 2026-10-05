#!/usr/bin/env python3
"""Tests for scripts/render_landscape.py + scripts/landscape_step.sh
(run: python -m unittest test.test_render_landscape).

Needs the render deps (pip install -r render/requirements.txt) for the render /
fit / step tests; skipped otherwise. All writes happen in temp dirs.
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "scripts"))
import render_landscape as rl  # noqa: E402
import render_share as rs  # noqa: E402

HAVE_DEPS = all(importlib.util.find_spec(m) for m in ("PIL", "cairosvg"))
ARCHIVE = json.loads((REPO / "public" / "joy-archive.json").read_text(encoding="utf-8"))["days"]

LINE = "Share your leftovers instead of letting them go to waste."
SHORT = "A neighbour left soup on the step. That was all it took."
LONG = ARCHIVE["2026-09-22"]["paragraph"]           # 637 chars, longest so far
ABSURD = " ".join([LONG] * 3)
LONG_LINE = ("Call someone you have not spoken to in a long while, tell them one specific thing "
             "you remember about them, and listen for as long as they want to talk.")


def joy(paragraph=SHORT, line=LINE, date="2026-10-01", seed="default"):
    j = {"date": date, "line": line, "paragraph": paragraph}
    if seed == "default":
        j["seed"] = {"sourceUrl": "https://www.example.org/story/1", "sourceTitle": "A Kind Story"}
    elif seed is not None:
        j["seed"] = seed
    return j


class ConstantsAndSourceTest(unittest.TestCase):
    def test_canvas_and_safe_box(self):
        self.assertEqual((rl.W, rl.H), (1200, 630))
        self.assertGreaterEqual(rl.SAFE_X0, 60)
        self.assertLessEqual(rl.SAFE_X1, 1200 - 60)
        self.assertGreaterEqual(rl.SAFE_Y0, 24)
        self.assertLessEqual(rl.SAFE_Y1, 630 - 24)

    def test_two_column_grid_matches_site_ratio(self):
        # .letter { grid-template-columns: 1fr 1.5px 1.2fr }
        self.assertAlmostEqual(rl.RIGHT_W / rl.LEFT_W, 1.2, delta=0.01)
        self.assertEqual(rl.LEFT_X + rl.LEFT_W + rl.COL_GAP, rl.DIV_X)
        self.assertEqual(rl.RIGHT_X + rl.RIGHT_W, rl.X1)

    def test_font_ladders(self):
        self.assertEqual(list(rl.PARA_SIZES), sorted(rl.PARA_SIZES, reverse=True))
        self.assertEqual(list(rl.LINE_SIZES), sorted(rl.LINE_SIZES, reverse=True))
        self.assertGreaterEqual(rl.PARA_MIN, 16)       # legible minimum at 1200 wide
        self.assertGreater(rl.LINE_MIN, max(rl.PARA_SIZES))

    def test_source_text_from_seed_only(self):
        self.assertEqual(rl.source_text(joy()), "Source: A Kind Story · example.org")
        self.assertEqual(rl.source_text(joy(seed={"sourceTitle": "  Only   Title "})), "Source: Only Title")
        self.assertEqual(rl.source_text(joy(seed={"sourceUrl": "https://news.example.com/x"})),
                         "Source: news.example.com")
        self.assertIsNone(rl.source_text(joy(seed=None)))
        self.assertIsNone(rl.source_text(joy(seed={})))
        self.assertIsNone(rl.source_text(joy(seed={"sourceUrl": "javascript:alert(1)"})))
        self.assertIsNone(rl.source_text(joy(seed={"sourceUrl": "not a url"})))

    def test_render_share_does_not_import_landscape(self):
        # isolation: the share/portrait render can never be broken by this module
        self.assertNotIn("render_landscape", (REPO / "scripts" / "render_share.py").read_text(encoding="utf-8"))
        self.assertNotIn("render_landscape", (REPO / "scripts" / "render_portrait.py").read_text(encoding="utf-8"))


class WorkflowWiringTest(unittest.TestCase):
    def test_landscape_is_its_own_optional_step(self):
        wf = (REPO / ".github" / "workflows" / "daily.yml").read_text(encoding="utf-8")
        i = wf.index("- name: Render landscape postcard (optional)")
        step = wf[i:wf.index("- name:", i + 10)]
        self.assertIn("id: landscape", step)
        self.assertIn("if: steps.render.outcome == 'success'", step)
        self.assertIn("continue-on-error: true", step)
        self.assertIn("bash scripts/landscape_step.sh", step)
        # runs after the share render, before the commit; share step itself unchanged
        self.assertLess(wf.index("id: render\n"), i)
        self.assertLess(i, wf.index("- name: Commit if changed"))
        share = wf[wf.index("- name: Render share images + FB handoff"):wf.index("# Optional 1200x630 landscape")]
        self.assertNotIn("landscape", share)
        # the failure gate only looks at the share render
        gate = wf[wf.index("- name: Fail visibly"):]
        self.assertNotIn("landscape", gate)
        # dry run renders the landscape into the artifact dir, failure tolerated
        dry = wf[wf.index("- name: Render (dry run"):wf.index("- name: Upload dry-run artifact")]
        self.assertIn('if python scripts/render_landscape.py --out-dir "$out"', dry)


@unittest.skipUnless(HAVE_DEPS, "render deps not installed")
class AutoFitTest(unittest.TestCase):
    def test_short_uses_largest_sizes(self):
        p = rl.fit(joy(SHORT))
        self.assertEqual((p["line_size"], p["para_size"]), (rl.LINE_SIZES[0], rl.PARA_SIZES[0]))

    def test_long_paragraph_shrinks_paragraph_only_and_reflows(self):
        p = rl.fit(joy(LONG))
        self.assertEqual(p["line_size"], rl.LINE_SIZES[0])
        self.assertLess(p["para_size"], rl.PARA_SIZES[0])
        self.assertGreaterEqual(p["para_size"], rl.PARA_MIN)
        self.assertLessEqual(p["right_h"], rl.BODY_AVAIL)
        probe = rs.ImageDraw.Draw(rs.Image.new("RGB", (8, 8)))
        for ln in p["para_lines"]:
            self.assertLessEqual(probe.textlength(ln, font=p["f_para"]), rl.RIGHT_W)
        self.assertEqual(" ".join(p["para_lines"]), " ".join(LONG.split()))  # nothing dropped
        self.assertEqual(" ".join(p["src_lines"]), rl.source_text(joy(LONG)))  # source wraps, never cut

    def test_long_line_steps_line_down(self):
        p = rl.fit(joy(SHORT, line=LONG_LINE))
        self.assertLess(p["line_size"], rl.LINE_SIZES[0])
        self.assertGreaterEqual(p["line_size"], rl.LINE_MIN)
        self.assertEqual(p["para_size"], rl.PARA_SIZES[0])
        self.assertEqual(" ".join(p["line_lines"]), LONG_LINE)

    def test_absurd_paragraph_fails(self):
        with self.assertRaises(rl.LandscapeFitError):
            rl.fit(joy(ABSURD))

    def test_absurd_line_fails(self):
        with self.assertRaises(rl.LandscapeFitError):
            rl.fit(joy(SHORT, line=" ".join([LONG_LINE] * 3)))

    def test_unbreakable_word_fails(self):
        with self.assertRaises(rl.LandscapeFitError):
            rl.fit(joy("x" * 120))

    def test_missing_fields_fail(self):
        with self.assertRaises(rl.LandscapeFitError):
            rl.fit({"date": "2026-10-01", "line": "L", "paragraph": ""})

    def test_every_archive_day_fits(self):
        sizes = {}
        for date, day in ARCHIVE.items():
            if day.get("paragraph") and day.get("line"):
                with self.subTest(date=date):
                    p = rl.fit(day)
                    sizes[date] = (p["line_size"], p["para_size"])
        self.assertGreaterEqual(len(sizes), 30)
        self.assertEqual(sizes["2026-09-22"][1], min(s[1] for s in sizes.values()))


@unittest.skipUnless(HAVE_DEPS, "render deps not installed")
class RenderTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.stamp = rs.extract_stamp_svg((REPO / "public" / "index.html").read_text(encoding="utf-8"))

    def check_safe(self, img):
        self.assertEqual(img.size, (1200, 630))
        for what, x0, y0, x1, y1 in img.info["landscape_boxes"]:
            with self.subTest(element=what):
                self.assertGreaterEqual(x0, rl.SAFE_X0)
                self.assertLessEqual(x1, rl.SAFE_X1)
                self.assertGreaterEqual(y0, rl.SAFE_Y0)
                self.assertLessEqual(y1, rl.SAFE_Y1)
        # light pixels (paper, text, stamp) only inside the card; edges stay table-dark
        mask = img.split()[0].point(lambda v: 255 if v > 120 else 0)
        x0, y0, x1, y1 = mask.getbbox()
        self.assertGreaterEqual(x0, rl.CARD_X0)
        self.assertGreaterEqual(y0, rl.CARD_Y0)
        self.assertLessEqual(x1, rl.CARD_X1)
        self.assertLessEqual(y1, rl.CARD_Y1)

    def test_long_story_inside_safe_margins(self):
        img = rl.render_landscape(joy(LONG), self.stamp)
        self.check_safe(img)
        kinds = {b[0] for b in img.info["landscape_boxes"]}
        self.assertTrue({"line", "para", "source", "stamp", "postmark", "button", "edition"} <= kinds)

    def test_long_line_inside_safe_margins(self):
        self.check_safe(rl.render_landscape(joy(SHORT, line=LONG_LINE), self.stamp))

    def test_no_seed_no_source_line(self):
        img = rl.render_landscape(joy(LONG, seed=None), self.stamp)
        self.assertNotIn("source", {b[0] for b in img.info["landscape_boxes"]})
        self.assertIsNone(img.info["landscape_plan"]["src"])

    def test_brand_red_button_is_exact(self):
        img = rl.render_landscape(joy(), self.stamp)
        bx0, by0, bx1, by1 = next(b[1:] for b in img.info["landscape_boxes"] if b[0] == "button")
        self.assertEqual(img.getpixel((int(bx0) + 4, int(by0) + 4)), rs.POSTAL)

    def test_cli_writes_dated_file_atomically(self):
        with tempfile.TemporaryDirectory() as d:
            j = Path(d) / "joy.json"
            j.write_text(json.dumps(joy(LONG)), encoding="utf-8")
            self.assertEqual(rl.main(["--joy", str(j), "--out-dir", d]), 0)
            self.assertEqual(sorted(p.name for p in (Path(d) / "art").iterdir()), ["2026-10-01-landscape.png"])
            from PIL import Image
            with Image.open(Path(d) / "art" / "2026-10-01-landscape.png") as im:
                self.assertEqual(im.size, (1200, 630))

    def test_cli_failure_writes_nothing_and_warns(self):
        with tempfile.TemporaryDirectory() as d:
            j = Path(d) / "joy.json"
            j.write_text(json.dumps(joy(ABSURD)), encoding="utf-8")
            summary = Path(d) / "summary.md"
            old = os.environ.get("GITHUB_STEP_SUMMARY")
            os.environ["GITHUB_STEP_SUMMARY"] = str(summary)
            try:
                from contextlib import redirect_stderr
                import io
                with redirect_stderr(io.StringIO()):
                    self.assertEqual(rl.main(["--joy", str(j), "--out-dir", d]), 1)
            finally:
                if old is None:
                    os.environ.pop("GITHUB_STEP_SUMMARY", None)
                else:
                    os.environ["GITHUB_STEP_SUMMARY"] = old
            self.assertFalse((Path(d) / "art" / "2026-10-01-landscape.png").exists())
            self.assertEqual(list((Path(d) / "art").glob("*")) if (Path(d) / "art").exists() else [], [])
            self.assertIn("Landscape postcard skipped", summary.read_text(encoding="utf-8"))


def sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


@unittest.skipUnless(HAVE_DEPS, "render deps not installed")
class StepIsolationTest(unittest.TestCase):
    """scripts/landscape_step.sh in a scratch copy of the repo, after a real share render."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        (self.root / "scripts").mkdir()
        for f in ("render_share.py", "render_portrait.py", "render_landscape.py",
                  "build_fb_handoff.py", "landscape_step.sh"):
            shutil.copy2(REPO / "scripts" / f, self.root / "scripts" / f)
        (self.root / "render").symlink_to(REPO / "render")
        (self.root / "public").mkdir()
        shutil.copy2(REPO / "public" / "index.html", self.root / "public" / "index.html")
        self.joy = json.loads((REPO / "public" / "joy.json").read_text(encoding="utf-8"))
        (self.root / "public" / "joy.json").write_text(json.dumps(self.joy), encoding="utf-8")
        self.date = self.joy["date"]
        self.summary = self.root / "summary.md"
        # the share step, exactly as the workflow runs it
        for cmd in (["scripts/render_share.py"], ["scripts/build_fb_handoff.py"]):
            subprocess.run([sys.executable, *cmd], cwd=self.root, check=True, capture_output=True)
        self.fb = self.root / "public" / "fb"
        self.art = self.root / "public" / "art"
        self.share_files = [self.root / "public" / "share-vertical.png", self.root / "public" / "fb-share.png",
                            self.art / f"{self.date}-portrait.png", self.fb / "today.reel.txt",
                            self.fb / f"{self.date}.reel.txt"]
        self.before = {p: sha(p) for p in self.share_files}
        self.today_before = (self.fb / "today.txt").read_text(encoding="utf-8")
        self.assertIn("Portrait: ", self.today_before)
        self.assertNotIn("Landscape:", self.today_before)

    def tearDown(self):
        self._tmp.cleanup()

    def run_step(self):
        env = dict(os.environ, PYTHON=sys.executable, GITHUB_STEP_SUMMARY=str(self.summary))
        return subprocess.run(["bash", "scripts/landscape_step.sh"], cwd=self.root, env=env,
                              capture_output=True, text=True)

    def break_renderer(self, write_file: bool):
        """Replace render_landscape.py with one that fails (optionally after writing)."""
        body = "import sys, pathlib\n"
        if write_file:
            body += (f"pathlib.Path('public/art/{self.date}-landscape.png').write_bytes(b'half')\n"
                     f"pathlib.Path('public/art/.{self.date}-landscape.png.tmp').write_bytes(b'tmp')\n")
        body += "raise SystemExit('LandscapeFitError: forced')\n"
        (self.root / "scripts" / "render_landscape.py").write_text(body, encoding="utf-8")

    def assert_share_intact(self):
        for p, h in self.before.items():
            with self.subTest(file=p.name):
                self.assertEqual(sha(p), h)
        self.assertEqual((self.fb / "today.txt").read_text(encoding="utf-8"), self.today_before)
        self.assertEqual((self.fb / f"{self.date}.txt").read_text(encoding="utf-8"), self.today_before)

    def test_success_adds_landscape_line_after_portrait(self):
        r = self.run_step()
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertTrue((self.art / f"{self.date}-landscape.png").is_file())
        for p, h in self.before.items():   # share/portrait/reel files untouched
            self.assertEqual(sha(p), h, p.name)
        today = (self.fb / "today.txt").read_text(encoding="utf-8")
        ls = f"Landscape: https://gethappyinfo.com/art/{self.date}-landscape.png"
        portrait = f"Portrait: https://gethappyinfo.com/art/{self.date}-portrait.png"
        self.assertIn(f"{portrait}\n{ls}\n\nReel caption:\n", today)
        # nothing else changed: removing the line gives the share step's text back
        self.assertEqual(today.replace(ls + "\n", ""), self.today_before)
        self.assertEqual((self.fb / f"{self.date}.txt").read_text(encoding="utf-8"), today)

    def test_render_failure_leaves_everything_else_intact(self):
        self.break_renderer(write_file=False)
        r = self.run_step()
        self.assertEqual(r.returncode, 1)
        self.assertIn("::warning::Landscape postcard", r.stdout)
        self.assertFalse((self.art / f"{self.date}-landscape.png").exists())
        self.assert_share_intact()
        self.assertIn("Landscape postcard", self.summary.read_text(encoding="utf-8"))

    def test_crash_after_partial_write_removes_file_and_line(self):
        self.break_renderer(write_file=True)
        r = self.run_step()
        self.assertEqual(r.returncode, 1)
        self.assertEqual(sorted(p.name for p in self.art.iterdir()), [f"{self.date}-portrait.png"])
        self.assert_share_intact()

    def test_stale_same_day_landscape_dropped_on_failure(self):
        # a re-run where the share step saw an older landscape for the same date
        (self.art / f"{self.date}-landscape.png").write_bytes(b"old")
        subprocess.run([sys.executable, "scripts/build_fb_handoff.py"], cwd=self.root, check=True,
                       capture_output=True)
        self.assertIn("Landscape:", (self.fb / "today.txt").read_text(encoding="utf-8"))
        self.break_renderer(write_file=False)
        r = self.run_step()
        self.assertEqual(r.returncode, 1)
        self.assertFalse((self.art / f"{self.date}-landscape.png").exists())
        self.assert_share_intact()


if __name__ == "__main__":
    unittest.main()
