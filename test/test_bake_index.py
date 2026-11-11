#!/usr/bin/env python3
"""Tests for scripts/bake_index.py and its use in build_og_page.py.

Run: python -m unittest test.test_bake_index. All writes go to a temp scratch repo.
"""
from __future__ import annotations

import html
import io
import json
import re
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "scripts"))
import bake_index as bi  # noqa: E402
import build_og_page as og  # noqa: E402

SITE = "https://gethappyinfo.com"
INDEX = """<!doctype html>
<html><head>
  <meta charset="utf-8" />
  <meta name="description" content="A small daily postcard." />
  <meta property="og:title" content="Get Happy Info" />
  <meta property="og:url" content="https://gethappyinfo.com/" />
  <meta property="og:image" content="https://gethappyinfo.com/og-image.png" />
  <meta name="twitter:image" content="https://gethappyinfo.com/og-image.png" />
  <link rel="canonical" href="https://gethappyinfo.com/" />
  <!-- joy:jsonld:start --><!-- joy:jsonld:end -->
</head><body>
<p class="micro-joy" id="task"><!-- joy:line:start -->Loading a little joy…<!-- joy:line:end --></p>
<ul class="news" id="news"><!-- joy:story:start --><!-- joy:story:end --></ul>
</body></html>
"""
TODAY = {"date": "2026-11-11", "line": "Say <thanks> & mean it.",
         "paragraph": "A \"kind\" story </script><!-- with risky chars.",
         "seed": {"sourceTitle": "Src", "sourceUrl": "https://example.org/a?b=1&c=2"}}
OLD = {"date": "2026-11-10", "line": "Yesterday's line.", "paragraph": "Yesterday's story."}


def ld(page: str) -> dict:
    m = re.search(r'<script type="application/ld\+json">(.*?)</script>', page, re.S)
    assert m, "no JSON-LD"
    return json.loads(m.group(1))


def visible_text(page: str) -> str:
    body = re.sub(r"(?is)<(script|style|noscript)\b.*?</\1>", " ", page)
    body = re.sub(r"(?s)<!--.*?-->", " ", body)
    return " ".join(html.unescape(re.sub(r"<[^>]+>", " ", body)).split())


class Scratch(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        pub = self.root / "public"
        pub.mkdir()
        (pub / "index.html").write_text(INDEX, encoding="utf-8")
        (pub / "joy.json").write_text(json.dumps(TODAY), encoding="utf-8")
        (pub / "joy-archive.json").write_text(
            json.dumps({"days": {"2026-11-10": OLD, "2026-11-11": TODAY,
                                 "2026-05-28": {"dailyTask": "Old task", "lastUpdated": "2026-05-28"}}}),
            encoding="utf-8")

    def tearDown(self):
        self._tmp.cleanup()

    def run_main(self, *argv):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = bi.main(list(argv), repo=self.root)
        return code, out.getvalue() + err.getvalue()

    def index(self):
        return (self.root / "public" / "index.html").read_text(encoding="utf-8")


class BakeIndexTest(Scratch):
    def test_bakes_line_story_jsonld(self):
        code, _ = self.run_main()
        self.assertEqual(code, 0)
        page = self.index()
        self.assertIn("<!-- joy:line:start -->Say &lt;thanks&gt; &amp; mean it.<!-- joy:line:end -->", page)
        self.assertNotIn("</script><!-- with", page)  # paragraph escaped everywhere
        self.assertIn('class="kind-paragraph">A &quot;kind&quot; story &lt;/script&gt;&lt;!-- with risky chars.</p>', page)
        self.assertIn('href="https://example.org/a?b=1&amp;c=2" target="_blank" rel="noopener noreferrer">Story source</a>', page)
        data = ld(page)
        types = [n["@type"] for n in data["@graph"]]
        self.assertEqual(types, ["WebSite", "Organization", "CreativeWork"])
        work = data["@graph"][2]
        self.assertEqual(work["headline"], TODAY["line"])
        self.assertEqual(work["text"], TODAY["paragraph"])
        self.assertEqual(work["datePublished"], "2026-11-11")
        self.assertEqual(work["url"], f"{SITE}/2026-11-11")
        self.assertEqual(work["isBasedOn"]["url"], TODAY["seed"]["sourceUrl"])
        self.assertNotIn("image", work)  # no art files exist → no invented image
        self.assertEqual(data["@graph"][1]["name"], "Get Happy")
        self.assertIn('rel="canonical" href="https://gethappyinfo.com/"', page)

    def test_image_only_when_art_exists(self):
        art = self.root / "public" / "art"
        art.mkdir()
        (art / "2026-11-11-landscape.png").write_bytes(b"x")
        self.run_main()
        self.assertEqual(ld(self.index())["@graph"][2]["image"], f"{SITE}/art/2026-11-11-landscape.png")

    def test_idempotent_and_check(self):
        self.assertEqual(self.run_main("--check")[0], 1)
        self.run_main()
        first = self.index()
        code, out = self.run_main()
        self.assertIn("no change", out)
        self.assertEqual(first, self.index())
        self.assertEqual(self.run_main("--check")[0], 0)

    def test_rebake_replaces_previous_day(self):
        self.run_main()
        (self.root / "public" / "joy.json").write_text(json.dumps(OLD), encoding="utf-8")
        self.run_main()
        page = self.index()
        self.assertIn("Yesterday&#x27;s story.", page)
        self.assertNotIn("mean it", page)
        self.assertEqual(page.count("joy:story:start"), 1)

    def test_no_new_shape_keeps_placeholder(self):
        (self.root / "public" / "joy.json").write_text(json.dumps({"date": "2026-11-11", "line": "x"}),
                                                        encoding="utf-8")
        self.run_main()
        page = self.index()
        self.assertIn("Loading a little joy…<!-- joy:line:end -->", page)
        self.assertIn("<!-- joy:story:start --><!-- joy:story:end -->", page)
        self.assertEqual([n["@type"] for n in ld(page)["@graph"]], ["WebSite", "Organization"])

    def test_missing_markers_warns(self):
        (self.root / "public" / "index.html").write_text("<html><head></head></html>", encoding="utf-8")
        code, out = self.run_main()
        self.assertEqual(code, 1)
        self.assertIn("missing the joy:", out)

    def test_bad_source_url_not_linked(self):
        bad = dict(TODAY, seed={"sourceTitle": "t", "sourceUrl": "javascript:alert(1)"})
        (self.root / "public" / "joy.json").write_text(json.dumps(bad), encoding="utf-8")
        self.run_main()
        page = self.index()
        self.assertNotIn("javascript:", page)
        self.assertNotIn("isBasedOn", page)


class DatedPageTest(Scratch):
    def test_dated_page_gets_own_day_and_canonical(self):
        bi.main([], repo=self.root)
        page, _ = og.bake(self.root, "2026-11-10", no_landscape=True)
        self.assertIn('rel="canonical" href="https://gethappyinfo.com/2026-11-10"', page)
        self.assertIn("Yesterday's line.<!-- joy:line:end -->", page)
        self.assertNotIn("mean it", page)
        work = ld(page)["@graph"][2]
        self.assertEqual(work["url"], f"{SITE}/2026-11-10")
        self.assertEqual(work["headline"], OLD["line"])

    def test_dated_page_old_shape_day_has_placeholder(self):
        bi.main([], repo=self.root)
        page, _ = og.bake(self.root, "2026-05-28", no_landscape=True)
        self.assertIn("Loading a little joy…<!-- joy:line:end -->", page)
        self.assertNotIn("kind-paragraph", page)
        self.assertEqual(len(ld(page)["@graph"]), 2)


class RealIndexTest(unittest.TestCase):
    """Read-only checks on the committed public/index.html."""

    def test_real_index_is_baked_and_fresh(self):
        page = (REPO / "public" / "index.html").read_text(encoding="utf-8")
        self.assertIn('<link rel="canonical" href="https://gethappyinfo.com/" />', page)
        data = ld(page)
        self.assertEqual(data["@graph"][0]["@type"], "WebSite")
        joy = json.loads((REPO / "public" / "joy.json").read_text(encoding="utf-8"))
        if bi.is_new_shape(joy):
            self.assertIn(html.escape(" ".join(joy["line"].split()), quote=False), page)
            self.assertGreater(len(visible_text(page)), 600)

    def test_js_still_overwrites_same_targets(self):
        page = (REPO / "public" / "index.html").read_text(encoding="utf-8")
        self.assertIn("taskEl.textContent = data.line", page)
        self.assertIn("newsEl.innerHTML = '<li style=\"border:0;padding:0;opacity:1;animation:none\">'", page)



class SafeUrlAndSanitizeTest(unittest.TestCase):
    """Hardening: urllib.parse safe_url + workflow-command sanitizer."""

    def test_safe_url_accepts_normal_http_https(self):
        self.assertEqual(bi.safe_url("https://example.org/a?b=1&c=2"),
                         "https://example.org/a?b=1&c=2")
        self.assertEqual(bi.safe_url("http://example.org/path"),
                         "http://example.org/path")
        # scheme case is allowed; original string is returned unchanged
        self.assertEqual(bi.safe_url("HttpS://ok.example/x"),
                         "HttpS://ok.example/x")

    def test_safe_url_rejects_non_http_schemes(self):
        for u in ("javascript:alert(1)", "JAVASCRIPT:alert(1)",
                  "vbscript:msgbox(1)", "data:text/html,<script>alert(1)</script>",
                  "file:///etc/passwd", "//evil.example", "httpsx://evil",
                  "", None):
            self.assertIsNone(bi.safe_url(u), msg=repr(u))

    def test_safe_url_rejects_newline_javascript(self):
        self.assertIsNone(bi.safe_url("https://example.com/foo\njavascript:alert(1)"))
        self.assertIsNone(bi.safe_url("https://example.com/foo\rjavascript:alert(1)"))
        self.assertIsNone(bi.safe_url("https://example.com/foo\r\njavascript:alert(1)"))

    def test_safe_url_rejects_control_chars_and_spaces(self):
        self.assertIsNone(bi.safe_url("https://example.com/foo\x00bar"))
        self.assertIsNone(bi.safe_url("https://example.com/foo\x1fbar"))
        self.assertIsNone(bi.safe_url("https://example.com/foo\x7fbar"))
        self.assertIsNone(bi.safe_url("https://example.com/foo\tbar"))
        self.assertIsNone(bi.safe_url("https://example.com/foo bar"))
        self.assertIsNone(bi.safe_url(" https://example.com/foo"))
        self.assertIsNone(bi.safe_url("https://example.com/foo "))
        self.assertIsNone(bi.safe_url("https://example.com/foo\u2028bar"))

    def test_safe_url_rejects_missing_host(self):
        for u in ("https://", "http://", "https:///", "http:///path",
                  "https:example.com", "https:"):
            self.assertIsNone(bi.safe_url(u), msg=repr(u))

    def test_sanitize_strips_newlines_and_command_injection(self):
        raw = "JSONDecodeError: bad\n::error::pwned\n::warning title=x::y"
        out = bi.sanitize_workflow_message(raw)
        self.assertNotIn("\n", out)
        self.assertNotIn("\r", out)
        self.assertNotIn("::", out)
        self.assertIn("%3A%3A", out)
        # collapsed to a single line of spaces
        self.assertEqual(out, " ".join(out.split()))

    def test_sanitize_escapes_percent_and_truncates(self):
        out = bi.sanitize_workflow_message("100% done")
        self.assertEqual(out, "100%25 done")
        long = "A" * 500
        out = bi.sanitize_workflow_message(long)
        self.assertEqual(len(out), 300)
        self.assertTrue(out.startswith("A" * 300))

    def test_bake_warning_sanitizes_exception_text(self):
        """main() must not print raw newlines or '::' from an exception."""
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        pub = root / "public"
        pub.mkdir()
        (pub / "index.html").write_text(INDEX, encoding="utf-8")
        (pub / "joy.json").write_text(json.dumps(TODAY), encoding="utf-8")

        def boom(*_a, **_k):
            raise ValueError("bad\n::error::injected-command")

        orig = bi.bake_index
        bi.bake_index = boom
        self.addCleanup(lambda: setattr(bi, "bake_index", orig))
        buf_out, buf_err = io.StringIO(), io.StringIO()
        with redirect_stdout(buf_out), redirect_stderr(buf_err):
            rc = bi.main([], repo=root)
        logged = buf_out.getvalue() + buf_err.getvalue()
        self.assertEqual(rc, 1)
        self.assertIn("::warning::index bake failed:", logged)
        payload = logged.split("::warning::index bake failed:", 1)[1]
        self.assertNotIn("\n", payload.strip())
        self.assertNotIn("::", payload)
        self.assertIn("injected-command", payload)


if __name__ == "__main__":
    unittest.main()
