#!/usr/bin/env python3
"""Tests for the V3 content-library additions to update_joy.py — stdlib unittest,
no network, no API key. Run: python -m unittest test.test_update_joy  (from repo root)
or: python test/test_update_joy.py
"""
from __future__ import annotations

import datetime
import importlib.util
import json
import re
import tempfile
import unittest
from html.parser import HTMLParser
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent


def load_module():
    spec = importlib.util.spec_from_file_location("uj", REPO / "scripts" / "update_joy.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


uj = load_module()

# A minimal GNN-shaped RSS item (dc:creator, boilerplate in <description>, RFC-822 date).
FIXTURE = """<?xml version="1.0" encoding="UTF-8"?>
<rss xmlns:dc="http://purl.org/dc/elements/1.1/">
<channel>
<item>
  <title>3 Teens Win Global Earth Prize for Tamarind Powder That Removes Microplastics</title>
  <link>https://www.goodnewsnetwork.org/3-teens-win-global-earth-prize/</link>
  <dc:creator>Andy Corbley</dc:creator>
  <pubDate>Tue, 26 May 2026 13:00:50 +0000</pubDate>
  <description>&lt;p&gt;Three teenagers have won a global prize for inventing a tamarind-based
  powder that pulls microplastics out of water. It &#8230;&#8230; The post 3 Teens Win Global
  Earth Prize appeared first on Good News Network .&lt;/p&gt;</description>
</item>
</channel>
</rss>"""


def long_excerpt():
    return ("A dog that had been saved from a county pound went on to rescue its new owner "
            "from a house fire one winter night, dragging the whole family awake and out the "
            "front door together before the heavy smoke could spread through every single room.")


def sample_item(url="https://www.goodnewsnetwork.org/dog-saved-from-pound/"):
    return {"text": "Dog Saved from Pound Rescues New Owner from House Fire",
            "url": url, "excerpt": long_excerpt(),
            "creator": "Andy Corbley", "date": "2026-06-01"}


class _Sink(HTMLParser):
    """Parses HTML; raising parser errors would surface malformed markup."""
    pass


def well_formed(html_text: str) -> bool:
    try:
        _Sink().feed(html_text)
        return True
    except Exception:
        return False


class TmpEnv(unittest.TestCase):
    """Repoint every update_joy output path into a throwaway dir per test."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self._saved = {}
        paths = {
            "ROOT": self.tmp,
            "STORY_DIR": self.tmp / "public" / "story",
            "KINDNESS_DIR": self.tmp / "public" / "kindness",
            "SITEMAP_FILE": self.tmp / "public" / "sitemap.xml",
            "ROBOTS_FILE": self.tmp / "public" / "robots.txt",
            "LLMS_FILE": self.tmp / "public" / "llms.txt",
            "INDEX_JSON_FILE": self.tmp / "public" / "index.json",
            "TODAY_PAGES_FILE": self.tmp / "public" / "today-pages.json",
            "KINDNESS_SLUGS_FILE": self.tmp / "data" / "kindness-slugs.json",
            "GEN_LOG_FILE": self.tmp / "data" / "generation-log.json",
            "JOY_FILE": self.tmp / "public" / "joy.json",
            "ARCHIVE_FILE": self.tmp / "public" / "joy-archive.json",
            "RECENT_FILE": self.tmp / "data" / "recent.json",
            "TASKS_FILE": self.tmp / "data" / "tasks.txt",
        }
        for name, p in paths.items():
            self._saved[name] = getattr(uj, name)
            setattr(uj, name, p)
        (self.tmp / "public").mkdir(parents=True, exist_ok=True)
        (self.tmp / "data").mkdir(parents=True, exist_ok=True)
        uj.TASKS_FILE.write_text("Smile at a stranger.\nCompliment the next person you see.\n",
                                 encoding="utf-8")
        self.date = datetime.date(2026, 6, 1)

    def tearDown(self):
        for name, val in self._saved.items():
            setattr(uj, name, val)


class TestFeedParse(TmpEnv):
    def test_fetch_feed_extracts_fields(self):
        import io
        class FakeResp(io.BytesIO):
            def __enter__(self): return self
            def __exit__(self, *a): return False
        orig = uj.urllib.request.urlopen
        uj.urllib.request.urlopen = lambda *a, **k: FakeResp(FIXTURE.encode("utf-8"))
        try:
            items = uj.fetch_feed("http://x")
        finally:
            uj.urllib.request.urlopen = orig
        self.assertEqual(len(items), 1)
        it = items[0]
        self.assertEqual(it["creator"], "Andy Corbley")
        self.assertEqual(it["date"], "2026-05-26")
        self.assertIn("microplastics", it["excerpt"])
        self.assertNotIn("appeared first on", it["excerpt"])  # boilerplate stripped
        self.assertNotIn("<p>", it["excerpt"])                # tags stripped

    def test_clean_excerpt_and_pubdate(self):
        self.assertEqual(uj.parse_pub_date("bogus"), "")
        self.assertEqual(uj.clean_excerpt(""), "")
        self.assertNotIn("The post", uj.clean_excerpt("Hello. The post X appeared first on Y ."))


class TestSlugs(TmpEnv):
    def test_slug_from_url_stable_and_safe(self):
        self.assertEqual(uj.slug_from_url("https://x.org/dog-saved-from-pound/"),
                         "dog-saved-from-pound")
        # reserved + date-like bases must not shadow real routes -> hashed suffix
        self.assertTrue(uj.slug_from_url("https://x.org/index/").startswith("index-"))
        self.assertNotEqual(uj.slug_from_url("https://x.org/2026-01-01/"), "2026-01-01")
        # deterministic
        self.assertEqual(uj.slug_from_url("https://x.org/a-b/"),
                         uj.slug_from_url("https://x.org/a-b/"))

    def test_kindness_ledger_authority_and_collision(self):
        led = {}
        a = uj.kindness_slug("Smile at a stranger", led)
        self.assertEqual(a, "smile-at-a-stranger")
        self.assertEqual(uj.kindness_slug("Smile at a stranger", led), a)  # stable
        # two different texts that slugify identically -> suffix, both recorded
        led2 = {"Smile, at a stranger!": "smile-at-a-stranger"}
        b = uj.kindness_slug("Smile at a stranger", led2)
        self.assertEqual(b, "smile-at-a-stranger-2")
        self.assertEqual(len(set(led2.values())), 2)


class TestVoicePrompts(unittest.TestCase):
    def test_prompt_blocks_load_with_placeholders(self):
        story = uj.load_prompt("STORY PROMPT")
        self.assertIn("{{TITLE}}", story)
        self.assertIn("{{EXCERPT}}", story)
        kind = uj.load_prompt("KINDNESS PROMPT")
        self.assertIn("{{TASK}}", kind)
        with self.assertRaises(ValueError):
            uj.load_prompt("NONEXISTENT BLOCK")


class TestGates(TmpEnv):
    def test_groundedness(self):
        self.assertTrue(uj.groundedness_ok("Three teens won.", "3 teens 30000 trees"))
        self.assertFalse(uj.groundedness_ok("It raised 500 dollars.", "no numbers here"))

    def test_groundedness_thousands_separator(self):
        # Faithful reformatting of a real figure must NOT read as a fabricated stat.
        self.assertTrue(uj.groundedness_ok("30,000 volunteers came.", "30000 volunteers"))
        self.assertTrue(uj.groundedness_ok("They planted 1,000,000 trees.", "1000000 trees"))
        self.assertTrue(uj.groundedness_ok("Source had 30000.", "30,000 in source"))
        # A genuinely invented number is still caught.
        self.assertFalse(uj.groundedness_ok("It cost 47,500 dollars.", "no figures at all"))

    def test_headline_distance(self):
        self.assertTrue(uj.headline_distinct("A little good news today",
                                             "Dog Saved from Pound Rescues Owner"))
        self.assertFalse(uj.headline_distinct("Dog Saved from Pound Rescues Owner",
                                              "Dog Saved from Pound Rescues Owner"))

    def test_summary_uniqueness(self):
        self.assertTrue(uj.summary_unique("totally distinct text here", ["something else entirely"]))
        self.assertFalse(uj.summary_unique("the quick brown fox jumps",
                                           ["the quick brown fox jumps over"]))


class TestRender(TmpEnv):
    def _render(self, item=None):
        import os
        os.environ["GHI_LLM"] = "off"
        try:
            item = item or sample_item()
            return uj.render_story_page(item, uj.summarize_story(item), self.date)
        finally:
            os.environ.pop("GHI_LLM", None)

    def test_story_page_render_and_generate_once(self):
        slug = self._render()
        self.assertEqual(slug, "dog-saved-from-pound")
        path = uj.STORY_DIR / slug / "index.html"
        html_text = path.read_text(encoding="utf-8")
        self.assertTrue(well_formed(html_text))
        self.assertIn('rel="canonical"', html_text)
        # JSON-LD parses and is a NewsArticle
        ld = re.search(r'application/ld\+json">(.*?)</script>', html_text, re.S).group(1)
        self.assertEqual(json.loads(ld)["@type"], "NewsArticle")
        # attribution: outbound credit link present; canonical is OURS not the source
        self.assertIn('goodnewsnetwork.org/dog-saved-from-pound', html_text)
        self.assertIn('rel="noopener nofollow"', html_text)
        self.assertIn('<link rel="canonical" href="https://gethappyinfo.com/story/', html_text)
        # copyright: no source-domain <img>
        self.assertNotRegex(html_text, r'<img[^>]+goodnewsnetwork\.org')
        # generate-once: second render returns same slug, file unchanged
        before = path.read_text(encoding="utf-8")
        self.assertEqual(self._render(), slug)
        self.assertEqual(path.read_text(encoding="utf-8"), before)

    def test_gate_failure_skips_and_logs(self):
        bad = {"text": "Dog", "url": "https://www.goodnewsnetwork.org/x/",
               "excerpt": "short", "creator": "", "date": "2026-06-01"}
        # invented numbers + too short -> skipped, logged, no file
        res = uj.render_story_page(
            bad, {"headline": "It raised 999 dollars in 7 minutes flat",
                  "summary": "tiny", "why": "x"}, self.date)
        self.assertIsNone(res)
        log = json.loads(uj.GEN_LOG_FILE.read_text())
        self.assertEqual(len(log), 1)
        self.assertFalse((uj.STORY_DIR / "x").exists())


class TestPlumbingCumulative(TmpEnv):
    def test_sitemap_and_index_are_cumulative(self):
        import os
        os.environ["GHI_LLM"] = "off"
        try:
            # Distinct excerpts so the uniqueness gate doesn't (correctly) dedupe them.
            i1 = sample_item("https://www.goodnewsnetwork.org/story-one/")
            i2 = sample_item("https://www.goodnewsnetwork.org/story-two/")
            i2["excerpt"] = ("A small coastal town replanted its battered dunes with native beach "
                             "grasses all through the spring, and within just a few short weeks the "
                             "long shoreline that winter storms had stripped completely bare was "
                             "holding firm again, slowly giving the nesting shorebirds a quiet, "
                             "sheltered place to return to and raise their young once more.")
            s1 = uj.render_story_page(i1, uj.summarize_story(i1), self.date)
            s2 = uj.render_story_page(i2, uj.summarize_story(i2), self.date)
            led = {}
            k = uj.render_kindness_page("Smile at a stranger",
                                        uj.expand_kindness("Smile at a stranger"), led)
            uj.regenerate_plumbing(self.date)
        finally:
            os.environ.pop("GHI_LLM", None)
        sm = uj.SITEMAP_FILE.read_text()
        self.assertIn(f"/story/{s1}/", sm)
        self.assertIn(f"/story/{s2}/", sm)            # both days/pages present (cumulative)
        self.assertIn(f"/kindness/{k}/", sm)
        self.assertEqual(sm.count(f"/story/{s1}/"), 1)  # one canonical url, no double-index
        idx = json.loads(uj.INDEX_JSON_FILE.read_text())
        self.assertEqual(len(idx["stories"]), 2)
        self.assertEqual(len(idx["kindness"]), 1)
        self.assertTrue(uj.LLMS_FILE.read_text().startswith("# Get Happy"))


class TestDegradeSafe(TmpEnv):
    def _fake_feeds(self):
        # enough clean items for slot 1 (animal) + slots 2-3
        def item(t, u):
            return {"text": t, "url": u, "excerpt": long_excerpt(),
                    "creator": "R", "date": "2026-06-01"}
        return {
            "animals": [item("Rescued Puppy Reunited With Family", "https://www.goodnewsnetwork.org/puppy/")],
            "inspiring": [item("Volunteers Plant a Million Trees", "https://www.goodnewsnetwork.org/trees/")],
            "health": [item("Simple Walk Boosts Mood Study Finds", "https://www.goodnewsnetwork.org/walk/")],
            "earth": [], "science": [],
        }

    def test_summarizer_failure_never_blanks_postcard(self):
        import os
        orig_tok = os.environ.get("CLOUDFLARE_API_TOKEN")
        orig_acct = uj.WORKERS_AI_ACCOUNT
        os.environ["CLOUDFLARE_API_TOKEN"] = "dummy-token"  # take the generation branch
        uj.WORKERS_AI_ACCOUNT = "dummy-acct"
        orig_fetch, orig_sum = uj.fetch_all, uj.summarize_story
        uj.fetch_all = lambda feeds: self._fake_feeds()
        def boom(item):
            raise RuntimeError("API exploded")
        uj.summarize_story = boom
        try:
            rc = uj.main(force=True)
        finally:
            uj.fetch_all, uj.summarize_story = orig_fetch, orig_sum
            uj.WORKERS_AI_ACCOUNT = orig_acct
            if orig_tok is None:
                os.environ.pop("CLOUDFLARE_API_TOKEN", None)
            else:
                os.environ["CLOUDFLARE_API_TOKEN"] = orig_tok
        self.assertEqual(rc, 0)                                  # run still succeeds
        joy = json.loads(uj.JOY_FILE.read_text())                # postcard written
        self.assertEqual(len(joy["topNews"]), uj.NUM_HEADLINES)  # full, not blank
        self.assertTrue(joy["dailyTask"])
        self.assertFalse(any(uj.STORY_DIR.glob("*/index.html")))  # no story pages
        log = json.loads(uj.GEN_LOG_FILE.read_text())            # failures logged
        self.assertTrue(any(e["reason"].startswith("error:") for e in log))

    def test_failed_review_skips_page_and_logs(self):
        import os
        orig_tok = os.environ.get("CLOUDFLARE_API_TOKEN")
        orig_acct = uj.WORKERS_AI_ACCOUNT
        os.environ["CLOUDFLARE_API_TOKEN"] = "dummy-token"  # take the generation branch
        uj.WORKERS_AI_ACCOUNT = "dummy-acct"
        orig_fetch, orig_sum, orig_rev = uj.fetch_all, uj.summarize_story, uj.review_summary
        uj.fetch_all = lambda feeds: self._fake_feeds()
        uj.summarize_story = lambda item: {           # writer succeeds...
            "headline": "A small good thing happened today",
            "summary": "A clean, grounded summary of the item.",
            "why": "It is a real, specific bit of good news.",
        }
        uj.review_summary = lambda item, summary: {   # ...but the reviewer rejects it
            "ok": False, "reason": "added an unsupported fact"}
        try:
            rc = uj.main(force=True)
        finally:
            uj.fetch_all, uj.summarize_story, uj.review_summary = orig_fetch, orig_sum, orig_rev
            uj.WORKERS_AI_ACCOUNT = orig_acct
            if orig_tok is None:
                os.environ.pop("CLOUDFLARE_API_TOKEN", None)
            else:
                os.environ["CLOUDFLARE_API_TOKEN"] = orig_tok
        self.assertEqual(rc, 0)                                   # run still succeeds
        self.assertFalse(any(uj.STORY_DIR.glob("*/index.html")))  # rejected => no page
        log = json.loads(uj.GEN_LOG_FILE.read_text())
        self.assertTrue(any(e["reason"].startswith("review:") for e in log))  # logged as review skip

    def test_no_api_key_is_skip_only_not_failure(self):
        import os
        os.environ.pop("GHI_LLM", None)
        orig_acct = uj.WORKERS_AI_ACCOUNT
        uj.WORKERS_AI_ACCOUNT = ""                      # no creds -> skip-only
        orig_fetch = uj.fetch_all
        uj.fetch_all = lambda feeds: self._fake_feeds()
        try:
            rc = uj.main(force=True)
        finally:
            uj.fetch_all = orig_fetch
            uj.WORKERS_AI_ACCOUNT = orig_acct
        self.assertEqual(rc, 0)
        self.assertTrue(uj.JOY_FILE.exists())
        self.assertFalse(any(uj.STORY_DIR.glob("*/index.html")))

    def test_gen_log_prunes_to_window(self):
        old = (self.date - datetime.timedelta(days=uj.GEN_LOG_DAYS + 5)).isoformat()
        uj.GEN_LOG_FILE.write_text(json.dumps([{"date": old, "reason": "x", "url": "", "slug": ""}]))
        uj.log_skip("new", url="u", date=self.date)
        log = json.loads(uj.GEN_LOG_FILE.read_text())
        self.assertEqual(len(log), 1)               # old pruned, new kept
        self.assertEqual(log[0]["reason"], "new")


if __name__ == "__main__":
    unittest.main(verbosity=2)
