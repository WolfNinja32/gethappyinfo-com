#!/usr/bin/env python3
"""Tests for scripts/build_og_page.py (run: python -m unittest test.test_og_page).

All writes happen in a temp scratch repo; the real public/ is never touched.
Stdlib only (no render deps).
"""
from __future__ import annotations

import io
import json
import re
import shutil
import struct
import subprocess
import sys
import tempfile
import unittest
import zlib
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "scripts"))
import build_og_page as og  # noqa: E402
from build_fb_handoff import SIGN_OFF  # noqa: E402

LINE = "Share your leftovers instead of letting them go to waste."
DATE = "2026-10-01"
SITE = "https://gethappyinfo.com"

INDEX_HTML = """<!doctype html>
<html><head>
  <meta charset="utf-8" />
  <title>Get Happy Info</title>
  <meta property="og:title" content="Get Happy Info — a daily postcard of good" />
  <meta property="og:description" content="One kind act, one short paragraph. Sent to your screen every morning." />
  <meta property="og:type" content="website" />
  <meta property="og:url" content="https://gethappyinfo.com/" />
  <meta property="og:image" content="https://gethappyinfo.com/og-image.png" />
  <meta property="og:image:width" content="1200" />
  <meta property="og:image:height" content="630" />
  <meta name="twitter:card" content="summary_large_image" />
  <meta name="twitter:image" content="https://gethappyinfo.com/og-image.png" />
</head><body><script>
const m = location.pathname.match(/^\\/(\\d{4}-\\d{2}-\\d{2})\\/?$/);
</script></body></html>
"""


def _chunk(ctype: bytes, body: bytes) -> bytes:
    c = ctype + body
    return struct.pack(">I", len(body)) + c + struct.pack(">I", zlib.crc32(c) & 0xFFFFFFFF)


def make_png(path: Path, w: int, h: int, rgb=(0, 0, 0)) -> None:
    """Minimal structurally-valid RGB PNG (stdlib only).

    For the common 1200x630 case, copy the repo og-image.png (fast).
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    if (w, h) == (1200, 630) and rgb == (0, 0, 0):
        src = REPO / "public" / "og-image.png"
        if src.is_file():
            shutil.copyfile(src, path)
            return
    row = b"\x00" + bytes(rgb) * w
    raw = row * h
    ihdr = struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0)
    data = b"\x89PNG\r\n\x1a\n" + _chunk(b"IHDR", ihdr) + _chunk(b"IDAT", zlib.compress(raw, 9)) + _chunk(b"IEND", b"")
    path.write_bytes(data)


def meta(html: str, key: str) -> str | None:
    m = re.search(r'<(?:meta)\s+(?:property|name)="%s"\s+content="([^"]*)"' % re.escape(key), html)
    return m.group(1) if m else None


class ScratchRepoTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        pub = self.root / "public"
        pub.mkdir()
        (pub / "index.html").write_text(INDEX_HTML, encoding="utf-8")
        # fallback image used by index.html
        shutil.copyfile(REPO / "public" / "og-image.png", pub / "og-image.png")
        self.write_joy(DATE, LINE)
        # copy real SIGN_OFF source is imported; scripts dir not needed on disk

    def tearDown(self):
        self._tmp.cleanup()

    def write_joy(self, date, line, archive=None):
        (self.root / "public" / "joy.json").write_text(
            json.dumps({"date": date, "line": line, "paragraph": "p"}), encoding="utf-8")
        days = archive if archive is not None else {}
        (self.root / "public" / "joy-archive.json").write_text(
            json.dumps({"days": days}), encoding="utf-8")

    def touch_landscape(self, date=DATE, size=(1200, 630)):
        make_png(self.root / "public" / "art" / f"{date}-landscape.png", size[0], size[1])

    def run_main(self, *argv):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = og.main(list(argv), repo=self.root)
        return code, out.getvalue(), err.getvalue()

    def page(self, date=DATE):
        return (self.root / "public" / f"{date}.html").read_text(encoding="utf-8")


class LandscapeTagsTest(ScratchRepoTest):
    def test_landscape_tags_when_valid_png(self):
        self.touch_landscape()
        code, stdout, _ = self.run_main("--date", DATE)
        self.assertEqual(code, 0)
        self.assertIn("landscape", stdout)
        html = self.page()
        self.assertEqual(meta(html, "og:image"), f"{SITE}/art/{DATE}-landscape.png")
        self.assertEqual(meta(html, "og:image:width"), "1200")
        self.assertEqual(meta(html, "og:image:height"), "630")
        self.assertEqual(meta(html, "og:url"), f"{SITE}/{DATE}")
        self.assertEqual(meta(html, "og:title"), LINE)
        self.assertEqual(meta(html, "og:description"), SIGN_OFF)
        self.assertEqual(meta(html, "og:type"), "website")
        self.assertEqual(meta(html, "twitter:card"), "summary_large_image")
        self.assertEqual(meta(html, "twitter:image"), f"{SITE}/art/{DATE}-landscape.png")

    def test_fallback_when_missing(self):
        code, stdout, _ = self.run_main("--date", DATE)
        self.assertEqual(code, 0)
        self.assertIn("fallback", stdout)
        html = self.page()
        self.assertEqual(meta(html, "og:image"), f"{SITE}/og-image.png")
        self.assertEqual(meta(html, "twitter:image"), f"{SITE}/og-image.png")
        self.assertEqual(meta(html, "og:image:width"), "1200")
        self.assertEqual(meta(html, "og:image:height"), "630")
        self.assertEqual(meta(html, "og:title"), LINE)
        self.assertEqual(meta(html, "og:description"), SIGN_OFF)

    def test_fallback_wrong_size(self):
        self.touch_landscape(size=(100, 100))
        code, stdout, _ = self.run_main("--date", DATE)
        self.assertEqual(code, 0)
        self.assertIn("not 1200x630", stdout)
        self.assertEqual(meta(self.page(), "og:image"), f"{SITE}/og-image.png")

    def test_fallback_invalid_png(self):
        p = self.root / "public" / "art" / f"{DATE}-landscape.png"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(b"not a png")
        code, stdout, _ = self.run_main("--date", DATE)
        self.assertEqual(code, 0)
        self.assertIn("not a valid PNG", stdout)
        self.assertEqual(meta(self.page(), "og:image"), f"{SITE}/og-image.png")

    def test_no_landscape_flag(self):
        self.touch_landscape()
        code, stdout, _ = self.run_main("--date", DATE, "--no-landscape")
        self.assertEqual(code, 0)
        self.assertIn("fallback", stdout)
        self.assertEqual(meta(self.page(), "og:image"), f"{SITE}/og-image.png")


class LineAndNoInventTest(ScratchRepoTest):
    def test_archive_line_when_not_joy_date(self):
        other = "2026-09-15"
        self.write_joy(DATE, LINE, archive={other: {"line": "Archive line exact words."}})
        code, _, _ = self.run_main("--date", other)
        self.assertEqual(code, 0)
        self.assertEqual(meta(self.page(other), "og:title"), "Archive line exact words.")
        self.assertEqual(meta(self.page(other), "og:description"), SIGN_OFF)

    def test_no_line_keeps_index_title_desc(self):
        self.write_joy(DATE, LINE, archive={})
        other = "2026-09-99"  # invalid… use real missing date
        other = "2026-08-01"
        code, stdout, _ = self.run_main("--date", other)
        self.assertEqual(code, 0)
        self.assertIn("no line", stdout)
        html = self.page(other)
        self.assertEqual(meta(html, "og:title"), "Get Happy Info — a daily postcard of good")
        self.assertEqual(meta(html, "og:description"),
                         "One kind act, one short paragraph. Sent to your screen every morning.")
        # never invent Pass it on without a line
        self.assertNotEqual(meta(html, "og:description"), SIGN_OFF)

    def test_whitespace_collapsed_not_rewritten(self):
        self.write_joy(DATE, "  Hello   world  ")
        self.run_main("--date", DATE)
        self.assertEqual(meta(self.page(), "og:title"), "Hello world")

    def test_html_escape_in_title(self):
        self.write_joy(DATE, 'Say "hi" & <smile>')
        self.run_main("--date", DATE)
        raw = self.page()
        self.assertIn('content="Say &quot;hi&quot; &amp; &lt;smile&gt;"', raw)
        self.assertNotIn('content="Say "hi"', raw)


class HomepageUntouchedTest(ScratchRepoTest):
    def test_index_html_bytes_unchanged(self):
        before = (self.root / "public" / "index.html").read_bytes()
        self.touch_landscape()
        self.run_main("--date", DATE)
        after = (self.root / "public" / "index.html").read_bytes()
        self.assertEqual(before, after)
        # body script preserved on dated page
        self.assertIn("location.pathname.match", self.page())


class IdempotentAndCheckTest(ScratchRepoTest):
    def test_idempotent_no_change_second_run(self):
        self.touch_landscape()
        c1, o1, _ = self.run_main("--date", DATE)
        c2, o2, _ = self.run_main("--date", DATE)
        self.assertEqual(c1, 0)
        self.assertEqual(c2, 0)
        self.assertIn("wrote", o1)
        self.assertIn("no change", o2)

    def test_check_stale_and_fresh(self):
        self.touch_landscape()
        code, _, err = self.run_main("--date", DATE, "--check")
        self.assertEqual(code, 1)
        self.assertIn("STALE", err)
        self.run_main("--date", DATE)
        code, _, err = self.run_main("--date", DATE, "--check")
        self.assertEqual(code, 0)
        self.assertNotIn("STALE", err)

    def test_out_dir_art_dir_dry(self):
        out = self.root / "artifact"
        art = self.root / "artifact" / "art"
        make_png(art / f"{DATE}-landscape.png", 1200, 630)
        code, stdout, _ = self.run_main(
            "--date", DATE, "--out-dir", str(out), "--art-dir", str(art))
        self.assertEqual(code, 0)
        self.assertTrue((out / f"{DATE}.html").is_file())
        self.assertFalse((self.root / "public" / f"{DATE}.html").exists())
        self.assertEqual(meta((out / f"{DATE}.html").read_text(encoding="utf-8"), "og:image"),
                         f"{SITE}/art/{DATE}-landscape.png")


class GitIndexTest(ScratchRepoTest):
    def test_git_index_requires_staged_bytes(self):
        subprocess.run(["git", "init"], cwd=self.root, check=True, capture_output=True)
        subprocess.run(["git", "config", "user.email", "t@t"], cwd=self.root, check=True, capture_output=True)
        subprocess.run(["git", "config", "user.name", "t"], cwd=self.root, check=True, capture_output=True)
        # seed a commit so index works
        (self.root / "README").write_text("x", encoding="utf-8")
        subprocess.run(["git", "add", "README"], cwd=self.root, check=True, capture_output=True)
        subprocess.run(["git", "commit", "-m", "i"], cwd=self.root, check=True, capture_output=True)
        self.touch_landscape()
        # not staged → fallback
        code, stdout, _ = self.run_main("--date", DATE, "--git-index")
        self.assertEqual(code, 0)
        self.assertIn("not staged", stdout)
        self.assertEqual(meta(self.page(), "og:image"), f"{SITE}/og-image.png")
        # stage → landscape
        subprocess.run(["git", "add", f"public/art/{DATE}-landscape.png"],
                       cwd=self.root, check=True, capture_output=True)
        code, stdout, _ = self.run_main("--date", DATE, "--git-index")
        self.assertEqual(code, 0)
        self.assertIn("landscape", stdout.split("(")[-1])  # mode
        self.assertEqual(meta(self.page(), "og:image"), f"{SITE}/art/{DATE}-landscape.png")


class PngSizeTest(unittest.TestCase):
    def test_png_size_rejects_truncated(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "x.png"
            make_png(p, 8, 8)
            p.write_bytes(p.read_bytes()[:-5])
            self.assertIsNone(og.png_size(p))

    def test_png_size_reads_og_image(self):
        size = og.png_size(REPO / "public" / "og-image.png")
        self.assertEqual(size, (1200, 630))


class WorkflowWiringTest(unittest.TestCase):
    def test_dated_page_bake_is_warn_only_in_commit(self):
        wf = (REPO / ".github" / "workflows" / "daily.yml").read_text(encoding="utf-8")
        commit = wf[wf.index("- name: Commit if changed"):]
        self.assertIn("scripts/build_og_page.py --git-index", commit)
        self.assertIn('::warning::Dated page bake failed', commit)
        # must not gate the job on og bake failure
        gate = wf[wf.index("- name: Fail visibly"):]
        self.assertNotIn("build_og_page", gate)
        # bake runs after share paths are staged (git add $SHARE_PATHS appears before)
        share_add = commit.index("git add $SHARE_PATHS")
        og_bake = commit.index("scripts/build_og_page.py --git-index")
        self.assertLess(share_add, og_bake)

    def test_dry_run_includes_dated_page(self):
        wf = (REPO / ".github" / "workflows" / "daily.yml").read_text(encoding="utf-8")
        dry = wf[wf.index("- name: Render (dry run"):wf.index("- name: Upload dry-run artifact")]
        self.assertIn("scripts/build_og_page.py", dry)
        self.assertIn("--no-landscape", dry)
        self.assertIn("og-fallback", dry)

    def test_homepage_not_rewritten_by_script(self):
        src = (REPO / "scripts" / "build_og_page.py").read_text(encoding="utf-8")
        # writes only <date>.html under out_dir; never writes index.html
        self.assertNotIn('index.html").write', src)
        self.assertIn('f"{d}.html"', src)


class RealIndexSmokeTest(unittest.TestCase):
    """Bake against the real public/index.html into a temp out-dir (read-only on repo)."""

    def test_bake_real_index_fallback(self):
        with tempfile.TemporaryDirectory() as d:
            out = Path(d)
            page, mode = og.bake(REPO, "2099-01-01", no_landscape=True)
            self.assertIn("fallback", mode)
            self.assertIn('property="og:image" content="https://gethappyinfo.com/og-image.png"', page)
            self.assertIn('property="og:url" content="https://gethappyinfo.com/2099-01-01"', page)
            # homepage title kept when no line
            self.assertIn("Get Happy Info — a daily postcard of good", page)
            og.write_atomic(out / "2099-01-01.html", page)
            # real index untouched
            head = (REPO / "public" / "index.html").read_text(encoding="utf-8")
            self.assertIn('content="https://gethappyinfo.com/"', head)


if __name__ == "__main__":
    unittest.main()
