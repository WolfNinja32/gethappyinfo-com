"""Story no-reuse: available pool + permanent used archive (Oct 29)."""
from __future__ import annotations

import json

from test.test_update_joy import TmpEnv, uj

GNN = "https://www.goodnewsnetwork.org"


def item(url, title, excerpt="An excerpt."):
    return {"text": title, "url": url, "excerpt": excerpt}


class TestUrlKey(TmpEnv):
    def test_tracking_param_variants_same_key(self):
        base = uj.url_key(f"{GNN}/dog-rescues-kitten/")
        for v in [
            "http://goodnewsnetwork.org/dog-rescues-kitten",
            f"{GNN}/dog-rescues-kitten/?utm_source=fb&utm_medium=social",
            f"{GNN}/dog-rescues-kitten?fbclid=abc123#comments",
            f"{GNN}/dog-rescues-kitten/amp/",
            "https://m.goodnewsnetwork.org/dog-rescues-kitten/?gclid=x",
        ]:
            self.assertEqual(uj.url_key(v), base, v)

    def test_meaningful_query_kept(self):
        self.assertNotEqual(uj.url_key("https://x.org/a?id=1"), uj.url_key("https://x.org/a?id=2"))


class TestLedger(TmpEnv):
    def setUp(self):
        super().setUp()
        uj.today_pt = lambda: self.date

    def test_used_story_rejected(self):
        led = uj.StoryLedger([], [])
        led.mark_used(f"{GNN}/oregon-108-year-old", "Oregon woman turns 108 and still volunteers", "2026-10-25")
        led.add_candidates({"inspiring": [item(f"{GNN}/oregon-108-year-old/", "Oregon woman turns 108 and still volunteers")]}, "2026-10-26")
        self.assertEqual(led.available, [])
        self.assertIsNone(uj.pick_story(led, self.date))

    def test_tracking_param_url_rejected(self):
        led = uj.StoryLedger([], [])
        led.mark_used(f"{GNN}/kingfisher-sits-on-hand", "Kingfisher lands on her hand", "2026-10-16")
        self.assertTrue(led.is_used(f"{GNN}/kingfisher-sits-on-hand/?utm_campaign=rss&fbclid=1"))
        self.assertTrue(led.is_used("http://www.goodnewsnetwork.org/kingfisher-sits-on-hand/amp"))

    def test_same_story_different_url_rejected(self):
        led = uj.StoryLedger([], [])
        led.mark_used(f"{GNN}/joshua-service-cleans-queens-dumping-site",
                      "Joshua Service Turns Queens Dumping Site Into Community Garden", "2026-10-26")
        # syndicated copy, different host + slug, small headline edits
        self.assertTrue(led.is_used("https://news.yahoo.com/man-transforms-dump-123.html",
                                    "Joshua Service turns a Queens dumping site into a community garden"))
        self.assertTrue(led.is_used("https://example.com/x",
                                    "Community Garden in Queens: Joshua Service Turns Dumping Site"))
        # genuinely different story is not blocked
        self.assertFalse(led.is_used("https://example.com/y", "Sea turtles return to Florida beach in record numbers"))

    def test_one_story_per_day(self):
        led = uj.StoryLedger([], [])
        led.add_candidates({
            "inspiring": [item(f"{GNN}/a", "Teen raises money for bikes for kids"),
                          item(f"{GNN}/b", "Neighbors rebuild elderly man's fence overnight")],
            "animals": [item(f"{GNN}/c", "Dog rescues kitten from storm drain")],
        }, self.date.isoformat())
        seed = uj.pick_story(led, self.date)
        self.assertEqual(seed["sourceUrl"], f"{GNN}/a")
        self.assertEqual(len(led.used_on(self.date.isoformat())), 1)
        self.assertEqual(len(led.available), 2)
        self.assertEqual(set(seed), {"summary", "sourceUrl", "sourceTitle"})

    def test_main_publishes_one_and_never_repeats(self):
        feed = {"inspiring": [item(f"{GNN}/a", "Teen raises money for bikes for kids"),
                              item(f"{GNN}/b", "Neighbors rebuild elderly man's fence overnight")]}
        uj.fetch_all = lambda feeds: feed
        seen = []
        for day in (1, 2, 3):
            d = self.date.replace(day=day)
            uj.today_pt = lambda d=d: d
            captured = {}
            uj.write_postcard_paragraph = lambda line, seed, c=captured: c.setdefault("seed", seed) and None
            self.assertEqual(uj.main(force=True), 0)
            seen.append(captured.get("seed"))
        urls = [s["sourceUrl"] for s in seen if s]
        self.assertEqual(urls, [f"{GNN}/a", f"{GNN}/b"])  # day 3: pool exhausted
        self.assertIsNone(seen[2])
        used = json.loads(uj.USED_FILE.read_text())
        self.assertEqual([e["date"] for e in used], ["2026-06-01", "2026-06-02"])

    def test_pool_exhaustion_falls_back_to_last_good(self):
        last = {"date": "2026-05-31", "line": "Bring a treat to share.",
                "paragraph": "He left a foil pan on the counter.",
                "seed": {"summary": "Lasagna.", "sourceUrl": f"{GNN}/lasagna/",
                         "sourceTitle": "Break-room lasagna wins hearts"}}
        uj.JOY_FILE.write_text(json.dumps(last))
        uj.ARCHIVE_FILE.write_text(json.dumps({"days": {"2026-05-31": last}}))
        # only candidate is the already-published story under a tracking URL
        uj.fetch_all = lambda feeds: {"inspiring": [item(f"{GNN}/lasagna?utm_source=x", "Break-room lasagna wins hearts")]}
        called = []
        uj.write_postcard_paragraph = lambda line, seed: called.append(seed) or None
        uj.today_pt = lambda: self.date
        self.assertEqual(uj.main(force=True), 0)
        self.assertEqual(called, [None])  # writer got no (repeat) seed
        joy = json.loads(uj.JOY_FILE.read_text())
        self.assertEqual(joy["date"], "2026-06-01")
        self.assertEqual(joy["paragraph"], last["paragraph"])  # last-good, not blank/invented
        self.assertTrue(joy["paragraph"])

    def test_archive_persistence_never_pruned(self):
        old = [{"url": f"{GNN}/ancient", "key": uj.url_key(f"{GNN}/ancient"),
                "title": "Ancient story about a heroic firefighter dog", "date": "2025-01-01"}]
        uj.USED_FILE.write_text(json.dumps(old))
        uj.AVAILABLE_FILE.write_text("[]")
        uj.fetch_all = lambda feeds: {"inspiring": [item(f"{GNN}/ancient/", "Ancient story about a heroic firefighter dog"),
                                                     item(f"{GNN}/fresh", "Fresh story about a library garden")]}
        uj.today_pt = lambda: self.date
        uj.main(force=True)
        used = json.loads(uj.USED_FILE.read_text())
        self.assertEqual(used[0]["date"], "2025-01-01")  # a year+ old, still there
        self.assertEqual(used[-1]["url"], f"{GNN}/fresh")
        reloaded = uj.StoryLedger.load()
        self.assertTrue(reloaded.is_used(f"{GNN}/fresh?utm_medium=rss"))

    def test_first_run_seeds_used_from_history(self):
        arch = {"days": {"2026-05-20": {"date": "2026-05-20", "line": "x", "paragraph": "y",
                "seed": {"summary": "s", "sourceUrl": f"{GNN}/old-one", "sourceTitle": "Old one about a choir of grandmothers"}}}}
        uj.ARCHIVE_FILE.write_text(json.dumps(arch))
        led = uj.StoryLedger.load()  # no USED_FILE yet
        self.assertTrue(led.is_used(f"{GNN}/old-one/"))

    def test_pool_prunes_stale_but_keeps_used(self):
        led = uj.StoryLedger([{"url": f"{GNN}/s", "key": uj.url_key(f"{GNN}/s"), "title": "Stale thing happened here",
                               "topic": "inspiring", "added": "2026-01-01"}],
                             [{"url": f"{GNN}/u", "title": "", "date": "2020-01-01"}])
        led.prune_pool(self.date)
        self.assertEqual(led.available, [])
        self.assertEqual(len(led.used), 1)
