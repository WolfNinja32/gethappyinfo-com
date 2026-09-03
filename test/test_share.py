#!/usr/bin/env python3
"""Automated coverage for share-fix option 2 (PR test plan).

Four cases:
1. On `/` — dated URL uses America/Los_Angeles today
2. On `/YYYY-MM-DD` — dated URL uses that archive date
3. Archive-miss — still shares the dated URL for the requested day
4. On-voice share text + button label `✉ pass it on ↗` unchanged

Run from repo root:
  python -m unittest test.test_share
or:
  python test/test_share.py
"""
from __future__ import annotations

import datetime
import json
import re
import subprocess
import unittest
from pathlib import Path
from zoneinfo import ZoneInfo

REPO = Path(__file__).resolve().parent.parent
INDEX = REPO / "public" / "index.html"

BUTTON_LABEL = "✉ pass it on ↗"
SHARE_PREFIX = "Today's kindness from Get Happy: "


def read_index() -> str:
    return INDEX.read_text(encoding="utf-8")


def extract_helpers(html: str) -> str:
    """Pull laTodayIso + datedShareUrl source from the page script."""
    m = re.search(
        r"function laTodayIso\(\) \{.*?function datedShareUrl\(iso\) \{.*?\}",
        html,
        re.S,
    )
    if not m:
        raise AssertionError("laTodayIso / datedShareUrl helpers not found in public/index.html")
    return m.group(0)


def node_eval(helpers: str, expression: str) -> str:
    """Evaluate a JS expression with the page helpers in scope (fixed origin)."""
    script = f"""
const window = {{ location: {{ origin: 'https://gethappyinfo.com' }} }};
{helpers}
const __out = ({expression});
process.stdout.write(String(__out));
"""
    proc = subprocess.run(
        ["node", "-e", script],
        check=True,
        capture_output=True,
        text=True,
    )
    return proc.stdout


class ShareFixTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.html = read_index()
        cls.helpers = extract_helpers(cls.html)

    def test_button_label_unchanged(self):
        """Case 4 (label): send button still exactly ✉ pass it on ↗."""
        self.assertIn(f">{BUTTON_LABEL}</button>", self.html)
        self.assertIn('id="send"', self.html)

    def test_on_voice_share_text(self):
        """Case 4 (copy): share payload uses on-voice kindness sentence + line."""
        self.assertIn(f'var text = "{SHARE_PREFIX}" + line;', self.html)
        self.assertIn("var line = (taskEl.textContent || fallbackTask).trim();", self.html)
        self.assertIn("navigator.share({ title: 'Get Happy Info', text: text, url: shareUrl })", self.html)
        # Clipboard / prompt fallback concatenates text + dated URL
        self.assertIn("var shareMessage = text + ' ' + shareUrl;", self.html)
        # Old generic blurb must be gone
        self.assertNotIn("Take a look at the Happiness Postcard site I found.", self.html)

        line = "Leave a book you loved somewhere for a stranger to find."
        expected = f"{SHARE_PREFIX}{line}"
        got = node_eval(
            self.helpers,
            f'("{SHARE_PREFIX}" + {json.dumps(line)})',
        )
        self.assertEqual(got, expected)

    def test_home_path_uses_la_today(self):
        """Case 1: on `/`, share URL is origin + LA calendar today."""
        la_today = datetime.datetime.now(ZoneInfo("America/Los_Angeles")).date().isoformat()
        js_today = node_eval(self.helpers, "laTodayIso()")
        self.assertEqual(js_today, la_today)

        url = node_eval(
            self.helpers,
            "datedShareUrl((null) || laTodayIso())",
        )
        self.assertEqual(url, f"https://gethappyinfo.com/{la_today}")
        self.assertNotEqual(url, "https://gethappyinfo.com/")

    def test_archive_path_uses_archive_date(self):
        """Case 2: on `/YYYY-MM-DD`, share URL uses that archive date."""
        archive = "2026-05-27"
        url = node_eval(self.helpers, f"datedShareUrl({json.dumps(archive)} || laTodayIso())")
        self.assertEqual(url, f"https://gethappyinfo.com/{archive}")

    def test_archive_miss_sets_dated_url(self):
        """Case 3: renderArchiveMiss sets shareUrl via datedShareUrl(reqDate)."""
        self.assertRegex(
            self.html,
            r"function renderArchiveMiss\(reqDate\) \{[\s\S]*?shareUrl = datedShareUrl\(reqDate\);",
        )
        req = "2026-01-15"
        url = node_eval(self.helpers, f"datedShareUrl({json.dumps(req)})")
        self.assertEqual(url, f"https://gethappyinfo.com/{req}")


if __name__ == "__main__":
    unittest.main()
