#!/usr/bin/env python3
"""Acceptance tests for postcard pipeline v4 (spec tests 1–12) + leftover helpers.

Run from repo root:
  python -m unittest test.test_update_joy
"""
from __future__ import annotations

import datetime
import importlib.util
import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

REPO = Path(__file__).resolve().parent.parent


def load_module():
    spec = importlib.util.spec_from_file_location("uj", REPO / "scripts" / "update_joy.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


uj = load_module()


def long_excerpt():
    return (
        "Neighbors brought warm meals to a family after a hard week, leaving "
        "casseroles on the porch without asking for thanks or a story in return."
    )


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
            "VOICE_FILE": REPO / "scripts" / "voice.md",
            "VOICE_FALLBACK_FILE": REPO / "scripts" / "voice-fallback.md",
        }
        for name, p in paths.items():
            self._saved[name] = getattr(uj, name)
            setattr(uj, name, p)
        (self.tmp / "public").mkdir(parents=True, exist_ok=True)
        (self.tmp / "data").mkdir(parents=True, exist_ok=True)
        uj.TASKS_FILE.write_text(
            "Smile at a stranger.\n"
            "Compliment the next person you see.\n"
            "Leave a kind note where a stranger will find it.\n",
            encoding="utf-8",
        )
        self.date = datetime.date(2026, 6, 1)
        self._saved_llm = os.environ.get("GHI_LLM")
        os.environ["GHI_LLM"] = "off"
        self._saved_acct = uj.WORKERS_AI_ACCOUNT
        # Ensure offline path does not depend on live creds
        uj.WORKERS_AI_ACCOUNT = ""
        # Snapshot callables tests may monkeypatch
        self._saved_fns = {
            "fetch_all": uj.fetch_all,
            "today_pt": uj.today_pt,
            "write_postcard_paragraph": uj.write_postcard_paragraph,
            "review_postcard": uj.review_postcard,
            "call_workers_ai": uj.call_workers_ai,
            "pick_seed": uj.pick_seed,
        }

    def tearDown(self):
        for name, val in self._saved.items():
            setattr(uj, name, val)
        for name, val in self._saved_fns.items():
            setattr(uj, name, val)
        uj.WORKERS_AI_ACCOUNT = self._saved_acct
        if self._saved_llm is None:
            os.environ.pop("GHI_LLM", None)
        else:
            os.environ["GHI_LLM"] = self._saved_llm
        os.environ.pop("CLOUDFLARE_API_TOKEN", None)


class TestShapeHelpers(unittest.TestCase):
    def test_shape_gate(self):
        """Acceptance 12 (helpers): date-only / line-without-paragraph is not new."""
        self.assertFalse(uj.is_new_shape({"date": "2026-09-04"}))
        self.assertFalse(uj.is_new_shape({"date": "2026-09-04", "line": "Hi", "paragraph": ""}))
        self.assertFalse(uj.is_new_shape({"line": "Hi"}))
        self.assertTrue(uj.is_new_shape({
            "date": "2026-09-04", "line": "Hi", "paragraph": "A short scene."
        }))
        self.assertTrue(uj.is_old_shape({
            "lastUpdated": "2026-09-03", "dailyTask": "Smile", "topNews": []
        }))
        self.assertFalse(uj.is_old_shape({
            "date": "2026-09-04", "line": "Hi", "paragraph": "A short scene."
        }))


class TestJoyContract(TmpEnv):
    def test_new_joy_shape(self):
        """Acceptance 1: four-key shape; seed optional; no ps; no topNews."""
        uj.fetch_all = lambda feeds: {}
        uj.today_pt = lambda: self.date
        rc = uj.main(force=True)
        self.assertEqual(rc, 0)
        joy = json.loads(uj.JOY_FILE.read_text())
        self.assertEqual(joy["date"], "2026-06-01")
        self.assertTrue(joy["line"].strip())
        self.assertTrue(joy["paragraph"].strip())
        self.assertNotIn("ps", joy)
        self.assertNotIn("topNews", joy)
        self.assertNotIn("dailyTask", joy)
        self.assertNotIn("lastUpdated", joy)
        # seed optional — absent on research miss is fine
        if "seed" in joy:
            s = joy["seed"]
            self.assertTrue(s["summary"] and s["sourceUrl"] and s["sourceTitle"])

    def test_line_equals_date_seeded_task(self):
        """Acceptance 2: on new-copy path, line == date-seeded tasks.txt pick."""
        uj.fetch_all = lambda feeds: {}
        uj.today_pt = lambda: self.date
        expected = uj.pick_task(self.date)
        rc = uj.main(force=True)
        self.assertEqual(rc, 0)
        joy = json.loads(uj.JOY_FILE.read_text())
        self.assertEqual(joy["line"], expected)


class TestGroundingModes(TmpEnv):
    def test_seeded_grounding_rejects_extra_name(self):
        """Acceptance 3: seeded draft with name not in seed fails (not written)."""
        seed = {
            "summary": "A neighbor left soup on a porch.",
            "sourceUrl": "https://www.goodnewsnetwork.org/soup/",
            "sourceTitle": "Neighbor Leaves Soup",
        }
        # Simulate writer inventing a name; grounding rejects → reuse/fallback
        uj.fetch_all = lambda feeds: {
            "inspiring": [{
                "text": seed["sourceTitle"],
                "url": seed["sourceUrl"],
                "excerpt": seed["summary"],
                "creator": "R",
                "date": "2026-06-01",
            }],
            "animals": [], "health": [], "earth": [], "science": [],
        }
        uj.today_pt = lambda: self.date
        uj.write_postcard_paragraph = lambda line, seed=None: (
            "Barack Obama personally delivered soup to every porch on Maple Street "
            "and raised $50,000 for the cause overnight."
        )
        uj.review_postcard = lambda paragraph, line=None, seed=None: {
            "ok": False, "reason": "added unsupported name/number"
        }
        # Seed a last-good so we can observe reuse (not fallback)
        last = {
            "date": "2026-05-31",
            "line": "Hold the door for someone.",
            "paragraph": "She held the door. That was the whole story.",
            "seed": seed,
        }
        uj.JOY_FILE.write_text(json.dumps(last), encoding="utf-8")
        rc = uj.main(force=True)
        self.assertEqual(rc, 0)
        joy = json.loads(uj.JOY_FILE.read_text())
        self.assertEqual(joy["date"], "2026-06-01")
        self.assertEqual(joy["line"], last["line"])
        self.assertEqual(joy["paragraph"], last["paragraph"])
        self.assertNotIn("Obama", joy["paragraph"])

    def test_nonese_allows_scenic_rejects_public_figure(self):
        """Acceptance 11: no-seed allows imagined scene; rejects named public figure."""
        scenic = (
            "She kept index cards in the glove box and left one under a wiper "
            "on a street she did not live on. No name. Just noticing."
        )
        bad = (
            "Taylor Swift funded a nationwide note-leaving campaign with Spotify "
            "on March 3, 2024, according to CNN."
        )
        # Direct unit checks via monkeypatched review that mimics prompt contract
        # (GHI_LLM=off auto-passes; call the real prompt path with a stub AI).
        calls = []

        def fake_ai(prompt, model=None, params=None):
            calls.append(prompt)
            if "Taylor Swift" in prompt or "CNN" in prompt:
                return json.dumps({"ok": False, "reason": "named public figure / news"})
            return json.dumps({"ok": True, "reason": "grounded"})

        os.environ.pop("GHI_LLM", None)
        uj.WORKERS_AI_ACCOUNT = "dummy"
        os.environ["CLOUDFLARE_API_TOKEN"] = "dummy-token"
        orig = uj.call_workers_ai
        uj.call_workers_ai = fake_ai
        try:
            ok = uj.review_postcard(scenic, line="Leave a kind note where a stranger will find it.", seed=None)
            bad_v = uj.review_postcard(bad, line="Leave a kind note where a stranger will find it.", seed=None)
        finally:
            uj.call_workers_ai = orig
            os.environ["GHI_LLM"] = "off"
            os.environ.pop("CLOUDFLARE_API_TOKEN", None)
            uj.WORKERS_AI_ACCOUNT = ""
        self.assertTrue(ok["ok"], ok)
        self.assertFalse(bad_v["ok"], bad_v)
        # Must use NOSEED prompt, not seeded
        self.assertTrue(any("imagined" in c.lower() or "TODAY'S LINE" in c for c in calls))
        self.assertFalse(any("SEED summary:" in c for c in calls))


class TestResearchAndDegrade(TmpEnv):
    def test_research_fail_no_invented_seed(self):
        """Acceptance 4: research fail ⇒ no seed; file still written from line."""
        uj.fetch_all = lambda feeds: {t: [] for t in
                                      ["animals", "inspiring", "health", "earth", "science"]}
        uj.today_pt = lambda: self.date
        rc = uj.main(force=True)
        self.assertEqual(rc, 0)
        joy = json.loads(uj.JOY_FILE.read_text())
        self.assertNotIn("seed", joy)
        self.assertEqual(joy["line"], uj.pick_task(self.date))
        self.assertTrue(joy["paragraph"])

    def test_writer_fail_reuses_unit(self):
        """Acceptance 5: writer fail + last-good ⇒ reuse unit; date today; line not rotated."""
        last = {
            "date": "2026-05-31",
            "line": "Bring a treat to share with the people around you.",
            "paragraph": "He left a foil pan on the counter. They ate standing up.",
            "seed": {
                "summary": "A coworker brought lasagna.",
                "sourceUrl": "https://www.goodnewsnetwork.org/lasagna/",
                "sourceTitle": "Break-room lasagna",
            },
        }
        uj.JOY_FILE.write_text(json.dumps(last), encoding="utf-8")
        uj.ARCHIVE_FILE.write_text(json.dumps({"days": {"2026-05-31": last}}), encoding="utf-8")
        uj.fetch_all = lambda feeds: {}
        uj.today_pt = lambda: self.date
        uj.write_postcard_paragraph = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom"))
        new_line = uj.pick_task(self.date)
        self.assertNotEqual(new_line, last["line"])
        rc = uj.main(force=True)
        self.assertEqual(rc, 0)
        joy = json.loads(uj.JOY_FILE.read_text())
        self.assertEqual(joy["date"], "2026-06-01")
        self.assertEqual(joy["line"], last["line"])
        self.assertEqual(joy["paragraph"], last["paragraph"])
        self.assertEqual(joy["seed"], last["seed"])
        # Archive snapshot for today is the reused unit
        arch = json.loads(uj.ARCHIVE_FILE.read_text())
        self.assertEqual(arch["days"]["2026-06-01"]["line"], last["line"])

    def test_first_run_fallback_contains_line(self):
        """Acceptance 6: first-run ⇒ fallback with {{LINE}}; differs per line."""
        uj.fetch_all = lambda feeds: {}
        uj.today_pt = lambda: self.date
        # Force writer fail and no last-good
        uj.write_postcard_paragraph = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom"))
        # Only old-shape history
        old = {"lastUpdated": "2026-05-30", "dailyTask": "Smile", "topNews": []}
        uj.JOY_FILE.write_text(json.dumps(old), encoding="utf-8")
        uj.ARCHIVE_FILE.write_text(json.dumps({"days": {"2026-05-30": old}}), encoding="utf-8")
        line_a = uj.pick_task(self.date)
        rc = uj.main(force=True)
        self.assertEqual(rc, 0)
        joy_a = json.loads(uj.JOY_FILE.read_text())
        self.assertEqual(joy_a["line"], line_a)
        self.assertIn(line_a, joy_a["paragraph"])

        line_b = "Plant something — even a single seed."
        fb_a = uj.render_voice_fallback(line_a)
        fb_b = uj.render_voice_fallback(line_b)
        self.assertNotEqual(fb_a, fb_b)
        self.assertIn(line_a, fb_a)
        self.assertIn(line_b, fb_b)

    def test_fallback_escapes_markdown_specials(self):
        """Advisory: escape */_/`/[ ] from tasks when substituting {{LINE}}."""
        tricky = "Try *this*_and_`that` [link]"
        out = uj.render_voice_fallback(tricky)
        self.assertIn(r"\*", out)
        self.assertIn(r"\_", out)
        self.assertIn(r"\`", out)
        self.assertIn(r"\[", out)
        self.assertIn(r"\]", out)

    def test_missing_cf_account_degrades_green(self):
        """Missing CLOUDFLARE_ACCOUNT_ID → green run + non-blank card + warning."""
        os.environ.pop("GHI_LLM", None)
        os.environ["CLOUDFLARE_API_TOKEN"] = "token-present"
        uj.WORKERS_AI_ACCOUNT = ""  # account id missing
        uj.fetch_all = lambda feeds: {}
        uj.today_pt = lambda: self.date
        buf = io.StringIO()
        with mock.patch("sys.stderr", buf):
            # also capture stdout for ::warning::
            out = io.StringIO()
            with mock.patch("sys.stdout", out):
                rc = uj.main(force=True)
        self.assertEqual(rc, 0)
        joy = json.loads(uj.JOY_FILE.read_text())
        self.assertTrue(joy.get("line") and joy.get("paragraph"))
        combined = out.getvalue() + buf.getvalue()
        self.assertIn("::warning::", combined)
        self.assertTrue(
            "CLOUDFLARE" in combined or "missing" in combined.lower() or "fallback" in combined.lower()
        )
        os.environ["GHI_LLM"] = "off"
        os.environ.pop("CLOUDFLARE_API_TOKEN", None)


class TestDenylistScope(TmpEnv):
    def test_denylist_title_only_keeps_memorial_kindness(self):
        """Acceptance 10: title/feed scope; memorial 'died' kindness title kept;
        paragraphs are never denylist-filtered."""
        title = "Town Plants a Tree Where a Neighbor Died at Peace After Decades of Quiet Kindness"
        self.assertFalse(uj.is_denylisted(title), "memorial-adjacent died should not deny")
        # Violence still denied
        self.assertTrue(uj.is_denylisted("Police Investigate Fatal Shooting Downtown"))
        # Paragraph with hard-truth word is NOT run through denylist by pipeline
        para = "She died last spring. They planted roses where she used to sit."
        # pick_seed must accept the memorial title
        by_topic = {
            "inspiring": [{
                "text": title,
                "url": "https://www.goodnewsnetwork.org/tree-memorial/",
                "excerpt": "Neighbors planted a tree.",
                "creator": "R",
                "date": "2026-06-01",
            }],
            "animals": [], "health": [], "earth": [], "science": [],
        }
        seed = uj.pick_seed(by_topic, set(), self.date)
        self.assertIsNotNone(seed)
        self.assertEqual(seed["sourceTitle"], title)
        # Sanity: denylist function is not applied to paragraph text in helpers
        # (no paragraph_denylist exists)
        self.assertFalse(hasattr(uj, "paragraph_denylist"))


class TestVoicePrompts(unittest.TestCase):
    def test_postcard_prompt_blocks_exist(self):
        pc = uj.load_prompt("POSTCARD PROMPT")
        self.assertIn("{{LINE}}", pc)
        self.assertIn("{{SEED_BLOCK}}", pc)
        seeded = uj.load_prompt("POSTCARD REVIEW SEEDED")
        self.assertIn("{{SUMMARY}}", seeded)
        noseed = uj.load_prompt("POSTCARD REVIEW NOSEED")
        self.assertIn("{{LINE}}", noseed)
        self.assertIn("imagined", noseed.lower())
        # Legacy blocks still load for leftover helpers
        self.assertIn("{{TITLE}}", uj.load_prompt("STORY PROMPT"))


class TestIndexDualRead(unittest.TestCase):
    """Acceptance 8 + 12: index.html dual-read / shape gate (static checks)."""

    @classmethod
    def setUpClass(cls):
        cls.html = (REPO / "public" / "index.html").read_text(encoding="utf-8")

    def test_shape_gate_in_page(self):
        self.assertIn("function isNewShape(data)", self.html)
        self.assertIn("function isOldShape(data)", self.html)
        self.assertIn("data.line.trim()", self.html)
        self.assertIn("data.paragraph.trim()", self.html)

    def test_old_days_hide_topnews(self):
        self.assertIn("function renderOld(data)", self.html)
        self.assertIn("clearWorld()", self.html)
        # Must not map topNews into the news list on the new dual-read path
        self.assertNotIn("data.topNews", self.html)

    def test_new_days_use_line_paragraph(self):
        self.assertIn("function renderNew(data", self.html)
        self.assertIn("data.line", self.html)
        self.assertIn("data.paragraph", self.html)
        self.assertIn("kind-paragraph", self.html)

    def test_meta_not_three_stories(self):
        self.assertNotIn("three pieces of good news", self.html.lower())


class TestWorkflowStdlib(unittest.TestCase):
    """Acceptance 9: AI optional; workflow still stdlib-friendly."""

    def test_daily_yml_hardcodes_account_id_token_from_secret(self):
        yml = (REPO / ".github" / "workflows" / "daily.yml").read_text()
        # Account ID is public/non-secret (hardcoded); only the API token is a secret.
        self.assertIn('CLOUDFLARE_ACCOUNT_ID: "130f3aaa660ea95551e837c8d2ba4b21"', yml)
        self.assertNotIn("secrets.CLOUDFLARE_ACCOUNT_ID", yml)
        self.assertIn("secrets.CLOUDFLARE_API_TOKEN", yml)


if __name__ == "__main__":
    unittest.main(verbosity=2)
